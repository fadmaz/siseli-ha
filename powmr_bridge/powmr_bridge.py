import base64
import json
import logging
import os
import signal
import threading
import time
import warnings
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import paho.mqtt.client as mqtt
from scapy.all import ARP, Ether, IP, Raw, TCP, UDP, AsyncSniffer, getmacbyip, sendp  # type: ignore

warnings.filterwarnings("ignore", category=DeprecationWarning)
logging.getLogger("scapy.runtime").setLevel(logging.ERROR)

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
DEVICE_ID = os.getenv("DEVICE_ID", "powmr_rwb1")
DEVICE_NAME = os.getenv("DEVICE_NAME", "PowMr 6.2kW Inverter")
STATE_TOPIC = os.getenv("STATE_TOPIC", f"powmr/{DEVICE_ID}/state")
AVAILABILITY_TOPIC = os.getenv("AVAILABILITY_TOPIC", f"powmr/{DEVICE_ID}/availability")

SNIFF_IFACE = os.getenv("SNIFF_IFACE", "").strip() or None
LOG_VERBOSE = os.getenv("LOG_VERBOSE", "true").strip().lower() in {"1", "true", "yes", "on"}

INV_MAC: Optional[str] = None
RTR_MAC: Optional[str] = None
RUNNING = True
DISCOVERY_PUBLISHED = False
LAST_STATE: Dict[str, object] = {}
sniffer: Optional[AsyncSniffer] = None

FLOW_BUFFERS: Dict[Tuple[str, int, str, int], bytearray] = {}
SEGMENT_CACHE: Dict[Tuple[str, int, str, int], Dict[Tuple[int, int, bytes], float]] = {}

KNOWN_INVERTER_MACS = set()
KNOWN_ROUTER_MACS = set()
LAST_PACKET_TS = 0.0

