import json
from typing import Dict

import paho.mqtt.client as mqtt

from . import state as _state
from .config import *
from .loggers import log
from .sensors import SENSORS

RUNNING = True

def display_sensor_name(base_name: str) -> str:
    return f"{ENTITY_PREFIX} {base_name}".strip() if ENTITY_PREFIX else base_name


def device_info() -> Dict[str, object]:
    return {
        "identifiers": [DEVICE_ID],
        "name": DEVICE_NAME,
        "manufacturer": MANUFACTURER,
        "model": MODEL_NAME,
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
    topic = f"{MQTT_DISCOVERY_PREFIX}/sensor/{DEVICE_ID}/{key}/config"
    payload = {
        "name": display_sensor_name(str(meta["name"])),
        "unique_id": f"{DEVICE_ID}_{key}",
        "state_topic": STATE_TOPIC,
        "value_template": f"{{{{ value_json.{key} }}}}",
        "availability_topic": AVAILABILITY_TOPIC,
        "payload_available": "online",
        "payload_not_available": "offline",
        "device": device_info(),
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

    client.publish(AVAILABILITY_TOPIC, "online", retain=True)
    _state.DISCOVERY_PUBLISHED = True
    log("[HA MQTT] Discovery published")


def on_connect(_client, _userdata, _flags, rc, _properties=None):
    code = int(rc) if rc is not None else -1
    if code == 0:
        log(f"[HA MQTT] Connected to {MQTT_HOST}:{MQTT_PORT}")
        publish_discovery()
        if any(v is not None for v in _state.LAST_STATE.values()):
            client.publish(STATE_TOPIC, json.dumps(_state.LAST_STATE), retain=MQTT_RETAIN)
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



