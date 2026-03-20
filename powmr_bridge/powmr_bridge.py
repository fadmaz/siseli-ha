import base64
import json
import logging
import os
import signal
import threading
import time
import warnings
from datetime import datetime
from typing import Dict, Optional, Tuple

import paho.mqtt.client as mqtt
from scapy.all import ARP, Ether, IP, Raw, TCP, UDP, AsyncSniffer, getmacbyip, sendp  # type: ignore

warnings.filterwarnings("ignore", category=DeprecationWarning)
logging.getLogger("scapy.runtime").setLevel(logging.ERROR)

# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------

INVERTER_IP = os.getenv("INVERTER_IP", "192.168.1.139")
ROUTER_IP = os.getenv("ROUTER_IP", "192.168.1.1")

TARGET_HOST = os.getenv("TARGET_HOST", "8.212.18.157")
TARGET_PORT = int(os.getenv("TARGET_PORT", "1883"))
LISTEN_PORT = int(os.getenv("LISTEN_PORT", "18899"))  # kept for compatibility

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

# -----------------------------------------------------------------------------
# Runtime state
# -----------------------------------------------------------------------------

INV_MAC: Optional[str] = None
RTR_MAC: Optional[str] = None
RUNNING = True
DISCOVERY_PUBLISHED = False
LAST_STATE: Dict[str, object] = {}
TCP_STREAMS: Dict[Tuple[str, int, str, int], bytearray] = {}
KNOWN_INVERTER_MACS = set()
KNOWN_ROUTER_MACS = set()
sniffer: Optional[AsyncSniffer] = None

# -----------------------------------------------------------------------------
# Sensor definitions
# -----------------------------------------------------------------------------

