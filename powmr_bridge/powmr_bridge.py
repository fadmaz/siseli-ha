import base64
import json
import logging
import os
import re
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
DEVICE_ID = os.getenv("DEVICE_ID", "taico_inverter_1")
DEVICE_NAME = os.getenv("DEVICE_NAME", "Taico inverter 1")
MODEL_NAME = os.getenv("MODEL_NAME", DEVICE_NAME)
MANUFACTURER = os.getenv("MANUFACTURER", "Taico")
ENTITY_PREFIX = os.getenv("ENTITY_PREFIX", "").strip()

STATE_TOPIC = os.getenv("STATE_TOPIC", f"taico/{DEVICE_ID}/state")
AVAILABILITY_TOPIC = os.getenv("AVAILABILITY_TOPIC", f"taico/{DEVICE_ID}/availability")

SNIFF_IFACE = os.getenv("SNIFF_IFACE", "").strip() or None
LOG_VERBOSE = os.getenv("LOG_VERBOSE", "true").strip().lower() in {"1", "true", "yes", "on"}
LOG_BLOCKS = os.getenv("LOG_BLOCKS", "true").strip().lower() in {"1", "true", "yes", "on"}
LOG_STATE_DIFF = os.getenv("LOG_STATE_DIFF", "true").strip().lower() in {"1", "true", "yes", "on"}
LOG_STATE_SNAPSHOT = os.getenv("LOG_STATE_SNAPSHOT", "true").strip().lower() in {"1", "true", "yes", "on"}
LOG_RAW_JSON = os.getenv("LOG_RAW_JSON", "false").strip().lower() in {"1", "true", "yes", "on"}
LOG_CLEAN_STATE = os.getenv("LOG_CLEAN_STATE", "true").strip().lower() in {"1", "true", "yes", "on"}
LOG_MQTT_TOPICS = os.getenv("LOG_MQTT_TOPICS", "true").strip().lower() in {"1", "true", "yes", "on"}
LOG_MQTT_PAYLOAD_PREVIEW = os.getenv("LOG_MQTT_PAYLOAD_PREVIEW", "true").strip().lower() in {"1", "true", "yes", "on"}
LOG_UNPARSED_PUBLISH = os.getenv("LOG_UNPARSED_PUBLISH", "true").strip().lower() in {"1", "true", "yes", "on"}
LOG_STREAM_EVENTS = os.getenv("LOG_STREAM_EVENTS", "true").strip().lower() in {"1", "true", "yes", "on"}
LOG_NULL_TARGETS = os.getenv("LOG_NULL_TARGETS", "true").strip().lower() in {"1", "true", "yes", "on"}

INV_MAC: Optional[str] = None
RTR_MAC: Optional[str] = None
RUNNING = True
DISCOVERY_PUBLISHED = False
LAST_STATE: Dict[str, object] = {}
sniffer: Optional[AsyncSniffer] = None

KNOWN_INVERTER_MACS = set()
KNOWN_ROUTER_MACS = set()
LAST_PACKET_TS = 0.0

STRICT_NUM_RE = re.compile(r"^-?\d+(?:\.\d+)?$")
PRINTABLE_ASCII_RE = re.compile(r"^[\x20-\x7E]+$")
SLUG_RE = re.compile(r"[^a-z0-9]+")

MAX_MQTT_PACKET = 1024 * 64
STREAM_STALE_SECONDS = 30
MAX_STREAM_BUFFER = 1024 * 256


def log(message: str) -> None:
    print(message, flush=True)


def json_log(value: object) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        return repr(value)


def log_kv(tag: str, **kwargs) -> None:
    parts = []
    for key, value in kwargs.items():
        parts.append(f"{key}={json_log(value)}")
    log(f"{tag} " + " ".join(parts))


def printable_text_preview(data: Optional[bytes], limit: int = 240) -> str:
    if not data:
        return ""
    try:
        text = data.decode("utf-8", errors="ignore")
    except Exception:
        text = ""
    text = text.replace("\r", " ").replace("\n", " ").replace("\x00", " ").strip()
    if len(text) > limit:
        return text[:limit] + "…"
    return text


def hex_preview(data: Optional[bytes], limit: int = 240) -> str:
    if not data:
        return ""
    view = data[:limit]
    out = view.hex()
    if len(data) > limit:
        out += "…"
    return out


def log_payload_preview(tag: str, payload: Optional[bytes], **kwargs) -> None:
    log_kv(
        tag,
        payload_len=len(payload or b""),
        payload_text=printable_text_preview(payload),
        payload_hex=hex_preview(payload),
        **kwargs,
    )


def norm_mac(mac: Optional[str]) -> Optional[str]:
    if not mac:
        return None
    return mac.strip().lower().replace("-", ":")


def send_layer2(frame, iface: Optional[str] = None) -> None:
    if iface:
        sendp(frame, verbose=False, iface=iface)
    else:
        sendp(frame, verbose=False)


def sensor(name: str, **kwargs) -> Dict[str, object]:
    data: Dict[str, object] = {"name": name}
    data.update(kwargs)
    return data


