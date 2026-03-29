import json
from typing import Dict

import paho.mqtt.client as mqtt

from . import state as _state
from .config import *
from .loggers import log
from .sensors import SENSORS, get_group_title, get_grouped_sensor_keys, get_sensor_group

RUNNING = True

_SECTION_PREFIXES = (
    "Device Info - ",
    "Battery Status - ",
    "BMS Status - ",
    "Grid Status - ",
    "Load Status - ",
    "PV Panel Status - ",
    "Settings - ",
)


def _trim_section_prefix(name: str) -> str:
    for prefix in _SECTION_PREFIXES:
        if name.startswith(prefix):
            return name[len(prefix):]
    return name


def display_sensor_name(base_name: str) -> str:
    trimmed = _trim_section_prefix(base_name)
    return f"{ENTITY_PREFIX} {trimmed}".strip() if ENTITY_PREFIX else trimmed


def device_id_for_group(group: str) -> str:
    if group == "main":
        return DEVICE_ID
    return f"{DEVICE_ID}_{group}"


def state_topic_for_group(group: str) -> str:
    if group == "main":
        return STATE_TOPIC
    if STATE_TOPIC.endswith("/state"):
        return f"{STATE_TOPIC[:-6]}/{group}/state"
    return f"{STATE_TOPIC}/{group}"


def availability_topic_for_group(group: str) -> str:
    if group == "main":
        return AVAILABILITY_TOPIC
    if AVAILABILITY_TOPIC.endswith("/availability"):
        return f"{AVAILABILITY_TOPIC[:-13]}/{group}/availability"
    return f"{AVAILABILITY_TOPIC}/{group}"


def device_info(group: str) -> Dict[str, object]:
    if group == "main":
        return {
            "identifiers": [DEVICE_ID],
            "name": DEVICE_NAME,
            "manufacturer": MANUFACTURER,
            "model": MODEL_NAME,
        }
    group_title = get_group_title(group)
    group_device_id = device_id_for_group(group)
    return {
        "identifiers": [group_device_id],
        "name": f"{DEVICE_NAME} {group_title}".strip(),
        "manufacturer": MANUFACTURER,
        "model": MODEL_NAME,
        "via_device": DEVICE_ID,
    }


def create_mqtt_client() -> mqtt.Client:
    try:
        c = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION1,
            client_id=f"{DEVICE_ID}_bridge",
            protocol=mqtt.MQTTv311,
        )
    except Exception:
        c = mqtt.Client(client_id=f"{DEVICE_ID}_bridge", protocol=mqtt.MQTTv311)

    if MQTT_USER:
        c.username_pw_set(MQTT_USER, MQTT_PASSWORD)

    c.reconnect_delay_set(min_delay=5, max_delay=30)
    c.will_set(AVAILABILITY_TOPIC, "offline", retain=True)
    return c


client = create_mqtt_client()


def publish_sensor_discovery(key: str) -> None:
    if key not in SENSORS:
        return

    meta = SENSORS[key]
    group = get_sensor_group(key)
    group_device_id = device_id_for_group(group)
    topic = f"{MQTT_DISCOVERY_PREFIX}/sensor/{group_device_id}/{key}/config"
    payload = {
        "name": display_sensor_name(str(meta["name"])),
        "unique_id": f"{group_device_id}_{key}",
        "state_topic": state_topic_for_group(group),
        "value_template": f"{{{{ value_json.{key} }}}}",
        "availability_topic": availability_topic_for_group(group),
        "payload_available": "online",
        "payload_not_available": "offline",
        "device": device_info(group),
        "icon": meta.get("icon"),
    }

    if meta.get("unit"):
        payload["unit_of_measurement"] = meta["unit"]
    if meta.get("device_class"):
        payload["device_class"] = meta["device_class"]
    if meta.get("state_class"):
        payload["state_class"] = meta["state_class"]
    if meta.get("entity_category"):
        payload["entity_category"] = meta["entity_category"]
    if "enabled_by_default" in meta:
        payload["enabled_by_default"] = bool(meta["enabled_by_default"])

    client.publish(topic, json.dumps(payload), retain=True)
    _state.PUBLISHED_SENSOR_KEYS.add(key)


def publish_discovery() -> None:
    for key in sorted(SENSORS.keys()):
        publish_sensor_discovery(key)

    for group in get_grouped_sensor_keys():
        client.publish(availability_topic_for_group(group), "online", retain=True)
    _state.DISCOVERY_PUBLISHED = True
    log("[HA MQTT] Discovery published")


def publish_grouped_state(state_payload: Dict[str, object]) -> None:
    grouped_state: Dict[str, Dict[str, object]] = {}
    for key, value in state_payload.items():
        group = get_sensor_group(key)
        grouped_state.setdefault(group, {})[key] = value

    for group, payload in grouped_state.items():
        client.publish(state_topic_for_group(group), json.dumps(payload), retain=MQTT_RETAIN)


def on_connect(_client, _userdata, _flags, rc, _properties=None):
    code = int(rc) if rc is not None else -1
    if code == 0:
        log(f"[HA MQTT] Connected to {MQTT_HOST}:{MQTT_PORT}")
        publish_discovery()
        if any(v is not None for v in _state.LAST_STATE.values()):
            publish_grouped_state(_state.LAST_STATE)
    else:
        log(f"[HA MQTT ERROR] Connection failed with rc={code}")


def on_disconnect(_client, _userdata, rc, _properties=None):
    code = int(rc) if rc is not None else -1
    if code != 0 and RUNNING:
        log(f"[HA MQTT] Disconnected (rc={code}), retrying...")


client.on_connect = on_connect
client.on_disconnect = on_disconnect


def start_mqtt() -> None:
    try:
        client.connect_async(MQTT_HOST, MQTT_PORT, 60)
        client.loop_start()
    except Exception as exc:
        log(f"[HA MQTT ERROR] {exc}")