SENSORS: Dict[str, Dict[str, object]] = {
    "grid_v": {
        "name": "Grid Voltage",
        "unit": "V",
        "device_class": "voltage",
        "state_class": "measurement",
        "icon": "mdi:transmission-tower",
    },
    "grid_hz": {
        "name": "Grid Frequency",
        "unit": "Hz",
        "device_class": "frequency",
        "state_class": "measurement",
        "icon": "mdi:current-ac",
    },
    "out_v": {
        "name": "Output Voltage",
        "unit": "V",
        "device_class": "voltage",
        "state_class": "measurement",
        "icon": "mdi:power-plug",
    },
    "out_hz": {
        "name": "Output Frequency",
        "unit": "Hz",
        "device_class": "frequency",
        "state_class": "measurement",
        "icon": "mdi:current-ac",
    },
    "load_w": {
        "name": "Active Load",
        "unit": "W",
        "device_class": "power",
        "state_class": "measurement",
        "icon": "mdi:home-lightning-bolt",
    },
    "apparent_va": {
        "name": "Apparent Load",
        "unit": "VA",
        "device_class": "apparent_power",
        "state_class": "measurement",
        "icon": "mdi:flash",
    },
    "bat_v": {
        "name": "Battery Voltage",
        "unit": "V",
        "device_class": "voltage",
        "state_class": "measurement",
        "icon": "mdi:battery",
    },
    "bat_cap": {
        "name": "Battery Capacity",
        "unit": "%",
        "device_class": "battery",
        "state_class": "measurement",
        "icon": "mdi:battery-high",
    },
    "dischg_current": {
        "name": "Battery Discharge Current",
        "unit": "A",
        "device_class": "current",
        "state_class": "measurement",
        "icon": "mdi:battery-minus",
    },
    "bat_temp": {
        "name": "Inverter Temperature",
        "unit": "°C",
        "device_class": "temperature",
        "state_class": "measurement",
        "icon": "mdi:thermometer",
    },
    "pv_w": {
        "name": "PV Power",
        "unit": "W",
        "device_class": "power",
        "state_class": "measurement",
        "icon": "mdi:solar-power",
    },
    "pv_v": {
        "name": "PV Voltage",
        "unit": "V",
        "device_class": "voltage",
        "state_class": "measurement",
        "icon": "mdi:solar-panel",
    },
    "max_chg": {
        "name": "Max Charge Current",
        "unit": "A",
        "device_class": "current",
        "state_class": "measurement",
        "icon": "mdi:current-dc",
    },
    "util_chg": {
        "name": "Utility Charge Current",
        "unit": "A",
        "device_class": "current",
        "state_class": "measurement",
        "icon": "mdi:current-dc",
    },
    "bulk_v": {
        "name": "Bulk Charging Voltage",
        "unit": "V",
        "device_class": "voltage",
        "state_class": "measurement",
        "icon": "mdi:battery-charging-high",
    },
    "float_v": {
        "name": "Float Charging Voltage",
        "unit": "V",
        "device_class": "voltage",
        "state_class": "measurement",
        "icon": "mdi:battery-charging-medium",
    },
    "cut_v": {
        "name": "Low Battery Cut-off",
        "unit": "V",
        "device_class": "voltage",
        "state_class": "measurement",
        "icon": "mdi:battery-off-outline",
    },
    "pv_today_kwh": {
        "name": "PV Today Energy",
        "unit": "kWh",
        "device_class": "energy",
        "icon": "mdi:solar-power-variant",
    },
    "pv_month_kwh": {
        "name": "PV Month Energy",
        "unit": "kWh",
        "device_class": "energy",
        "icon": "mdi:calendar-month",
    },
    "pv_year_kwh": {
        "name": "PV Year Energy",
        "unit": "kWh",
        "device_class": "energy",
        "icon": "mdi:calendar-range",
    },
    "pv_total_kwh": {
        "name": "PV Total Energy",
        "unit": "kWh",
        "device_class": "energy",
        "state_class": "total_increasing",
        "icon": "mdi:counter",
    },
    "bms_remaining_ah": {
        "name": "BMS Remaining Capacity",
        "unit": "Ah",
        "icon": "mdi:battery-medium",
    },
    "bms_nominal_ah": {
        "name": "BMS Nominal Capacity",
        "unit": "Ah",
        "icon": "mdi:battery-outline",
    },
    "bms_cell_count": {
        "name": "BMS Cell Count",
        "state_class": "measurement",
        "icon": "mdi:battery-sync",
    },
    "bms_min_cell_mv": {
        "name": "BMS Min Cell Voltage",
        "unit": "mV",
        "state_class": "measurement",
        "icon": "mdi:battery-low",
    },
    "bms_max_cell_mv": {
        "name": "BMS Max Cell Voltage",
        "unit": "mV",
        "state_class": "measurement",
        "icon": "mdi:battery-high",
    },
    "bms_min_cell_pos": {
        "name": "BMS Min Cell Position",
        "state_class": "measurement",
        "icon": "mdi:numeric",
    },
    "bms_max_cell_pos": {
        "name": "BMS Max Cell Position",
        "state_class": "measurement",
        "icon": "mdi:numeric",
    },
    "bms_cell_delta_mv": {
        "name": "BMS Cell Delta",
        "unit": "mV",
        "state_class": "measurement",
        "icon": "mdi:battery-sync",
    },
}

for i in range(1, 17):
    SENSORS[f"cell_{i}_mv"] = {
        "name": f"Cell {i} Voltage",
        "unit": "mV",
        "state_class": "measurement",
        "icon": "mdi:battery-heart-variant",
    }

MQTT_PACKET_TYPES = {
    1: "CONNECT",
    2: "CONNACK",
    3: "PUBLISH",
    4: "PUBACK",
    5: "PUBREC",
    6: "PUBREL",
    7: "PUBCOMP",
    8: "SUBSCRIBE",
    9: "SUBACK",
    10: "UNSUBSCRIBE",
    11: "UNSUBACK",
    12: "PINGREQ",
    13: "PINGRESP",
    14: "DISCONNECT",
}


def log(message: str) -> None:
    print(message, flush=True)


def norm_mac(mac: Optional[str]) -> Optional[str]:
    if not mac:
        return None
    return mac.strip().lower().replace("-", ":")


