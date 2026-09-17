import json
from typing import Dict

import paho.mqtt.client as mqtt

from . import state as _state
from .config import *
from .loggers import log, log_error_always
from .sensors import (
    SENSOR_GROUP_TITLES,
    SENSORS,
    get_group_title,
    get_sensor_group,
)

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


def wire_identity() -> Dict[str, object]:
    """Identity the inverter reports about itself, for the HA device registry.

    Home Assistant shows these on the device page header, which is where a user looks
    for a firmware version -- the bridge decodes one the vendor portal itself leaves
    blank, and it was buried in a diagnostic sensor. model is left as the configured
    MODEL_NAME because the user chose it; the wire's model code goes to hw_version so
    nothing configured is overridden.

    Read from the shared state, which load_cached_state has already populated by the
    time discovery is published. On a first-ever start there is no cache and these are
    simply omitted; the next reconnect republishes discovery with them.
    """
    snapshot = _state.snapshot_state()
    fields = {
        "sw_version": snapshot.get("firmware_version"),
        "hw_version": snapshot.get("model_code"),
        "serial_number": snapshot.get("dtu_id"),
    }
    return {k: str(v) for k, v in fields.items() if v}


def device_info(group: str) -> Dict[str, object]:
    if group == "main":
        return {
            "identifiers": [DEVICE_ID],
            "name": DEVICE_NAME,
            "manufacturer": MANUFACTURER,
            "model": MODEL_NAME,
            **wire_identity(),
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
        # Every entity points at the single last-will topic. paho supports exactly
        # one will, so per-group availability meant a broker-detected disconnect
        # marked only the 12 main-group entities unavailable while the other ~191
        # kept showing their last values as though they were live.
        "availability_topic": AVAILABILITY_TOPIC,
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
    if EXPIRE_AFTER_SEC:
        # Backstop for the watchdog itself dying. Every publish rewrites all groups
        # from one snapshot, so no group can expire while telemetry is flowing.
        payload["expire_after"] = EXPIRE_AFTER_SEC

    client.publish(topic, json.dumps(payload), retain=True)
    _state.PUBLISHED_SENSOR_KEYS.add(key)


def publish_discovery() -> None:
    for key in sorted(SENSORS.keys()):
        publish_sensor_discovery(key)

    # The watchdog's current verdict, never a literal. on_connect calls this on every
    # reconnect, so publishing True here re-marked a stale bridge as available and the
    # edge-triggered watchdog could never take it back.
    publish_availability(_state.AVAILABILITY_ONLINE)
    _state.DISCOVERY_PUBLISHED = True
    log("[HA MQTT] Discovery published", level="info")


def publish_availability(online: bool) -> None:
    """Set the single availability topic that every entity references."""
    client.publish(AVAILABILITY_TOPIC, "online" if online else "offline", retain=True)


def stale_discovery_topics(device_id=None) -> set:
    """Discovery topics this configuration will never publish to again.

    A key's group is baked into both the discovery topic and the unique_id, so when
    the grouping changed (v2.5.21 moved the calculated sensors onto the main device)
    the old retained config stayed on the broker and Home Assistant kept the orphan
    entity alive alongside the new one, frozen at its last value.

    Scope is deliberately narrow: regrouping orphans only. Every sensor that is still
    declared keeps its config, including the ones with no decode path.
    """
    target = device_id or DEVICE_ID
    # Built from the real mapping: "main" maps to the bare device id, so a literal
    # "<id>_main" is a topic no version has ever written and must not be swept.
    group_ids = {
        target if group == "main" else "%s_%s" % (target, group)
        for group in SENSOR_GROUP_TITLES
    }
    candidates = {
        "%s/sensor/%s/%s/config" % (MQTT_DISCOVERY_PREFIX, gid, key)
        for key in SENSORS
        for gid in group_ids
    }
    if target != DEVICE_ID:
        return candidates
    live = {
        "%s/sensor/%s/%s/config"
        % (MQTT_DISCOVERY_PREFIX, device_id_for_group(get_sensor_group(key)), key)
        for key in SENSORS
    }
    return candidates - live


def _read_discovery_marker() -> Dict[str, object]:
    try:
        with open(DISCOVERY_MARKER_FILE, "r") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def cleanup_stale_discovery() -> int:
    """Clear retained discovery configs orphaned by a regrouping or a rename.

    Idempotent and gated by a marker file so it runs once rather than on every
    reconnect. The marker lives outside state.json, because that file is merged
    wholesale into LAST_STATE at boot and any key added here would be republished as
    though it were a sensor reading.
    """
    if not DISCOVERY_CLEANUP or _state.DISCOVERY_CLEANED:
        return 0

    marker = _read_discovery_marker()
    previous_id = marker.get("device_id")
    already_done = (
        marker.get("schema") == 1
        and previous_id == DEVICE_ID
        and marker.get("discovery_prefix") == MQTT_DISCOVERY_PREFIX
    )
    if already_done:
        _state.DISCOVERY_CLEANED = True
        return 0

    topics = stale_discovery_topics()
    if previous_id and previous_id != DEVICE_ID:
        topics |= stale_discovery_topics(previous_id)

    # Availability topics from before every entity shared one.
    legacy_availability = {
        availability_topic_for_group(group) for group in SENSOR_GROUP_TITLES
    } - {AVAILABILITY_TOPIC}

    for topic in sorted(topics) + sorted(legacy_availability):
        client.publish(topic, "", retain=True)

    try:
        _state.atomic_write_json(
            DISCOVERY_MARKER_FILE,
            {
                "schema": 1,
                "device_id": DEVICE_ID,
                "discovery_prefix": MQTT_DISCOVERY_PREFIX,
            },
        )
    except Exception as exc:
        log("[HA MQTT] Could not record discovery cleanup marker: %s" % exc, level="warning")

    _state.DISCOVERY_CLEANED = True
    cleared = len(topics) + len(legacy_availability)
    log("[HA MQTT] Cleared %d stale discovery topics" % cleared, level="info")
    return cleared


def publish_grouped_state(state_payload: Dict[str, object]) -> bool:
    """Publish one state topic per device group. True when every publish was accepted.

    The return value exists because paho does not raise when the broker is gone: a
    QoS 0 publish on a disconnected client returns MQTT_ERR_NO_CONN and the message is
    dropped. Every call site used to discard that, so the bridge reported "Published to
    HA" for payloads that reached nothing.
    """
    grouped_state: Dict[str, Dict[str, object]] = {}
    for key, value in list(state_payload.items()):
        group = get_sensor_group(key)
        grouped_state.setdefault(group, {})[key] = value

    delivered = True
    for group, payload in grouped_state.items():
        try:
            result = client.publish(
                state_topic_for_group(group), json.dumps(payload), retain=MQTT_RETAIN
            )
        except Exception as exc:
            # Without this the exception unwinds into parse_payload's handler and is
            # printed as [PARSER ERROR] -- a broker fault attributed to the decoder.
            log_error_always(f"[HA MQTT ERROR] Publish to {group} failed: {exc}")
            return False
        if getattr(result, "rc", 0) != 0:
            delivered = False
    return delivered


def broker_is_connected() -> bool:
    """Whether the client currently has a live connection to the broker."""
    try:
        return bool(client.is_connected())
    except Exception:
        return False


#: The KIND of connection failure currently being reported, or None while connected.
#: A kind rather than a bool, so a repeat stays silent while a CHANGE of kind still gets
#: its line -- an unreachable broker that later starts refusing credentials is new
#: information, and a bool cannot express that. Written only from the paho network
#: thread, which is the single caller of all three callbacks below.
LAST_CONNECT_FAILURE = None


def on_connect(_client, _userdata, _flags, rc, _properties=None):
    global LAST_CONNECT_FAILURE
    # paho does not suppress callback exceptions: anything escaping here propagates
    # out of loop_forever and kills the network thread, after which publish() queues
    # into a dead loop and the bridge goes silent with nothing in the log.
    try:
        code = int(rc) if rc is not None else -1
        if code == 0:
            # Re-armed only on success, and only here. 2.6.21 cleared it in the
            # preamble before rc was read, so a CONNACK that REFUSED the connection
            # re-armed the unreachable message and the two failures cross-contaminated.
            LAST_CONNECT_FAILURE = None
            log(f"[HA MQTT] Connected to {MQTT_HOST}:{MQTT_PORT}", level="info")
            cleanup_stale_discovery()
            publish_discovery()
            # The snapshot is taken under the lock the capture and health threads
            # publish under, so this replay can never land after, and so overwrite, a
            # newer state they already sent.
            with _state.PUBLISH_LOCK:
                snapshot = _state.snapshot_state()
                if any(v is not None for v in snapshot.values()):
                    publish_grouped_state(snapshot)
        else:
            # Gated: a refusing broker sends a CONNACK on every retry, so an ungated
            # line here is one error every reconnect delay for as long as the
            # credentials stay wrong -- thousands a day.
            if LAST_CONNECT_FAILURE != f"rc={code}":
                LAST_CONNECT_FAILURE = f"rc={code}"
                log(
                    f"[HA MQTT ERROR] Broker refused the connection with rc={code}"
                    f" ({'bad credentials' if code in (4, 5) else 'see the MQTT spec'})",
                    level="error",
                )
    except Exception as exc:
        log_error_always(f"[HA MQTT ERROR] on_connect failed: {exc}")


def on_disconnect(_client, _userdata, rc, _properties=None):
    try:
        code = int(rc) if rc is not None else -1
        if code != 0 and _state.RUNNING:
            log(f"[HA MQTT] Disconnected (rc={code}), retrying...", level="warning")
    except Exception as exc:
        log_error_always(f"[HA MQTT ERROR] on_disconnect failed: {exc}")


def on_connect_fail(_client=None, _userdata=None):
    """paho reports an unreachable broker only here.

    connect_async plus loop_start retries forever in the network thread, and on_connect
    fires only on a CONNACK -- so its rc != 0 branch covers a broker that answers and
    refuses, never one that is not there. Without this callback a wrong MQTT_HOST, a
    stopped broker or a blocked port produced no output at all, while the parser went
    on logging "Published to HA" for every payload.

    One line per outage, not per retry: paho retries every few seconds and this would
    otherwise fill the log. on_connect resets it, so a later outage is reported again.
    """
    global LAST_CONNECT_FAILURE
    if LAST_CONNECT_FAILURE == "unreachable":
        return
    LAST_CONNECT_FAILURE = "unreachable"
    log_error_always(
        f"[HA MQTT] Cannot reach the broker at {MQTT_HOST}:{MQTT_PORT}. Retrying in the "
        "background; nothing will be published to Home Assistant until it answers. "
        "Check the host, the port, and that the broker is running."
    )


client.on_connect = on_connect
client.on_connect_fail = on_connect_fail
client.on_disconnect = on_disconnect


def start_mqtt() -> None:
    try:
        client.connect_async(MQTT_HOST, MQTT_PORT, 60)
        client.loop_start()
    except Exception as exc:
        log(f"[HA MQTT ERROR] {exc}", level="error")



