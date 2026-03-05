import asyncio
import json
import base64
import threading
import time
import warnings
import logging
import os
import paho.mqtt.client as mqtt
from datetime import datetime
from scapy.all import ARP, Ether, sendp, getmacbyip, conf

# Вимикаємо спам-попередження
warnings.filterwarnings("ignore", category=DeprecationWarning)
logging.getLogger("scapy.runtime").setLevel(logging.ERROR)

# --- НАЛАШТУВАННЯ ---
TARGET_HOST = os.getenv('TARGET_HOST', '8.212.18.157')
TARGET_PORT = int(os.getenv('TARGET_PORT', 1883))
LISTEN_PORT = int(os.getenv('LISTEN_PORT', 18899))

# --- HA MQTT ---
HA_BROKER = os.getenv('MQTT_HOST', 'core-mosquitto') 
HA_PORT = int(os.getenv('MQTT_PORT', 1883))
HA_USER = os.getenv('MQTT_USER', '')
HA_PASS = os.getenv('MQTT_PASSWORD', '')

INVERTER_IP = os.getenv('INVERTER_IP', '')
ROUTER_IP = os.getenv('ROUTER_IP', '')
INVERTER_MAC_MANUAL = os.getenv('INVERTER_MAC', '').strip()
ROUTER_MAC_MANUAL = os.getenv('ROUTER_MAC', '').strip()

DEVICE_ID = "powmr_rwb1"
STATE_TOPIC = f"powmr/{DEVICE_ID}/state"

# Визначаємо найкращий інтерфейс
def get_best_iface():
    try:
        import subprocess
        out = subprocess.check_output("ip route | grep default", shell=True).decode()
        iface = out.split()[4]
        return iface
    except: return "enp0s3"

conf.iface = get_best_iface()

SENSORS = {
    "grid_v": ["Grid Voltage", "V", "voltage", "mdi:transmission-tower"],
    "grid_hz": ["Grid Frequency", "Hz", "frequency", "mdi:current-ac"],
    "out_v": ["Output Voltage", "V", "voltage", "mdi:power-plug"],
    "out_hz": ["Output Frequency", "Hz", "frequency", "mdi:current-ac"],
    "load_w": ["Active Load", "W", "power", "mdi:home-lightning-bolt"],
    "apparent_va": ["Apparent Load", "VA", "apparent_power", "mdi:flash"],
    "load_pct": ["Load Percentage", "%", "power_factor", "mdi:gauge"],
    "bat_v": ["Battery Voltage", "V", "voltage", "mdi:battery"],
    "bat_cap": ["Battery Capacity", "%", "battery", "mdi:battery-high"],
    "dischg_current": ["Battery Discharge Current", "A", "current", "mdi:battery-minus"],
    "bat_temp": ["Inverter Temperature", "°C", "temperature", "mdi:thermometer"],
    "pv_w": ["PV Power", "W", "power", "mdi:solar-power"],
    "pv_v": ["PV Voltage", "V", "voltage", "mdi:solar-panel"],
    "max_chg": ["Max Charge Current", "A", "current", "mdi:current-dc"],
    "util_chg": ["Utility Charge Current", "A", "current", "mdi:current-dc"],
    "bulk_v": ["Bulk Charging Voltage", "V", "voltage", "mdi:battery-charging-high"],
    "float_v": ["Float Charging Voltage", "V", "voltage", "mdi:battery-charging-medium"],
    "cut_v": ["Low Battery Cut-off", "V", "voltage", "mdi:battery-off-outline"],
    "sbu_return_grid": ["SBU Return to Grid Volts", "V", "voltage", "mdi:transmission-tower-export"],
    "sbu_return_bat": ["SBU Return to Battery Volts", "V", "voltage", "mdi:battery-arrow-up"]
}

class ArpSpoofer:
    def __init__(self, target_ip, gateway_ip, target_mac=None, gateway_mac=None):
        self.target_ip = target_ip     
        self.gateway_ip = gateway_ip   
        self.target_mac = target_mac
        self.gateway_mac = gateway_mac
        self.running = False

    def get_mac(self, ip):
        mac = getmacbyip(ip)
        if mac: print(f"[ARP] Found MAC for {ip}: {mac}")
        else: print(f"[ARP] WARNING: Failed to find MAC for {ip}")
        return mac

    def run(self):
        t_mac = self.target_mac if self.target_mac else self.get_mac(self.target_ip)
        g_mac = self.gateway_mac if self.gateway_mac else self.get_mac(self.gateway_ip)
        if not t_mac or not g_mac: return
        self.running = True
        print(f"[ARP] Spoofing ACTIVE: {self.target_ip} ({t_mac}) <-> {self.gateway_ip} ({g_mac})")
        try:
            while self.running:
                sendp(Ether(dst=t_mac)/ARP(op=2, pdst=self.target_ip, psrc=self.gateway_ip, hwdst=t_mac), verbose=False)
                sendp(Ether(dst=g_mac)/ARP(op=2, pdst=self.gateway_ip, psrc=self.target_ip, hwdst=g_mac), verbose=False)
                time.sleep(2)
        except Exception: pass