def send_layer2(frame, iface: Optional[str] = None) -> None:
    if iface:
        sendp(frame, verbose=False, iface=iface)
    else:
        sendp(frame, verbose=False)


def mqtt_type_name(first_byte: int) -> str:
    return MQTT_PACKET_TYPES.get((first_byte >> 4) & 0x0F, f"TYPE_{(first_byte >> 4) & 0x0F}")


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


def publish_discovery() -> None:
    global DISCOVERY_PUBLISHED

    device_info = {
        "identifiers": [DEVICE_ID],
        "name": DEVICE_NAME,
        "manufacturer": "PowMr",
        "model": "Siseli / RWB1 compatible",
    }

    for key, meta in SENSORS.items():
        topic = f"{MQTT_DISCOVERY_PREFIX}/sensor/{DEVICE_ID}/{key}/config"
        payload = {
            "name": f"PowMr {meta['name']}",
            "unique_id": f"{DEVICE_ID}_{key}",
            "state_topic": STATE_TOPIC,
            "value_template": f"{{{{ value_json.{key} }}}}",
            "availability_topic": AVAILABILITY_TOPIC,
            "payload_available": "online",
            "payload_not_available": "offline",
            "device": device_info,
            "icon": meta.get("icon"),
        }

        if meta.get("unit"):
            payload["unit_of_measurement"] = meta["unit"]
        if meta.get("device_class"):
            payload["device_class"] = meta["device_class"]
        if meta.get("state_class"):
            payload["state_class"] = meta["state_class"]

        client.publish(topic, json.dumps(payload), retain=True)

    client.publish(AVAILABILITY_TOPIC, "online", retain=True)
    DISCOVERY_PUBLISHED = True
    log("[HA MQTT] Discovery published")


def on_connect(_client, _userdata, _flags, rc, _properties=None):
    code = int(rc) if rc is not None else -1
    if code == 0:
        log(f"[HA MQTT] Connected to {MQTT_HOST}:{MQTT_PORT}")
        publish_discovery()
        if LAST_STATE:
            client.publish(STATE_TOPIC, json.dumps(LAST_STATE), retain=True)
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


def decode_remaining_length(buf: bytes, start_index: int = 1) -> Tuple[Optional[int], Optional[int]]:
    multiplier = 1
    value = 0
    index = start_index

    while True:
        if index >= len(buf):
            return None, None

        encoded = buf[index]
        value += (encoded & 127) * multiplier
        index += 1

        if (encoded & 128) == 0:
            return value, index

        multiplier *= 128
        if multiplier > 128 * 128 * 128 * 128:
            raise ValueError("Malformed MQTT remaining length")


def is_duplicate_segment(flow_key: Tuple[str, int, str, int], seq: int, payload: bytes) -> bool:
    now = time.time()
    cache = SEGMENT_CACHE.setdefault(flow_key, {})

    stale_keys = [k for k, ts in cache.items() if now - ts > 15]
    for k in stale_keys:
        del cache[k]

    sig = (seq, len(payload), payload[:16])
    if sig in cache:
        return True

    cache[sig] = now
    return False


def extract_mqtt_packets(flow_key: Tuple[str, int, str, int], chunk: bytes) -> List[bytes]:
    buf = FLOW_BUFFERS.setdefault(flow_key, bytearray())
    buf.extend(chunk)

    if len(buf) > 1024 * 1024:
        del buf[:-512 * 1024]

    packets: List[bytes] = []

    while True:
        if len(buf) < 2:
            break

        try:
            remaining_len, header_end = decode_remaining_length(buf, 1)
        except Exception as exc:
            log(f"[MQTT ERROR] remaining length decode failed: {exc}")
            buf.clear()
            break

        if remaining_len is None or header_end is None:
            break

        total_len = header_end + remaining_len
        if len(buf) < total_len:
            break

        packet = bytes(buf[:total_len])
        del buf[:total_len]
        packets.append(packet)

    return packets


