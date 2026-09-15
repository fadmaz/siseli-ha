import ipaddress
import re
import os

STATE_CACHE_FILE = "/data/state.json"
# Kept out of state.json, which is merged wholesale into LAST_STATE at boot.
DISCOVERY_MARKER_FILE = "/data/discovery_state.json"

INVERTER_IP = os.getenv("INVERTER_IP", "192.168.1.139")
ROUTER_IP = os.getenv("ROUTER_IP", "192.168.1.1")

TARGET_HOST = os.getenv("TARGET_HOST", "8.212.18.157")
TARGET_PORT = int(os.getenv("TARGET_PORT", "1883"))

#: Deprecated and unused: nothing ever opened a socket. Kept for good, because
#: Supervisor validates stored options before an update and a removed key blocks it.
LISTEN_PORT_DEPRECATED = os.getenv("LISTEN_PORT", "").strip()

AUTO_INTERCEPT = os.getenv("AUTO_INTERCEPT", "true").strip().lower() in {"1", "true", "yes", "on"}
INVERTER_MAC_CFG = os.getenv("INVERTER_MAC", "").strip().lower() or None
ROUTER_MAC_CFG = os.getenv("ROUTER_MAC", "").strip().lower() or None

MQTT_HOST = os.getenv("MQTT_HOST", "core-mosquitto")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_USER = os.getenv("MQTT_USER", "").strip()
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD", "")

MQTT_DISCOVERY_PREFIX = os.getenv("MQTT_DISCOVERY_PREFIX", "homeassistant")
DISCOVERY_NODE_RE = re.compile(r"[^a-zA-Z0-9_-]+")


def sanitize_device_id(value: str) -> str:
    """Make DEVICE_ID safe to use as a Home Assistant discovery node id.

    HA's discovery topic matcher accepts only [a-zA-Z0-9_-], so a value with a space
    creates zero entities with nothing logged anywhere. A '+' or '#' is worse: it is a
    wildcard, publishing to it is a protocol violation, and mosquitto closes the
    connection -- which paho then retries, in a loop.

    Case is deliberately preserved. Lowercasing would rename the topics of every user
    whose id contains a capital, turning a safety fix into a breaking change.
    """
    cleaned = DISCOVERY_NODE_RE.sub("_", (value or "").strip()).strip("_")
    return cleaned or "siseli_inverter_1"


DEVICE_ID_RAW = os.getenv("DEVICE_ID", "siseli_inverter_1")
DEVICE_ID = sanitize_device_id(DEVICE_ID_RAW)
DEVICE_NAME = os.getenv("DEVICE_NAME", "Siseli Inverter 1")
MODEL_NAME = os.getenv("MODEL_NAME", DEVICE_NAME)
MANUFACTURER = os.getenv("MANUFACTURER", "Siseli Compatible")
ENTITY_PREFIX = os.getenv("ENTITY_PREFIX", "").strip()
INVERTER_COUNT = int(os.getenv("INVERTER_COUNT", "1"))
BATTERY_COUNT = int(os.getenv("BATTERY_COUNT", "1"))
BATTERY_CAPACITY_PER_BATTERY_AH = float(os.getenv("BATTERY_CAPACITY_PER_BATTERY_AH", "0.0"))

# Blank means "derive from DEVICE_ID". These used to ship a literal default naming
# siseli_inverter_1, so Supervisor always materialised the key, the fallback below was
# unreachable, and changing DEVICE_ID did not move the topics.
_LEGACY_STATE_TOPIC = "siseli/siseli_inverter_1/state"
_LEGACY_AVAILABILITY_TOPIC = "siseli/siseli_inverter_1/availability"

STATE_TOPIC = os.getenv("STATE_TOPIC", "").strip() or f"siseli/{DEVICE_ID}/state"
AVAILABILITY_TOPIC = os.getenv("AVAILABILITY_TOPIC", "").strip() or f"siseli/{DEVICE_ID}/availability"

SNIFF_IFACE = os.getenv("SNIFF_IFACE", "").strip() or None

UPDATE_INTERVAL_SEC = int(os.getenv("UPDATE_INTERVAL_SEC", "10"))
EXPIRE_AFTER_SEC = int(os.getenv("EXPIRE_AFTER_SEC", "1800"))
RESET_ENERGY_COUNTERS = os.getenv("RESET_ENERGY_COUNTERS", "false").strip().lower() in {"1", "true", "yes", "on"}
DISCOVERY_CLEANUP = os.getenv("DISCOVERY_CLEANUP", "true").strip().lower() in {"1", "true", "yes", "on"}
TELEMETRY_TIMEOUT_SEC = int(os.getenv("TELEMETRY_TIMEOUT_SEC", "1800"))

