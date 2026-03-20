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
SCHEMA_DEBUG_DONE = False

SENSORS = {
    "grid_v": {"name": "Grid Voltage", "unit": "V", "device_class": "voltage", "state_class": "measurement", "icon": "mdi:transmission-tower"},
    "grid_hz": {"name": "Grid Frequency", "unit": "Hz", "device_class": "frequency", "state_class": "measurement", "icon": "mdi:current-ac"},
    "out_v": {"name": "Output Voltage", "unit": "V", "device_class": "voltage", "state_class": "measurement", "icon": "mdi:power-plug"},
    "out_hz": {"name": "Output Frequency", "unit": "Hz", "device_class": "frequency", "state_class": "measurement", "icon": "mdi:current-ac"},
    "load_w": {"name": "Active Load", "unit": "W", "device_class": "power", "state_class": "measurement", "icon": "mdi:home-lightning-bolt"},
    "apparent_va": {"name": "Apparent Load", "unit": "VA", "device_class": "apparent_power", "state_class": "measurement", "icon": "mdi:flash"},
    "load_pct": {"name": "Load Percentage", "unit": "%", "state_class": "measurement", "icon": "mdi:gauge"},
    "bat_v": {"name": "Battery Voltage", "unit": "V", "device_class": "voltage", "state_class": "measurement", "icon": "mdi:battery"},
    "bat_cap": {"name": "Battery Capacity", "unit": "%", "device_class": "battery", "state_class": "measurement", "icon": "mdi:battery-high"},
    "dischg_current": {"name": "Battery Discharge Current", "unit": "A", "device_class": "current", "state_class": "measurement", "icon": "mdi:battery-minus"},
    "bat_temp": {"name": "Inverter Temperature", "unit": "°C", "device_class": "temperature", "state_class": "measurement", "icon": "mdi:thermometer"},
    "pv_w": {"name": "PV Power", "unit": "W", "device_class": "power", "state_class": "measurement", "icon": "mdi:solar-power"},
    "pv_v": {"name": "PV Voltage", "unit": "V", "device_class": "voltage", "state_class": "measurement", "icon": "mdi:solar-panel"},
    "max_chg": {"name": "Max Charge Current", "unit": "A", "device_class": "current", "state_class": "measurement", "icon": "mdi:current-dc"},
    "util_chg": {"name": "Utility Charge Current", "unit": "A", "device_class": "current", "state_class": "measurement", "icon": "mdi:current-dc"},
    "bulk_v": {"name": "Bulk Charging Voltage", "unit": "V", "device_class": "voltage", "state_class": "measurement", "icon": "mdi:battery-charging-high"},
    "float_v": {"name": "Float Charging Voltage", "unit": "V", "device_class": "voltage", "state_class": "measurement", "icon": "mdi:battery-charging-medium"},
    "cut_v": {"name": "Low Battery Cut-off", "unit": "V", "device_class": "voltage", "state_class": "measurement", "icon": "mdi:battery-off-outline"},
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
        client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION1,
            client_id=f"{DEVICE_ID}_bridge",
            protocol=mqtt.MQTTv311,
        )
    except Exception:
        client = mqtt.Client(client_id=f"{DEVICE_ID}_bridge", protocol=mqtt.MQTTv311)

    if MQTT_USER:
        client.username_pw_set(MQTT_USER, MQTT_PASSWORD)

    client.reconnect_delay_set(min_delay=5, max_delay=30)
    client.will_set(AVAILABILITY_TOPIC, "offline", retain=True)
    return client


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
    DEBUG_DUMPS_LEFT = 6

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
    def _ascii_preview(data: bytes, limit: int = 32) -> str:
        out = []
        for b in data[:limit]:
            if 32 <= b <= 126:
                out.append(chr(b))
            else:
                out.append(".")
        return "".join(out)

    @staticmethod
    def _u16_words(data: bytes, count: int = 12):
        words = []
        max_len = min(len(data) // 2, count)
        for i in range(max_len):
            start = i * 2
            words.append(int.from_bytes(data[start:start + 2], "little"))
        return words

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
            state["load_pct"] = int.from_bytes(ps4z[29:31], "little")
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
    def parse_payload(payload_bytes: bytes) -> None:
        global SCHEMA_DEBUG_DONE

        try:
            log(f"[PARSER] candidate len={len(payload_bytes)}")

            idx = payload_bytes.find(b'{"b":')
            if idx == -1:
                idx = payload_bytes.find(b'"b":')
                if idx > 0:
                    payload_bytes = b"{" + payload_bytes[idx:]
                    idx = 0

            if idx == -1:
                idx = payload_bytes.find(b"{")

            if idx == -1:
                log("[PARSER] JSON marker not found")
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

            if not SCHEMA_DEBUG_DONE:
                top_keys = list(raw_json.keys()) if isinstance(raw_json, dict) else []
                log(f"[PARSER DEBUG] top_keys={top_keys}")

                if isinstance(raw_json, dict) and isinstance(raw_json.get('b'), dict):
                    log(f"[PARSER DEBUG] b_keys={list(raw_json['b'].keys())}")

                SCHEMA_DEBUG_DONE = True

            if SolarParser.DEBUG_DUMPS_LEFT > 0:
                log(f"[BLOCKS] found={sorted(blocks.keys())}")
                for name in sorted(blocks.keys()):
                    data = blocks[name]
                    hex_preview = data[:24].hex()
                    ascii_preview = SolarParser._ascii_preview(data, 24)
                    words = SolarParser._u16_words(data, 10)
                    log(
                        f"[BLOCK] name={name} len={len(data)} "
                        f"hex={hex_preview} ascii={ascii_preview} words={words}"
                    )
                SolarParser.DEBUG_DUMPS_LEFT -= 1

            state = SolarParser._try_old_schema(blocks)

            if state:
                LAST_STATE.update(state)
                if DISCOVERY_PUBLISHED:
                    client.publish(STATE_TOPIC, json.dumps(LAST_STATE), retain=True)
                log(f"[{datetime.now().strftime('%H:%M:%S')}] Published {len(state)} values to HA")
            else:
                found_names = sorted(blocks.keys())
                log(f"[PARSER] JSON decoded but known blocks not found. discovered={found_names[:20]}")

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
        log(f"[LEARN] inverter mac = {INV_MAC}")

    if dst_ip == INVERTER_IP and not RTR_MAC:
        RTR_MAC = src_mac
        log(f"[LEARN] router mac = {RTR_MAC}")

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
                        ptype = mqtt_type_name(packet[0])
                        if LOG_VERBOSE:
                            log(
                                f"[MQTT PACKET] {src_ip}:{int(pkt[TCP].sport)} -> "
                                f"{dst_ip}:{int(pkt[TCP].dport)} type={ptype} len={len(packet)} "
                                f"first16={packet[:16].hex()}"
                            )

                        if ((packet[0] >> 4) & 0x0F) == 3:
                            topic, publish_payload = extract_publish_payload(packet)
                            if topic is not None:
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
    log("--- PowMr Bridge 2.0.7 ---")
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