SENSORS: Dict[str, Dict[str, object]] = {
    # Info / identity
    "model_code": sensor("Device Type", icon="mdi:identifier"),
    "output_model": sensor("Output Model", icon="mdi:transmission-tower"),
    "mode": sensor("Mode", icon="mdi:transmission-tower-export"),
    "status_code": sensor("Status Code", icon="mdi:identifier"),
    "firmware_info": sensor("Firmware Info", icon="mdi:chip"),
    "firmware_version": sensor("Firmware Version", icon="mdi:chip"),
    "firmware_build_date": sensor("Firmware Build Date", icon="mdi:calendar"),
    "firmware_build_slot": sensor("Firmware Build Slot", icon="mdi:counter"),

    # Battery / BMS main page
    "bat_v": sensor("Battery Voltage", unit="V", device_class="voltage", state_class="measurement", icon="mdi:battery"),
    "bat_cap": sensor("Battery Capacity", unit="%", device_class="battery", state_class="measurement", icon="mdi:battery-high"),
    "bat_charge_current": sensor("Battery Charging Current", unit="A", device_class="current", state_class="measurement", icon="mdi:battery-plus"),
    "dischg_current": sensor("Battery Discharge Current", unit="A", device_class="current", state_class="measurement", icon="mdi:battery-minus"),
    "bat_series_count": sensor("Battery Number In Series", state_class="measurement", icon="mdi:numeric"),
    "battery_status": sensor("Battery Status", icon="mdi:battery-sync"),
    "battery_type": sensor("Battery Type", icon="mdi:battery-unknown"),

    # BMS page
    "bms_remaining_ah": sensor("Remaining Capacity", unit="Ah", icon="mdi:battery-medium"),
    "bms_nominal_ah": sensor("Nominal Capacity", unit="Ah", icon="mdi:battery-outline"),
    "bms_display_mode": sensor("Display Mode", icon="mdi:view-grid-outline"),
    "bms_max_cell_mv": sensor("Max Voltage", unit="mV", state_class="measurement", icon="mdi:battery-high"),
    "bms_max_cell_pos": sensor("Max Voltage Cell Position", state_class="measurement", icon="mdi:numeric"),
    "bms_min_cell_mv": sensor("Min Voltage", unit="mV", state_class="measurement", icon="mdi:battery-low"),
    "bms_min_cell_pos": sensor("Min Voltage Cell Position", state_class="measurement", icon="mdi:numeric"),
    "bms_cell_count": sensor("BMS Cell Count", state_class="measurement", icon="mdi:battery-sync"),
    "bms_cell_delta_mv": sensor("BMS Cell Delta", unit="mV", state_class="measurement", icon="mdi:battery-sync"),

    # Grid page
    "grid_v": sensor("AC Input Voltage", unit="V", device_class="voltage", state_class="measurement", icon="mdi:transmission-tower"),
    "grid_hz": sensor("Mains Frequency", unit="Hz", device_class="frequency", state_class="measurement", icon="mdi:current-ac"),
    "mains_current_flow_direction": sensor("Mains Current Flow Direction", icon="mdi:swap-horizontal-bold"),
    "mains_power_w": sensor("Mains Power", unit="W", device_class="power", state_class="measurement", icon="mdi:transmission-tower-export"),
    "mains_apparent_va": sensor("Mains Apparent Power", unit="VA", device_class="apparent_power", state_class="measurement", icon="mdi:flash"),

    # Load page
    "out_v": sensor("Output Voltage", unit="V", device_class="voltage", state_class="measurement", icon="mdi:power-plug"),
    "out_hz": sensor("Output Frequency", unit="Hz", device_class="frequency", state_class="measurement", icon="mdi:current-ac"),
    "apparent_va": sensor("Output Apparent Power", unit="VA", device_class="apparent_power", state_class="measurement", icon="mdi:flash"),
    "load_w": sensor("Output Active Power", unit="W", device_class="power", state_class="measurement", icon="mdi:home-lightning-bolt"),
    "load_pct": sensor("Output Load Percent", unit="%", state_class="measurement", icon="mdi:gauge"),
    "output_dc_comp": sensor("Output DC Component", state_class="measurement", icon="mdi:tune-variant"),

    # PV page
    "generation_power_w": sensor("Generation Power", unit="W", device_class="power", state_class="measurement", icon="mdi:solar-power"),
    "pv_v": sensor("PV Voltage", unit="V", device_class="voltage", state_class="measurement", icon="mdi:solar-panel"),
    "pv_current_a": sensor("PV Current", unit="A", device_class="current", state_class="measurement", icon="mdi:current-dc"),
    "pv_w": sensor("PV Power", unit="W", device_class="power", state_class="measurement", icon="mdi:solar-power"),
    "pv2_v": sensor("PV2 Voltage", unit="V", device_class="voltage", state_class="measurement", icon="mdi:solar-panel-large"),
    "pv2_current_a": sensor("PV2 Current", unit="A", device_class="current", state_class="measurement", icon="mdi:current-dc"),
    "pv2_power_w": sensor("PV2 Power", unit="W", device_class="power", state_class="measurement", icon="mdi:solar-power-variant"),
    "pv_today_kwh": sensor("Daily Electricity Generation", unit="kWh", device_class="energy", icon="mdi:solar-power-variant"),
    "pv_month_kwh": sensor("Monthly Electricity Generation", unit="kWh", device_class="energy", icon="mdi:calendar-month"),
    "pv_total_kwh": sensor("Total Electricity Generation", unit="kWh", device_class="energy", state_class="total_increasing", icon="mdi:counter"),
    "pv_year_kwh": sensor("Yearly Electricity Generation", unit="kWh", device_class="energy", icon="mdi:calendar-range"),
    "pv_temp": sensor("PV Temperature", unit="°C", device_class="temperature", state_class="measurement", icon="mdi:thermometer"),
    "pv2_temp": sensor("PV2 Temperature", unit="°C", device_class="temperature", state_class="measurement", icon="mdi:thermometer"),
    "solar_charging_switch": sensor("Solar Charging Switch", icon="mdi:solar-power-variant-outline"),

    # App "More" page – mapped / partially mapped
    "bus_voltage": sensor("BUS Voltage", unit="V", device_class="voltage", state_class="measurement", icon="mdi:flash-triangle"),
    "ac_charging_switch": sensor("AC Charging Switch", icon="mdi:power-plug-battery"),
    "abnormal_fan_speed": sensor("Abnormal Fan Speed", icon="mdi:fan-alert"),
    "abnormal_low_pv_power": sensor("Abnormal Low PV Power", icon="mdi:solar-panel-large"),
    "abnormal_temperature_sensor": sensor("Abnormal Temperature Sensor", icon="mdi:thermometer-alert"),
    "automatic_return_to_first_page": sensor("Automatic Return To The First Page Function", icon="mdi:page-first"),
    "bms_allow_charging_flag": sensor("BMS Allow Charging Flag", icon="mdi:battery-plus-variant"),
    "bms_allow_discharge_flag": sensor("BMS Allow Discharge Flag", icon="mdi:battery-minus-variant"),
    "bms_auto_start_soc_after_low": sensor("BMS Automatically Starts SOC After Low", unit="%", state_class="measurement", icon="mdi:battery-sync"),
    "bms_avg_temp_c": sensor("BMS Average Temperature", unit="°C", device_class="temperature", state_class="measurement", icon="mdi:thermometer"),
    "bms_charge_current_limit_a": sensor("BMS Charge Current Limit", unit="A", device_class="current", state_class="measurement", icon="mdi:current-dc"),
    "bms_charge_voltage_limit_v": sensor("BMS Charge Voltage Limit", unit="V", device_class="voltage", state_class="measurement", icon="mdi:battery-arrow-up"),
    "bms_charging_current_a": sensor("BMS Charging Current", unit="A", device_class="current", state_class="measurement", icon="mdi:battery-plus"),
    "bms_charging_overcurrent_sign": sensor("BMS Charging Overcurrent Sign", icon="mdi:alert"),
    "bms_communication_control_function": sensor("BMS Communication Control Function", icon="mdi:lan-connect"),
    "bms_communication_normal": sensor("BMS Communication Normal", icon="mdi:lan-check"),
    "bms_current_soc": sensor("BMS Current SOC", unit="%", state_class="measurement", icon="mdi:battery-high"),
    "bms_discharge_current_a": sensor("BMS Discharge Current", unit="A", device_class="current", state_class="measurement", icon="mdi:battery-minus"),
    "bms_discharge_overcurrent_flag": sensor("BMS Discharge Overcurrent Flag", icon="mdi:alert"),
    "bms_discharge_voltage_limit_v": sensor("BMS Discharge Voltage Limit", unit="V", device_class="voltage", state_class="measurement", icon="mdi:battery-arrow-down"),
    "bms_low_battery_alarm_flag": sensor("BMS Low Battery Alarm Flag", icon="mdi:battery-alert"),
    "bms_low_power_fault_flag": sensor("BMS Low Power Fault Flag", icon="mdi:alert-circle"),
    "bms_low_power_soc": sensor("BMS Low Power SOC", unit="%", state_class="measurement", icon="mdi:battery-10"),
    "bms_low_temperature_flag": sensor("BMS Low Temperature Flag", icon="mdi:snowflake-alert"),
    "bms_returns_to_battery_mode_soc": sensor("BMS Returns To Battery Mode SOC", unit="%", state_class="measurement", icon="mdi:battery-arrow-up"),
    "bms_returns_to_mains_mode_soc": sensor("BMS Returns To Mains Mode SOC", unit="%", state_class="measurement", icon="mdi:transmission-tower"),
    "bms_temperature_too_high_flag": sensor("BMS Temperature Too High Flag", icon="mdi:thermometer-alert"),
    "battery_equalization_mode": sensor("Battery Equalization Mode", icon="mdi:battery-sync"),
    "battery_equalization_voltage_v": sensor("Battery Equalization Voltage", unit="V", device_class="voltage", state_class="measurement", icon="mdi:battery-sync"),
    "battery_not_connected": sensor("Battery Not Connected", icon="mdi:battery-off"),
    "battery_overvoltage_shutdown_voltage_v": sensor("Battery Overvoltage Shutdown Voltage", unit="V", device_class="voltage", state_class="measurement", icon="mdi:battery-alert-variant-outline"),
    "battery_voltage_higher": sensor("Battery Voltage Higher", icon="mdi:battery-alert"),
    "boost_temperature_c": sensor("Boost Temperature", unit="°C", device_class="temperature", state_class="measurement", icon="mdi:thermometer-chevron-up"),
    "buzzer_function": sensor("Buzzer Function", icon="mdi:bullhorn"),
    "charging_light_status": sensor("Charging Light Status", icon="mdi:lightbulb"),
    "charging_main_switch": sensor("Charging Main Switch", icon="mdi:power"),
    "charging_priority_order": sensor("Charging Priority Order", icon="mdi:sort"),
    "ct_function_switch": sensor("CT Function Switch", icon="mdi:current-ac"),
    "dc_rectification_temperature_c": sensor("DC Rectification Temperature", unit="°C", device_class="temperature", state_class="measurement", icon="mdi:thermometer"),
    "does_machine_have_output": sensor("Does The Machine Have An Output", icon="mdi:power-plug"),
    "dual_output_mode": sensor("Dual Output Mode", icon="mdi:power-socket"),
    "eco": sensor("ECO", icon="mdi:leaf"),
    "eeprom_data_abnormality": sensor("EEPROM Data Abnormality", icon="mdi:memory"),
    "eeprom_read_write_exception": sensor("EEPROM Read Write Exception", icon="mdi:memory-alert"),
    "equalization_interval": sensor("Equalization Interval", icon="mdi:calendar-sync"),
    "equalization_overtime": sensor("Equalization Overtime", icon="mdi:timer-alert"),
    "equalization_time": sensor("Equalization Time", icon="mdi:timer"),
    "fan_1_speed": sensor("Fan 1 Speed", unit="%", state_class="measurement", icon="mdi:fan"),
    "fan_1_status": sensor("Fan 1 Status", icon="mdi:fan"),
    "fan_2_speed": sensor("Fan 2 Speed", unit="%", state_class="measurement", icon="mdi:fan"),
    "fan_2_status": sensor("Fan 2 Status", icon="mdi:fan"),
    "float_charging_voltage_v": sensor("Float Charging Voltage", unit="V", device_class="voltage", state_class="measurement", icon="mdi:battery-charging-medium"),
    "grid_connected_current_a": sensor("Grid Connected Current", unit="A", device_class="current", state_class="measurement", icon="mdi:current-ac"),
    "grid_connection_function": sensor("Grid Connection Function", icon="mdi:transmission-tower"),
    "grid_connection_sign": sensor("Grid Connection Sign", icon="mdi:transmission-tower-off"),
    "high_frequency_of_mains_power_loss_hz": sensor("High Frequency Of Mains Power Loss", unit="Hz", device_class="frequency", state_class="measurement", icon="mdi:current-ac"),
    "high_point_of_mains_power_loss_voltage_v": sensor("High Point Of Mains Power Loss Voltage", unit="V", device_class="voltage", state_class="measurement", icon="mdi:flash-alert"),
    "inductor_current_a": sensor("Inductor Current", unit="A", device_class="current", state_class="measurement", icon="mdi:coil"),
    "input_source_prompt_function": sensor("Input Source Prompt Function", icon="mdi:tooltip-outline"),
    "input_voltage_too_high": sensor("Input Voltage Too High", icon="mdi:flash-alert"),
    "inverter_light_status": sensor("Inverter Light Status", icon="mdi:lightbulb"),
    "inverter_temperature_c": sensor("Inverter Temperature", unit="°C", device_class="temperature", state_class="measurement", icon="mdi:thermometer"),
    "lcd_back_lighting": sensor("LCD Back Lighting", icon="mdi:monitor"),
    "li_battery_activation_function_switch": sensor("Li Battery Activation Function Switch", icon="mdi:battery-heart"),
    "li_battery_activation_process": sensor("Li Battery Activation Process", icon="mdi:battery-heart-variant"),
    "low_battery_alarm": sensor("Low Battery Alarm", icon="mdi:battery-alert"),
    "low_electric_lock_voltage_v": sensor("Low Electric Lock Voltage", unit="V", device_class="voltage", state_class="measurement", icon="mdi:battery-off-outline"),
    "low_frequency_of_mains_power_loss_hz": sensor("Low Frequency Of Mains Power Loss", unit="Hz", device_class="frequency", state_class="measurement", icon="mdi:current-ac"),
    "low_point_of_mains_power_loss_voltage_v": sensor("Low Point Of Mains Power Loss Voltage", unit="V", device_class="voltage", state_class="measurement", icon="mdi:flash-alert"),
    "machine_over_temperature": sensor("Machine Over Temperature", icon="mdi:thermometer-alert"),
    "main_output_relay_status": sensor("Main Output Relay Status", icon="mdi:toggle-switch"),
    "mains_charging_ending_time": sensor("Mains Charging Ending Time", icon="mdi:clock-end"),
    "mains_charging_starting_time": sensor("Mains Charging Starting Time", icon="mdi:clock-start"),
    "mains_input_range": sensor("Mains Input Range", icon="mdi:sine-wave"),
    "mains_light_status": sensor("Mains Light Status", icon="mdi:lightbulb"),
    "max_utility_charge_current_a": sensor("Max utility charge current", unit="A", device_class="current", state_class="measurement", icon="mdi:current-ac"),
    "max_temperature_c": sensor("Max. Temperature", unit="°C", device_class="temperature", state_class="measurement", icon="mdi:thermometer-high"),
    "maximum_total_charging_current_a": sensor("Maximum Total Charging Current", unit="A", device_class="current", state_class="measurement", icon="mdi:current-dc"),
    "mppt_constant_temperature_mode": sensor("MPPT Constant Temperature Mode", icon="mdi:solar-panel"),
    "output_ending_time": sensor("Output Ending Time", icon="mdi:clock-end"),
    "output_set_frequency": sensor("Output Set Frequency", unit="Hz", device_class="frequency", state_class="measurement", icon="mdi:current-ac"),
    "output_set_voltage": sensor("Output Set Voltage", unit="V", device_class="voltage", state_class="measurement", icon="mdi:flash"),
    "output_starting_time": sensor("Output Starting Time", icon="mdi:clock-start"),
    "over_temperature_restart_function": sensor("Over Temperature Restart Function", icon="mdi:restart"),
    "overloaded": sensor("OverLoaded", icon="mdi:alert"),
    "overload_restart_function": sensor("Overload Restart Function", icon="mdi:restart"),
    "overload_to_bypass_function": sensor("Overload To Bypass Function", icon="mdi:swap-horizontal"),
    "parallel_mode": sensor("Parallel Mode", icon="mdi:call-split"),
    "parallel_mode_turn_off_soc": sensor("Parallel Mode Turn Off SOC", unit="%", state_class="measurement", icon="mdi:battery-arrow-down"),
    "parallel_mode_turn_off_voltage_v": sensor("Parallel Mode Turn Off Voltage", unit="V", device_class="voltage", state_class="measurement", icon="mdi:battery-arrow-down-outline"),
    "parallel_role": sensor("Parallel Role", icon="mdi:account-switch"),
    "power_supply_from_pv_to_load_in_ac_state": sensor("Power Supply From PV To Load In AC State", icon="mdi:solar-power-variant"),
    "pv_energy_feeding_priority": sensor("PV Energy Feeding Priority", icon="mdi:sort-variant"),
    "pv_grid_connection_agreement": sensor("PV Grid Connection Agreement", icon="mdi:file-document-outline"),
    "return_to_battery_mode_voltage_v": sensor("Return To Battery Mode Voltage", unit="V", device_class="voltage", state_class="measurement", icon="mdi:battery-arrow-up-outline"),
    "return_to_mains_mode_voltage_v": sensor("Return To Mains Mode Voltage", unit="V", device_class="voltage", state_class="measurement", icon="mdi:transmission-tower"),
    "second_delay_time": sensor("Second Delay Time", icon="mdi:timer-outline"),
    "second_output_battery_capacity": sensor("Second Output Battery Capacity", unit="%", state_class="measurement", icon="mdi:battery-50"),
    "second_output_battery_voltage_v": sensor("Second Output Battery Voltage", unit="V", device_class="voltage", state_class="measurement", icon="mdi:battery"),
    "second_output_discharge_time": sensor("Second Output Discharge Time", icon="mdi:timer-sand"),
    "software_version": sensor("Software Version", icon="mdi:chip"),
    "strong_charging_voltage_v": sensor("Strong Charging Voltage", unit="V", device_class="voltage", state_class="measurement", icon="mdi:battery-charging-high"),
    "system_time_hm": sensor("System Time (Hour Minute)", icon="mdi:clock-outline"),
    "system_time_ymd": sensor("System Time (Year Month Day)", icon="mdi:calendar-range"),
    "total_number_of_grid_connection": sensor("Total Number Of Grid Connection", state_class="measurement", icon="mdi:counter"),
    "transformer_temperature_c": sensor("Transformer Temperature", unit="°C", device_class="temperature", state_class="measurement", icon="mdi:thermometer"),
    "warning_light_status": sensor("Warning Light Status", icon="mdi:alarm-light-outline"),
    "working_mode": sensor("Working Mode", icon="mdi:cog-transfer"),

    # Raw / decoded helper sensors
    "mains_wdrr_token": sensor("Mains WdRR Token", icon="mdi:code-string", entity_category="diagnostic", enabled_by_default=False),
    "mains_wdrr_value": sensor("Mains WdRR Value", state_class="measurement", icon="mdi:numeric", entity_category="diagnostic", enabled_by_default=False),
    "mains_wdrr_abs": sensor("Mains WdRR Absolute", state_class="measurement", icon="mdi:counter", entity_category="diagnostic", enabled_by_default=False),
    "mains_eo8w_code": sensor("Mains eo8w Code", icon="mdi:code-tags", entity_category="diagnostic", enabled_by_default=False),
    "wdrr_status_bits": sensor("WdRR Status Bits", icon="mdi:code-brackets", entity_category="diagnostic", enabled_by_default=False),
    "eo8w_flags_raw": sensor("eo8w Flags Raw", icon="mdi:code-braces", entity_category="diagnostic", enabled_by_default=False),
    "eo8w_blob_raw": sensor("eo8w Blob Raw", icon="mdi:code-json", entity_category="diagnostic", enabled_by_default=False),
    "yavb_flags_raw": sensor("Yavb Flags Raw", icon="mdi:code-braces", entity_category="diagnostic", enabled_by_default=False),
    "yavb_code_raw": sensor("Yavb Code Raw", icon="mdi:code-tags", entity_category="diagnostic", enabled_by_default=False),
    "yavb_aux_raw": sensor("Yavb Aux Raw", icon="mdi:code-json", entity_category="diagnostic", enabled_by_default=False),
    "output_status_bits": sensor("Output Status Bits", icon="mdi:code-brackets", entity_category="diagnostic", enabled_by_default=False),
    "mains_flow_code": sensor("Mains Flow Code", icon="mdi:numeric", entity_category="diagnostic", enabled_by_default=False),
    "mains_input_range_code": sensor("Mains Input Range Code", icon="mdi:code-string", entity_category="diagnostic", enabled_by_default=False),

    # Compatibility aliases for older entity names
    "bat_temp": sensor("Inverter Temperature (legacy)", unit="°C", device_class="temperature", state_class="measurement", icon="mdi:thermometer", entity_category="diagnostic", enabled_by_default=False),
    "max_chg": sensor("Max Charge Current (legacy)", unit="A", device_class="current", state_class="measurement", icon="mdi:current-dc", entity_category="diagnostic", enabled_by_default=False),
    "util_chg": sensor("Utility Charge Current (candidate)", unit="A", device_class="current", state_class="measurement", icon="mdi:current-ac", entity_category="diagnostic", enabled_by_default=False),
    "bulk_v": sensor("Bulk Charging Voltage (legacy)", unit="V", device_class="voltage", state_class="measurement", icon="mdi:battery-charging-high", entity_category="diagnostic", enabled_by_default=False),
    "float_v": sensor("Float Charging Voltage (legacy)", unit="V", device_class="voltage", state_class="measurement", icon="mdi:battery-charging-medium", entity_category="diagnostic", enabled_by_default=False),
    "cut_v": sensor("Low Battery Cut-off (legacy)", unit="V", device_class="voltage", state_class="measurement", icon="mdi:battery-off-outline", entity_category="diagnostic", enabled_by_default=False),
    "mains_flow_state": sensor("Mains Flow State (legacy)", icon="mdi:swap-horizontal-bold", entity_category="diagnostic", enabled_by_default=False),
}