# The availability watchdog floors TELEMETRY_TIMEOUT_SEC at this multiple of the
# largest recently observed gap between decoded payloads, capped by the ceiling.
# Deliberately not add-on options: Supervisor stores an option's value the first time
# the user saves the configuration page, and a stored value shadows every later change
# to the shipped default. TELEMETRY_TIMEOUT_SEC therefore cannot be fixed by raising
# it in config.yaml, so the protection has to come from measured cadence instead.
# Three intervals at the observed 600 s worst-case gap reproduces the 1800 s default.
TELEMETRY_TIMEOUT_MULTIPLIER = float(os.getenv("TELEMETRY_TIMEOUT_MULTIPLIER", "3"))
TELEMETRY_TIMEOUT_CEILING_SEC = int(os.getenv("TELEMETRY_TIMEOUT_CEILING_SEC", "3600"))

# How long after process start the sensors stay available before any payload has been
# decoded. Separate from TELEMETRY_TIMEOUT_SEC, which it used to borrow: the grace was
# the full 1800 s timeout, so a restart on an install whose inverter had gone quiet
# presented the previous run's cached values as current for half an hour.
#
# This bounds process start -> first DECODED payload, which is structurally larger than
# the gap between payloads: the inverter's connection to the cloud is long-lived, so a
# restarted bridge always joins mid-stream, builds a fresh TcpFlowState and discards
# until a frame boundary -- costing the payload in progress. A real 2.6.15 restart log
# shows exactly that, joining on a PINGREQ and taking several health ticks to decode.
#
# So the worst case composes: the 15 s ARP wait, plus one payload lost to the mid-stream
# join, plus one full gap at the measured 600 s worst case. 1200 s is the smallest value
# that cannot flap on the cadence actually measured; 600 would bet that the mid-stream
# join never costs a payload, on the one path where it structurally does.
#
# Still an inference from a related measurement. Nobody has timed process start to first
# payload across restarts; do that and this can come down. Same env-only treatment and
# the same reason as its two neighbours above.
STARTUP_GRACE_SEC = int(os.getenv("STARTUP_GRACE_SEC", "1200"))

# Largest interval the energy integrator will credit in one step. Since 2.6.19 durations
# are measured on time.monotonic(), so a clock step cannot reach the integrator at all;
# what remains for this to bound is a genuinely long real gap -- a wedged stream, or a
# process that stopped being scheduled. Its job is to stop a
# clock jump or a suspended process dumping a fabricated block of kWh -- NOT to bound
# normal operation. It was previously derived from UPDATE_INTERVAL_SEC, which is an
# MQTT publish throttle and has nothing to do with how often the inverter reports:
# at the default that produced a 60 s ceiling against a measured 300 s cadence, so
# every counter accrued a fifth of the real energy. The floor below sits above the
# observed 600 s worst-case gap, and the runtime raises it further from measured
# cadence.
ENERGY_MAX_DT_SEC = int(os.getenv("ENERGY_MAX_DT_SEC", "1200"))
FORWARD_ALL_INVERTER_TRAFFIC = os.getenv("FORWARD_ALL_INVERTER_TRAFFIC", "false").strip().lower() in {"1", "true", "yes", "on"}
MQTT_RETAIN = os.getenv("MQTT_RETAIN", "true").strip().lower() in {"1", "true", "yes", "on"}
LOG_LEVEL_STR = os.getenv("LOG_LEVEL", "info").strip().lower()

#: Fine-grained debug switches. These were previously read from ten separate
#: environment variables that had no add-on option, so from the UI they were all-on
#: (LOG_LEVEL=debug) or all-off -- there was no way to enable just the unparsed-payload
#: dump, which is exactly what someone with an unsupported inverter needs.
#:
#: LOG_VERBOSE is split in two here, because it conflated two different questions:
#:   xray    -- per-frame capture trace, for "no data is arriving at all"
#:   packets -- reassembled MQTT packets, for "data arrives but nothing parses"
DEBUG_FLAG_NAMES = (
    "xray",
    "packets",
    "blocks",
    "state_diff",
    "state_snapshot",
    "raw_json",
    "clean_state",
    "mqtt_topics",
    "mqtt_payload_preview",
    "unparsed_publish",
    "stream_events",
    "null_targets",
)

_ENABLED_DEBUG_FLAGS = {
    flag.strip().lower()
    for flag in os.getenv("DEBUG_FLAGS", "").replace("\n", ",").split(",")
    if flag.strip()
}
UNKNOWN_DEBUG_FLAGS = sorted(_ENABLED_DEBUG_FLAGS - set(DEBUG_FLAG_NAMES))