def extract_publish_payload(packet: bytes) -> Tuple[Optional[str], Optional[bytes]]:
    if not packet:
        return None, None

    first = packet[0]
    packet_type = (first >> 4) & 0x0F
    if packet_type != 3:
        return None, None

    remaining_len, pos = decode_remaining_length(packet, 1)
    if remaining_len is None or pos is None:
        return None, None

    if len(packet) < pos + 2:
        return None, None

    topic_len = int.from_bytes(packet[pos:pos + 2], "big")
    pos += 2

    if len(packet) < pos + topic_len:
        return None, None

    topic = packet[pos:pos + topic_len].decode("utf-8", errors="ignore")
    pos += topic_len

    qos = (first >> 1) & 0x03
    if qos > 0:
        if len(packet) < pos + 2:
            return topic, None
        pos += 2

    if len(packet) < pos:
        return topic, None

    payload = packet[pos:]
    return topic, payload


class SolarParser:
    @staticmethod
    def _safe_b64decode(value: str) -> Optional[bytes]:
        try:
            s = value.strip()
            if not s:
                return None
            pad = len(s) % 4
            if pad:
                s += "=" * (4 - pad)
            data = base64.b64decode(s, validate=False)
            if not data:
                return None
            return data
        except Exception:
            return None

    @staticmethod
    def _walk_for_blocks(obj):
        found = []

        if isinstance(obj, dict):
            possible_name = None
            possible_value = None

            for key in ("cn", "code", "name", "n", "c", "id"):
                val = obj.get(key)
                if isinstance(val, str) and val.strip():
                    possible_name = val.strip()
                    break

            for key in ("co", "cv", "data", "d", "value", "v"):
                val = obj.get(key)
                if isinstance(val, str) and val.strip():
                    possible_value = val.strip()
                    break

            if possible_name and possible_value:
                found.append((possible_name, possible_value))

            for v in obj.values():
                found.extend(SolarParser._walk_for_blocks(v))

        elif isinstance(obj, list):
            for item in obj:
                found.extend(SolarParser._walk_for_blocks(item))

        return found

    @staticmethod
    def _parse_ascii_text(data: bytes) -> Tuple[str, List[str]]:
        text = data.decode("utf-8", errors="ignore")
        text = text.replace("\r", " ").replace("\n", " ").replace("\x00", " ").strip()
        if text.startswith("("):
            text = text[1:]
        text = text.strip()

        parts = [p.strip() for p in text.split(" ") if p.strip()]
        cleaned = []
        for p in parts:
            while p and p[-1] in "),;:\t":
                p = p[:-1]
            if p:
                cleaned.append(p)

        return text, cleaned

    @staticmethod
    def _to_float(token: str) -> Optional[float]:
        try:
            cleaned = "".join(ch for ch in token if ch.isdigit() or ch in ".-")
            if cleaned in {"", "-", ".", "-."}:
                return None
            return float(cleaned)
        except Exception:
            return None

    @staticmethod
    def _to_int(token: str) -> Optional[int]:
        try:
            cleaned = "".join(ch for ch in token if ch.isdigit() or ch == "-")
            if cleaned in {"", "-"}:
                return None
            return int(cleaned)
        except Exception:
            return None

    @staticmethod
    def _parse_cost_energy(tokens: List[str]) -> Dict[str, object]:
        state: Dict[str, object] = {}
        work = list(tokens)

        if work and len(work[0]) == 6 and work[0].isdigit():
            work = work[1:]
        if work and ":" in work[0]:
            work = work[1:]

        nums: List[float] = []
        for tok in work:
            val = SolarParser._to_float(tok)
            if val is not None:
                nums.append(val)

        if len(nums) >= 4:
            state["pv_today_kwh"] = round(nums[0], 3)
            state["pv_month_kwh"] = round(nums[1], 3)
            state["pv_year_kwh"] = round(nums[2], 3)
            state["pv_total_kwh"] = round(nums[3], 3)

        return state

    @staticmethod
    def _parse_bms_capacity(tokens: List[str]) -> Dict[str, object]:
        state: Dict[str, object] = {}
        if len(tokens) >= 2:
            rem = SolarParser._to_float(tokens[0])
            nom = SolarParser._to_float(tokens[1])
            if rem is not None:
                state["bms_remaining_ah"] = round(rem, 1)
            if nom is not None:
                state["bms_nominal_ah"] = round(nom, 1)
        return state

    @staticmethod
    def _parse_cell_list(tokens: List[str]) -> Dict[str, object]:
        state: Dict[str, object] = {}
        cell_values: List[int] = []

        for tok in tokens:
            val = SolarParser._to_int(tok)
            if val is not None and 2000 <= val <= 5000:
                cell_values.append(val)

        if not cell_values:
            return state

        cell_values = cell_values[:16]

        state["bms_cell_count"] = len(cell_values)
        for idx, mv in enumerate(cell_values, start=1):
            state[f"cell_{idx}_mv"] = mv

        min_mv = min(cell_values)
        max_mv = max(cell_values)
        min_pos = cell_values.index(min_mv) + 1
        max_pos = cell_values.index(max_mv) + 1

        state["bms_min_cell_mv"] = min_mv
        state["bms_max_cell_mv"] = max_mv
        state["bms_min_cell_pos"] = min_pos
        state["bms_max_cell_pos"] = max_pos
        state["bms_cell_delta_mv"] = max_mv - min_mv

        return state

    @staticmethod
    def _try_old_schema(blocks: Dict[str, bytes]) -> Dict[str, object]:
        lower_blocks = {k.lower(): v for k, v in blocks.items()}
        ps4z = lower_blocks.get("ps4z")
        sgx0 = lower_blocks.get("sgx0") or lower_blocks.get("sgxo")

        state: Dict[str, object] = {}

        if ps4z and len(ps4z) >= 44:
            state["grid_v"] = round(int.from_bytes(ps4z[5:7], "little") / 10.0, 1)
            state["grid_hz"] = round(int.from_bytes(ps4z[7:9], "little") / 10.0, 1)
            state["bat_v"] = round(int.from_bytes(ps4z[13:15], "little") / 10.0, 1)
            state["bat_cap"] = int.from_bytes(ps4z[15:17], "little")
            state["out_v"] = round(int.from_bytes(ps4z[21:23], "little") / 10.0, 1)
            state["out_hz"] = round(int.from_bytes(ps4z[23:25], "little") / 10.0, 1)
            state["apparent_va"] = int.from_bytes(ps4z[25:27], "little")
            state["load_w"] = int.from_bytes(ps4z[27:29], "little")
            state["pv_v"] = round(int.from_bytes(ps4z[39:41], "little") / 10.0, 1)

            pv_w = int.from_bytes(ps4z[41:43], "little")
            state["pv_w"] = pv_w if pv_w < 6500 else 0

            bat_v = float(state.get("bat_v") or 0)
            load_w = float(state.get("load_w") or 0)
            grid_v = float(state.get("grid_v") or 0)

            if bat_v > 0 and grid_v < 100 and load_w > 0:
                state["dischg_current"] = round(load_w / bat_v, 1)
            else:
                state["dischg_current"] = 0

        if sgx0 and len(sgx0) >= 42:
            state["max_chg"] = int.from_bytes(sgx0[13:15], "little")
            state["util_chg"] = int.from_bytes(sgx0[17:19], "little")
            state["float_v"] = round(int.from_bytes(sgx0[21:23], "little") / 10.0, 1)
            state["bulk_v"] = round(int.from_bytes(sgx0[23:25], "little") / 10.0, 1)
            state["cut_v"] = round(int.from_bytes(sgx0[27:29], "little") / 10.0, 1)
            state["bat_temp"] = int(sgx0[41])

        return state

    @staticmethod
    def _try_ascii_schema(blocks: Dict[str, bytes]) -> Dict[str, object]:
        state: Dict[str, object] = {}
        parsed = {name: SolarParser._parse_ascii_text(data) for name, data in blocks.items()}

        vals = parsed.get("2l0E", ("", []))[1]
        if len(vals) >= 2:
            v = SolarParser._to_float(vals[0])
            hz = SolarParser._to_float(vals[1])
            if v is not None:
                state["grid_v"] = round(v, 1)
            if hz is not None:
                state["grid_hz"] = round(hz, 1)

        vals = parsed.get("WdRR", ("", []))[1]
        if len(vals) >= 4:
            out_v = SolarParser._to_float(vals[0])
            out_hz = SolarParser._to_float(vals[1])
            apparent = SolarParser._to_int(vals[2])
            load_w = SolarParser._to_int(vals[3])

            if out_v is not None:
                state["out_v"] = round(out_v, 1)
            if out_hz is not None:
                state["out_hz"] = round(out_hz, 1)
            if apparent is not None:
                state["apparent_va"] = apparent
            if load_w is not None:
                state["load_w"] = load_w

        vals = parsed.get("2ONL", ("", []))[1]
        if len(vals) >= 3:
            bat_v = SolarParser._to_float(vals[1])
            bat_cap = SolarParser._to_int(vals[2])
            if bat_v is not None:
                state["bat_v"] = round(bat_v, 1)
            if bat_cap is not None:
                state["bat_cap"] = bat_cap

        vals = parsed.get("Mpod", ("", []))[1]
        if len(vals) >= 3:
            pv_v = SolarParser._to_float(vals[0])
            pv_w = SolarParser._to_int(vals[2])
            if pv_v is not None:
                state["pv_v"] = round(pv_v, 1)
            if pv_w is not None:
                state["pv_w"] = pv_w

        vals = parsed.get("dHrK", ("", []))[1]
        if len(vals) >= 5:
            cut_v = SolarParser._to_float(vals[1])
            max_chg = SolarParser._to_int(vals[2])
            float_v = SolarParser._to_float(vals[3])
            bulk_v = SolarParser._to_float(vals[4])

            if cut_v is not None:
                state["cut_v"] = round(cut_v, 1)
            if max_chg is not None:
                state["max_chg"] = max_chg
            if float_v is not None:
                state["float_v"] = round(float_v, 1)
            if bulk_v is not None:
                state["bulk_v"] = round(bulk_v, 1)

        vals = parsed.get("COST", ("", []))[1]
        if vals:
            state.update(SolarParser._parse_cost_energy(vals))

        vals = parsed.get("uxJp", ("", []))[1]
        if vals:
            state.update(SolarParser._parse_bms_capacity(vals))

        vals = parsed.get("v09K", ("", []))[1]
        if vals:
            state.update(SolarParser._parse_cell_list(vals))

        bat_v = float(state.get("bat_v") or 0)
        load_w = float(state.get("load_w") or 0)
        grid_v = float(state.get("grid_v") or 0)
        if bat_v > 0 and grid_v < 100 and load_w > 0:
            state["dischg_current"] = round(load_w / bat_v, 1)
        elif "bat_v" in state:
            state["dischg_current"] = 0

        return state

    @staticmethod
    def parse_payload(payload_bytes: bytes) -> None:
        try:
            idx = payload_bytes.find(b'{"b":')
            if idx == -1:
                idx = payload_bytes.find(b'"b":')
                if idx > 0:
                    payload_bytes = b"{" + payload_bytes[idx:]
                    idx = 0

            if idx == -1:
                idx = payload_bytes.find(b"{")

            if idx == -1:
                return

            raw = payload_bytes[idx:].decode("utf-8", errors="ignore")
            end = raw.rfind("}")
            if end != -1:
                raw = raw[:end + 1]

            raw_json = json.loads(raw)

            candidate_pairs = SolarParser._walk_for_blocks(raw_json)
            blocks: Dict[str, bytes] = {}
            seen = set()

            for name, encoded in candidate_pairs:
                key = name.strip()
                if not key:
                    continue

                dedupe_key = (key, encoded[:32])
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)

                decoded = SolarParser._safe_b64decode(encoded)
                if decoded is None:
                    continue

                blocks[key] = decoded

            state = SolarParser._try_old_schema(blocks)
            if not state:
                state = SolarParser._try_ascii_schema(blocks)

            if state:
                LAST_STATE.update(state)
                if DISCOVERY_PUBLISHED:
                    client.publish(STATE_TOPIC, json.dumps(LAST_STATE), retain=True)
                log(f"[{datetime.now().strftime('%H:%M:%S')}] Published {len(state)} values to HA")

        except Exception as exc:
            log(f"[PARSER ERROR] {exc}")