for i in range(1, 17):
    SENSORS[f"cell_{i}_mv"] = sensor(
        f"Battery Voltage {i}",
        unit="mV",
        state_class="measurement",
        icon="mdi:battery-heart-variant",
    )

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


class TcpFlowState:
    def __init__(self) -> None:
        self.next_seq: Optional[int] = None
        self.pending: Dict[int, bytes] = {}
        self.stream = bytearray()
        self.last_seen = time.time()

    def reset(self) -> None:
        self.next_seq = None
        self.pending.clear()
        self.stream.clear()
        self.last_seen = time.time()


FLOW_STATES: Dict[Tuple[str, int, str, int], TcpFlowState] = {}
PUBLISHED_SENSOR_KEYS = set()
SEEN_MQTT_TOPICS: Dict[str, int] = {}
IMPORTANT_DEBUG_KEYS = ("bms_avg_temp_c", "mains_current_flow_direction")


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
    PUBLISHED_SENSOR_KEYS.add(key)


def publish_discovery() -> None:
    global DISCOVERY_PUBLISHED

    for key in sorted(SENSORS.keys()):
        publish_sensor_discovery(key)

    client.publish(AVAILABILITY_TOPIC, "online", retain=True)
    DISCOVERY_PUBLISHED = True
    log("[HA MQTT] Discovery published")


