"""Shared test helpers.

Not collected by pytest (``python_files = ["test_*.py"]``). Import as
``from tests.helpers import ...`` -- ``tests/`` is a package and ``siseli_local_bridge/``
is on ``sys.path`` via ``conftest.py``.

Three things live here that the test suite previously duplicated by hand: the base
environment dict, the module-global isolation dance, and MQTT/TCP byte builders.
"""

import base64
import importlib
import json
import os
from collections import namedtuple
from contextlib import contextmanager
from unittest import mock

# ---------------------------------------------------------------- environment

#: Every option the add-on reads, at its shipped default. Tests override selectively.
BASE_ENV = {
    "MQTT_HOST": "core-mosquitto",
    "MQTT_PORT": "1883",
    "MQTT_USER": "",
    "MQTT_PASSWORD": "",
    "TARGET_HOST": "8.212.18.157",
    "TARGET_PORT": "1883",
    "LISTEN_PORT": "18899",
    "INVERTER_IP": "192.168.1.139",
    "ROUTER_IP": "192.168.1.1",
    "INVERTER_MAC": "",
    "ROUTER_MAC": "",
    "AUTO_INTERCEPT": "true",
    "MQTT_DISCOVERY_PREFIX": "homeassistant",
    "DEVICE_ID": "siseli_local_inverter_1",
    "DEVICE_NAME": "Siseli Local Inverter 1",
    "MODEL_NAME": "Siseli Local Inverter 1",
    "MANUFACTURER": "Siseli Compatible",
    "ENTITY_PREFIX": "Siseli",
    "INVERTER_COUNT": "1",
    "BATTERY_COUNT": "1",
    "BATTERY_CAPACITY_PER_BATTERY_AH": "0.0",
    "STATE_TOPIC": "",
    "AVAILABILITY_TOPIC": "",
    "SNIFF_IFACE": "",
    "LOG_VERBOSE": "false",
    "DEBUG_FLAGS": "",
    "LOG_LEVEL": "info",
    "UPDATE_INTERVAL_SEC": "10",
    "EXPIRE_AFTER_SEC": "1800",
    "RESET_ENERGY_COUNTERS": "false",
    "DISCOVERY_CLEANUP": "true",
    "TELEMETRY_TIMEOUT_SEC": "1800",
    "FORWARD_ALL_INVERTER_TRAFFIC": "false",
    "MQTT_RETAIN": "true",
}


@contextmanager
def patched_env(**overrides):
    """BASE_ENV plus overrides, with the real environment cleared.

    ``clear=True`` matters: without it a developer's own LOG_LEVEL leaks in and
    silently changes results between machines.
    """
    env = dict(BASE_ENV)
    env.update({k: str(v) for k, v in overrides.items()})
    with mock.patch.dict(os.environ, env, clear=True):
        yield env


def reload_config(**overrides):
    """Reload config.py under BASE_ENV + overrides. Returns the module."""
    import src.siseli_local_bridge.config as cfg

    with patched_env(**overrides):
        importlib.reload(cfg)
    return cfg


def reload_loggers(**overrides):
    """Reload config.py then loggers.py, in that order.

    loggers.py binds CURRENT_LOG_LEVEL from config at import time, so reloading
    only one of the two leaves them disagreeing.
    """
    import src.siseli_local_bridge.config as cfg
    import src.siseli_local_bridge.loggers as log_mod

    with patched_env(**overrides):
        importlib.reload(cfg)
        importlib.reload(log_mod)
    return log_mod


@contextmanager
def patch_consts(module_path, **consts):
    """Patch config constants ON THE CONSUMING MODULE.

    core/mqtt/parsers all do ``from .config import *``, so they hold bound copies.
    Reloading config.py does not change them -- patch here instead::

        with patch_consts("src.siseli_local_bridge.parsers", INVERTER_COUNT=2):
            ...
    """
    module = importlib.import_module(module_path)
    with mock.patch.multiple(module, **consts):
        yield module


@contextmanager
def capture_logs():
    """Collect everything loggers.log() prints. Yields a growing list of lines."""
    lines = []
    with mock.patch("builtins.print", side_effect=lambda m, **kw: lines.append(str(m))):
        yield lines