class ArpSpoofer:
    def resolve_macs(self) -> None:
        global INV_MAC, RTR_MAC

        INV_MAC = norm_mac(INVERTER_MAC_CFG) or INV_MAC
        RTR_MAC = norm_mac(ROUTER_MAC_CFG) or RTR_MAC

        while RUNNING and (not INV_MAC or not RTR_MAC):
            if not INV_MAC:
                INV_MAC = norm_mac(getmacbyip(INVERTER_IP))
            if not RTR_MAC:
                RTR_MAC = norm_mac(getmacbyip(ROUTER_IP))

            if not INV_MAC or not RTR_MAC:
                log("[ARP] Waiting for MAC addresses...")
                time.sleep(2)

        if RUNNING:
            log(f"[ARP] Inverter MAC: {INV_MAC}")
            log(f"[ARP] Router MAC:   {RTR_MAC}")

    def run(self) -> None:
        self.resolve_macs()
        if not RUNNING:
            return

        log(f"[ARP] Interception ACTIVE: {INVERTER_IP} <-> {ROUTER_IP}")

        while RUNNING:
            try:
                send_layer2(Ether(dst=INV_MAC) / ARP(op=2, pdst=INVERTER_IP, psrc=ROUTER_IP, hwdst=INV_MAC), SNIFF_IFACE)
                send_layer2(Ether(dst=RTR_MAC) / ARP(op=2, pdst=ROUTER_IP, psrc=INVERTER_IP, hwdst=RTR_MAC), SNIFF_IFACE)
            except Exception as exc:
                log(f"[ARP ERROR] {exc}")

            time.sleep(2)