def _debug(flag: str) -> bool:
    """LOG_LEVEL=debug turns everything on; otherwise honour the explicit list."""
    return LOG_LEVEL_STR == "debug" or flag in _ENABLED_DEBUG_FLAGS


LOG_VERBOSE = _debug("xray")
LOG_PACKETS = _debug("packets")
LOG_BLOCKS = _debug("blocks")
LOG_STATE_DIFF = _debug("state_diff")
LOG_STATE_SNAPSHOT = _debug("state_snapshot")
LOG_RAW_JSON = _debug("raw_json")
LOG_CLEAN_STATE = _debug("clean_state")
LOG_MQTT_TOPICS = _debug("mqtt_topics")
LOG_MQTT_PAYLOAD_PREVIEW = _debug("mqtt_payload_preview")
LOG_UNPARSED_PUBLISH = _debug("unparsed_publish")
LOG_STREAM_EVENTS = _debug("stream_events")
LOG_NULL_TARGETS = _debug("null_targets")

#: The flags actually in effect. Public on purpose: `from .config import *` skips
#: any name beginning with an underscore, so a consumer cannot call _debug().
ACTIVE_DEBUG_FLAGS = tuple(name for name in DEBUG_FLAG_NAMES if _debug(name))

#: Deprecated. Kept in the schema so Supervisor does not reject stored options, but
#: deliberately ignored -- honouring it would preserve the per-packet output it was
#: meant to remove. Kept in the schema for good, for the same reason as LISTEN_PORT.
LOG_VERBOSE_DEPRECATED = os.getenv("LOG_VERBOSE", "").strip().lower() in {"1", "true", "yes", "on"}


STRICT_NUM_RE = re.compile(r"^-?\d+(?:\.\d+)?$")
PRINTABLE_ASCII_RE = re.compile(r"^[ -~]+$")
SLUG_RE = re.compile(r"[^a-z0-9]+")

# Internal, not a user-facing option: bounds how often the state cache is
# rewritten from the capture thread.
STATE_CACHE_INTERVAL_SEC = 30

# Bounds on how long reassembly waits for a segment the sniffer never saw. A passive
# capture drops packets under kernel buffer pressure while the real receiver got them
# and ACKed them, so the retransmission that would fill the gap never arrives.
MAX_PENDING_SEGMENTS = 64
MAX_PENDING_BYTES = 64 * 1024
STREAM_GAP_TIMEOUT_SEC = 5

MAX_MQTT_PACKET = 1024 * 64
STREAM_STALE_SECONDS = 30
MAX_STREAM_BUFFER = 1024 * 256


