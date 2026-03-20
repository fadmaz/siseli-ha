import base64
import json
import logging
import os
import signal
import threading
import time
import warnings
from datetime import datetime
from typing import Dict, Optional

import paho.mqtt.client as mqtt
from scapy.all import ARP, Ether, IP, Raw, TCP, UDP, getmacbyip, sendp, sniff  # type: ignore

warnings.filterwarnings("ignore", category=DeprecationWarning)
logging.getLogger("scapy.runtime").setLevel(logging.ERROR)

INVERTER_IP = os.getenv("INVERTER_IP", "192.168.1.139")
ROUTER_IP = os.getenv("ROUTER_IP", "192.168.1.1")
TARGET_HOST = os.getenv("TARGET_HOST", "8.212.18.157")
TARGET_PORT = int(os.getenv("TARGET_PORT", "1883"))
LISTEN_PORT = int(os.getenv("LISTEN_PORT", "18899"))  # kept for backward compatibility

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

SENSORS = {
    "grid_v": {
        "name": "Grid Voltage",
        "unit": "V",
        "device_class": "voltage",
        "icon": "mdi:transmission-tower",
    },
    "grid_hz": {
        "name": "Grid Frequency",
        "unit": "Hz",
        "device_class": "frequency",
        "icon": "mdi:current-ac",
    },
    "out_v": {
        "name": "Output Voltage",
        "unit": "V",
        "device_class": "voltage",
        "icon": "mdi:power-plug",
    },
    "out_hz": {
        "name": "Output Frequency",
        "unit": "Hz",
        "device_class": "frequency",
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


def log(message: str) -> None:
    print(message, flush=True)


client = mqtt.Client(client_id=f"{DEVICE_ID}_bridge", protocol=mqtt.MQTTv311)
if MQTT_USER:
    client.username_pw_set(MQTT_USER, MQTT_PASSWORD)
client.reconnect_delay_set(min_delay=5, max_delay=30)
client.will_set(AVAILABILITY_TOPIC, "offline", retain=True)


def publish_discovery() -> None:
    global DISCOVERY_PUBLISHED

    for key, meta in SENSORS.items():
        topic = f"{MQTT_DISCOVERY_PREFIX}/sensor/{DEVICE_ID}/{key}/config"
        payload = {
            "name": f"PowMr {meta['name']}",
            "unique_id": f"{DEVICE_ID}_{key}",
            "state_topic": STATE_TOPIC,
            "value_template": f"{{{{ value_json.{key} }}}}",
            "unit_of_measurement": meta.get("unit"),
            "icon": meta.get("icon"),
            "availability_topic": AVAILABILITY_TOPIC,
            "payload_available": "online",
            "payload_not_available": "offline",
            "device": {
                "identifiers": [DEVICE_ID],
                "name": DEVICE_NAME,
                "manufacturer": "PowMr",
                "model": "Siseli / RWB1 compatible",
            },
        }

        if meta.get("device_class"):
            payload["device_class"] = meta["device_class"]
        if meta.get("state_class"):
            payload["state_class"] = meta["state_class"]

        client.publish(topic, json.dumps(payload), retain=True)

    client.publish(AVAILABILITY_TOPIC, "online", retain=True)
    DISCOVERY_PUBLISHED = True
    log("[HA MQTT] Discovery published")


def on_connect(_client, _userdata, _flags, rc):
    if rc == 0:
        log(f"[HA MQTT] Connected to {MQTT_HOST}:{MQTT_PORT}")
        publish_discovery()
        if LAST_STATE:
            client.publish(STATE_TOPIC, json.dumps(LAST_STATE), retain=True)
    else:
        log(f"[HA MQTT ERROR] Connection failed with rc={rc}")


def on_disconnect(_client, _userdata, rc):
    if rc != 0 and RUNNING:
        log(f"[HA MQTT] Disconnected (rc={rc}), retrying...")


client.on_connect = on_connect
client.on_disconnect = on_disconnect


def start_mqtt() -> None:
    try:
        client.connect_async(MQTT_HOST, MQTT_PORT, 60)
        client.loop_start()
    except Exception as exc:
        log(f"[HA MQTT ERROR] {exc}")


class SolarParser:
    @staticmethod
    def parse_payload(payload_bytes: bytes) -> None:
        try:
            idx = payload_bytes.find(b'{"b":')
            if idx == -1:
                return

            raw_json = json.loads(payload_bytes[idx:].decode("utf-8", errors="ignore"))
            blocks = {}

            for item in raw_json.get("b", {}).get("ct", []):
                name = item.get("cn")
                content = item.get("co")
                if name and content:
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
        except Exception as exc:
            log(f"[PARSER] Ignored payload: {exc}")


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
                sendp(
                    Ether(dst=INV_MAC) / ARP(op=2, pdst=INVERTER_IP, psrc=ROUTER_IP, hwdst=INV_MAC),
                    verbose=False,
                    iface=SNIFF_IFACE,
                )
                sendp(
                    Ether(dst=RTR_MAC) / ARP(op=2, pdst=ROUTER_IP, psrc=INVERTER_IP, hwdst=RTR_MAC),
                    verbose=False,
                    iface=SNIFF_IFACE,
                )
            except Exception as exc:
                log(f"[ARP ERROR] {exc}")

            time.sleep(2)


arp_spoofer = ArpSpoofer()


def packet_callback(pkt) -> None:
    global INV_MAC, RTR_MAC

    if IP not in pkt or Ether not in pkt:
        return

    src_mac = pkt[Ether].src.lower()
    src_ip = pkt[IP].src
    dst_ip = pkt[IP].dst

    if LOG_VERBOSE and (src_ip == INVERTER_IP or dst_ip == INVERTER_IP):
        proto = "TCP" if TCP in pkt else ("UDP" if UDP in pkt else "OTHER")
        port = f":{pkt[TCP].dport}" if TCP in pkt else ""
        log(f"[X-RAY] {src_ip} ({src_mac}) -> {dst_ip}{port} [{proto}]")

    if src_ip == INVERTER_IP:
        if INV_MAC and src_mac != INV_MAC:
            return

        if TCP in pkt and pkt[TCP].dport == TARGET_PORT and dst_ip == TARGET_HOST and Raw in pkt:
            payload = bytes(pkt[Raw].load)
            if payload and (payload[0] & 0xF0) == 0x30:
                SolarParser.parse_payload(payload)

        if AUTO_INTERCEPT and RTR_MAC:
            try:
                fwd_pkt = Ether(dst=RTR_MAC) / pkt[IP]
                sendp(fwd_pkt, verbose=False, iface=SNIFF_IFACE)
            except Exception as exc:
                log(f"[FWD ERROR] inverter->router {exc}")

    elif AUTO_INTERCEPT and dst_ip == INVERTER_IP:
        if RTR_MAC and src_mac != RTR_MAC:
            return

        if INV_MAC:
            try:
                fwd_pkt = Ether(dst=INV_MAC) / pkt[IP]
                sendp(fwd_pkt, verbose=False, iface=SNIFF_IFACE)
            except Exception as exc:
                log(f"[FWD ERROR] router->inverter {exc}")


def shutdown(*_args) -> None:
    global RUNNING

    if not RUNNING:
        return

    RUNNING = False

    try:
        client.publish(AVAILABILITY_TOPIC, "offline", retain=True)
        client.disconnect()
        client.loop_stop()
    except Exception:
        pass

    log("[Bridge] Stopped")
    raise SystemExit(0)


signal.signal(signal.SIGTERM, shutdown)
signal.signal(signal.SIGINT, shutdown)

if __name__ == "__main__":
    log("--- PowMr Bridge 2.0.0 ---")
    log(f"[Config] INVERTER_IP={INVERTER_IP} ROUTER_IP={ROUTER_IP} TARGET={TARGET_HOST}:{TARGET_PORT}")
    log(f"[Config] AUTO_INTERCEPT={AUTO_INTERCEPT} MQTT={MQTT_HOST}:{MQTT_PORT} LISTEN_PORT={LISTEN_PORT}")

    if SNIFF_IFACE:
        log(f"[Config] SNIFF_IFACE={SNIFF_IFACE}")

    start_mqtt()

    if AUTO_INTERCEPT:
        threading.Thread(target=arp_spoofer.run, daemon=True).start()
        while RUNNING and (not INV_MAC or not RTR_MAC):
            time.sleep(1)
    else:
        INV_MAC = INVERTER_MAC_CFG
        RTR_MAC = ROUTER_MAC_CFG
        log("[ARP] AUTO_INTERCEPT disabled; relying on existing network redirection")

    sniff_kwargs = {
        "filter": f"ip host {INVERTER_IP}",
        "prn": packet_callback,
        "store": 0,
    }

    if SNIFF_IFACE:
        sniff_kwargs["iface"] = SNIFF_IFACE

    log("[Bridge] Sniffer started")
    sniff(**sniff_kwargs)