arp_spoofer = ArpSpoofer()


def packet_callback(pkt) -> None:
    global INV_MAC, RTR_MAC, LAST_PACKET_TS

    LAST_PACKET_TS = time.time()

    if IP not in pkt or Ether not in pkt:
        return

    src_mac = norm_mac(pkt[Ether].src)
    src_ip = pkt[IP].src
    dst_ip = pkt[IP].dst

    if src_ip == INVERTER_IP and not INV_MAC:
        INV_MAC = src_mac

    if dst_ip == INVERTER_IP and not RTR_MAC:
        RTR_MAC = src_mac

    if src_ip == INVERTER_IP and src_mac:
        KNOWN_INVERTER_MACS.add(src_mac)

    if dst_ip == INVERTER_IP and src_mac:
        KNOWN_ROUTER_MACS.add(src_mac)

    if LOG_VERBOSE and (src_ip == INVERTER_IP or dst_ip == INVERTER_IP):
        proto = "TCP" if TCP in pkt else ("UDP" if UDP in pkt else "OTHER")
        port = f":{pkt[TCP].dport}" if TCP in pkt else ""
        log(f"[X-RAY] {src_ip} ({src_mac}) -> {dst_ip}{port} [{proto}]")

    if src_ip == INVERTER_IP:
        if INV_MAC and src_mac != INV_MAC:
            return

        if TCP in pkt and dst_ip == TARGET_HOST and int(pkt[TCP].dport) == TARGET_PORT:
            if Raw in pkt:
                payload = bytes(pkt[Raw].load)
                if payload:
                    flow_key = (src_ip, int(pkt[TCP].sport), dst_ip, int(pkt[TCP].dport))
                    seq = int(pkt[TCP].seq)

                    if is_duplicate_segment(flow_key, seq, payload):
                        return

                    packets = extract_mqtt_packets(flow_key, payload)

                    for packet in packets:
                        if LOG_VERBOSE:
                            ptype = mqtt_type_name(packet[0])
                            log(
                                f"[MQTT PACKET] {src_ip}:{int(pkt[TCP].sport)} -> "
                                f"{dst_ip}:{int(pkt[TCP].dport)} type={ptype} len={len(packet)} "
                                f"first16={packet[:16].hex()}"
                            )

                        if ((packet[0] >> 4) & 0x0F) == 3:
                            topic, publish_payload = extract_publish_payload(packet)
                            if LOG_VERBOSE and topic is not None:
                                log(f"[MQTT PUBLISH] topic={topic} payload_len={len(publish_payload or b'')}")
                            if publish_payload:
                                SolarParser.parse_payload(publish_payload)

            if AUTO_INTERCEPT and RTR_MAC:
                try:
                    fwd_pkt = Ether(dst=RTR_MAC) / pkt[IP]
                    send_layer2(fwd_pkt, SNIFF_IFACE)
                except Exception as exc:
                    log(f"[FWD ERROR] inverter->router {exc}")

        return

    if dst_ip == INVERTER_IP:
        if RTR_MAC and src_mac != RTR_MAC:
            return

        if AUTO_INTERCEPT and INV_MAC:
            try:
                fwd_pkt = Ether(dst=INV_MAC) / pkt[IP]
                send_layer2(fwd_pkt, SNIFF_IFACE)
            except Exception as exc:
                log(f"[FWD ERROR] router->inverter {exc}")


