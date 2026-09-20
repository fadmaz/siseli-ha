"""A byte-level golden of Device A's startup, decode and reconnect behaviour.

Why this exists: the PI30 change (`docs/PI30_DESIGN.md`) rewires `parse_payload`,
`on_connect`, the discovery sweep, the sensor registry and the startup sequence, while
promising that **every Device A install stays byte-for-byte unchanged**. Nothing in the
suite could have caught a breach of that promise: the existing tests assert individual
behaviours, not the exact bytes, and none of them run the startup that ships.

So this file records what the maintainer's own configuration produces today -- every
MQTT publish in order, with its payload, retain flag and QoS, every log line, the bytes
of `/data/state.json`, and `LAST_STATE` in insertion order, which is the order every
replayed state payload is serialised in.

It is deliberately written **before** any PI30 code, on a release that is confirmed on
real hardware (2.6.24, see risks #6 and #9 in `docs/ARCHITECTURE.md`).

Two things are summarised rather than listed, because stored literally they came to
485 KB -- four times the largest file in the repository -- and both are already asserted
elsewhere, from the registry rather than from a copy of it:

- **Discovery-config payloads** become a hash. There are 207 per reconnect, and their
  fields are pinned one by one in `test_mqtt.py` and `test_sensors.py`. Topic, order,
  retain and QoS stay verbatim, so a reordering or a changed retain still fails here, and
  a changed payload fails naming its topic.
- **A run of sweep clears** becomes one line carrying its length and a hash of the exact
  ordered topics. A sweep emits up to 2691 of them. `test_discovery_migration.py` asserts
  which topics belong in that set; this file pins how many there were, in what order, and
  that nothing else moved around them.

Everything a user's dashboard actually reads is stored in full: every state payload,
availability, `state.json`, and `LAST_STATE` in insertion order.

Regenerate deliberately, never reflexively::

    SISELI_REGEN_GOLDEN=1 py -3.12 -m pytest siseli_bridge/tests/test_device_a_golden.py

and read the diff. A change here is a change every existing installation will see.
"""

import hashlib
import json
import os
import pathlib
import unittest
from unittest import mock

from src.siseli_bridge import config as cfg
from src.siseli_bridge import core
from src.siseli_bridge import loggers
from src.siseli_bridge import mqtt as mqtt_mod
from src.siseli_bridge import parsers as parser_module
from src.siseli_bridge import state as shared_state
from src.siseli_bridge.version import __version__ as VERSION
from tests.captures import CAPTURE_IDENTITY, CAPTURE_TELEMETRY
from tests.helpers import FakeMqttClient, envelope, isolated_state, patched_env

GOLDEN = pathlib.Path(__file__).parent / "golden" / "device_a_startup.json"
REGENERATE = os.getenv("SISELI_REGEN_GOLDEN") == "1"

#: The topic a real payload arrives on. It is the only source of `dtu_id`, so a run
#: without it would not exercise the identity path at all.
TOPIC = "dtu/34545375423553743260/pub/event/dev_prop_post"

#: Every option that shapes a topic, a payload or a startup line. The golden is only
#: meaningful for one configuration, so the test imposes that configuration rather than
#: reading whichever one it happens to run under.
#:
#: The configuration it imposes is the shipped one: `run.sh` exports every option from
#: `config.yaml`'s defaults, and `helpers.BASE_ENV` is that same set. An environment that
#: sets none of them is *not* the same thing -- `config.py`'s own fallback for
#: ENTITY_PREFIX is `""` while `run.sh` passes `Siseli`, and that prefix is in the name of
#: all 207 discovery payloads. Recorded from a bare shell, this file described a
#: configuration no installation runs.
#:
#: Imposing it also makes the test immune to what the rest of the suite does to these
#: values. `config` is reloaded in place by `helpers.reload_config`, and any test that
#: reloads a consuming module re-binds that module's copies from it; before this test
#: existed, one such reload left `core.UPDATE_INTERVAL_SEC` at 700 for the remainder of
#: the session (fixed in `test_core.py`). A golden that skipped whenever it found values
#: it did not expect would have gone silent in CI for a reason no one would have read.
PINNED_OPTIONS = (
    "DEVICE_ID", "DEVICE_NAME", "MANUFACTURER", "MODEL_NAME", "ENTITY_PREFIX",
    "STATE_TOPIC", "AVAILABILITY_TOPIC", "MQTT_DISCOVERY_PREFIX", "MQTT_RETAIN",
    "MQTT_HOST", "MQTT_PORT", "INVERTER_IP", "ROUTER_IP", "TARGET_HOST", "TARGET_PORT",
    "AUTO_INTERCEPT", "FORWARD_ALL_INVERTER_TRAFFIC", "SNIFF_IFACE", "INVERTER_COUNT",
    "BATTERY_COUNT", "BATTERY_CAPACITY_PER_BATTERY_AH", "UPDATE_INTERVAL_SEC",
    "EXPIRE_AFTER_SEC", "TELEMETRY_TIMEOUT_SEC", "DISCOVERY_CLEANUP",
    "RESET_ENERGY_COUNTERS", "STATE_CACHE_INTERVAL_SEC", "LOG_LEVEL_STR", "LOG_VERBOSE",
    "ACTIVE_DEBUG_FLAGS",
)