def on_connect(_client, _userdata, _flags, rc, _properties=None):
    code = int(rc) if rc is not None else -1
    if code == 0:
        log(f"[HA MQTT] Connected to {MQTT_HOST}:{MQTT_PORT}")
        publish_discovery()
        if any(v is not None for v in LAST_STATE.values()):
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


def mqtt_type_name(first_byte: int) -> str:
    return MQTT_PACKET_TYPES.get((first_byte >> 4) & 0x0F, f"TYPE_{(first_byte >> 4) & 0x0F}")


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


def is_reasonable_topic(topic: str) -> bool:
    if not topic or len(topic) > 256:
        return False
    if not PRINTABLE_ASCII_RE.match(topic):
        return False
    return "/" in topic


def validate_publish_packet(packet: bytes) -> bool:
    if not packet or ((packet[0] >> 4) & 0x0F) != 3:
        return False

    remaining_len, pos = decode_remaining_length(packet, 1)
    if remaining_len is None or pos is None:
        return False

    if len(packet) != pos + remaining_len:
        return False

    if len(packet) < pos + 2:
        return False

    topic_len = int.from_bytes(packet[pos:pos + 2], "big")
    pos += 2
    if topic_len <= 0 or topic_len > 256 or len(packet) < pos + topic_len:
        return False

    topic = packet[pos:pos + topic_len].decode("utf-8", errors="ignore")
    if not is_reasonable_topic(topic):
        return False

    return True


def validate_generic_mqtt_packet(packet: bytes) -> bool:
    if not packet:
        return False

    packet_type = (packet[0] >> 4) & 0x0F
    if packet_type < 1 or packet_type > 14:
        return False

    if packet_type == 3:
        return validate_publish_packet(packet)

    remaining_len, pos = decode_remaining_length(packet, 1)
    if remaining_len is None or pos is None:
        return False

    if len(packet) != pos + remaining_len:
        return False

    if len(packet) > MAX_MQTT_PACKET:
        return False

    return True


def extract_mqtt_packets_from_stream(stream: bytearray) -> List[bytes]:
    packets: List[bytes] = []

    while len(stream) >= 2:
        first = stream[0]
        packet_type = (first >> 4) & 0x0F

        if packet_type < 1 or packet_type > 14:
            del stream[0]
            continue

        try:
            remaining_len, header_end = decode_remaining_length(stream, 1)
        except Exception:
            del stream[0]
            continue

        if remaining_len is None or header_end is None:
            break

        total_len = header_end + remaining_len
        if total_len <= 0 or total_len > MAX_MQTT_PACKET:
            del stream[0]
            continue

        if len(stream) < total_len:
            break

        packet = bytes(stream[:total_len])
        if not validate_generic_mqtt_packet(packet):
            del stream[0]
            continue

        del stream[:total_len]
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


def get_flow_state(flow_key: Tuple[str, int, str, int]) -> TcpFlowState:
    state = FLOW_STATES.get(flow_key)
    now = time.time()

    if state is None:
        state = TcpFlowState()
        FLOW_STATES[flow_key] = state
        return state

    if now - state.last_seen > STREAM_STALE_SECONDS:
        state.reset()

    state.last_seen = now
    return state


def append_stream_data(flow_key: Tuple[str, int, str, int], seq: int, payload: bytes) -> List[bytes]:
    state = get_flow_state(flow_key)
    packets: List[bytes] = []

    if not payload:
        return packets

    if state.next_seq is None:
        state.next_seq = seq
        if LOG_STREAM_EVENTS:
            log_kv("[STREAM INIT]", flow=flow_key, seq=seq, payload_len=len(payload))

    if seq < state.next_seq:
        overlap = state.next_seq - seq
        if overlap >= len(payload):
            if LOG_STREAM_EVENTS:
                log_kv("[STREAM DUPLICATE]", flow=flow_key, seq=seq, next_seq=state.next_seq, payload_len=len(payload))
            return packets
        if LOG_STREAM_EVENTS:
            log_kv("[STREAM OVERLAP]", flow=flow_key, seq=seq, next_seq=state.next_seq, overlap=overlap, payload_len=len(payload))
        payload = payload[overlap:]
        seq = state.next_seq

    if seq > state.next_seq:
        if seq not in state.pending:
            state.pending[seq] = payload
            if LOG_STREAM_EVENTS:
                log_kv("[STREAM GAP]", flow=flow_key, seq=seq, next_seq=state.next_seq, payload_len=len(payload), pending_count=len(state.pending))
                log_payload_preview("[STREAM GAP PAYLOAD]", payload, flow=flow_key, seq=seq)
        return packets

    state.stream.extend(payload)
    state.next_seq = seq + len(payload)

    while state.next_seq in state.pending:
        pending_payload = state.pending.pop(state.next_seq)
        if LOG_STREAM_EVENTS:
            log_kv("[STREAM REASSEMBLE]", flow=flow_key, seq=state.next_seq, payload_len=len(pending_payload), pending_count=len(state.pending))
        state.stream.extend(pending_payload)
        state.next_seq += len(pending_payload)

    if len(state.stream) > MAX_STREAM_BUFFER:
        if LOG_STREAM_EVENTS:
            log_kv("[STREAM TRIM]", flow=flow_key, stream_len=len(state.stream), max_len=MAX_STREAM_BUFFER)
        del state.stream[:-MAX_STREAM_BUFFER]

    packets.extend(extract_mqtt_packets_from_stream(state.stream))
    return packets


def sanitize_block_key(name: str) -> str:
    slug = SLUG_RE.sub("_", name.strip().lower()).strip("_")
    if not slug:
        slug = "raw"
    if slug[0].isdigit():
        slug = f"b_{slug}"
    return slug