def health_logger() -> None:
    while RUNNING:
        time.sleep(30)
        age = time.time() - LAST_PACKET_TS if LAST_PACKET_TS else -1
        if age < 0:
            log("[HEALTH] No packets captured yet")
        else:
            inv_list = sorted(x for x in KNOWN_INVERTER_MACS if x)
            rtr_list = sorted(x for x in KNOWN_ROUTER_MACS if x)
            log(f"[HEALTH] Last packet seen {int(age)}s ago; inverter_macs={inv_list}; router_macs={rtr_list}")


def shutdown(*_args) -> None:
    global RUNNING, sniffer

    if not RUNNING:
        return

    RUNNING = False

    try:
        if sniffer is not None:
            sniffer.stop()
    except Exception:
        pass

    try:
        client.publish(AVAILABILITY_TOPIC, "offline", retain=True)
        client.disconnect()
        client.loop_stop()
    except Exception:
        pass

    log("[Bridge] Stopped")


signal.signal(signal.SIGTERM, shutdown)
signal.signal(signal.SIGINT, shutdown)


if __name__ == "__main__":
    log("--- PowMr Bridge 2.1.3 ---")
    log(f"[Config] INVERTER_IP={INVERTER_IP} ROUTER_IP={ROUTER_IP}")
    log(f"[Config] TARGET={TARGET_HOST}:{TARGET_PORT} MQTT={MQTT_HOST}:{MQTT_PORT}")
    log(f"[Config] AUTO_INTERCEPT={AUTO_INTERCEPT} LISTEN_PORT={LISTEN_PORT}")
    log(f"[Config] SNIFF_IFACE={SNIFF_IFACE or 'auto'}")

    start_mqtt()

    if AUTO_INTERCEPT:
        threading.Thread(target=arp_spoofer.run, daemon=True).start()
        wait_start = time.time()
        while RUNNING and time.time() - wait_start < 15 and (not INV_MAC or not RTR_MAC):
            time.sleep(1)
    else:
        INV_MAC = norm_mac(INVERTER_MAC_CFG)
        RTR_MAC = norm_mac(ROUTER_MAC_CFG)
        log("[ARP] AUTO_INTERCEPT disabled; relying on existing network redirection")

    threading.Thread(target=health_logger, daemon=True).start()

    sniff_kwargs = {
        "filter": f"ip host {INVERTER_IP}",
        "prn": packet_callback,
        "store": False,
    }
    if SNIFF_IFACE:
        sniff_kwargs["iface"] = SNIFF_IFACE

    sniffer = AsyncSniffer(**sniff_kwargs)
    sniffer.start()
    log("[Bridge] Sniffer started")

    try:
        while RUNNING:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        shutdown()