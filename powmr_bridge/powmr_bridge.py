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

SENSORS = {
    "grid_v": {"name": "Grid Voltage", "unit": "V", "device_class": "voltage", "state_class": "measurement", "icon": "mdi:transmission-tower"},
    "grid_hz": {"name": "Grid Frequency", "unit": "Hz", "device_class": "frequency", "state_class": "measurement", "icon": "mdi:current-ac"},
    "out_v": {"name": "Output Voltage", "unit": "V", "device_class": "voltage", "state_class": "measurement", "icon": "mdi:power-plug"},
    "out_hz": {"name": "Output Frequency", "unit": "Hz", "device_class": "frequency", "state_class": "measurement", "icon": "mdi:current-ac"},
    "load_w": {"name": "Active Load", "unit": "W", "device_class": "power", "state_class": "measurement", "icon": "mdi:home-lightning-bolt"},
    "apparent_va": {"name": "Apparent Load", "unit": "VA", "device_class": "apparent_power", "state_class": "measurement", "icon": "mdi:flash"},
    "bat_v": {"name": "Battery Voltage", "unit": "V", "device_class": "voltage", "state_class": "measurement", "icon": "mdi:battery"},
    "bat_cap": {"name": "Battery Capacity", "unit": "%", "device_class": "battery", "state_class": "measurement", "icon": "mdi:battery-high"},
    "dischg_current": {"name": "Battery Discharge Current", "unit": "A", "device_class": "current", "state_class": "measurement", "icon": "mdi:battery-minus"},
    "bat_temp": {"name": "Inverter Temperature", "unit": "°C", "device_class":