@contextmanager
def isolated_state():
    """Save and restore every module global the parser/MQTT layers mutate.

    Replaces the three different ad-hoc idioms the suite used to carry.
    """
    from src.siseli_local_bridge import parsers as parser_mod
    from src.siseli_local_bridge import state as shared_state

    saved_state = dict(shared_state.LAST_STATE)
    saved_published = set(shared_state.PUBLISHED_SENSOR_KEYS)
    saved_discovery = shared_state.DISCOVERY_PUBLISHED
    saved_flows = dict(parser_mod.FLOW_STATES)
    saved_topics = dict(parser_mod.SEEN_MQTT_TOPICS)
    saved_energy_clocks = dict(parser_mod.LAST_ENERGY_TS)
    saved_publish_ts = parser_mod.LAST_PUBLISH_TS
    saved_pending = parser_mod.PENDING_PUBLISH
    saved_evict = parser_mod._FLOW_EVICT_COUNTER
    saved_telemetry_ts = shared_state.LAST_TELEMETRY_TS
    saved_intervals = list(shared_state.TELEMETRY_INTERVALS)
    saved_clamp_logged = parser_mod.ENERGY_DT_CLAMP_LOGGED
    saved_availability = shared_state.AVAILABILITY_ONLINE
    saved_grid_rejected = parser_mod.GRID_VALUE_REJECTED_LOGGED
    saved_unsupported = parser_mod.UNSUPPORTED_PROTOCOL_LOGGED
    saved_current_rejected = parser_mod.BATTERY_CURRENT_REJECTED_LOGGED
    try:
        yield
    finally:
        shared_state.LAST_STATE.clear()
        shared_state.LAST_STATE.update(saved_state)
        shared_state.PUBLISHED_SENSOR_KEYS.clear()
        shared_state.PUBLISHED_SENSOR_KEYS.update(saved_published)
        shared_state.DISCOVERY_PUBLISHED = saved_discovery
        parser_mod.FLOW_STATES.clear()
        parser_mod.FLOW_STATES.update(saved_flows)
        parser_mod.SEEN_MQTT_TOPICS.clear()
        parser_mod.SEEN_MQTT_TOPICS.update(saved_topics)
        parser_mod.LAST_ENERGY_TS.clear()
        parser_mod.LAST_ENERGY_TS.update(saved_energy_clocks)
        parser_mod.LAST_PUBLISH_TS = saved_publish_ts
        parser_mod.PENDING_PUBLISH = saved_pending
        parser_mod._FLOW_EVICT_COUNTER = saved_evict
        shared_state.LAST_TELEMETRY_TS = saved_telemetry_ts
        shared_state.TELEMETRY_INTERVALS.clear()
        shared_state.TELEMETRY_INTERVALS.extend(saved_intervals)
        parser_mod.ENERGY_DT_CLAMP_LOGGED = saved_clamp_logged
        shared_state.AVAILABILITY_ONLINE = saved_availability
        parser_mod.GRID_VALUE_REJECTED_LOGGED = saved_grid_rejected
        parser_mod.UNSUPPORTED_PROTOCOL_LOGGED = saved_unsupported
        parser_mod.BATTERY_CURRENT_REJECTED_LOGGED = saved_current_rejected


# ---------------------------------------------------------------- fake broker

Published = namedtuple("Published", "topic payload retain qos")
PublishResult = namedtuple("PublishResult", "rc mid")


class FakeMqttClient:
    """Stand-in for the module-level ``mqtt.client`` singleton.

    Usage::

        with mock.patch("src.siseli_local_bridge.mqtt.client", FakeMqttClient()) as c:
            ...
    """

    def __init__(self, publish_rc=0):
        self.published = []
        self.retained = {}
        self.will = None
        self.credentials = None
        self.delays = None
        self.connect_calls = []
        self.loop_started = False
        self.disconnected = False
        self.on_connect = None
        self.on_connect_fail = None
        self.on_disconnect = None
        #: what is_connected() reports; publish_rc drives whether a publish is accepted
        self.connected = True
        self.publish_rc = publish_rc
        #: set to an Exception instance to simulate the broker going away
        self.raise_on_publish = None
        self._mid = 0

    # --- the paho surface that mqtt.py / core.py actually touch --------------
    def publish(self, topic, payload=None, qos=0, retain=False):
        if self.raise_on_publish:
            raise self.raise_on_publish
        self._mid += 1
        self.published.append(Published(topic, payload, retain, qos))
        if retain:
            self.retained[topic] = payload
        return PublishResult(self.publish_rc, self._mid)

    def will_set(self, topic, payload=None, qos=0, retain=False):
        self.will = (topic, payload, retain)

    def username_pw_set(self, user, password=None):
        self.credentials = (user, password)

    def reconnect_delay_set(self, min_delay=1, max_delay=120):
        self.delays = (min_delay, max_delay)

    def connect_async(self, host, port, keepalive=60):
        self.connect_calls.append((host, port, keepalive))

    def loop_start(self):
        self.loop_started = True

    def loop_stop(self):
        self.loop_started = False

    def disconnect(self):
        self.disconnected = True
        self.connected = False

    def is_connected(self):
        return self.connected

    # --- assertion sugar ----------------------------------------------------
    def topics(self):
        return [p.topic for p in self.published]

    def last(self, topic):
        for p in reversed(self.published):
            if p.topic == topic:
                return p
        raise AssertionError(f"nothing published to {topic!r}; got {self.topics()}")

    def json_at(self, topic):
        return json.loads(self.last(topic).payload)

    def discovery_configs(self):
        """Every non-empty retained /config payload, keyed by topic."""
        return {
            p.topic: json.loads(p.payload)
            for p in self.published
            if p.topic.endswith("/config") and p.payload
        }

    def cleared_topics(self):
        """Topics that received an empty retained payload (a discovery delete)."""
        return [p.topic for p in self.published if p.retain and not p.payload]