SENSORS = {
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
    "load_pct": {
        "name": "Load Percentage",
        "unit": "%",
        "state_class": "measurement",
        "icon": "mdi:gauge",
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
}

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

def log(message: str) -> None:
    print(message, flush=True)


def send_layer2(frame, iface: Optional[str] = None) -> None:
    if iface:
        sendp(frame, verbose=False, iface=iface)
    else:
        sendp(frame, verbose=False)


def create_mqtt_client() -> mqtt.Client:
    try:
        client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION1,  # paho-mqtt v2
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

# -----------------------------------------------------------------------------
# MQTT discovery
# -----------------------------------------------------------------------------

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

# -----------------------------------------------------------------------------
# Payload parsing
# -----------------------------------------------------------------------------

class SolarParser:
    @staticmethod
    def parse_payload(payload_bytes: bytes) -> None:
        try:
            log(f"[PARSER] candidate len={len(payload_bytes)}")

            idx = payload_bytes.find(b'{"b":')
            if idx == -1:
                log("[PARSER] JSON marker not found")
                return

            raw_json = json.loads(payload_bytes[idx:].decode("utf-8", errors="ignore"))
            blocks = {}

            for item in raw_json.get("b", {}).get("ct", []):
                name = item.get("cn")
                content = item.get("co")
                if not name or not content:
                    continue
                try:
                    blocks[name] = base64.b64decode(content)
                except Exception:
                    continue

            state: Dict[str, object] = {}

            ps4z = blocks.get("PS4Z")
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

            sgx0 = blocks.get("Sgx0")
            if sgx0 and len(sgx0) >= 42:
                state["max_chg"] = int.from_bytes(sgx0[13:15], "little")
                state["util_chg"] = int.from_bytes(sgx0[17:19], "little")
                state["float_v"] = round(int.from_bytes(sgx0[21:23], "little") / 10.0, 1)
                state["bulk_v"] = round(int.from_bytes(sgx0[23:25], "little") / 10.0, 1)
                state["cut_v"] = round(int.from_bytes(sgx0[27:29], "little") / 10.0, 1)
                state["bat_temp"] = int(sgx0[41])

            if state:
                LAST_STATE.update(state)
                if DISCOVERY_PUBLISHED:
                    client.publish(STATE_TOPIC, json.dumps(LAST_STATE), retain=True)
                log(f"[{datetime.now().strftime('%H:%M:%S')}] Published {len(state)} values to HA")
            else:
                log("[PARSER] JSON decoded but no known blocks found")

        except Exception as exc:
            log(f"[PARSER ERROR] {exc}")


def extract_json_from_stream(flow_key: Tuple[str, int, str, int], chunk: bytes) -> Optional[bytes]:
    """
    Reassemble TCP payload chunks and extract a single JSON object that starts with {"b":
    """
    if flow_key not in TCP_STREAMS:
        TCP_STREAMS[flow_key] = bytearray()

    buf = TCP_STREAMS[flow_key]
    buf.extend(chunk)

    # Prevent unbounded growth
    if len(buf) > 262144:
        del buf[:-131072]

    start = buf.find(b'{"b":')
    if start == -1:
        return None

    depth = 0
    in_string = False
    escape = False

    for i in range(start, len(buf)):
        c = buf[i]

        if in_string:
            if escape:
                escape = False
            elif c == 92:   # backslash
                escape = True
            elif c == 34:   # "
                in_string = False
            continue

        if c == 34:         # "
            in_string = True
        elif c == 123:      # {
            depth += 1
        elif c == 125:      # }
            depth -= 1
            if depth == 0:
                payload = bytes(buf[start:i + 1])
                del buf[:i + 1]
                return payload

    # Drop leading junk before JSON start
    if start > 0:
        del buf[:start]

    return None

# -----------------------------------------------------------------------------
# ARP spoofing
# -----------------------------------------------------------------------------

class ArpSpoofer:
    def resolve_macs(self) -> None:
        global INV_MAC, RTR_MAC

        INV_MAC = INVERTER_MAC_CFG or INV_MAC
        RTR_MAC = ROUTER_MAC_CFG or RTR_MAC

        while RUNNING and (not INV_MAC or not RTR_MAC):
            if not INV_MAC:
                INV_MAC = getmacbyip(INVERTER_IP)
            if not RTR_MAC:
                RTR_MAC = getmacbyip(ROUTER_IP)

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
                send_layer2(
                    Ether(dst=INV_MAC) / ARP(op=2, pdst=INVERTER_IP, psrc=ROUTER_IP, hwdst=INV_MAC),
                    SNIFF_IFACE,
                )
                send_layer2(
                    Ether(dst=RTR_MAC) / ARP(op=2, pdst=ROUTER_IP, psrc=INVERTER_IP, hwdst=RTR_MAC),
                    SNIFF_IFACE,
                )
            except Exception as exc:
                log(f"[ARP ERROR] {exc}")

            time.sleep(2)


arp_spoofer = ArpSpoofer()

# -----------------------------------------------------------------------------
# Packet handling
# -----------------------------------------------------------------------------

def packet_callback(pkt) -> None:
    global INV_MAC, RTR_MAC

    if IP not in pkt or Ether not in pkt:
        return

    src_mac = pkt[Ether].src.lower()
    src_ip = pkt[IP].src
    dst_ip = pkt[IP].dst

    # Learn MACs dynamically, but do not trust them as hard filters
    if src_ip == INVERTER_IP:
        KNOWN_INVERTER_MACS.add(src_mac)
        if not INV_MAC:
            INV_MAC = src_mac

    if src_ip == ROUTER_IP:
        KNOWN_ROUTER_MACS.add(src_mac)
        if not RTR_MAC:
            RTR_MAC = src_mac

    if LOG_VERBOSE and (src_ip == INVERTER_IP or dst_ip == INVERTER_IP):
        proto = "TCP" if TCP in pkt else ("UDP" if UDP in pkt else "OTHER")
        port = f":{pkt[TCP].dport}" if TCP in pkt else ""
        log(f"[X-RAY] {src_ip} ({src_mac}) -> {dst_ip}{port} [{proto}]")

    # Inverter -> cloud MQTT
    if src_ip == INVERTER_IP and TCP in pkt and dst_ip == TARGET_HOST and pkt[TCP].dport == TARGET_PORT:
        if Raw in pkt:
            payload = bytes(pkt[Raw].load)
            if payload:
                if LOG_VERBOSE:
                    head = payload[:16].hex()
                    log(f"[MQTT RAW] len={len(payload)} first16={head}")

                flow_key = (src_ip, int(pkt[TCP].sport), dst_ip, int(pkt[TCP].dport))
                json_payload = extract_json_from_stream(flow_key, payload)

                if json_payload:
                    if LOG_VERBOSE:
                        log(f"[MQTT JSON] len={len(json_payload)}")
                    SolarParser.parse_payload(json_payload)

        if AUTO_INTERCEPT and RTR_MAC:
            try:
                fwd_pkt = Ether(dst=RTR_MAC) / pkt[IP]
                send_layer2(fwd_pkt, SNIFF_IFACE)
            except Exception as exc:
                log(f"[FWD ERROR] inverter->router {exc}")

    # Router/cloud -> inverter
    elif dst_ip == INVERTER_IP:
        if AUTO_INTERCEPT and INV_MAC:
            try:
                fwd_pkt = Ether(dst=INV_MAC) / pkt[IP]
                send_layer2(fwd_pkt, SNIFF_IFACE)
            except Exception as exc:
                log(f"[FWD ERROR] router->inverter {exc}")

# -----------------------------------------------------------------------------
# Shutdown
# -----------------------------------------------------------------------------

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

# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------

if __name__ == "__main__":
    log("--- PowMr Bridge 2.0.0 ---")
    log(f"[Config] INVERTER_IP={INVERTER_IP} ROUTER_IP={ROUTER_IP}")
    log(f"[Config] TARGET={TARGET_HOST}:{TARGET_PORT} MQTT={MQTT_HOST}:{MQTT_PORT}")
    log(f"[Config] AUTO_INTERCEPT={AUTO_INTERCEPT} LISTEN_PORT={LISTEN_PORT}")
    if SNIFF_IFACE:
        log(f"[Config] SNIFF_IFACE={SNIFF_IFACE}")

    start_mqtt()

    if AUTO_INTERCEPT:
        threading.Thread(target=arp_spoofer.run, daemon=True).start()
        # Give ARP some time to resolve MACs
        wait_start = time.time()
        while RUNNING and time.time() - wait_start < 15 and (not INV_MAC or not RTR_MAC):
            time.sleep(1)
    else:
        INV_MAC = INVERTER_MAC_CFG
        RTR_MAC = ROUTER_MAC_CFG
        log("[ARP] AUTO_INTERCEPT disabled; relying on existing network redirection")

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