BOOT_ID = "golden-boot-0000-0000"
START = 10_000.0          # monotonic seconds; fixed so the energy clocks are stable


class _Clock:
    """A monotonic clock the test moves by hand."""

    def __init__(self, now=START):
        self.now = now

    def __call__(self):
        return self.now


class _FixedNow:
    """Stands in for `datetime` so the per-payload line's timestamp is fixed."""

    @staticmethod
    def now():
        import datetime as _dt

        return _dt.datetime(2026, 1, 1, 12, 0, 0)


#: The modules that hold a bound copy of every option, in the order they are patched.
CONSUMERS = (("mqtt", mqtt_mod), ("parsers", parser_module), ("core", core))


def shipped_options():
    """The options `run.sh` exports, as `config.py` parses them.

    Reloads `config` under BASE_ENV to get the parsed values -- ints, bools and the
    topics derived from DEVICE_ID -- rather than restating them here, where they could
    drift from `config.yaml` without anything noticing. It leaves `config` holding the
    shipped configuration, which is the correct resting state for it.
    """
    import importlib

    with patched_env():
        importlib.reload(cfg)
    return {name: getattr(cfg, name) for name in PINNED_OPTIONS if hasattr(cfg, name)}


def _options_now():
    """The options as the running code holds them, per consuming module."""
    seen = {}
    for alias, module in CONSUMERS:
        for name in PINNED_OPTIONS:
            if hasattr(module, name):
                seen[f"{alias}.{name}"] = _jsonable(getattr(module, name))
    seen["loggers.CURRENT_LOG_LEVEL"] = loggers.CURRENT_LOG_LEVEL
    return seen


def _jsonable(value):
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