def validate_config() -> None:
    """Validate critical configuration at startup. Calls sys.exit on fatal errors."""
    import sys

    errors: list = []

    for name, val in [("INVERTER_IP", INVERTER_IP), ("ROUTER_IP", ROUTER_IP)]:
        try:
            ipaddress.ip_address(val)
        except ValueError:
            errors.append(f"{name} is not a valid IP address: {val!r}")

    for name, val in [
        ("TARGET_PORT", TARGET_PORT),
        ("MQTT_PORT", MQTT_PORT),
    ]:
        if not (1 <= val <= 65535):
            errors.append(f"{name} must be 1-65535, got {val}")

    if UPDATE_INTERVAL_SEC < 1:
        errors.append(f"UPDATE_INTERVAL_SEC must be >= 1, got {UPDATE_INTERVAL_SEC}")

    if TELEMETRY_TIMEOUT_SEC < 30:
        errors.append(f"TELEMETRY_TIMEOUT_SEC must be >= 30, got {TELEMETRY_TIMEOUT_SEC}")

    if EXPIRE_AFTER_SEC and TELEMETRY_TIMEOUT_SEC > EXPIRE_AFTER_SEC:
        errors.append(
            f"TELEMETRY_TIMEOUT_SEC ({TELEMETRY_TIMEOUT_SEC}) must not exceed "
            f"EXPIRE_AFTER_SEC ({EXPIRE_AFTER_SEC}); Home Assistant would expire the "
            f"sensors before the bridge decided they were stale"
        )

    if EXPIRE_AFTER_SEC < 0:
        errors.append(f"EXPIRE_AFTER_SEC must be >= 0, got {EXPIRE_AFTER_SEC}")
    elif EXPIRE_AFTER_SEC and UPDATE_INTERVAL_SEC >= EXPIRE_AFTER_SEC:
        # Otherwise the two options fight: the publish throttle would outlast the
        # expiry window and every entity would flap to unavailable between updates.
        errors.append(
            f"UPDATE_INTERVAL_SEC ({UPDATE_INTERVAL_SEC}) must be less than "
            f"EXPIRE_AFTER_SEC ({EXPIRE_AFTER_SEC})"
        )

    if not MQTT_HOST.strip():
        errors.append("MQTT_HOST must not be empty")

    if not TARGET_HOST.strip():
        errors.append("TARGET_HOST must not be empty")
    else:
        # Checked on the exact string, because that is what the capture compares with
        # each packet's destination: a hostname, an IPv6 address or a stray space can
        # never match, and every broker packet would be dropped with no other symptom.
        try:
            ipaddress.IPv4Address(TARGET_HOST)
        except ValueError:
            errors.append(
                f"TARGET_HOST must be an IPv4 address, got {TARGET_HOST!r}: it is compared "
                f"with each packet's destination, so anything else never matches and the "
                f"inverter's cloud traffic would be dropped"
            )

    if DEVICE_ID != "siseli_inverter_1":
        for name, value, legacy in (
            ("STATE_TOPIC", STATE_TOPIC, _LEGACY_STATE_TOPIC),
            ("AVAILABILITY_TOPIC", AVAILABILITY_TOPIC, _LEGACY_AVAILABILITY_TOPIC),
        ):
            if value == legacy:
                # Never rewritten automatically -- that would silently move every
                # entity. The user has to clear the field themselves.
                print(
                    f"[CONFIG WARNING] {name} is still the old default {legacy!r} while "
                    f"DEVICE_ID is {DEVICE_ID!r}. Clear the field to derive it from the "
                    f"device id.",
                    flush=True,
                )

    for name, value in (("STATE_TOPIC", STATE_TOPIC), ("AVAILABILITY_TOPIC", AVAILABILITY_TOPIC)):
        if any(ch in value for ch in "+#") or value.startswith("/") or "//" in value:
            errors.append(f"{name} is not a valid MQTT topic: {value!r}")

    if DEVICE_ID != DEVICE_ID_RAW:
        # A warning, not an error: the sanitised value works, and refusing to start
        # would be worse than quietly correcting it.
        print(
            f"[CONFIG WARNING] DEVICE_ID {DEVICE_ID_RAW!r} contains characters Home "
            f"Assistant's MQTT discovery cannot match; using {DEVICE_ID!r} instead.",
            flush=True,
        )

    if INVERTER_COUNT < 1:
        errors.append(f"INVERTER_COUNT must be >= 1, got {INVERTER_COUNT}")
    if BATTERY_COUNT < 1:
        errors.append(f"BATTERY_COUNT must be >= 1, got {BATTERY_COUNT}")
    if BATTERY_CAPACITY_PER_BATTERY_AH < 0:
        errors.append(
            "BATTERY_CAPACITY_PER_BATTERY_AH must be >= 0, "
            f"got {BATTERY_CAPACITY_PER_BATTERY_AH}"
        )

    data_dir = os.path.dirname(STATE_CACHE_FILE)
    if data_dir:
        try:
            os.makedirs(data_dir, exist_ok=True)
        except OSError as exc:
            print(
                f"[CONFIG WARNING] Cannot create state cache directory {data_dir!r}: {exc}",
                flush=True,
            )

    if UNKNOWN_DEBUG_FLAGS:
        print(
            f"[CONFIG WARNING] Unknown DEBUG_FLAGS ignored: {', '.join(UNKNOWN_DEBUG_FLAGS)}. "
            f"Valid flags: {', '.join(DEBUG_FLAG_NAMES)}",
            flush=True,
        )

    if LISTEN_PORT_DEPRECATED:
        print(
            "[CONFIG WARNING] LISTEN_PORT is unused and ignored; the bridge observes "
            "traffic rather than listening on a socket. It stays in the options only so "
            "existing configurations keep validating.",
            flush=True,
        )

    if LOG_VERBOSE_DEPRECATED:
        print(
            "[CONFIG WARNING] LOG_VERBOSE is deprecated and ignored. Use DEBUG_FLAGS "
            "with 'xray' and/or 'packets' instead.",
            flush=True,
        )

    if _ENABLED_DEBUG_FLAGS and LOG_LEVEL_STR in {"warning", "error"}:
        print(
            f"[CONFIG WARNING] DEBUG_FLAGS are set but LOG_LEVEL is {LOG_LEVEL_STR!r}, "
            f"which suppresses their output. Set LOG_LEVEL to 'info' to see them.",
            flush=True,
        )

    if errors:
        for err in errors:
            print(f"[CONFIG ERROR] {err}", flush=True)
        sys.exit(f"[Config] Aborting: {len(errors)} configuration error(s) found.")