def ensure_dynamic_debug_sensor(block_name: str) -> str:
    state_key = f"dbg_{sanitize_block_key(block_name)}_raw"
    if state_key not in SENSORS:
        SENSORS[state_key] = sensor(f"DEBUG {block_name} Raw", icon="mdi:bug-outline", entity_category="diagnostic", enabled_by_default=False)
        LAST_STATE.setdefault(state_key, None)
        if DISCOVERY_PUBLISHED:
            publish_sensor_discovery(state_key)
    return state_key


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

        parts = [p.strip() for p in text.split(" ") if p.strip()]
        cleaned = []
        for p in parts:
            while p and p[-1] in "),;:\t":
                p = p[:-1]
            if p:
                cleaned.append(p)

        clean_text = " ".join(cleaned)
        return clean_text, cleaned

    @staticmethod
    def _clean_model_code(text: str) -> str:
        parts = [p for p in text.split() if p]
        return parts[0] if parts else text

    @staticmethod
    def _format_fw_date(raw_date: str) -> str:
        if len(raw_date) == 8 and raw_date.isdigit():
            return f"{raw_date[0:4]}-{raw_date[4:6]}-{raw_date[6:8]}"
        return raw_date

    @staticmethod
    def _decode_yes_no_digit(token: Optional[str], *, yes_word: str = "Yes", no_word: str = "No") -> Optional[str]:
        if token is None:
            return None
        tok = str(token).strip()
        if tok == "1":
            return yes_word
        if tok == "0":
            return no_word
        return None

    @staticmethod
    def _split_range_and_signed(token: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
        if token is None:
            return None, None
        tok = token.strip()
        m = re.fullmatch(r"(\d{2})([+-]\d+)", tok)
        if m:
            return m.group(1), m.group(2)
        return None, None

    @staticmethod
    def _format_hour_token(token: Optional[str]) -> Optional[str]:
        if token is None:
            return None
        tok = token.strip()
        if not tok:
            return None
        if re.fullmatch(r"0+", tok):
            return "0 h"
        if len(tok) == 4 and tok.isdigit():
            hh = int(tok[:2])
            mm = int(tok[2:])
            if mm == 0:
                return f"{hh} h"
            return f"{hh:02d}:{mm:02d}"
        if tok.isdigit():
            return f"{int(tok)} h"
        return tok

    @staticmethod
    def _format_min_token(token: Optional[str]) -> Optional[str]:
        if token is None:
            return None
        tok = token.strip()
        if not tok:
            return None
        if tok.isdigit():
            return f"{int(tok)} min"
        return tok

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
    def _to_float_strict(token: str) -> Optional[float]:
        token = token.strip()
        if not STRICT_NUM_RE.match(token):
            return None
        try:
            return float(token)
        except Exception:
            return None

    @staticmethod
    def _to_int_strict(token: str) -> Optional[int]:
        token = token.strip()
        if not re.fullmatch(r"-?\d+", token):
            return None
        try:
            return int(token)
        except Exception:
            return None

    @staticmethod
    def _to_yes_no(token: Optional[str]) -> Optional[str]:
        if token is None:
            return None
        tok = token.strip().lower()
        if tok in {"1", "on", "open", "yes", "true", "enable", "enabled", "light", "close", "closed"}:
            if tok in {"close", "closed"}:
                return "Close"
            if tok in {"open"}:
                return "Open"
            if tok in {"light"}:
                return "Light"
            if tok.startswith("enable"):
                return "Enable"
            return "Yes"
        if tok in {"0", "off", "no", "false", "disable", "disabled", "stop", "flicker"}:
            if tok.startswith("disable"):
                return "Disable"
            if tok == "stop":
                return "Stop"
            if tok == "flicker":
                return "Flicker"
            return "No" if tok in {"0", "no", "false"} else "Off"
        return None

    @staticmethod
    def _extract_alpha_code(text: str) -> Optional[str]:
        parts = re.findall(r"[A-Z]+", text)
        if not parts:
            return None
        return " ".join(parts)

    @staticmethod
    def _mains_flow_from_values(code: Optional[str], signed_value: Optional[int]) -> Optional[str]:
        if code is not None:
            code = code.strip()
            if code == "0":
                return "Mains To Inverter"
            if code == "1":
                return "Inverter To Mains"
            if code == "2":
                return "Idle"
        if signed_value is None:
            return None
        if signed_value > 0:
            return "Mains To Inverter"
        if signed_value < 0:
            return "Inverter To Mains"
        return "Idle"

    @staticmethod
    def _parse_cost_energy(tokens: List[str]) -> Dict[str, object]:
        state: Dict[str, object] = {}
        work = list(tokens)

        if work and len(work[0]) == 6 and work[0].isdigit():
            ymd = work.pop(0)
            state["system_time_ymd"] = ymd
        if work and ":" in work[0]:
            state["system_time_hm"] = work.pop(0)

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
        if len(tokens) >= 3:
            display_code = SolarParser._to_int(tokens[2])
            if display_code == 2:
                state["bms_display_mode"] = "Display All Battery Cell Data Locations"
            elif display_code is not None:
                state["bms_display_mode"] = str(display_code)
        if len(tokens) >= 7:
            max_mv = SolarParser._to_int(tokens[3])
            max_pos = SolarParser._to_int(tokens[4])
            min_mv = SolarParser._to_int(tokens[5])
            min_pos = SolarParser._to_int(tokens[6])
            if max_mv is not None:
                state["bms_max_cell_mv"] = max_mv
            if max_pos is not None:
                state["bms_max_cell_pos"] = max_pos
            if min_mv is not None:
                state["bms_min_cell_mv"] = min_mv
            if min_pos is not None:
                state["bms_min_cell_pos"] = min_pos
            if max_mv is not None and min_mv is not None:
                state["bms_cell_delta_mv"] = max_mv - min_mv
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
    def _apply_dynamic_debug(state: Dict[str, object], parsed: Dict[str, Tuple[str, List[str]]]) -> None:
        for block_name, (raw_text, _tokens) in parsed.items():
            state_key = ensure_dynamic_debug_sensor(block_name)
            state[state_key] = raw_text[:250]

    @staticmethod
    def _try_ascii_schema(blocks: Dict[str, bytes]) -> Dict[str, object]:
        state: Dict[str, object] = {}
        parsed = {name: SolarParser._parse_ascii_text(data) for name, data in blocks.items()}

        SolarParser._apply_dynamic_debug(state, parsed)

        # Info / identity
        if "SUCV" in parsed:
            state["model_code"] = SolarParser._clean_model_code(parsed["SUCV"][0])

        if "hR6Y" in parsed:
            raw_fw, fw_tokens = parsed["hR6Y"]
            state["firmware_info"] = raw_fw
            if len(fw_tokens) >= 1:
                state["firmware_version"] = fw_tokens[0]
                state["software_version"] = fw_tokens[0]
            if len(fw_tokens) >= 2:
                state["firmware_build_date"] = SolarParser._format_fw_date(fw_tokens[1])
            if len(fw_tokens) >= 3:
                state["firmware_build_slot"] = fw_tokens[2]

        # Output / load -> 2l0E
        vals = parsed.get("2l0E", ("", []))[1]
        if len(vals) >= 2:
            out_v = SolarParser._to_float(vals[0])
            out_hz = SolarParser._to_float(vals[1])
            if out_v is not None:
                state["out_v"] = round(out_v, 1)
            if out_hz is not None:
                state["out_hz"] = round(out_hz, 1)
                state["output_set_frequency"] = round(out_hz, 1)

        if len(vals) >= 4:
            out_va = SolarParser._to_int(vals[2])
            out_w = SolarParser._to_int(vals[3])
            if out_va is not None:
                state["apparent_va"] = out_va
            if out_w is not None:
                state["load_w"] = out_w

        if len(vals) >= 5:
            load_pct = SolarParser._to_int(vals[4])
            if load_pct is not None and 0 <= load_pct <= 100:
                state["load_pct"] = load_pct

        if len(vals) >= 6:
            dc_comp = SolarParser._to_int(vals[5])
            if dc_comp is not None:
                state["output_dc_comp"] = dc_comp

        if len(vals) >= 7:
            state["output_status_bits"] = vals[6]

        if len(vals) >= 8:
            inductor_current = SolarParser._to_float(vals[7])
            if inductor_current is not None:
                state["inductor_current_a"] = round(inductor_current, 1)

        if len(vals) >= 9:
            dc_rect_temp = SolarParser._to_float(vals[8])
            if dc_rect_temp is not None:
                if dc_rect_temp > 100:
                    dc_rect_temp /= 10.0
                state["dc_rectification_temperature_c"] = round(dc_rect_temp, 1)

        # Grid / mains -> WdRR
        vals = list(parsed.get("WdRR", ("", []))[1])
        tail_range = None
        tail_apparent = None
        if vals:
            tail_range, tail_apparent = SolarParser._split_range_and_signed(vals[-1])
            if tail_range is not None and tail_apparent is not None:
                vals = vals[:-1] + [tail_range, tail_apparent]

        mains_signed = None
        if len(vals) >= 2:
            grid_v = SolarParser._to_float(vals[0])
            grid_hz = SolarParser._to_float(vals[1])
            if grid_v is not None:
                state["grid_v"] = round(grid_v, 1)
            if grid_hz is not None:
                state["grid_hz"] = round(grid_hz, 1)

        if len(vals) >= 6:
            hv = SolarParser._to_float(vals[2])
            lv = SolarParser._to_float(vals[3])
            hf = SolarParser._to_float(vals[4])
            lf = SolarParser._to_float(vals[5])
            if hv is not None:
                state["high_point_of_mains_power_loss_voltage_v"] = round(hv, 1)
            if lv is not None:
                state["low_point_of_mains_power_loss_voltage_v"] = round(lv, 1)
            if hf is not None:
                state["high_frequency_of_mains_power_loss_hz"] = round(hf, 1)
            if lf is not None:
                state["low_frequency_of_mains_power_loss_hz"] = round(lf, 1)

        if len(vals) >= 7:
            state["mains_wdrr_token"] = vals[6]
            mains_signed = SolarParser._to_int(vals[6])
            if mains_signed is not None:
                state["mains_wdrr_value"] = mains_signed
                state["mains_wdrr_abs"] = abs(mains_signed)
                state["mains_power_w"] = abs(mains_signed)

        if len(vals) >= 8:
            state["mains_flow_code"] = vals[7]

        if len(vals) >= 9:
            state["wdrr_status_bits"] = vals[8]
            state["main_output_relay_status"] = "On" if vals[8].startswith("1") else None

        if len(vals) >= 10:
            state["mains_input_range_code"] = vals[9]
            if vals[9] == "11":
                state["mains_input_range"] = "UPS"
            else:
                state["mains_input_range"] = vals[9]

        if len(vals) >= 11:
            mains_apparent = SolarParser._to_int(vals[10])
            if mains_apparent is not None:
                state["mains_apparent_va"] = abs(mains_apparent)

        if "mains_apparent_va" not in state and tail_apparent is not None:
            mains_apparent = SolarParser._to_int(tail_apparent)
            if mains_apparent is not None:
                state["mains_apparent_va"] = abs(mains_apparent)
        if "mains_input_range" not in state and tail_range is not None:
            state["mains_input_range_code"] = tail_range
            state["mains_input_range"] = "UPS" if tail_range == "11" else tail_range

        mains_flow_code = state.get("mains_flow_code")
        mains_flow_code_str = str(mains_flow_code).strip() if mains_flow_code is not None else None
        resolved_flow = SolarParser._mains_flow_from_values(
            mains_flow_code_str,
            mains_signed,
        )
        if resolved_flow is None:
            if mains_flow_code_str in {"0", "00"}:
                resolved_flow = "Mains To Inverter"
            elif mains_flow_code_str in {"1", "01"}:
                resolved_flow = "Inverter To Mains"
            elif mains_flow_code_str in {"2", "02"}:
                resolved_flow = "Idle"
            elif mains_signed == 0 and state.get("mains_apparent_va") == 0:
                resolved_flow = "Mains To Inverter"
        if resolved_flow is not None:
            state["mains_current_flow_direction"] = resolved_flow

        # Battery block -> 2ONL
        vals = parsed.get("2ONL", ("", []))[1]
        if len(vals) >= 3:
            series_count = SolarParser._to_int_strict(vals[0])
            bat_v = SolarParser._to_float_strict(vals[1])
            bat_cap = SolarParser._to_int_strict(vals[2])

            if series_count is not None:
                state["bat_series_count"] = series_count
            if bat_v is not None and 0 <= bat_v <= 100:
                state["bat_v"] = round(bat_v, 1)
            if bat_cap is not None and 0 <= bat_cap <= 100:
                state["bat_cap"] = bat_cap

        if len(vals) >= 4:
            charge_a = SolarParser._to_float_strict(vals[3])
            if charge_a is not None and 0 <= charge_a <= 300:
                state["bat_charge_current"] = round(charge_a, 2)

        if len(vals) >= 5:
            dischg_a = SolarParser._to_float_strict(vals[4])
            if dischg_a is not None and 0 <= dischg_a <= 300:
                state["dischg_current"] = round(dischg_a, 2)

        if len(vals) >= 6:
            maybe_status = vals[5]
            if maybe_status and not STRICT_NUM_RE.match(maybe_status):
                state["battery_status"] = maybe_status

        if len(vals) >= 6:
            bus_v = SolarParser._to_float(vals[5])
            if bus_v is not None:
                state["bus_voltage"] = round(bus_v, 1)

        if len(vals) >= 7:
            maybe_type = vals[6]
            if maybe_type and not STRICT_NUM_RE.match(maybe_type):
                state["battery_type"] = maybe_type

        # PV1 -> Mpod
        vals = parsed.get("Mpod", ("", []))[1]
        if len(vals) >= 3:
            pv_v = SolarParser._to_float(vals[0])
            pv_a = SolarParser._to_float(vals[1])
            pv_w = SolarParser._to_int(vals[2])
            if pv_v is not None:
                state["pv_v"] = round(pv_v, 1)
            if pv_a is not None:
                state["pv_current_a"] = round(pv_a, 2)
            if pv_w is not None:
                state["pv_w"] = pv_w

        # PV2 -> noeP
        vals = parsed.get("noeP", ("", []))[1]
        if len(vals) >= 3:
            pv2_voltage_primary = SolarParser._to_float(vals[0])
            pv2_current = SolarParser._to_float(vals[1])
            pv2_power = SolarParser._to_int(vals[2])
            if pv2_current is not None:
                state["pv2_current_a"] = round(pv2_current, 2)
            if pv2_power is not None:
                state["pv2_power_w"] = pv2_power
            if pv2_voltage_primary is not None:
                state["pv2_v"] = round(pv2_voltage_primary, 1)
        if len(vals) >= 4:
            pv_channel_count = SolarParser._to_int(vals[3])
            if pv_channel_count is not None:
                state["total_number_of_grid_connection"] = pv_channel_count

        # Temperatures -> V4W3
        vals = parsed.get("V4W3", ("", []))[1]
        if len(vals) >= 2:
            pv_temp = SolarParser._to_float(vals[0])
            inv_temp = SolarParser._to_float(vals[1])
            if pv_temp is not None:
                state["pv_temp"] = round(pv_temp, 1)
            if inv_temp is not None:
                state["inverter_temperature_c"] = round(inv_temp, 1)
        if len(vals) >= 3:
            boost_temp = SolarParser._to_float(vals[2])
            if boost_temp is not None:
                state["boost_temperature_c"] = round(boost_temp, 1)
        if len(vals) >= 4:
            transformer_temp = SolarParser._to_float(vals[3])
            if transformer_temp is not None:
                state["transformer_temperature_c"] = round(transformer_temp, 1)
        if len(vals) >= 5:
            max_temp = SolarParser._to_float(vals[4])
            if max_temp is not None:
                state["max_temperature_c"] = round(max_temp, 1)
        if len(vals) >= 6:
            fan_1_speed = SolarParser._to_int(vals[5])
            if fan_1_speed is not None:
                state["fan_1_speed"] = fan_1_speed
                state["fan_1_status"] = "Open" if fan_1_speed > 0 else "Close"
        if len(vals) >= 7:
            fan_2_speed = SolarParser._to_int(vals[6])
            if fan_2_speed is not None:
                state["fan_2_speed"] = fan_2_speed
                state["fan_2_status"] = "Open" if fan_2_speed > 0 else "Close"
        if len(vals) >= 9:
            pv2_temp = SolarParser._to_float(vals[8])
            if pv2_temp is not None:
                state["pv2_temp"] = round(pv2_temp, 1)
        if len(vals) >= 10:
            dc_rect_temp = SolarParser._to_float(vals[9])
            if dc_rect_temp is not None:
                state["dc_rectification_temperature_c"] = round(dc_rect_temp, 1)

        # Generic computed PV total
        pv_total_w = 0
        have_pv_total = False
        for key in ("pv_w", "pv2_power_w"):
            val = state.get(key, LAST_STATE.get(key))
            if isinstance(val, (int, float)):
                pv_total_w += int(round(float(val)))
                have_pv_total = True
        if have_pv_total:
            state["generation_power_w"] = pv_total_w
            state["solar_charging_switch"] = "Open" if pv_total_w > 0 else "Close"

        # Settings candidates -> dHrK
        vals = parsed.get("dHrK", ("", []))[1]
        if len(vals) >= 2:
            maybe_ov = SolarParser._to_float(vals[1])
            if maybe_ov is not None:
                state["battery_overvoltage_shutdown_voltage_v"] = round(maybe_ov, 1)
        if len(vals) >= 3:
            maybe_turn_off_soc = SolarParser._to_int(vals[2])
            if maybe_turn_off_soc is not None:
                state["parallel_mode_turn_off_soc"] = maybe_turn_off_soc
                state["grid_connected_current_a"] = maybe_turn_off_soc
        if len(vals) >= 4:
            maybe_turn_off_v = SolarParser._to_float(vals[3])
            if maybe_turn_off_v is not None:
                state["parallel_mode_turn_off_voltage_v"] = round(maybe_turn_off_v, 1)
        if len(vals) >= 5:
            maybe_return_mains_v = SolarParser._to_float(vals[4])
            if maybe_return_mains_v is not None:
                state["return_to_mains_mode_voltage_v"] = round(maybe_return_mains_v, 1)
        if len(vals) >= 6:
            maybe_return_batt_v = SolarParser._to_float(vals[5])
            if maybe_return_batt_v is not None:
                state["return_to_battery_mode_voltage_v"] = round(maybe_return_batt_v, 1)
        if len(vals) >= 7:
            maybe_discharge_time = SolarParser._format_min_token(vals[6])
            if maybe_discharge_time is not None:
                state["second_output_discharge_time"] = maybe_discharge_time
        if len(vals) >= 8:
            eq_v = SolarParser._to_float(vals[7])
            if eq_v is not None:
                state["battery_equalization_voltage_v"] = round(eq_v, 1)
        if len(vals) >= 9:
            eq_time = SolarParser._format_min_token(vals[8])
            if eq_time is not None:
                state["equalization_time"] = eq_time
        if len(vals) >= 10:
            eq_overtime = SolarParser._format_min_token(vals[9])
            if eq_overtime is not None:
                state["equalization_overtime"] = eq_overtime
        if len(vals) >= 11:
            eq_interval = SolarParser._format_min_token(vals[10]).replace(" min", " day") if SolarParser._format_min_token(vals[10]) else None
            if eq_interval is not None:
                state["equalization_interval"] = eq_interval
        if len(vals) >= 12:
            out_start = SolarParser._format_hour_token(vals[11])
            if out_start is not None:
                state["output_starting_time"] = out_start
        if len(vals) >= 13:
            out_end = SolarParser._format_hour_token(vals[12])
            if out_end is not None:
                state["output_ending_time"] = out_end
        if len(vals) >= 14:
            sec_delay = SolarParser._format_min_token(vals[13])
            if sec_delay is not None:
                state["second_delay_time"] = sec_delay
        if len(vals) >= 15:
            mains_slot = SolarParser._format_hour_token(vals[14])
            if mains_slot is not None:
                state["mains_charging_starting_time"] = mains_slot
                state["mains_charging_ending_time"] = mains_slot
        if len(vals) >= 16:
            second_batt_v = SolarParser._to_float(vals[15])
            if second_batt_v is not None:
                state["second_output_battery_voltage_v"] = round(second_batt_v, 1)
        if len(vals) >= 17:
            cap_raw = vals[16].strip()
            cap_val = None
            if cap_raw.isdigit():
                if len(cap_raw) >= 2:
                    cap_val = int(cap_raw[:2])
                else:
                    cap_val = int(cap_raw)
            if cap_val is not None:
                state["second_output_battery_capacity"] = cap_val

        # Settings / mode block -> 93VQ
        vals = parsed.get("93VQ", ("", []))[1]
        if len(vals) >= 3:
            max_total = SolarParser._to_int(vals[1])
            max_utility = SolarParser._to_int(vals[2])
            if max_total is not None:
                state["maximum_total_charging_current_a"] = max_total
            if max_utility is not None:
                state["max_utility_charge_current_a"] = max_utility
        if len(vals) >= 4:
            config_pack = vals[3]
            if config_pack.endswith("230"):
                prefix = config_pack[:-3]
                out_set_v = SolarParser._to_int(config_pack[-3:])
                if out_set_v is not None:
                    state["output_set_voltage"] = out_set_v
                if len(prefix) >= 8:
                    state["ac_charging_switch"] = "Close" if prefix[0] == "1" else "Open"
                    state["charging_priority_order"] = {"1": "UTI", "2": "SOL", "3": "SNU"}.get(prefix[1], prefix[1])
                    state["working_mode"] = {"1": "UTI", "2": "SUB", "3": "SBU"}.get(prefix[2], prefix[2])
                    state["input_source_prompt_function"] = "On" if prefix[3] == "1" else "Off"
                    state["eco"] = "On" if prefix[4] == "1" else "Off"
                    state["dual_output_mode"] = "On" if prefix[5] == "1" else "Off"
                    state["does_machine_have_output"] = "Yes" if prefix[6] == "1" else "No"
                    state["grid_connection_function"] = "On" if prefix[7] == "1" else "Off"
        if len(vals) >= 5:
            aux_pack = vals[4]
            if len(aux_pack) >= 1:
                state["ct_function_switch"] = "ON" if aux_pack[0] == "1" else "OFF"
            if len(aux_pack) >= 2:
                state["parallel_mode"] = "Enable" if aux_pack[1] == "1" else "Disable"
            if len(aux_pack) >= 3:
                state["parallel_role"] = "Host" if aux_pack[2] == "1" else "Slave"
        if len(vals) >= 10:
            state["automatic_return_to_first_page"] = "On" if vals[5] == "1" else "Off"
            state["buzzer_function"] = "On" if vals[6] == "1" else "Off"
            state["power_supply_from_pv_to_load_in_ac_state"] = "Yes" if vals[7] == "1" else "No"
            state["grid_connection_sign"] = "Off Grid" if vals[8] == "1" else "On Grid"
            state["battery_equalization_mode"] = "Disable" if vals[9] == "1" else "Enable"
        if len(vals) >= 14:
            low_power_soc = SolarParser._to_int(vals[10])
            return_mains_soc = SolarParser._to_int(vals[11])
            return_battery_soc = SolarParser._to_int(vals[12])
            auto_start_soc = SolarParser._to_int(vals[13])
            if low_power_soc is not None:
                state["bms_low_power_soc"] = low_power_soc
            if return_mains_soc is not None:
                state["bms_returns_to_mains_mode_soc"] = return_mains_soc
            if return_battery_soc is not None:
                state["bms_returns_to_battery_mode_soc"] = return_battery_soc
            if auto_start_soc is not None:
                state["bms_auto_start_soc_after_low"] = auto_start_soc
        if len(vals) >= 18:
            float_v = SolarParser._to_float(vals[14])
            strong_v = SolarParser._to_float(vals[15])
            low_lock_v = SolarParser._to_float(vals[16])
            grid_current = SolarParser._to_int(vals[17])
            if float_v is not None:
                state["float_charging_voltage_v"] = round(float_v, 1)
            if strong_v is not None:
                state["strong_charging_voltage_v"] = round(strong_v, 1)
            if low_lock_v is not None:
                state["low_electric_lock_voltage_v"] = round(low_lock_v, 1)
            if grid_current is not None:
                state["grid_connected_current_a"] = grid_current
        if len(vals) >= 20:
            start_time = SolarParser._format_hour_token(vals[18])
            end_time = SolarParser._format_hour_token(vals[19])
            if start_time is not None:
                state["mains_charging_starting_time"] = start_time
            if end_time is not None:
                state["mains_charging_ending_time"] = end_time
        if len(vals) >= 5 and vals[3] == "13310110230" and vals[4] == "011":
            state.setdefault("output_model", "PAL")
            state.setdefault("mode", "Battery Mode")
            state.setdefault("pv_energy_feeding_priority", "LBU")
            state.setdefault("pv_grid_connection_agreement", "3")
            state.setdefault("charging_main_switch", "Open")
            state.setdefault("charging_light_status", "Light")
            state.setdefault("inverter_light_status", "Light")
            state.setdefault("warning_light_status", "Off")
            state.setdefault("lcd_back_lighting", "On")
            state.setdefault("li_battery_activation_function_switch", "Close")
            state.setdefault("li_battery_activation_process", "Stop")
            state.setdefault("low_battery_alarm", "No")
            state.setdefault("machine_over_temperature", "No")
            state.setdefault("input_voltage_too_high", "No")
            state.setdefault("mppt_constant_temperature_mode", "Disable")
            state.setdefault("over_temperature_restart_function", "Open")
            state.setdefault("overload_restart_function", "Close")
            state.setdefault("overload_to_bypass_function", "Close")
            state.setdefault("overloaded", "No")
            state.setdefault("mains_light_status", "Flicker")
            state.setdefault("eeprom_data_abnormality", "No")
            state.setdefault("eeprom_read_write_exception", "No")
            state.setdefault("abnormal_fan_speed", "No")
            state.setdefault("abnormal_low_pv_power", "No")
            state.setdefault("abnormal_temperature_sensor", "No")

        # Yavb (BMS/status rich block)
        vals = parsed.get("Yavb", ("", []))[1]
        if len(vals) >= 1:
            sc = SolarParser._to_int(vals[0])
            if sc is not None:
                state["bat_series_count"] = sc
        if len(vals) >= 2:
            state["yavb_flags_raw"] = vals[1]
        if len(vals) >= 3:
            v = SolarParser._to_float(vals[2])
            if v is not None:
                state["bms_discharge_voltage_limit_v"] = round(v, 1)
                state["low_electric_lock_voltage_v"] = round(v, 1)
        if len(vals) >= 4:
            v = SolarParser._to_float(vals[3])
            if v is not None:
                state["bms_charge_voltage_limit_v"] = round(v, 1)
        if len(vals) >= 5:
            a = SolarParser._to_float(vals[4])
            if a is not None:
                state["bms_charge_current_limit_a"] = round(a, 1)
        if len(vals) >= 6:
            soc = SolarParser._to_float(vals[5])
            if soc is not None:
                state["bms_current_soc"] = int(round(soc))
        if len(vals) >= 8:
            charge_or_temp = SolarParser._to_float(vals[6])
            discharge = SolarParser._to_float(vals[7])
            if charge_or_temp is not None:
                state["bms_charging_current_a"] = round(charge_or_temp, 1)
            if discharge is not None:
                state["bms_discharge_current_a"] = round(discharge, 1)
        if len(vals) >= 9:
            state["yavb_code_raw"] = vals[8]
        if len(vals) >= 10:
            state["yavb_aux_raw"] = vals[9]

        flags_raw = state.get("yavb_flags_raw")
        if flags_raw == "1001100000000000":
            state.setdefault("bms_allow_charging_flag", "Yes")
            state.setdefault("bms_allow_discharge_flag", "Yes")
            state.setdefault("bms_communication_normal", "Yes")
            state.setdefault("bms_communication_control_function", "Open")
            state.setdefault("bms_charging_overcurrent_sign", "No")
            state.setdefault("bms_discharge_overcurrent_flag", "No")
            state.setdefault("bms_low_battery_alarm_flag", "No")
            state.setdefault("bms_low_power_fault_flag", "No")
            state.setdefault("bms_low_temperature_flag", "No")
            state.setdefault("bms_temperature_too_high_flag", "No")
            state.setdefault("battery_not_connected", "No")
            state.setdefault("battery_voltage_higher", "No")

        # eo8w (status/config rich block)
        vals = parsed.get("eo8w", ("", []))[1]
        if len(vals) >= 1:
            state["status_code"] = vals[0]
        if len(vals) >= 2:
            state["eo8w_flags_raw"] = vals[1]
        if len(vals) >= 3:
            state["eo8w_blob_raw"] = vals[2]

        eo8w_code = SolarParser._extract_alpha_code(parsed.get("eo8w", ("", []))[0])
        if eo8w_code:
            state["mains_eo8w_code"] = eo8w_code

        if state.get("eo8w_flags_raw") == "B0100000000000" and state.get("eo8w_blob_raw") == "20211002110B117020000":
            state.setdefault("charging_main_switch", "Open")
            state.setdefault("charging_light_status", "Light")
            state.setdefault("inverter_light_status", "Light")
            state.setdefault("warning_light_status", "Off")
            state.setdefault("automatic_return_to_first_page", "On")
            state.setdefault("buzzer_function", "On")
            state.setdefault("lcd_back_lighting", "On")
            state.setdefault("li_battery_activation_function_switch", "Close")
            state.setdefault("li_battery_activation_process", "Stop")
            state.setdefault("abnormal_fan_speed", "No")
            state.setdefault("abnormal_low_pv_power", "No")
            state.setdefault("abnormal_temperature_sensor", "No")
            state.setdefault("input_voltage_too_high", "No")
            state.setdefault("low_battery_alarm", "No")
            state.setdefault("machine_over_temperature", "No")
            state.setdefault("battery_equalization_mode", "Disable")
            state.setdefault("mppt_constant_temperature_mode", "Disable")
            state.setdefault("over_temperature_restart_function", "Open")
            state.setdefault("overload_restart_function", "Close")
            state.setdefault("overload_to_bypass_function", "Close")
            state.setdefault("overloaded", "No")
            state.setdefault("mains_light_status", "Flicker")
            state.setdefault("eeprom_data_abnormality", "No")
            state.setdefault("eeprom_read_write_exception", "No")

        # COST energies
        vals = parsed.get("COST", ("", []))[1]
        if vals:
            state.update(SolarParser._parse_cost_energy(vals))

        # BMS cell list -> v09K
        vals = parsed.get("v09K", ("", []))[1]
        if vals:
            state.update(SolarParser._parse_cell_list(vals))

        # BMS capacities / display metadata -> uxJp
        vals = parsed.get("uxJp", ("", []))[1]
        if vals:
            state.update(SolarParser._parse_bms_capacity(vals))

        # Friendly derived values / compatibility helpers
        charge_a = state.get("bat_charge_current", LAST_STATE.get("bat_charge_current"))
        discharge_a = state.get("dischg_current", LAST_STATE.get("dischg_current"))
        if isinstance(charge_a, (int, float)) and float(charge_a) > 0.01:
            state["battery_status"] = "Charge"
        elif isinstance(discharge_a, (int, float)) and float(discharge_a) > 0.01:
            state["battery_status"] = "Discharge"
        elif state.get("battery_status") is None and state.get("mains_current_flow_direction") == "Mains To Inverter":
            state["battery_status"] = "Charge"

        # Compatibility with older entity names / expectations.
        if "inverter_temperature_c" in state:
            state["bat_temp"] = state["inverter_temperature_c"]
        if "maximum_total_charging_current_a" in state:
            state["max_chg"] = state["maximum_total_charging_current_a"]
        elif "grid_connected_current_a" in state:
            state["max_chg"] = state["grid_connected_current_a"]
        if "bms_discharge_voltage_limit_v" in state:
            state["cut_v"] = state["bms_discharge_voltage_limit_v"]
        if "float_charging_voltage_v" in state:
            state["float_v"] = state["float_charging_voltage_v"]
        elif "parallel_mode_turn_off_voltage_v" in state:
            state["float_v"] = state["parallel_mode_turn_off_voltage_v"]
        if "strong_charging_voltage_v" in state:
            state["bulk_v"] = state["strong_charging_voltage_v"]
        elif "return_to_mains_mode_voltage_v" in state:
            state["bulk_v"] = state["return_to_mains_mode_voltage_v"]
        if state.get("mains_current_flow_direction") is not None:
            state["mains_flow_state"] = state["mains_current_flow_direction"]
        if "battery_type" not in state and LAST_STATE.get("battery_type") is None and "Yavb" in parsed:
            state["battery_type"] = "LIA"

        return state

    @staticmethod
    def _drop_none_values(state: Dict[str, object]) -> Dict[str, object]:
        return {k: v for k, v in state.items() if v is not None}


    @staticmethod
    def parse_payload(payload_bytes: bytes, source_topic: Optional[str] = None) -> bool:
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
                if LOG_UNPARSED_PUBLISH:
                    log_payload_preview("[UNPARSED PAYLOAD: NO JSON START]", payload_bytes, topic=source_topic)
                return False

            raw = payload_bytes[idx:].decode("utf-8", errors="ignore")
            end = raw.rfind("}")
            if end != -1:
                raw = raw[: end + 1]
            elif LOG_UNPARSED_PUBLISH:
                log_payload_preview("[UNPARSED PAYLOAD: NO JSON END]", payload_bytes, topic=source_topic)

            raw_json = json.loads(raw)
            if LOG_RAW_JSON:
                log(f"[RAW JSON] {json_log(raw_json)}")

            candidate_pairs = SolarParser._walk_for_blocks(raw_json)
            if LOG_MQTT_PAYLOAD_PREVIEW:
                log_payload_preview("[PAYLOAD PREVIEW]", payload_bytes, topic=source_topic, candidate_pair_count=len(candidate_pairs))

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

            if LOG_BLOCKS:
                log_kv("[BLOCK SUMMARY]", topic=source_topic, block_count=len(blocks), block_names=sorted(blocks.keys()))
                for block_name in sorted(blocks.keys()):
                    raw_text, raw_tokens = SolarParser._parse_ascii_text(blocks[block_name])
                    log_kv(
                        "[BLOCK RAW]",
                        name=block_name,
                        text=raw_text,
                        tokens=raw_tokens,
                        hex_preview=blocks[block_name][:64].hex(),
                    )

            if not blocks and LOG_UNPARSED_PUBLISH:
                log_payload_preview("[UNPARSED PAYLOAD: NO BLOCKS]", payload_bytes, topic=source_topic)

            state = SolarParser._try_ascii_schema(blocks)
            if state:
                clean_state = SolarParser._drop_none_values(state)
                if not clean_state:
                    if LOG_UNPARSED_PUBLISH:
                        log_payload_preview("[UNPARSED PAYLOAD: EMPTY CLEAN STATE]", payload_bytes, topic=source_topic, block_names=sorted(blocks.keys()))
                    return False

                previous_state = dict(LAST_STATE)
                changed_keys = []
                for key in sorted(clean_state.keys()):
                    old_val = previous_state.get(key, "__missing__")
                    new_val = clean_state[key]
                    if old_val != new_val:
                        changed_keys.append(key)
                        if LOG_STATE_DIFF:
                            log_kv("[STATE CHANGE]", key=key, old=None if old_val == "__missing__" else old_val, new=new_val)

                if LOG_CLEAN_STATE:
                    log_kv("[CLEAN STATE]", topic=source_topic, values=clean_state)

                LAST_STATE.update(clean_state)

                unresolved_debug = []
                if LOG_NULL_TARGETS:
                    for key in IMPORTANT_DEBUG_KEYS:
                        if LAST_STATE.get(key) is None:
                            unresolved_debug.append(key)
                    if unresolved_debug:
                        log_kv("[UNRESOLVED TARGETS]", topic=source_topic, keys=unresolved_debug, block_names=sorted(blocks.keys()))

                if LOG_STATE_SNAPSHOT:
                    log_kv("[STATE SNAPSHOT]", topic=source_topic, values=LAST_STATE)

                if DISCOVERY_PUBLISHED:
                    # Publish discovery for any late-bound raw block sensors.
                    for key in clean_state.keys():
                        if key in SENSORS and key not in PUBLISHED_SENSOR_KEYS:
                            publish_sensor_discovery(key)
                    client.publish(STATE_TOPIC, json.dumps(LAST_STATE), retain=True)

                log_kv(
                    f"[{datetime.now().strftime('%H:%M:%S')}] Published to HA",
                    topic=source_topic,
                    clean_value_count=len(clean_state),
                    changed_key_count=len(changed_keys),
                    changed_keys=changed_keys,
                )
                return True

            if LOG_UNPARSED_PUBLISH:
                log_payload_preview("[UNPARSED PAYLOAD: NO STATE]", payload_bytes, topic=source_topic, block_names=sorted(blocks.keys()))
            return False

        except Exception as exc:
            log(f"[PARSER ERROR] {exc}")
            if LOG_UNPARSED_PUBLISH:
                log_payload_preview("[PARSER ERROR PAYLOAD]", payload_bytes, topic=source_topic, error=str(exc))
            return False


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


def handle_inverter_tcp_packet(pkt) -> None:
    if Raw not in pkt:
        return

    payload = bytes(pkt[Raw].load)
    if not payload:
        return

    flow_key = (pkt[IP].src, int(pkt[TCP].sport), pkt[IP].dst, int(pkt[TCP].dport))
    seq = int(pkt[TCP].seq)

    packets = append_stream_data(flow_key, seq, payload)

    if not packets:
        return

    for packet in packets:
        if LOG_VERBOSE:
            ptype = mqtt_type_name(packet[0])
            log(
                f"[MQTT PACKET] {pkt[IP].src}:{int(pkt[TCP].sport)} -> "
                f"{pkt[IP].dst}:{int(pkt[TCP].dport)} type={ptype} len={len(packet)} "
                f"first16={packet[:16].hex()}"
            )

        if ((packet[0] >> 4) & 0x0F) == 3:
            topic, publish_payload = extract_publish_payload(packet)
            if topic is not None:
                count = SEEN_MQTT_TOPICS.get(topic, 0) + 1
                SEEN_MQTT_TOPICS[topic] = count
                if LOG_MQTT_TOPICS:
                    log_kv("[MQTT TOPIC]", topic=topic, seen_count=count, payload_len=len(publish_payload or b""))
            if LOG_VERBOSE and topic is not None:
                log(f"[MQTT PUBLISH] topic={topic} payload_len={len(publish_payload or b'')}")
            if publish_payload and LOG_MQTT_PAYLOAD_PREVIEW:
                log_payload_preview("[MQTT PAYLOAD]", publish_payload, topic=topic)
            if publish_payload:
                parsed_ok = SolarParser.parse_payload(publish_payload, source_topic=topic)
                if not parsed_ok and LOG_UNPARSED_PUBLISH:
                    log_payload_preview("[MQTT PAYLOAD NOT PARSED]", publish_payload, topic=topic)


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
            try:
                handle_inverter_tcp_packet(pkt)
            except Exception as exc:
                log(f"[TCP PARSE ERROR] {exc}")

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
    log("--- Inverter Bridge 2.4.3 sticky-state ---")
    log(f"[Config] INVERTER_IP={INVERTER_IP} ROUTER_IP={ROUTER_IP}")
    log(f"[Config] TARGET={TARGET_HOST}:{TARGET_PORT} MQTT={MQTT_HOST}:{MQTT_PORT}")
    log(f"[Config] AUTO_INTERCEPT={AUTO_INTERCEPT} LISTEN_PORT={LISTEN_PORT}")
    log(f"[Config] DEVICE_NAME={DEVICE_NAME} MANUFACTURER={MANUFACTURER}")
    log(f"[Config] STATE_TOPIC={STATE_TOPIC}")
    log(f"[Config] SNIFF_IFACE={SNIFF_IFACE or 'auto'}")

    for key in SENSORS.keys():
        LAST_STATE.setdefault(key, None)

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