class TestDeviceAGolden(unittest.TestCase):
    """The behaviour every existing install depends on, recorded exactly.

    Each run below is a step of the real sequence, in the order production performs it:
    a first start with no cache, two payloads and a flush, a restart from the file the
    payloads wrote, then a reconnect with a matching marker and one naming a previous
    DEVICE_ID.
    """

    maxDiff = None

    @classmethod
    def setUpClass(cls):
        cls.shipped = shipped_options()

    def setUp(self):
        for _alias, module in CONSUMERS:
            overrides = {
                name: value for name, value in self.shipped.items() if hasattr(module, name)
            }
            p = mock.patch.multiple(module, **overrides)
            p.start()
            self.addCleanup(p.stop)
        # Log level is normalised once at import, so patching LOG_LEVEL_STR would not
        # reach it; the verdict function reads this name on every line.
        p = mock.patch.object(
            loggers, "CURRENT_LOG_LEVEL", loggers._normalize_level(self.shipped["LOG_LEVEL_STR"])
        )
        p.start()
        self.addCleanup(p.stop)

        ctx = isolated_state()
        ctx.__enter__()
        self.addCleanup(lambda: ctx.__exit__(None, None, None))
        self.path = parser_module.STATE_CACHE_FILE          # a temp dir, via conftest
        self.marker = mqtt_mod.DISCOVERY_MARKER_FILE
        self.clock = _Clock()
        self.lines = []
        self.client = FakeMqttClient()

        for target, value in (
            ("src.siseli_bridge.mqtt.client", self.client),
            ("src.siseli_bridge.parsers.datetime", _FixedNow),
        ):
            p = mock.patch(target, value)
            p.start()
            self.addCleanup(p.stop)
        for module, name, value in (
            (parser_module.time, "monotonic", self.clock),
            (shared_state, "host_boot_id", lambda: BOOT_ID),
        ):
            p = mock.patch.object(module, name, value)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch("builtins.print", side_effect=lambda m, **kw: self.lines.append(str(m)))
        p.start()
        self.addCleanup(p.stop)

    # ---------------------------------------------------------------- recording

    @staticmethod
    def _entry(published):
        """One publish as one line: topic, retain, qos, payload, tab-separated.

        One line per publish so a diff points at the publish that changed, and so the
        1449 clears a previous-DEVICE_ID sweep emits do not cost six lines each. A config
        payload is reduced to a hash; see the module docstring.
        """
        payload = published.payload
        if "/config" in published.topic and payload:
            payload = "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]
        return (
            f"{published.topic}\t{int(bool(published.retain))}\t{published.qos}\t{payload or ''}"
        )

    @staticmethod
    def _collapse_clears(entries):
        """Fold each run of retained empty publishes into one summarised line."""
        out, run = [], []

        def flush():
            if not run:
                return
            digest = hashlib.sha256("\n".join(run).encode("utf-8")).hexdigest()[:32]
            out.append(
                f"<{len(run)} cleared topics>\tsha256:{digest}\tfirst={run[0]}\tlast={run[-1]}"
            )
            run.clear()

        for entry in entries:
            topic, retain, qos, payload = entry.split("\t", 3)
            if payload == "" and retain == "1":
                run.append(topic)
                continue
            flush()
            out.append(entry)
        flush()
        return out

    def _take(self):
        """Everything published and logged since the last call."""
        published = self._collapse_clears([self._entry(entry) for entry in self.client.published])
        self.client.published = []
        self.client.retained = {}
        lines, self.lines = [line.replace(VERSION, "<VERSION>") for line in self.lines], []
        return {"published": published, "log": lines}

    def _reset_process(self):
        """Forget everything a restart forgets, keeping what /data keeps."""
        shared_state.LAST_STATE.clear()
        shared_state.PUBLISHED_SENSOR_KEYS.clear()
        shared_state.DISCOVERY_PUBLISHED = False
        shared_state.DISCOVERY_CLEANED = False
        shared_state.LAST_TELEMETRY_TS = 0.0
        shared_state.TELEMETRY_INTERVALS.clear()
        parser_module.LAST_ENERGY_TS.clear()
        parser_module.RESTORED_ENERGY_DOMAINS.clear()
        parser_module.LAST_PUBLISH_TS = 0.0
        parser_module.PENDING_PUBLISH = False
        parser_module.LAST_CACHE_WRITE_TS = 0.0

    def _write_marker(self, device_id):
        shared_state.atomic_write_json(
            self.marker,
            {"schema": 1, "device_id": device_id, "discovery_prefix": mqtt_mod.MQTT_DISCOVERY_PREFIX},
        )
        shared_state.DISCOVERY_CLEANED = False

    def _record(self):
        runs = {}

        # (f) A first start: no cache, no marker, then the broker connects.
        self._reset_process()
        core.prepare_startup_state(self.path)
        mqtt_mod.on_connect(None, None, None, 0)
        runs["f_first_start"] = self._take()

        # (c) Two payloads a real device sends together, then the deferred flush.
        parser_module.SolarParser.parse_payload(envelope(CAPTURE_TELEMETRY), source_topic=TOPIC)
        self.clock.now = START + 2
        parser_module.SolarParser.parse_payload(envelope(CAPTURE_IDENTITY), source_topic=TOPIC)
        self.clock.now = START + 15
        core.publish_tick()
        runs["c_payloads_and_flush"] = self._take()
        runs["c_payloads_and_flush"]["state_json"] = json.loads(
            pathlib.Path(self.path).read_text(encoding="utf-8")
        )

        # (s) A restart: only /data survives. LAST_STATE is compared in order, because
        # that order is the byte order of every replayed state payload.
        self.clock.now = START + 300
        self._reset_process()
        core.prepare_startup_state(self.path)
        runs["s_restart_from_cache"] = self._take()
        runs["s_restart_from_cache"]["last_state"] = [
            [key, _jsonable(value)] for key, value in shared_state.LAST_STATE.items()
        ]

        # (b) The reconnect an existing install performs, with its marker already written.
        self._write_marker(mqtt_mod.DEVICE_ID)
        mqtt_mod.on_connect(None, None, None, 0)
        runs["b_reconnect_matching_marker"] = self._take()

        # (d) The one case that sweeps another id's topics: DEVICE_ID was changed.
        self._write_marker("siseli_inverter_previous")
        mqtt_mod.on_connect(None, None, None, 0)
        runs["d_marker_names_previous_device_id"] = self._take()

        return {"recorded_with": _options_now(), "runs": runs}

    # ---------------------------------------------------------------- the test

    def test_device_a_behaviour_is_unchanged(self):
        actual = self._record()

        if REGENERATE:
            GOLDEN.parent.mkdir(exist_ok=True)
            GOLDEN.write_text(json.dumps(actual, indent=1, sort_keys=False) + "\n", encoding="utf-8")
            self.skipTest(f"regenerated {GOLDEN.name}; read the diff before committing it")

        self.assertTrue(GOLDEN.is_file(), f"{GOLDEN} is missing; regenerate it deliberately")
        expected = json.loads(GOLDEN.read_text(encoding="utf-8"))

        # The test imposes this configuration, so a difference is never the developer's
        # environment: an option was renamed, removed, re-defaulted, or is no longer held
        # by the module that shapes the bytes. Each of those changes what installations
        # see, which is exactly what this file exists to notice.
        self.assertEqual(
            actual["recorded_with"], expected["recorded_with"],
            "the shipped configuration changed; regenerate the golden deliberately",
        )

        for name in expected["runs"]:
            with self.subTest(run=name):
                want, got = expected["runs"][name], actual["runs"].get(name)
                self.assertIsNotNone(got, f"run {name} was not recorded")
                for part in want:
                    self.assertEqual(
                        got.get(part), want[part],
                        f"{name}.{part} differs from the golden -- every Device A install "
                        f"sees this change",
                    )
        self.assertEqual(sorted(actual["runs"]), sorted(expected["runs"]))


if __name__ == "__main__":
    unittest.main()