# --- MQTT ---
ha_client = mqtt.Client()
if HA_USER and HA_PASS: ha_client.username_pw_set(HA_USER, HA_PASS)

def connect_ha_mqtt():
    try:
        ha_client.connect(HA_BROKER, HA_PORT, 60)
        ha_client.loop_start()
        print(f"[HA MQTT] Connected to {HA_BROKER}")
        publish_discovery()
    except Exception: pass

def publish_discovery():
    for key, data in SENSORS.items():
        topic = f"homeassistant/sensor/{DEVICE_ID}/{key}/config"
        payload = {
            "name": f"PowMr {data[0]}",
            "state_topic": STATE_TOPIC,
            "value_template": f"{{{{ value_json.{key} }}}}",
            "unit_of_measurement": data[1],
            "device_class": data[2],
            "icon": data[3],
            "unique_id": f"{DEVICE_ID}_{key}",
            "device": {"identifiers": [DEVICE_ID], "name": "PowMr 6.2kW Inverter", "manufacturer": "PowMr", "model": "RWB1 6200W"}
        }
        ha_client.publish(topic, json.dumps(payload), retain=True)

class SolarParser:
    @staticmethod
    def parse_payload(payload_bytes):
        try:
            start = payload_bytes.find(b'{')
            if start == -1: return
            raw_json = json.loads(payload_bytes[start:].decode('utf-8', errors='ignore'))
            state = {}
            if "b" in raw_json and "ct" in raw_json["b"]:
                blocks = {item["cn"]: base64.b64decode(item["co"]) for item in raw_json["b"]["ct"]}
                if "PS4Z" in blocks:
                    r = blocks["PS4Z"]
                    if len(r) >= 44:
                        state["grid_v"] = int.from_bytes(r[5:7], 'little') / 10.0
                        state["grid_hz"] = int.from_bytes(r[7:9], 'little') / 10.0
                        state["bat_v"] = int.from_bytes(r[13:15], 'little') / 10.0
                        state["bat_cap"] = int.from_bytes(r[15:17], 'little')
                        state["out_v"] = int.from_bytes(r[21:23], 'little') / 10.0
                        state["out_hz"] = int.from_bytes(r[23:25], 'little') / 10.0
                        state["apparent_va"] = int.from_bytes(r[25:27], 'little')
                        state["load_w"] = int.from_bytes(r[27:29], 'little')
                        state["load_pct"] = int.from_bytes(r[29:31], 'little')
                        state["pv_v"] = int.from_bytes(r[39:41], 'little') / 10.0
                        pv_w = int.from_bytes(r[41:43], 'little')
                        state["pv_w"] = pv_w if pv_w < 6500 else 0
                        state["dischg_current"] = round((state["load_w"] / state["bat_v"]), 1) if (state["grid_v"] < 100 and state["load_w"] > 0) else 0
                if "Sgx0" in blocks:
                    r = blocks["Sgx0"]
                    if len(r) >= 42:
                        state["max_chg"] = int.from_bytes(r[13:15], 'little')
                        state["util_chg"] = int.from_bytes(r[17:19], 'little')
                        state["sbu_return_grid"] = int.from_bytes(r[19:21], 'little') / 10.0
                        state["float_v"] = int.from_bytes(r[21:23], 'little') / 10.0
                        state["bulk_v"] = int.from_bytes(r[23:25], 'little') / 10.0
                        state["cut_v"] = int.from_bytes(r[27:29], 'little') / 10.0
                        state["sbu_return_bat"] = int.from_bytes(r[29:31], 'little') / 10.0
                        state["bat_temp"] = r[41]
            if state:
                ha_client.publish(STATE_TOPIC, json.dumps(state))
                print(f"[{datetime.now().strftime('%H:%M:%S')}] ➡️ Data: {len(state)} params.")
        except Exception: pass

async def handle_stream(reader, writer):
    try:
        while True:
            data = await reader.read(8192)
            if not data: break
            if data[0] & 0xF0 == 0x30: SolarParser.parse_payload(data)
            writer.write(data)
            await writer.drain()
    except Exception: pass
    finally: writer.close()

async def client_connected(ir, iw):
    peer = iw.get_extra_info('peername')
    print(f"[PROXY] New connection from {peer}")
    try:
        cr, cw = await asyncio.open_connection(TARGET_HOST, TARGET_PORT)
        await asyncio.gather(handle_stream(ir, cw), handle_stream(cr, iw))
    except Exception as e: print(f"[PROXY ERROR] Cloud connection failed: {e}")
    finally: iw.close()

async def main():
    if INVERTER_IP and ROUTER_IP:
        spoofer = ArpSpoofer(INVERTER_IP, ROUTER_IP, INVERTER_MAC_MANUAL, ROUTER_MAC_MANUAL)
        threading.Thread(target=spoofer.run, daemon=True).start()

    connect_ha_mqtt()
    proxy_server = await asyncio.start_server(client_connected, '0.0.0.0', LISTEN_PORT)
    print(f"--- PowMr Bridge 1.2.6 Active (Port {LISTEN_PORT}) ---")
    try:
        async with proxy_server: await proxy_server.serve_forever()
    except Exception: pass

if __name__ == "__main__": asyncio.run(main())
