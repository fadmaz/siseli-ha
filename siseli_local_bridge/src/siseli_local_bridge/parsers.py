import base64
import json
import re
import time
from datetime import datetime
from typing import Dict, FrozenSet, List, Optional, Tuple

from . import state as _shared_state
from .loggers import log, log_kv, json_log, log_payload_preview, log_error_always, hex_preview
from .sensors import SENSORS
from .config import (
    STATE_CACHE_FILE, STREAM_STALE_SECONDS, LOG_STREAM_EVENTS, MAX_STREAM_BUFFER,
    MAX_MQTT_PACKET, PRINTABLE_ASCII_RE, STRICT_NUM_RE,
    LOG_BLOCKS, LOG_STATE_DIFF, LOG_STATE_SNAPSHOT,
    LOG_RAW_JSON, LOG_CLEAN_STATE, LOG_MQTT_PAYLOAD_PREVIEW, LOG_UNPARSED_PUBLISH, LOG_NULL_TARGETS,
    UPDATE_INTERVAL_SEC, EXPIRE_AFTER_SEC, STATE_CACHE_INTERVAL_SEC, INVERTER_COUNT,
    MAX_PENDING_SEGMENTS, MAX_PENDING_BYTES, STREAM_GAP_TIMEOUT_SEC,
    BATTERY_COUNT, BATTERY_CAPACITY_PER_BATTERY_AH,
    ENERGY_MAX_DT_SEC, TELEMETRY_TIMEOUT_CEILING_SEC,
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



SEQ_MOD = 1 << 32
SEQ_HALF = 1 << 31


def seq_diff(a: int, b: int) -> int:
    """Distance from b to a in TCP sequence space."""
    return (a - b) % SEQ_MOD


def seq_lt(a: int, b: int) -> bool:
    """True when a precedes b, accounting for 32-bit wraparound (RFC 1982)."""
    return 0 < seq_diff(b, a) < SEQ_HALF


def seq_gt(a: int, b: int) -> bool:
    return seq_lt(b, a)


class TcpFlowState:
    def __init__(self) -> None:
        self.next_seq: Optional[int] = None
        self.pending: Dict[int, bytes] = {}
        self.pending_bytes = 0
        self.stream = bytearray()
        #: Last activity of any kind. Drives eviction of dead flows.
        self.last_seen = time.monotonic()
        #: Last time next_seq actually advanced. Drives the stale reset -- using
        #: last_seen for that meant a flow wedged behind a missing segment kept
        #: itself alive forever, because parking a segment counts as activity.
        self.last_progress = time.monotonic()
        #: When the current gap started, or None if there is no gap.
        self.gap_since: Optional[float] = None

    def reset(self) -> None:
        self.next_seq = None
        self.pending.clear()
        self.pending_bytes = 0
        self.stream.clear()
        now = time.monotonic()
        self.last_seen = now
        self.last_progress = now
        self.gap_since = None


FLOW_STATES: Dict[Tuple[str, int, str, int], TcpFlowState] = {}
SEEN_MQTT_TOPICS: Dict[str, int] = {}
IMPORTANT_DEBUG_KEYS = ("bms_avg_temp_c", "mains_current_flow_direction")
LAST_PUBLISH_TS: float = 0.0
LAST_CACHE_WRITE_TS: float = 0.0
# Set when a value changed but the throttle window has not elapsed, so the
# change is deferred rather than dropped.
PENDING_PUBLISH: bool = False
# One integration clock per domain. A single shared clock plus per-domain gating
# would silently lose energy: a payload carrying only grid data would advance the
# clock and the battery integrator would never be credited for that interval.
#: One-shot guard so an abnormal gap is reported once, not per payload.
ENERGY_DT_CLAMP_LOGGED = False

#: Plausibility bound on the WdRR signed power token, derived from the field itself:
#: it is five digits plus a sign, so a real reading can never exceed 99999. The bound
#: exists because this token is the only unguarded input to a total_increasing energy
#: counter -- every neighbouring field is range-checked -- and _accumulate_kwh is
#: monotonic, so one malformed token latches the Energy Dashboard permanently.
MAINS_POWER_MAX_W = 100000

#: Plausibility bound on a battery current, in amps. The guards it replaces read
#: 0..300, which is BELOW what this hardware declares for itself: the reference device
#: reports bms_charge_current_limit_a of 390 A, so a real reading between 300 and 390
#: was being discarded -- silently, which is the worse half.
#:
#: 1000 A is set by the largest current the hardware can plausibly reach, not picked.
#: The highest limit any capture has shown the BMS declare is 390 A, and the 11 kW
#: nameplate at a ~48 V bank implies about 229 A. 1000 leaves headroom over both for
#: banks larger than the one seen, while still rejecting a token that is obviously
#: garbage -- the 9999 A synthetic block remains rejected.
BATTERY_CURRENT_MAX_A = 1000

#: What the per-payload line reports. Four states, not three: a broker that has never
#: connected is not the same as one that dropped mid-run, and it is not throttling --
#: saying "throttled" there would swap a crash for a new false statement. "Published to
#: HA" is verbatim on purpose: captures/README.md correlates it against the vendor
#: portal's UpdateTime, and tests/captures.py records EXPECTED_TELEMETRY's provenance
#: from it.
PUBLISH_OUTCOMES = {
    "sent": "Published to HA",
    "throttled": "Decoded, publish throttled",
    "broker-unreachable": "Decoded but NOT published -- broker unreachable",
    "no-broker-yet": "Decoded but NOT published -- no broker connection yet",
}

#: One-shot guard so a rejected current is reported once, not per payload.
BATTERY_CURRENT_REJECTED_LOGGED = False
GRID_VALUE_REJECTED_LOGGED = False

#: Every block name _try_ascii_schema knows how to decode.
#:
#: This is a parallel declaration, NOT the thing the decoder loops over -- the names
#: stay as literals inside _try_ascii_schema, because rewiring fifteen decode branches
#: to gain a log line is exactly the kind of change nobody can re-verify without the
#: hardware. It exists so the parser can count how many of a payload's blocks it
#: recognised, which it previously had no way to know: a device speaking a different
#: protocol produced an empty state and a message that read the same as a truncated
#: token list. tests/test_truthfulness.py asserts this set against the literals.
KNOWN_BLOCK_NAMES: FrozenSet[str] = frozenset(
    {
        "2ONL", "2l0E", "93VQ", "COST", "Mpod", "SUCV", "V4W3", "WdRR",
        "Yavb", "dHrK", "eo8w", "hR6Y", "noeP", "uxJp", "v09K",
    }
)

#: The frame terminator these vendors append. Bytes, not a literal, so no escape
#: can be mangled by a generator script again.
_FRAME_TAIL = bytes((0x0D, 0x0A))

#: Width of the checksum a body may carry and still count as text. Two, because that
#: is the width of a CRC16 -- the thing being excused -- not a tunable. Three would
#: start excusing a real binary trailer.
_CHECKSUM_TAIL_BYTES = 2

#: One-shot guard so a foreign device is diagnosed once, not on every payload.
UNSUPPORTED_PROTOCOL_LOGGED = False
#: Integration clock per energy domain, holding time.monotonic() readings -- which are
#: meaningless across a process boundary and must never be persisted. A dict rather than one global per domain, so
#: adding a calculated energy counter does not need a new module-level name -- and so
#: the test isolation helper has one thing to save instead of a growing list.
LAST_ENERGY_TS: Dict[str, float] = {}
_FLOW_EVICT_COUNTER: int = 0
_FLOW_EVICT_INTERVAL: int = 200  # Prune stale TCP flows every N state lookups.


def _evict_stale_flows() -> None:
    """Remove FLOW_STATES entries inactive for longer than STREAM_STALE_SECONDS."""
    now = time.monotonic()
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


def dtu_id_from_topic(topic):
    """The collector id, from the topic segment the inverter publishes under.

    Topics look like ``dtu/12345678901234567890/pub/event/dev_prop_post``. The vendor
    portal shows the same twenty digits as the DTU, and its Serial Number is the first
    ten of them -- but the portal's trailing ``-1`` is a device index that appears
    nowhere on the wire, so only the raw id is reported and nothing is synthesised.
    Returns None unless the topic really has that shape.
    """
    if not topic:
        return None
    parts = str(topic).split("/")
    if len(parts) < 2 or parts[0].lower() != "dtu":
        return None
    candidate = parts[1]
    if not candidate.isdigit() or not (8 <= len(candidate) <= 32):
        return None
    return candidate


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


#: Upper bound for the variable-length control packets. The protocol allows up to
#: 256 MB, but an inverter's CONNECT and SUBSCRIBE carry a client id and a handful of
#: topics -- hundreds of bytes. Leaving these unbounded let a random byte with the
#: right nibble declare a 46 KB frame and swallow the genuine PUBLISHes behind it.
#: The bridge only reads these types to stay frame-aligned, so an over-tight bound
#: costs one byte-slide and a resync, never telemetry.
_CONTROL_MAX = 2048

#: Per control type: (required low-nibble flags, min remaining length, max remaining
#: length). -1 means "not fixed". MQTT 3.1.1 mandates specific reserved flag bits and
#: exact lengths for most control packets, and enforcing them is what makes byte-wise
#: resynchronisation converge instead of accepting arbitrary data as a frame.
_MQTT_TYPE_RULES = {
    1: (0, 10, _CONTROL_MAX),    # CONNECT
    2: (0, 2, 2),                # CONNACK
    4: (0, 2, 2),                # PUBACK
    5: (0, 2, 2),                # PUBREC
    6: (2, 2, 2),                # PUBREL
    7: (0, 2, 2),                # PUBCOMP
    8: (2, 5, _CONTROL_MAX),     # SUBSCRIBE
    9: (0, 3, _CONTROL_MAX),     # SUBACK
    10: (2, 5, _CONTROL_MAX),    # UNSUBSCRIBE
    11: (0, 2, 2),               # UNSUBACK
    12: (0, 0, 0),    # PINGREQ
    13: (0, 0, 0),    # PINGRESP
    14: (0, 0, 0),    # DISCONNECT
}


def _is_minimal_varint(packet: bytes, start: int, end: int) -> bool:
    """MQTT requires the shortest encoding of the remaining length.

    A trailing 0x00 continuation byte encodes the same value in more bytes, and
    accepting it lets a large class of random data pass as a valid header.
    """
    return (end - start) == 1 or packet[end - 1] != 0


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

    # The caller slices the buffer to exactly this length, so on its own this check is
    # tautological -- the per-type rules below are what actually reject a bogus frame.
    if len(packet) != pos + remaining_len:
        return False

    if len(packet) > MAX_MQTT_PACKET:
        return False

    if not _is_minimal_varint(packet, 1, pos):
        return False

    rule = _MQTT_TYPE_RULES.get(packet_type)
    if rule is None:
        return False

    required_flags, min_remaining, max_remaining = rule
    if (packet[0] & 0x0F) != required_flags:
        return False
    if remaining_len < min_remaining:
        return False
    if max_remaining >= 0 and remaining_len > max_remaining:
        return False

    # Packet identifiers are 1..65535; zero never appears on the wire.
    if packet_type in (4, 5, 6, 7, 8, 9, 10, 11) and remaining_len >= 2:
        if int.from_bytes(packet[pos:pos + 2], "big") == 0:
            return False

    if packet_type == 1:
        name_len = int.from_bytes(packet[pos:pos + 2], "big")
        name = packet[pos + 2:pos + 2 + name_len]
        if name not in (b"MQTT", b"MQIsdp"):
            return False

    if packet_type == 2:
        if packet[pos] > 1 or packet[pos + 1] > 5:
            return False

    return True


def _header_is_plausible(stream: bytearray, packet_type: int, remaining_len: int, header_end: int) -> bool:
    """Cheap validation using only the fixed header, before the body has arrived.

    The extractor has to decide between *waiting* for more TCP data and *sliding* one
    byte to resynchronise. Without this it always waited, so a bogus header declaring
    a length longer than the buffer stalled the stream indefinitely and then consumed
    the genuine packets queued behind it.
    """
    if not _is_minimal_varint(stream, 1, header_end):
        return False

    if packet_type == 3:
        # Reserved bit 0 of the flags is the RETAIN flag and may be set; QoS 3 is not
        # a valid value.
        if ((stream[0] >> 1) & 0x03) == 3:
            return False
        if remaining_len < 2:
            return False
        if len(stream) >= header_end + 2:
            topic_len = int.from_bytes(stream[header_end:header_end + 2], "big")
            if topic_len <= 0 or topic_len > 256 or topic_len > remaining_len:
                return False
            # If the topic itself has arrived, check it before committing to a length
            # that may be tens of kilobytes. This is what stops a random byte with the
            # PUBLISH nibble from swallowing the genuine packets queued behind it.
            topic_end = header_end + 2 + topic_len
            if len(stream) >= topic_end:
                topic = bytes(stream[header_end + 2:topic_end]).decode("utf-8", errors="ignore")
                if not is_reasonable_topic(topic):
                    return False
        return True

    rule = _MQTT_TYPE_RULES.get(packet_type)
    if rule is None:
        return False
    required_flags, min_remaining, max_remaining = rule
    if (stream[0] & 0x0F) != required_flags:
        return False
    if remaining_len < min_remaining:
        return False
    if max_remaining >= 0 and remaining_len > max_remaining:
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

        if not _header_is_plausible(stream, packet_type, remaining_len, header_end):
            del stream[0]
            continue

        if len(stream) < total_len:
            # The header looks real, so this is a genuine partial packet: wait for
            # the rest of the TCP stream rather than resynchronising.
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
    now = time.monotonic()

    _FLOW_EVICT_COUNTER += 1
    if _FLOW_EVICT_COUNTER >= _FLOW_EVICT_INTERVAL:
        _FLOW_EVICT_COUNTER = 0
        _evict_stale_flows()

    if state is None:
        state = TcpFlowState()
        FLOW_STATES[flow_key] = state
        return state

    if now - state.last_progress > STREAM_STALE_SECONDS:
        state.reset()

    state.last_seen = now
    return state


def reset_flow(flow_key: Tuple[str, int, str, int], initial_seq: Optional[int] = None) -> None:
    """Drop everything buffered for a flow, optionally seeding the next sequence.

    Called on SYN so a reconnect that reuses the same socket pair does not inherit
    the previous connection's next_seq and treat every segment as a giant gap.
    """
    state = FLOW_STATES.get(flow_key)
    if state is None:
        state = TcpFlowState()
        FLOW_STATES[flow_key] = state
    state.reset()
    if initial_seq is not None:
        state.next_seq = initial_seq % SEQ_MOD


def drop_flow(flow_key: Tuple[str, int, str, int]) -> None:
    """Forget a flow entirely. Called on FIN or RST."""
    FLOW_STATES.pop(flow_key, None)


def _resync_flow(state: TcpFlowState, flow_key: Tuple[str, int, str, int], reason: str) -> None:
    """Give up on a gap that will never be filled and restart from what we hold.

    A passive sniffer drops packets under kernel buffer pressure while the real
    receiver got them and ACKed them, so no retransmission ever arrives. Without
    this the flow parks segments forever and every sensor freezes.
    """
    if LOG_STREAM_EVENTS:
        log_kv("[STREAM RESYNC]", flow=flow_key, reason=reason,
               pending=len(state.pending), pending_bytes=state.pending_bytes)
    state.stream.clear()
    if state.pending:
        state.next_seq = min(state.pending)
    state.gap_since = None
    state.last_progress = time.monotonic()


def append_stream_data(flow_key: Tuple[str, int, str, int], seq: int, payload: bytes) -> List[bytes]:
    state = get_flow_state(flow_key)
    packets: List[bytes] = []

    if not payload:
        return packets

    if state.next_seq is None:
        state.next_seq = seq
        if LOG_STREAM_EVENTS:
            log_kv("[STREAM INIT]", flow=flow_key, seq=seq, payload_len=len(payload))

    # Sequence numbers are 32-bit and wrap. Plain < / > comparisons meant that after
    # a wrap every arriving segment looked like a duplicate and the flow never
    # advanced again.
    if seq_lt(seq, state.next_seq):
        overlap = seq_diff(state.next_seq, seq)
        if overlap >= len(payload):
            if LOG_STREAM_EVENTS:
                log_kv("[STREAM DUPLICATE]", flow=flow_key, seq=seq, next_seq=state.next_seq, payload_len=len(payload))
            return packets
        if LOG_STREAM_EVENTS:
            log_kv("[STREAM OVERLAP]", flow=flow_key, seq=seq, next_seq=state.next_seq, overlap=overlap, payload_len=len(payload))
        payload = payload[overlap:]
        seq = state.next_seq

    if seq_gt(seq, state.next_seq):
        now = time.monotonic()
        if seq not in state.pending:
            state.pending[seq] = payload
            state.pending_bytes += len(payload)
            if state.gap_since is None:
                state.gap_since = now
            if LOG_STREAM_EVENTS:
                log_kv("[STREAM GAP]", flow=flow_key, seq=seq, next_seq=state.next_seq, payload_len=len(payload), pending_count=len(state.pending))
                log_payload_preview("[STREAM GAP PAYLOAD]", payload, flow=flow_key, seq=seq)

        # A gap the sniffer missed is never retransmitted, because the real receiver
        # got the segment and ACKed it. Bound the wait rather than parking forever.
        if len(state.pending) > MAX_PENDING_SEGMENTS:
            _resync_flow(state, flow_key, "pending_segments")
        elif state.pending_bytes > MAX_PENDING_BYTES:
            _resync_flow(state, flow_key, "pending_bytes")
        elif state.gap_since is not None and (now - state.gap_since) > STREAM_GAP_TIMEOUT_SEC:
            _resync_flow(state, flow_key, "gap_timeout")
        else:
            return packets

        if state.next_seq is None or state.next_seq not in state.pending:
            return packets

    state.stream.extend(payload if seq == state.next_seq else b"")
    if seq == state.next_seq:
        state.next_seq = (seq + len(payload)) % SEQ_MOD

    while state.next_seq in state.pending:
        pending_payload = state.pending.pop(state.next_seq)
        state.pending_bytes -= len(pending_payload)
        if LOG_STREAM_EVENTS:
            log_kv("[STREAM REASSEMBLE]", flow=flow_key, seq=state.next_seq, payload_len=len(pending_payload), pending_count=len(state.pending))
        state.stream.extend(pending_payload)
        state.next_seq = (state.next_seq + len(pending_payload)) % SEQ_MOD

    state.last_progress = time.monotonic()
    if not state.pending:
        state.gap_since = None

    if len(state.stream) > MAX_STREAM_BUFFER:
        if LOG_STREAM_EVENTS:
            log_kv("[STREAM TRIM]", flow=flow_key, stream_len=len(state.stream), max_len=MAX_STREAM_BUFFER)
        del state.stream[:-MAX_STREAM_BUFFER]

    packets.extend(extract_mqtt_packets_from_stream(state.stream))
    return packets


def _get_mqtt_publish():
    """Minimal deferred import — only for MQTT publish callables not available in state.py."""
    from . import mqtt
    return mqtt.publish_sensor_discovery, mqtt.publish_grouped_state


def heartbeat_due(now: Optional[float] = None) -> bool:
    """Whether the retained state is old enough to be worth republishing.

    This has to be driven by a timer, not by an arriving payload: the whole point is
    to keep Home Assistant's expire_after window fresh while the inverter is quiet,
    and a payload-driven heartbeat cannot fire precisely when it is needed. Measured
    on real hardware, telemetry arrives every 300 s and can gap to 600 s.
    """
    if not EXPIRE_AFTER_SEC:
        return False
    now = now if now is not None else time.monotonic()
    interval = max(UPDATE_INTERVAL_SEC, EXPIRE_AFTER_SEC // 3)
    return (now - LAST_PUBLISH_TS) >= interval


def republish_state(now: Optional[float] = None) -> bool:
    """Republish the retained state so it does not age out. Returns True if sent."""
    global LAST_PUBLISH_TS, PENDING_PUBLISH
    if not _shared_state.DISCOVERY_PUBLISHED:
        return False
    snapshot = _shared_state.snapshot_state()
    if not snapshot:
        return False
    _, publish_grouped_state = _get_mqtt_publish()
    delivered = publish_grouped_state(snapshot)
    # Bookkeeping advances either way, for the same reason as the payload path: a
    # CONNACK republishes everything, so a dropped heartbeat self-heals.
    LAST_PUBLISH_TS = now if now is not None else time.monotonic()
    PENDING_PUBLISH = False
    return delivered


def _write_state_cache(snapshot: Dict[str, object], now: Optional[float] = None) -> bool:
    """Persist the state cache, at most once per STATE_CACHE_INTERVAL_SEC.

    Throttling is not optional here. This runs on the scapy capture callback, and
    adding fsync without it would make the hot path slower than the unsafe version it
    replaces -- a slow flush stalls libpcap and drops segments.
    """
    global LAST_CACHE_WRITE_TS
    now = now if now is not None else time.monotonic()
    if LAST_CACHE_WRITE_TS and (now - LAST_CACHE_WRITE_TS) < STATE_CACHE_INTERVAL_SEC:
        return False
    try:
        _shared_state.atomic_write_json(STATE_CACHE_FILE, snapshot)
        LAST_CACHE_WRITE_TS = now
        return True
    except Exception as exc:
        log(f"[CACHE WRITE ERROR] {exc}", level="error")
        return False


def _log_debug_block(block_name: str, raw_text: str) -> None:
    """Log raw debug block data instead of creating HA entities."""
    log(f"[DEBUG BLOCK] {block_name}: {raw_text[:250]}", level="debug")


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
    def _format_version_display(raw_version: str) -> str:
        version = raw_version.strip()
        if not version:
            return version

        if "." in version:
            head, tail = version.split(".", 1)
            if head.isdigit():
                head = str(int(head))
            else:
                head = head.lstrip("0") or "0"
            return f"{head}.{tail}"

        if version.isdigit():
            return str(int(version))
        return version.lstrip("0") or "0"

    @staticmethod
    def _power_to_kwh_delta(power_w: float, dt_seconds: float) -> float:
        if power_w <= 0 or dt_seconds <= 0:
            return 0.0
        return (power_w * dt_seconds) / 3_600_000.0

    @staticmethod
    def _energy_dt_seconds(domain: str, now_ts: float) -> float:
        """Seconds since this domain last integrated, and advance its clock.

        The first call for a domain establishes a baseline and returns 0 -- there is
        no interval to integrate over yet.
        """
        previous = LAST_ENERGY_TS.get(domain)
        LAST_ENERGY_TS[domain] = now_ts
        if previous is None:
            return 0.0

        dt_seconds = max(0.0, now_ts - previous)

        max_dt_seconds = SolarParser._energy_max_dt()
        if dt_seconds <= max_dt_seconds:
            return dt_seconds

        # Only reached when the gap is genuinely abnormal. Logged because the previous
        # bound fired on every single payload and threw away four fifths of the energy
        # in complete silence.
        global ENERGY_DT_CLAMP_LOGGED
        if not ENERGY_DT_CLAMP_LOGGED:
            ENERGY_DT_CLAMP_LOGGED = True
            log_kv(
                "[ENERGY GAP CLAMPED]",
                level="warning",
                domain=domain,
                dt_seconds=round(dt_seconds, 1),
                max_dt_seconds=round(max_dt_seconds, 1),
            )
        return max_dt_seconds

    @staticmethod
    def _energy_max_dt() -> float:
        """Largest interval the integrator will credit in one step.

        Guards against a genuinely long real gap -- a wedged stream, or a process that
        stopped being scheduled -- crediting a fabricated block of kWh. A clock jump is
        no longer among them: since 2.6.19 the interval is measured on time.monotonic()
        and a wall-clock step cannot reach this function. It must sit *above* normal operation: derived from
        UPDATE_INTERVAL_SEC it evaluated to 60 s against a measured 300 s cadence, so
        it truncated every interval and the counters accrued a fifth of the real
        energy. Floored on observed cadence for the same reason the availability
        watchdog is -- a configured value cannot be trusted to match the hardware.
        """
        observed = _shared_state.observed_telemetry_interval() * 2.0
        return min(
            max(float(ENERGY_MAX_DT_SEC), observed),
            float(TELEMETRY_TIMEOUT_CEILING_SEC),
        )

    @staticmethod
    def _battery_current(state: Dict[str, object], bms_key: str, legacy_key: str, factor: float):
        """Pick a current source and put both on the same basis.

        The BMS figures are whole-bank already, so they are used unscaled. The 2ONL
        figures are per-inverter and are scaled by INVERTER_COUNT -- without that the
        fallback silently switches basis mid-stream and steps the reported power.

        Only the *fresh* payload is consulted. Reading the BMS value from the cache
        meant a payload that carried a fresh 2ONL current still integrated an
        arbitrarily old BMS one.
        """
        bms = SolarParser._to_float_or_none(state.get(bms_key))
        legacy = SolarParser._to_float_or_none(state.get(legacy_key))

        if bms is not None and legacy is not None:
            scaled_legacy = legacy * factor
            bigger = max(bms, scaled_legacy)
            smaller = min(bms, scaled_legacy)
            # A source reading zero while the other reports current is the most
            # informative disagreement of all, and a ratio test cannot express it.
            one_reads_zero = smaller <= 0.01 < bigger
            ratio_differs = smaller > 0.01 and bigger / smaller > 2.0
            if one_reads_zero or ratio_differs:
                # No ground truth exists: the official app displays both and they
                # disagree too. Surfacing it beats silently picking a winner.
                log_kv(
                    "[ENERGY SOURCE DISAGREEMENT]",
                    level="warning",
                    bms_key=bms_key,
                    bms_value=bms,
                    legacy_key=legacy_key,
                    legacy_value=legacy,
                    legacy_scaled=round(scaled_legacy, 2),
                    using=bms_key,
                )

        if bms is not None:
            return bms
        if legacy is not None:
            return legacy * factor
        return None

    @staticmethod
    def _accumulate_kwh(state: Dict[str, object], key: str, power_w: float, dt_seconds: float) -> None:
        previous = SolarParser._to_float_or_none(_shared_state.LAST_STATE.get(key)) or 0.0
        total = previous + SolarParser._power_to_kwh_delta(power_w, dt_seconds)
        # max() keeps the sensor monotonic for state_class: total_increasing. It is a
        # floor, not protection -- the real guards are the input range checks and the
        # freshness gates below.
        state[key] = round(max(previous, total), 6)

    @staticmethod
    def _derive_battery_status(state: Dict[str, object]) -> None:
        """Label the battery from the same power figures the sensors publish.

        Deriving it independently let the two disagree: the status came from the
        inverter's ammeter and the power from the BMS, so a real installation showed
        "Idle" alongside 344 W of charge. Reading the calculated power instead makes
        that contradiction impossible rather than merely unlikely.
        """
        charge_w = SolarParser._to_float_or_none(state.get("c_battery_charge_power_w"))
        discharge_w = SolarParser._to_float_or_none(state.get("c_battery_discharge_power_w"))
        if charge_w is None or discharge_w is None:
            # No battery data in this payload; say nothing rather than guess.
            return

        if charge_w > 0:
            state["battery_status"] = "Charge"
        elif discharge_w > 0:
            state["battery_status"] = "Discharge"
        else:
            state["battery_status"] = "Idle"

    @staticmethod
    def _apply_energy_dashboard_calculations(state: Dict[str, object], now_ts: Optional[float] = None) -> None:
        """Derive the calculated power and energy sensors.

        Battery and grid are gated independently. A combined gate would let a
        grid-only payload through, find no battery current, and write
        c_battery_charge_power_w = 0 -- flapping battery power to zero every time a
        payload happened to omit the battery block.
        """
        factor = max(0.0, float(INVERTER_COUNT))
        if factor <= 0:
            factor = 1.0

        now = now_ts if now_ts is not None else time.monotonic()

        battery_keys = (
            "bat_v",
            "bat_charge_current",
            "dischg_current",
            "bms_charging_current_a",
            "bms_discharge_current_a",
        )
        if any(key in state for key in battery_keys):
            # bat_v may legitimately come from the cache: it is slow-moving, and the
            # block that carries it also carries the legacy currents.
            bat_v = SolarParser._to_float_or_none(
                state.get("bat_v", _shared_state.LAST_STATE.get("bat_v"))
            )
            charge_a = SolarParser._battery_current(
                state, "bms_charging_current_a", "bat_charge_current", factor
            )
            discharge_a = SolarParser._battery_current(
                state, "bms_discharge_current_a", "dischg_current", factor
            )

            charge_power_w = 0.0
            discharge_power_w = 0.0
            if bat_v is not None and bat_v >= 0:
                if charge_a is not None and charge_a > 0:
                    charge_power_w = bat_v * charge_a
                if discharge_a is not None and discharge_a > 0:
                    discharge_power_w = bat_v * discharge_a

            state["c_battery_charge_power_w"] = int(round(charge_power_w))
            state["c_battery_discharge_power_w"] = int(round(discharge_power_w))

            dt_seconds = SolarParser._energy_dt_seconds("battery", now)
            SolarParser._accumulate_kwh(state, "c_battery_charge_energy_kwh", charge_power_w, dt_seconds)
            SolarParser._accumulate_kwh(
                state, "c_battery_discharge_energy_kwh", discharge_power_w, dt_seconds
            )

            if BATTERY_CAPACITY_PER_BATTERY_AH > 0:
                state["c_bms_total_capacity_ah"] = round(
                    BATTERY_COUNT * BATTERY_CAPACITY_PER_BATTERY_AH, 1
                )

        if "mains_wdrr_value" in state:
            mains_signed_w = SolarParser._to_float_or_none(state.get("mains_wdrr_value"))
            grid_import_power_w = 0.0
            if mains_signed_w is not None and mains_signed_w > 0:
                grid_import_power_w = mains_signed_w * factor

            state["c_grid_import_power_w"] = int(round(grid_import_power_w))

            dt_seconds = SolarParser._energy_dt_seconds("grid", now)
            SolarParser._accumulate_kwh(
                state, "c_grid_import_energy_kwh", grid_import_power_w, dt_seconds
            )

        # Generation and load complete the calculated energy family. Battery and grid
        # already had integrated counters, so scaled power had a scaled energy partner
        # in two domains out of four -- and the device's own pv_*_kwh counters are
        # per-inverter, which is a different basis from c_generation_power_w and cannot
        # be compared with it. Each domain is gated on its power being present in THIS
        # payload and keeps its own clock, exactly as battery and grid do.
        if "c_generation_power_w" in state:
            generation_w = SolarParser._to_float_or_none(state.get("c_generation_power_w"))
            if generation_w is not None and generation_w >= 0:
                dt_seconds = SolarParser._energy_dt_seconds("generation", now)
                SolarParser._accumulate_kwh(
                    state, "c_generation_energy_kwh", generation_w, dt_seconds
                )

        if "c_load_w" in state:
            load_power_w = SolarParser._to_float_or_none(state.get("c_load_w"))
            if load_power_w is not None and load_power_w >= 0:
                dt_seconds = SolarParser._energy_dt_seconds("load", now)
                SolarParser._accumulate_kwh(
                    state, "c_load_energy_kwh", load_power_w, dt_seconds
                )

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
    def _plausible_current(value, field: str):
        """Return the current if it can be real, else None -- and say so, once.

        These bounds used to be four inline comparisons against 300 with no report.
        A rejected reading simply left its key absent, and the consequences ran on from
        there: _battery_current then picked whichever source survived WITHOUT the
        [ENERGY SOURCE DISAGREEMENT] warning, which only fires when both are present,
        and if both were dropped the charge and discharge power both read 0 and
        _derive_battery_status reported Idle. A high-current moment presented as an
        idle battery contributing no energy, with nothing in the log to say why.
        """
        global BATTERY_CURRENT_REJECTED_LOGGED
        if value is None:
            return None
        if 0 <= value <= BATTERY_CURRENT_MAX_A:
            return value
        if not BATTERY_CURRENT_REJECTED_LOGGED:
            BATTERY_CURRENT_REJECTED_LOGGED = True
            log_kv(
                "[BATTERY CURRENT REJECTED]",
                level="warning",
                field=field,
                parsed=value,
                max_a=BATTERY_CURRENT_MAX_A,
            )
        return None

    @staticmethod
    def _crc16_modbus(data: bytes) -> int:
        crc = 0xFFFF
        for byte in data:
            crc ^= byte
            for _ in range(8):
                crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
        return crc

    @staticmethod
    def _crc16_xmodem(data: bytes) -> int:
        """CRC16-XMODEM: poly 0x1021, init 0x0000, transmitted big-endian.

        The Voltronic/Axpert PI30 family computes it over the whole response
        INCLUDING the leading "(" and excluding the trailing CR. Excluding the paren
        matches nothing, so the scope is not a detail to tidy later.
        """
        crc = 0
        for byte in data:
            crc ^= byte << 8
            for _ in range(8):
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
        return crc

    @staticmethod
    def _is_printable(data: bytes) -> bool:
        return all(0x20 <= byte <= 0x7E or byte in (0x0A, 0x0D, 0x09) for byte in data)

    @staticmethod
    def _body_shape(body: bytes) -> str:
        """Classify one block body as ascii, ascii+binary_tail, or binary.

        Per body, and per byte-run within it. The previous single all() over every
        byte of every body meant one checksum byte anywhere forced the whole payload
        to "binary" -- the word the docs called the strongest signal of a different
        protocol family. Issue #32 is plain ASCII with a two-byte CRC and was told it
        was binary.
        """
        if SolarParser._is_printable(body):
            return "ascii"
        core = body.rstrip(_FRAME_TAIL)
        head = core[:-_CHECKSUM_TAIL_BYTES]
        if len(head) >= 2 and SolarParser._is_printable(head):
            return "ascii+binary_tail"
        return "binary"

    @staticmethod
    def _describe_foreign_blocks(blocks: Dict[str, bytes]) -> Dict[str, object]:
        """Say what a payload of unrecognised blocks actually looks like.

        This feeds a log line, never a sensor, so naming a suspected protocol here is
        a triage hint rather than a published value -- the rule against publishing
        without evidence does not reach it. It exists because the first foreign device
        reported (issue #30, a Beve Mega 6kW) produced fifteen unmatched names and a
        message indistinguishable from a truncated token list, and the reporter had no
        way to tell a wrong device from a broken add-on.
        """
        bodies = list(blocks.values())
        shapes = [SolarParser._body_shape(body) for body in bodies]
        # Worst shape wins the headline, but every shape present is reported, because
        # a mixed payload is itself the signal -- issue #30 sends Modbus frames and one
        # Voltronic ACK.
        for worst in ("binary", "ascii+binary_tail", "ascii"):
            if worst in shapes:
                break

        # Modbus RTU: address, function, byte count, data, CRC16 little-endian. The
        # CRC is what makes this a check rather than a shape guess -- a single false
        # positive needs a 16-bit collision, so a handful agreeing is conclusive.
        #
        # Counted, not all-or-nothing. On the raw payload that prompted this, 9 of 13
        # bodies verified: one carried a vendor function code and one was truncated by
        # the debug preview itself. An all() here would have stayed silent on the very
        # report it was written for. (The shipped fixture is 10 of 12 -- the truncated
        # block was left out of it, being not verbatim.)
        crc_ok = sum(
            1
            for body in bodies
            if len(body) > 4
            and SolarParser._crc16_modbus(body[:-2]) == int.from_bytes(body[-2:], "little")
        )

        # Voltronic / Axpert PI30: "(" then the payload then CRC16-XMODEM big-endian,
        # then CR. Same counted treatment and the same reason: issue #30's Modbus
        # device emits exactly one valid Voltronic frame ("(ACK9 "), so any single
        # match would mislabel it. That one frame is what sets the threshold below.
        voltronic_ok = 0
        for body in bodies:
            core = body.rstrip(_FRAME_TAIL)
            if (
                len(core) > 3
                and core[:1] == b"("
                and SolarParser._crc16_xmodem(core[:-2]) == int.from_bytes(core[-2:], "big")
            ):
                voltronic_ok += 1

        described: Dict[str, object] = {
            "block_count": len(blocks),
            "recognised": len(set(blocks) & KNOWN_BLOCK_NAMES),
            "names": sorted(blocks.keys()),
            "body": worst,
        }
        if len(set(shapes)) > 1:
            described["body_shapes"] = ",".join(f"{s}={shapes.count(s)}" for s in sorted(set(shapes)))
        if crc_ok:
            described["modbus_crc_ok"] = f"{crc_ok}/{len(bodies)}"
        if voltronic_ok:
            described["voltronic_crc_ok"] = f"{voltronic_ok}/{len(bodies)}"

        modbus_hint = crc_ok >= 3 and crc_ok * 2 >= len(bodies)
        voltronic_hint = voltronic_ok >= 3 and voltronic_ok * 2 >= len(bodies)
        if modbus_hint and not voltronic_hint:
            described["looks_like"] = "modbus_rtu"
        elif voltronic_hint and not modbus_hint:
            described["looks_like"] = "voltronic_pi30"
        return described

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
        """Decode the per-cell voltage list.

        Stops at the first out-of-range token rather than skipping it. Skipping
        silently renumbered every later cell -- a collapsed cell 3 made cell_3_mv
        report physical cell 4's voltage, which is exactly backwards from what the
        reading is for.

        The min/max/delta summary is deliberately NOT derived here. This block
        carries at most 16 cells while the pack may be larger (a 32-cell bank was
        observed reporting its minimum at position 32), so any summary computed from
        this list describes a subset. uxJp carries the BMS's own whole-bank summary
        and is the sole writer of those keys.
        """
        state: Dict[str, object] = {}
        cell_values: List[int] = []

        for tok in tokens:
            val = SolarParser._to_int(tok)
            if val is None or not (2000 <= val <= 5000):
                break
            cell_values.append(val)

        if not cell_values:
            return state

        state["bms_cell_count"] = len(cell_values)
        if len(cell_values) > 16:
            log(
                f"[CELLS] {len(cell_values)} cells reported but only 16 entities exist; "
                f"cells 17-{len(cell_values)} are not published",
                level="warning",
            )

        for idx, mv in enumerate(cell_values[:16], start=1):
            state[f"cell_{idx}_mv"] = mv

        return state

    @staticmethod
    def _apply_dynamic_debug(parsed: Dict[str, Tuple[str, List[str]]]) -> None:
        for block_name, (raw_text, _tokens) in parsed.items():
            _log_debug_block(block_name, raw_text)

    @staticmethod
    def _try_ascii_schema(blocks: Dict[str, bytes]) -> Dict[str, object]:
        state: Dict[str, object] = {}
        parsed = {name: SolarParser._parse_ascii_text(data) for name, data in blocks.items()}

        SolarParser._apply_dynamic_debug(parsed)

        # Info / identity
        if "SUCV" in parsed:
            state["model_code"] = SolarParser._clean_model_code(parsed["SUCV"][0])

        if "hR6Y" in parsed:
            raw_fw, fw_tokens = parsed["hR6Y"]
            state["firmware_info"] = raw_fw
            if len(fw_tokens) >= 1:
                state["firmware_version"] = fw_tokens[0]
                state["software_version"] = SolarParser._format_version_display(fw_tokens[0])
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
                # output_set_frequency was this same token rounded. The portal carries
                # Output Frequency 49.9 Hz and Output Set Frequency 50 Hz as two fields
                # at one instant, so they are different quantities -- and a sagging
                # output at 49.4 Hz would have published the user's setting as 49.

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
            if load_pct is not None and 0 <= load_pct <= 200:
                state["load_pct"] = load_pct

        if len(vals) >= 6:
            dc_comp = SolarParser._to_int(vals[5])
            if dc_comp is not None:
                state["output_dc_comp"] = dc_comp

        if len(vals) >= 7:
            # Named as bits historically; it is a number. Constant 11000 across every
            # capture on both devices, and it is the denominator of load_pct --
            # 550/11000 = 5%, 4056/11000 = 36.87% -> 36, matching the reported value
            # every time. The owner confirms 11 kW is the inverter's maximum output.
            # The raw string keeps its key so no entity is orphaned.
            state["output_status_bits"] = vals[6]
            rated_va = SolarParser._to_int_strict(vals[6])
            if rated_va is not None and 0 < rated_va <= 200000:
                state["rated_apparent_va"] = rated_va

        if len(vals) >= 8:
            inductor_current = SolarParser._to_float(vals[7])
            if inductor_current is not None:
                state["inductor_current_a"] = round(inductor_current, 1)

        # vals[8] was previously decoded as dc_rectification_temperature_c with a
        # `if > 100: /= 10` rescale, which turned the live value 01175 into 117.5 C.
        # V4W3 carries the same reading unscaled and is the sole writer now.

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
            if mains_signed is not None and -MAINS_POWER_MAX_W <= mains_signed <= MAINS_POWER_MAX_W:
                state["mains_wdrr_value"] = mains_signed
                state["mains_wdrr_abs"] = abs(mains_signed)
                state["mains_power_w"] = abs(mains_signed)
                state["c_mains_power_w"] = SolarParser._scale_main_power(abs(mains_signed))
            elif mains_signed is not None:
                # Dropped rather than clamped: the energy calculation keys off the
                # presence of mains_wdrr_value, so this cleanly skips the grid domain
                # for this payload instead of integrating a fabricated figure.
                global GRID_VALUE_REJECTED_LOGGED
                if not GRID_VALUE_REJECTED_LOGGED:
                    GRID_VALUE_REJECTED_LOGGED = True
                    log_kv(
                        "[GRID VALUE REJECTED]",
                        level="warning",
                        token=vals[6][:32],
                        parsed=mains_signed,
                        max_abs=MAINS_POWER_MAX_W,
                    )
                mains_signed = None

        if len(vals) >= 8:
            state["mains_flow_code"] = vals[7]

        if len(vals) >= 9:
            # This token is a number, not a bit field: it reads 11000, 08969 and 07969
            # across captures. main_output_relay_status took its leading character, so
            # the 07969 payload reported the relay Off while the same payload reported
            # 4055 W delivered to the load. Nothing here identifies the relay.
            state["wdrr_status_bits"] = vals[8]

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
            charge_a = SolarParser._plausible_current(
                SolarParser._to_float_strict(vals[3]), "bat_charge_current"
            )
            if charge_a is not None:
                state["bat_charge_current"] = round(charge_a, 2)

        if len(vals) >= 5:
            dischg_a = SolarParser._plausible_current(
                SolarParser._to_float_strict(vals[4]), "dischg_current"
            )
            if dischg_a is not None:
                state["dischg_current"] = round(dischg_a, 2)

        # Tokens 5 and 6 previously carried a pair of guesses: "if this position is not
        # a number, publish it as the battery status / the pack chemistry". Neither
        # position means that on the one device with byte-faithful captures -- token 5
        # is the bus voltage (369) and token 6 a twelve-digit status field
        # (110007200000) -- so the guard never fired and nothing supported the reading
        # it would have produced. battery_status is derived from the calculated power
        # instead, which is what makes it agree with the power sensors; battery_type
        # has no decode path at all and is listed in UNDECODED_SENSOR_KEYS.
        if len(vals) >= 6:
            bus_v = SolarParser._to_float(vals[5])
            if bus_v is not None:
                state["bus_voltage"] = round(bus_v, 1)

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
        # noeP token 3 is NOT the grid-connection count, and no single-inverter capture
        # is needed to say so. It read 2 in every capture taken while PV was live, which
        # is why it was once published as total_number_of_grid_connection -- this device
        # also has 2 parallel inverters and uxJp token 2 is also 2, so three unrelated
        # quantities all equalled 2. The 2026-08-21 23:41 capture has it at 0 with the
        # string dark while the portal still reported Total Number Of Grid Connection 2.
        # It tracks PV2 activity, not topology. Left undecoded: "PV2 is producing" is
        # not a quantity the portal names.

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
        if len(vals) >= 7:
            fan_2_speed = SolarParser._to_int(vals[6])
            if fan_2_speed is not None:
                state["fan_2_speed"] = fan_2_speed
        if len(vals) >= 9:
            pv2_temp = SolarParser._to_float(vals[8])
            if pv2_temp is not None:
                state["pv2_temp"] = round(pv2_temp, 1)
        if len(vals) >= 10:
            dc_rect_temp = SolarParser._to_float(vals[9])
            if dc_rect_temp is not None and 0 <= dc_rect_temp <= 150:
                state["dc_rectification_temperature_c"] = round(dc_rect_temp, 1)

        # Generic computed PV total.
        #
        # Read only from THIS payload. It used to fall back to _shared_state.LAST_STATE,
        # which made generation the one energy domain of four resting on a cached value
        # -- directly against the rule that a value is published only when this payload
        # carries evidence for it, and against the comment on the accumulator itself.
        # This device's payloads arrive as two disjoint block sets, so the identity
        # payload republished the previous payload's PV power every time. Harmless while
        # Mpod and noeP keep arriving; the moment they stop at dusk the last daylight
        # figure would be held and c_generation_energy_kwh would accrue invented kWh all
        # night.
        pv_total_w = 0
        have_pv_total = False
        for key in ("pv_w", "pv2_power_w"):
            val = state.get(key)
            if isinstance(val, (int, float)):
                pv_total_w += int(round(float(val)))
                have_pv_total = True
        if have_pv_total:
            state["generation_power_w"] = pv_total_w
            state["c_generation_power_w"] = SolarParser._scale_main_power(pv_total_w)
            # solar_charging_switch was "Open" if pv_total_w > 0, which reports the
            # switch as Close every night. The portal lists it beside AC Charging
            # Switch and Charging Main Switch as a configuration setting, and the one
            # switch that is genuinely decoded comes from a 93VQ config field.

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
                # This token is a state-of-charge percentage. It used to also be
                # written to grid_connected_current_a, which is declared in amps.
                # 93VQ[17] is the real grid current and is the sole writer now.
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
        # vals[14] was previously written to BOTH mains_charging_starting_time and
        # mains_charging_ending_time -- one token cannot be two different times.
        # 93VQ[18] and 93VQ[19] carry them separately and are the sole writers now.
        if len(vals) >= 16:
            second_batt_v = SolarParser._to_float(vals[15])
            if second_batt_v is not None:
                state["second_output_battery_voltage_v"] = round(second_batt_v, 1)
        if len(vals) >= 17:
            # dHrK[16] reads "50000" on the reference device and the portal reads 50%,
            # so the first two digits are the value there. That rules out a zero-padded
            # three-wide field -- 50 would render "050", giving "05000". It does NOT
            # rule out a variable-width number left-packed into five characters, under
            # which "10000" is 10 or 100 and neither reading can be preferred.
            #
            # Do not add a rule for 100 without a capture that settles it: set the
            # second output capacity to a single digit and see whether the token reads
            # "05000" (fixed two-digit field) or "50000" (variable width). Guessing here
            # is precisely what 2.6.1 had to remove.
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
            # A packed word: eight settings digits followed by the configured output
            # voltage. This used to require the tail to be exactly "230" -- the
            # reference device's setting -- so on a 120 V, 220 V or 240 V inverter the
            # test failed and all nine fields below silently produced nothing, with no
            # log line. The tail is now validated as a plausible mains voltage rather
            # than compared against one device's. It still gates the settings decode:
            # a tail that is not a voltage means this is not the word we think it is.
            if len(config_pack) >= 11 and config_pack.isdigit():
                prefix = config_pack[:-3]
                out_set_v = SolarParser._to_int_strict(config_pack[-3:])
                if out_set_v is not None and 100 <= out_set_v <= 300:
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
        # Yavb (BMS/status rich block)
        vals = parsed.get("Yavb", ("", []))[1]
        # vals[0] duplicates bat_series_count, which 2ONL already provides via the
        # strict integer parser. 2ONL is the battery block and is the sole writer.
        if len(vals) >= 2:
            state["yavb_flags_raw"] = vals[1]
        if len(vals) >= 3:
            v = SolarParser._to_float(vals[2])
            if v is not None:
                # This token is the BMS discharge cut-off. It was also written to
                # low_electric_lock_voltage_v, which 93VQ[16] carries as a separate
                # user setting; 93VQ is the sole writer of that key now.
                state["bms_discharge_voltage_limit_v"] = round(v, 1)
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
            charge_or_temp = SolarParser._plausible_current(
                SolarParser._to_float(vals[6]), "bms_charging_current_a"
            )
            discharge = SolarParser._plausible_current(
                SolarParser._to_float(vals[7]), "bms_discharge_current_a"
            )
            if charge_or_temp is not None:
                state["bms_charging_current_a"] = round(charge_or_temp, 1)
            if discharge is not None:
                state["bms_discharge_current_a"] = round(discharge, 1)
        if len(vals) >= 9:
            state["yavb_code_raw"] = vals[8]
        if len(vals) >= 10:
            state["yavb_aux_raw"] = vals[9]
        if len(vals) >= 9 and "bms_avg_temp_c" not in state:
            # Token 8 is the BMS average temperature in tenths of a Kelvin. One real
            # block from a second device carries both forms -- token 8 = 02921 and
            # token 10 = 18.95 -- and 2921/10 - 273.15 = 18.95 exactly, so the block
            # supplies its own conversion key. On this device token 8 = 03041 gives
            # 30.95, digit for digit what the vendor portal reports. Every observed
            # value ends in .95 because an integer deci-Kelvin minus 273.15 must.
            raw_dk = SolarParser._to_int_strict(vals[8])
            if raw_dk is not None:
                celsius = raw_dk / 10.0 - 273.15
                if -50.0 <= celsius <= 150.0:
                    state["bms_avg_temp_c"] = round(celsius, 2)

        if len(vals) >= 11:
            # Firmwares that append a plain-Celsius tail. Preferred when present: it is
            # the vendor's own conversion, and it is what proves the token 8 formula.
            bms_avg_temp = SolarParser._to_float(vals[10])
            if bms_avg_temp is not None and -50.0 <= bms_avg_temp <= 150.0:
                state["bms_avg_temp_c"] = round(bms_avg_temp, 2)

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

        # battery_status is derived after the energy calculation, from the same
        # resolved figures, so the two cannot contradict each other. It previously
        # read the inverter's own ammeter while the power sensors used the BMS, which
        # on a real installation reported "Idle" while 344 W flowed into the battery.

        # Compatibility with older entity names / expectations. Each alias has exactly
        # one source carrying the same quantity. Three of them used to fall back to an
        # unrelated setting when the source block was absent -- float_v to the
        # parallel-mode turn-off voltage, bulk_v to the return-to-mains threshold, both
        # from a different block entirely. Payload block sets vary, so on a real device
        # that published 44.0 V and 46.0 V as float and bulk charging voltage. An alias
        # is absent when its source is absent, like everything else here.
        if "inverter_temperature_c" in state:
            state["bat_temp"] = state["inverter_temperature_c"]
        if "maximum_total_charging_current_a" in state:
            state["max_chg"] = state["maximum_total_charging_current_a"]
        if "bms_discharge_voltage_limit_v" in state:
            state["cut_v"] = state["bms_discharge_voltage_limit_v"]
        if "float_charging_voltage_v" in state:
            state["float_v"] = state["float_charging_voltage_v"]
        if "strong_charging_voltage_v" in state:
            state["bulk_v"] = state["strong_charging_voltage_v"]
        if state.get("mains_current_flow_direction") is not None:
            state["mains_flow_state"] = state["mains_current_flow_direction"]
        # battery_type has no writer. It was defaulted to "LIA" whenever a Yavb block
        # existed, then narrowed to a guess at 2ONL token 6 that never fires on real
        # hardware. The official app does show LIA for the reference device, so the
        # constant happened to be right there -- and would have said LIA for every
        # other inverter too. Nothing on the wire carries it.

        # c_bms_total_capacity_ah is written inside the battery branch of the energy
        # calculation, so that a payload carrying no battery data at all produces an
        # empty state dict rather than a handful of derived-from-nothing values.
        SolarParser._apply_energy_dashboard_calculations(state)
        SolarParser._derive_battery_status(state)

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
                        # Full body, not [:64]. The cap silently cut issue #30's
                        # r8BV mid-frame and both of issue #32's telemetry blocks --
                        # exactly the data CONTRIBUTING.md asks reporters to send, and
                        # a truncated body cannot be a BLOCK_* fixture.
                        hex_preview=hex_preview(blocks[block_name], limit=4096),
                    )

            if not blocks:
                if LOG_UNPARSED_PUBLISH:
                    log_payload_preview("[UNPARSED PAYLOAD: NO BLOCKS]", payload_bytes, topic=source_topic)
                return False

            state = SolarParser._try_ascii_schema(blocks)
            if state:
                # The collector id travels in the MQTT topic of this very payload, so
                # it is per-payload evidence like any block field.
                dtu_id = dtu_id_from_topic(source_topic)
                if dtu_id:
                    state["dtu_id"] = dtu_id

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

                # One merge, one snapshot, both under the lock. Everything downstream
                # works from the snapshot so nothing iterates a dict the capture
                # thread may resize underneath it.
                snapshot = _shared_state.update_state(clean_state)
                _shared_state.record_telemetry()

                _write_state_cache(snapshot)

                unresolved_debug = []
                if LOG_NULL_TARGETS:
                    for key in IMPORTANT_DEBUG_KEYS:
                        if snapshot.get(key) is None:
                            unresolved_debug.append(key)
                    if unresolved_debug:
                        log_kv("[UNRESOLVED TARGETS]", topic=source_topic, keys=unresolved_debug, block_names=sorted(blocks.keys()))

                if LOG_STATE_SNAPSHOT:
                    log_kv("[STATE SNAPSHOT]", topic=source_topic, values=snapshot)

                # Set before the gate, not inside it. 2.6.21 initialised this within
                # `if DISCOVERY_PUBLISHED:` while reading it outside, so a broker that
                # had never connected -- the exact install the change was written for --
                # raised UnboundLocalError on every payload, which the handler below
                # swallowed as [PARSER ERROR] and reported as a failed decode.
                publish_outcome = "no-broker-yet"
                if _shared_state.DISCOVERY_PUBLISHED:
                    # Publish discovery for any late-bound raw block sensors.
                    for key in clean_state.keys():
                        if key in SENSORS and key not in _shared_state.PUBLISHED_SENSOR_KEYS:
                            publish_sensor_discovery(key)

                    global LAST_PUBLISH_TS, PENDING_PUBLISH
                    now = time.monotonic()
                    if changed_keys:
                        PENDING_PUBLISH = True

                    elapsed = now - LAST_PUBLISH_TS
                    # A change is deferred to the end of the throttle window, never
                    # dropped -- the previous `or` meant any change published
                    # immediately, so UPDATE_INTERVAL_SEC could never throttle
                    # anything and the option did nothing at all.
                    due = PENDING_PUBLISH and elapsed >= UPDATE_INTERVAL_SEC

                    if due:
                        publish_outcome = (
                            "sent" if publish_grouped_state(snapshot) else "broker-unreachable"
                        )
                        # Advanced even when the broker refused it: on_connect
                        # republishes the whole snapshot on every CONNACK, so a dropped
                        # publish self-heals and retrying at a dead socket would cost a
                        # no-op on every payload.
                        LAST_PUBLISH_TS = now
                        PENDING_PUBLISH = False
                    else:
                        publish_outcome = "throttled"

                # Say what actually happened. This line read "Published to HA" whether
                # or not anything was published: it sits outside the throttle gate, and
                # a QoS 0 publish to a dead broker is dropped without raising, so the
                # one message a user checks was the one that could not be trusted.
                outcome = PUBLISH_OUTCOMES[publish_outcome]
                log_kv(
                    f"[{datetime.now().strftime('%H:%M:%S')}] {outcome}",
                    level="info",
                    topic=source_topic,
                    clean_value_count=len(clean_state),
                    changed_key_count=len(changed_keys),
                    changed_values=changed_data,
                )
                return True

            # Blocks arrived and decoded, but nothing came out of them. Say which of
            # the two very different reasons it was, at warning level and without
            # needing a debug flag -- someone whose inverter is not supported has no
            # reason to have turned one on, which is precisely how issue #30 reached
            # "all sensors Unknown" with nothing in the log naming the cause.
            global UNSUPPORTED_PROTOCOL_LOGGED
            if not UNSUPPORTED_PROTOCOL_LOGGED:
                UNSUPPORTED_PROTOCOL_LOGGED = True
                described = SolarParser._describe_foreign_blocks(blocks)
                if described["recognised"] == 0:
                    log_kv(
                        "[UNSUPPORTED PROTOCOL]",
                        level="warning",
                        note="none of this device's blocks are ones this add-on decodes; it is not a supported inverter variant",
                        **described,
                    )
                else:
                    log_kv(
                        "[NO VALUES DECODED]",
                        level="warning",
                        note="blocks are recognised but yielded no values; they may be truncated",
                        **described,
                    )

            if LOG_UNPARSED_PUBLISH:
                log_payload_preview("[UNPARSED PAYLOAD: NO STATE]", payload_bytes, topic=source_topic, block_names=sorted(blocks.keys()))
            return False

        except Exception as exc:
            log_error_always(f"[PARSER ERROR] {exc}")
            if LOG_UNPARSED_PUBLISH:
                log_payload_preview("[PARSER ERROR PAYLOAD]", payload_bytes, topic=source_topic, error=str(exc))
            return False


