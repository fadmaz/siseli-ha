import ipaddress
import re
import os

STATE_CACHE_FILE = "/data/state.json"

INVERTER_IP = os.getenv("INVERTER_IP", "192.168.1.139")
ROUTER_IP = os.getenv("ROUTER_IP", "192.168.1.1")

TARGET_HOST = os.getenv("TARGET_HOST", "8.212.18.157")
TARGET_PORT = int(os.getenv("TARGET_PORT", "1883"))
LISTEN_PORT = int(os.getenv("LISTEN_PORT", "18899"))

AUTO_INTERCEPT = os.getenv("AUTO_INTERCEPT", "true").strip().lower() in {"1", "true", "yes", "on"}
INVERTER_MAC_CFG = os.getenv("INVERTER_MAC", "").strip().lower() or None
ROUTER_MAC_CFG = os.getenv("ROUTER_MAC", "").strip().lower() or None

MQTT_HOST = os.getenv("MQTT_HOST", "core-mosquitto")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_USER = os.getenv("MQTT_USER", "").strip()
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD", "")

MQTT_DISCOVERY_PREFIX = os.getenv("MQTT_DISCOVERY_PREFIX", "homeassistant")
DEVICE_ID = os.getenv("DEVICE_ID", "siseli_inverter_1")
DEVICE_NAME = os.getenv("DEVICE_NAME", "Siseli Inverter 1")
MODEL_NAME = os.getenv("MODEL_NAME", DEVICE_NAME)
MANUFACTURER = os.getenv("MANUFACTURER", "Siseli Compatible")
ENTITY_PREFIX = os.getenv("ENTITY_PREFIX", "").strip()
INVERTER_COUNT = int(os.getenv("INVERTER_COUNT", "1"))
BATTERY_COUNT = int(os.getenv("BATTERY_COUNT", "1"))
BATTERY_CAPACITY_PER_BATTERY_AH = float(os.getenv("BATTERY_CAPACITY_PER_BATTERY_AH", "0.0"))

STATE_TOPIC = os.getenv("STATE_TOPIC", f"siseli/{DEVICE_ID}/state")
AVAILABILITY_TOPIC = os.getenv("AVAILABILITY_TOPIC", f"siseli/{DEVICE_ID}/availability")

SNIFF_IFACE = os.getenv("SNIFF_IFACE", "").strip() or None

UPDATE_INTERVAL_SEC = int(os.getenv("UPDATE_INTERVAL_SEC", "10"))
MQTT_RETAIN = os.getenv("MQTT_RETAIN", "true").strip().lower() in {"1", "true", "yes", "on"}
LOG_LEVEL_STR = os.getenv("LOG_LEVEL", "info").strip().lower()

if LOG_LEVEL_STR == "debug":
    LOG_VERBOSE = True
    LOG_BLOCKS = True
    LOG_STATE_DIFF = True
    LOG_STATE_SNAPSHOT = True
    LOG_RAW_JSON = True
    LOG_CLEAN_STATE = True
    LOG_MQTT_TOPICS = True
    LOG_MQTT_PAYLOAD_PREVIEW = True
    LOG_UNPARSED_PUBLISH = True
    LOG_STREAM_EVENTS = True
    LOG_NULL_TARGETS = True
elif LOG_LEVEL_STR in {"warning", "error"}:
    LOG_VERBOSE = False
    LOG_BLOCKS = False
    LOG_STATE_DIFF = False
    LOG_STATE_SNAPSHOT = False
    LOG_RAW_JSON = False
    LOG_CLEAN_STATE = False
    LOG_MQTT_TOPICS = False
    LOG_MQTT_PAYLOAD_PREVIEW = False
    LOG_UNPARSED_PUBLISH = False
    LOG_STREAM_EVENTS = False
    LOG_NULL_TARGETS = False
else:
    LOG_VERBOSE = os.getenv("LOG_VERBOSE", "false").strip().lower() in {"1", "true", "yes", "on"}
    LOG_BLOCKS = os.getenv("LOG_BLOCKS", "false").strip().lower() in {"1", "true", "yes", "on"}
    LOG_STATE_DIFF = os.getenv("LOG_STATE_DIFF", "false").strip().lower() in {"1", "true", "yes", "on"}
    LOG_STATE_SNAPSHOT = os.getenv("LOG_STATE_SNAPSHOT", "false").strip().lower() in {"1", "true", "yes", "on"}
    LOG_RAW_JSON = os.getenv("LOG_RAW_JSON", "false").strip().lower() in {"1", "true", "yes", "on"}
    LOG_CLEAN_STATE = os.getenv("LOG_CLEAN_STATE", "false").strip().lower() in {"1", "true", "yes", "on"}
    LOG_MQTT_TOPICS = os.getenv("LOG_MQTT_TOPICS", "false").strip().lower() in {"1", "true", "yes", "on"}
    LOG_MQTT_PAYLOAD_PREVIEW = os.getenv("LOG_MQTT_PAYLOAD_PREVIEW", "false").strip().lower() in {"1", "true", "yes", "on"}
    LOG_UNPARSED_PUBLISH = os.getenv("LOG_UNPARSED_PUBLISH", "false").strip().lower() in {"1", "true", "yes", "on"}
    LOG_STREAM_EVENTS = os.getenv("LOG_STREAM_EVENTS", "false").strip().lower() in {"1", "true", "yes", "on"}
    LOG_NULL_TARGETS = os.getenv("LOG_NULL_TARGETS", "false").strip().lower() in {"1", "true", "yes", "on"}



STRICT_NUM_RE = re.compile(r"^-?\d+(?:\.\d+)?$")
PRINTABLE_ASCII_RE = re.compile(r"^[ -~]+$")
SLUG_RE = re.compile(r"[^a-z0-9]+")

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
        ("LISTEN_PORT", LISTEN_PORT),
    ]:
        if not (1 <= val <= 65535):
            errors.append(f"{name} must be 1-65535, got {val}")

    if UPDATE_INTERVAL_SEC < 1:
        errors.append(f"UPDATE_INTERVAL_SEC must be >= 1, got {UPDATE_INTERVAL_SEC}")

    if not MQTT_HOST.strip():
        errors.append("MQTT_HOST must not be empty")

    if not TARGET_HOST.strip():
        errors.append("TARGET_HOST must not be empty")

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

    if errors:
        for err in errors:
            print(f"[CONFIG ERROR] {err}", flush=True)
        sys.exit(f"[Config] Aborting: {len(errors)} configuration error(s) found.")
