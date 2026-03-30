import base64
import json
import os
import re
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from . import state as _shared_state
from .loggers import log, log_kv, json_log, log_payload_preview
from .sensors import SENSORS
from .config import (
    STATE_CACHE_FILE, STREAM_STALE_SECONDS, LOG_STREAM_EVENTS, MAX_STREAM_BUFFER,
    MAX_MQTT_PACKET, PRINTABLE_ASCII_RE, STRICT_NUM_RE, SLUG_RE,
    LOG_VERBOSE, LOG_BLOCKS, LOG_STATE_DIFF, LOG_STATE_SNAPSHOT,
    LOG_RAW_JSON, LOG_CLEAN_STATE, LOG_MQTT_TOPICS,
    LOG_MQTT_PAYLOAD_PREVIEW, LOG_UNPARSED_PUBLISH, LOG_NULL_TARGETS,
    UPDATE_INTERVAL_SEC, INVERTER_COUNT,
    BATTERY_COUNT, BATTERY_CAPACITY_PER_BATTERY_AH,
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


def mqtt_type_name(first_byte: int) -> str:
    """Return a human-readable MQTT packet type from the first byte."""
    ptype = (first_byte >> 4) & 0x0F
    return MQTT_PACKET_TYPES.get(ptype, f"UNKNOWN({ptype})")



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
SEEN_MQTT_TOPICS: Dict[str, int] = {}
IMPORTANT_DEBUG_KEYS = ("bms_avg_temp_c", "mains_current_flow_direction")
LAST_PUBLISH_TS: float = 0.0
LAST_ENERGY_TS: Optional[float] = None
_FLOW_EVICT_COUNTER: int = 0
_FLOW_EVICT_INTERVAL: int = 200  # Prune stale TCP flows every N state lookups.


def _evict_stale_flows() -> None:
    """Remove FLOW_STATES entries inactive for longer than STREAM_STALE_SECONDS."""
    now = time.time()
    stale = [k for k, v in FLOW_STATES.items() if now - v.last_seen > STREAM_STALE_SECONDS]
    for k in stale:
        del FLOW_STATES[k]
    if stale and LOG_STREAM_EVENTS:
        log_kv("[STREAM EVICT]", removed=len(stale), remaining=len(FLOW_STATES))


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
    global _FLOW_EVICT_COUNTER
    state = FLOW_STATES.get(flow_key)
    now = time.time()

    _FLOW_EVICT_COUNTER += 1
    if _FLOW_EVICT_COUNTER >= _FLOW_EVICT_INTERVAL:
        _FLOW_EVICT_COUNTER = 0
        _evict_stale_flows()

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


def _get_mqtt_publish():
    """Minimal deferred import — only for MQTT publish callables not available in state.py."""
    from . import mqtt
    return mqtt.publish_sensor_discovery, mqtt.publish_grouped_state


def _log_debug_block(block_name: str, raw_text: str) -> None:
    """Log raw debug block data instead of creating HA entities."""
    log(f"[DEBUG BLOCK] {block_name}: {raw_text[:250]}")


class SolarParser:
    @staticmethod
    def _to_float_or_none(value: object) -> Optional[float]:
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value.strip())
            except ValueError:
                return None
        return None

    @staticmethod
    def _power_to_kwh_delta(power_w: float, dt_seconds: float) -> float:
        if power_w <= 0 or dt_seconds <= 0:
            return 0.0
        return (power_w * dt_seconds) / 3_600_000.0

    @staticmethod
    def _energy_dt_seconds(now_ts: float) -> float:
        global LAST_ENERGY_TS
        if LAST_ENERGY_TS is None:
            LAST_ENERGY_TS = now_ts
            return 0.0

        dt_seconds = max(0.0, now_ts - LAST_ENERGY_TS)
        LAST_ENERGY_TS = now_ts
        # Bound dt so stale timestamps do not create unrealistic jumps.
        max_dt_seconds = max(float(UPDATE_INTERVAL_SEC) * 6.0, 60.0)
        return min(dt_seconds, max_dt_seconds)

    @staticmethod
    def _apply_energy_dashboard_calculations(state: Dict[str, object], now_ts: Optional[float] = None) -> None:
        factor = max(0.0, float(INVERTER_COUNT))
        if factor <= 0:
            factor = 1.0

        bat_v = SolarParser._to_float_or_none(state.get("bat_v", _shared_state.LAST_STATE.get("bat_v")))

        charge_a = SolarParser._to_float_or_none(
            state.get("bms_charging_current_a", _shared_state.LAST_STATE.get("bms_charging_current_a"))
        )
        if charge_a is None:
            charge_a = SolarParser._to_float_or_none(
                state.get("bat_charge_current", _shared_state.LAST_STATE.get("bat_charge_current"))
            )

        discharge_a = SolarParser._to_float_or_none(
            state.get("bms_discharge_current_a", _shared_state.LAST_STATE.get("bms_discharge_current_a"))
        )
        if discharge_a is None:
            discharge_a = SolarParser._to_float_or_none(
                state.get("dischg_current", _shared_state.LAST_STATE.get("dischg_current"))
            )

        charge_power_w = 0.0
        discharge_power_w = 0.0
        if bat_v is not None and bat_v >= 0:
            if charge_a is not None and charge_a > 0:
                charge_power_w = bat_v * charge_a
            if discharge_a is not None and discharge_a > 0:
                discharge_power_w = bat_v * discharge_a

        mains_signed_w = SolarParser._to_float_or_none(
            state.get("mains_wdrr_value", _shared_state.LAST_STATE.get("mains_wdrr_value"))
        )
        grid_import_power_w = 0.0
        if mains_signed_w is not None and mains_signed_w > 0:
            grid_import_power_w = mains_signed_w * factor

        state["c_battery_charge_power_w"] = int(round(charge_power_w))
        state["c_battery_discharge_power_w"] = int(round(discharge_power_w))
        state["c_grid_import_power_w"] = int(round(grid_import_power_w))

        now = now_ts if now_ts is not None else time.time()
        dt_seconds = SolarParser._energy_dt_seconds(now)

        prev_charge_kwh = SolarParser._to_float_or_none(
            _shared_state.LAST_STATE.get("c_battery_charge_energy_kwh")
        ) or 0.0
        prev_discharge_kwh = SolarParser._to_float_or_none(
            _shared_state.LAST_STATE.get("c_battery_discharge_energy_kwh")
        ) or 0.0
        prev_grid_import_kwh = SolarParser._to_float_or_none(
            _shared_state.LAST_STATE.get("c_grid_import_energy_kwh")
        ) or 0.0

        charge_kwh = prev_charge_kwh + SolarParser._power_to_kwh_delta(charge_power_w, dt_seconds)
        discharge_kwh = prev_discharge_kwh + SolarParser._power_to_kwh_delta(discharge_power_w, dt_seconds)
        grid_import_kwh = prev_grid_import_kwh + SolarParser._power_to_kwh_delta(grid_import_power_w, dt_seconds)

        state["c_battery_charge_energy_kwh"] = round(max(prev_charge_kwh, charge_kwh), 6)
        state["c_battery_discharge_energy_kwh"] = round(max(prev_discharge_kwh, discharge_kwh), 6)
        state["c_grid_import_energy_kwh"] = round(max(prev_grid_import_kwh, grid_import_kwh), 6)

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
    def _scale_main_power(raw_value: int) -> int:
        factor = float(INVERTER_COUNT)
        return int(round(float(raw_value) * factor))

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
            _log_debug_block(block_name, raw_text)

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
                state["c_load_w"] = SolarParser._scale_main_power(out_w)

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
                state["c_mains_power_w"] = SolarParser._scale_main_power(abs(mains_signed))

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
            val = state.get(key, _shared_state.LAST_STATE.get(key))
            if isinstance(val, (int, float)):
                pv_total_w += int(round(float(val)))
                have_pv_total = True
        if have_pv_total:
            state["generation_power_w"] = pv_total_w
            state["c_generation_power_w"] = SolarParser._scale_main_power(pv_total_w)
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
        charge_a = state.get("bat_charge_current", _shared_state.LAST_STATE.get("bat_charge_current"))
        discharge_a = state.get("dischg_current", _shared_state.LAST_STATE.get("dischg_current"))
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
        if "battery_type" not in state and _shared_state.LAST_STATE.get("battery_type") is None and "Yavb" in parsed:
            state["battery_type"] = "LIA"

        if BATTERY_CAPACITY_PER_BATTERY_AH > 0:
            total_capacity = round(BATTERY_COUNT * BATTERY_CAPACITY_PER_BATTERY_AH, 1)
            state["c_bms_total_capacity_ah"] = total_capacity

        SolarParser._apply_energy_dashboard_calculations(state)

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

                publish_sensor_discovery, publish_grouped_state = _get_mqtt_publish()

                previous_state = dict(_shared_state.LAST_STATE)
                changed_keys = []
                changed_data = []
                for key in sorted(clean_state.keys()):
                    old_val = previous_state.get(key, "__missing__")
                    new_val = clean_state[key]
                    if old_val != new_val:
                        changed_keys.append(key)
                        changed_data.append(f"{key}={new_val}")
                        if LOG_STATE_DIFF:
                            log_kv("[STATE CHANGE]", key=key, old=None if old_val == "__missing__" else old_val, new=new_val)

                if LOG_CLEAN_STATE:
                    log_kv("[CLEAN STATE]", topic=source_topic, values=clean_state)

                _shared_state.LAST_STATE.update(clean_state)

                # Persist state cache to survive container restarts
                try:
                    os.makedirs(os.path.dirname(STATE_CACHE_FILE), exist_ok=True)
                    with open(STATE_CACHE_FILE, "w") as _sf:
                        json.dump(dict(_shared_state.LAST_STATE), _sf)
                except Exception as _cache_exc:
                    log(f"[CACHE WRITE ERROR] {_cache_exc}")

                unresolved_debug = []
                if LOG_NULL_TARGETS:
                    for key in IMPORTANT_DEBUG_KEYS:
                        if _shared_state.LAST_STATE.get(key) is None:
                            unresolved_debug.append(key)
                    if unresolved_debug:
                        log_kv("[UNRESOLVED TARGETS]", topic=source_topic, keys=unresolved_debug, block_names=sorted(blocks.keys()))

                if LOG_STATE_SNAPSHOT:
                    log_kv("[STATE SNAPSHOT]", topic=source_topic, values=_shared_state.LAST_STATE)

                if _shared_state.DISCOVERY_PUBLISHED:
                    # Publish discovery for any late-bound raw block sensors.
                    for key in clean_state.keys():
                        if key in SENSORS and key not in _shared_state.PUBLISHED_SENSOR_KEYS:
                            publish_sensor_discovery(key)

                    global LAST_PUBLISH_TS
                    now = time.time()
                    if len(changed_keys) > 0 or (now - LAST_PUBLISH_TS) >= UPDATE_INTERVAL_SEC:
                        publish_grouped_state(_shared_state.LAST_STATE)
                        LAST_PUBLISH_TS = now

                log_kv(
                    f"[{datetime.now().strftime('%H:%M:%S')}] Published to HA",
                    topic=source_topic,
                    clean_value_count=len(clean_state),
                    changed_key_count=len(changed_keys),
                    changed_values=changed_data,
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