# ------------------------------------------------------- MQTT wire builders


def _varint(n):
    out = bytearray()
    while True:
        byte = n % 128
        n //= 128
        if n:
            byte |= 0x80
        out.append(byte)
        if not n:
            return bytes(out)


def publish_packet(topic="dev/telemetry", payload=b"", qos=0, packet_id=1, dup=False, retain=False):
    """A well-formed MQTT 3.1.1 PUBLISH.

    The default topic contains a ``/`` deliberately -- ``is_reasonable_topic``
    rejects topics without one.
    """
    topic_b = topic.encode()
    variable = len(topic_b).to_bytes(2, "big") + topic_b
    if qos:
        variable += packet_id.to_bytes(2, "big")
    body = variable + payload
    first = 0x30 | (int(dup) << 3) | ((qos & 3) << 1) | int(retain)
    return bytes([first]) + _varint(len(body)) + body


def control_packet(packet_type, body=b"", flags=0):
    """A non-PUBLISH control packet. CONNECT=1, PINGREQ=12, DISCONNECT=14, ..."""
    return bytes([((packet_type << 4) & 0xF0) | (flags & 0x0F)]) + _varint(len(body)) + body


def tcp_segments(data, mss=1460, start_seq=1000):
    """Split a byte stream into [(seq, chunk)] the way the wire would."""
    return [(start_seq + i, data[i:i + mss]) for i in range(0, len(data), mss)]


def reorder(segments, order):
    return [segments[i] for i in order]


def duplicate(segments, index):
    return segments[:index + 1] + [segments[index]] + segments[index + 1:]


# ------------------------------------------------------ payload envelopes


def envelope(blocks, name_key="cn", value_key="co"):
    """Build the JSON publish payload the inverter actually sends.

    ``blocks`` is a mapping or an iterable of ``(name, raw_bytes)`` pairs. The
    wrapper mirrors a real capture: the block list lives at ``b.ct`` inside an
    outer object that also carries routing fields.

    ``name_key``/``value_key`` are parameterised because ``_walk_for_blocks``
    accepts ``cn|code|name|n|c|id`` and ``co|cv|data|d|value|v``.
    """
    items = blocks.items() if hasattr(blocks, "items") else blocks
    ct = [
        {name_key: name, value_key: base64.b64encode(raw).decode()}
        for name, raw in items
    ]
    return json.dumps(
        {
            "c": 1,
            "t": "yqdUBYCD",
            "s": "Cjzbi4fWT",
            "i": 101,
            "e": 0,
            "b": {"sa": "", "ts": "2026-08-20T10:59:26.000+08:00", "lf": 0, "tf": 2, "cf": 2, "ct": ct},
        }
    ).encode()


def envelope_with_prefix_noise(blocks, prefix=b"\x00\x01garbage"):
    """parse_payload scans for the JSON start -- exercise the scan, not the happy path."""
    return prefix + envelope(blocks)


def envelope_unterminated(blocks):
    """Truncated tail -- exercises the rfind('}') recovery branch."""
    return envelope(blocks)[:-3]


# ------------------------------------------------------------ scapy packets


def inverter_packet(
    payload=b"",
    src="192.168.1.139",
    dst="8.212.18.157",
    sport=51234,
    dport=1883,
    seq=1000,
    flags="A",
    src_mac="aa:bb:cc:dd:ee:01",
    dst_mac="aa:bb:cc:dd:ee:02",
):
    """A scapy Ether/IP/TCP frame from the inverter. Import is deferred so the
    rest of this module works in environments without scapy installed."""
    from scapy.all import IP, TCP, Ether, Raw

    pkt = Ether(src=src_mac, dst=dst_mac) / IP(src=src, dst=dst) / TCP(
        sport=sport, dport=dport, seq=seq, flags=flags
    )
    return pkt / Raw(load=payload) if payload else pkt
