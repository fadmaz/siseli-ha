"""The MQTT protocol spoken by our impersonated cloud, on top of tcpstack.py.

Reuses the existing decode pipeline unchanged: once a PUBLISH frame's topic and
payload are pulled out of the reassembled byte stream, it goes straight into
SolarParser.parse_payload -- exactly what core.py's passive relay path already does
with payloads reconstructed from a sniffed TCP stream. Only how the bytes were
obtained differs; what they mean does not.

Protocol behaviour is inferred entirely from a real capture of the vendor cloud
(docs/architecture note: dongle_reboot.pcap, 2026-09-11) rather than from any spec
reading, so it only covers what that capture showed: CONNECT/CONNACK, SUBSCRIBE
(one filter, "dtu/<id>/sub/#") /SUBACK, PUBLISH/PUBLISH-reply at QoS 0, PINGREQ/
PINGRESP, DISCONNECT. Anything else (QoS 1/2, retained flag, will messages) is
accepted-and-ignored on the way in and never produced on the way out, because the
capture never exercised it.

The reply-PUBLISH shape is one generic rule, not per-topic special cases: every
inbound "dtu/<id>/pub/<kind>/<name>" the dongle sends gets an ack published to
"dtu/<id>/sub/<kind>/<name>_reply", echoing the envelope's own "c" and "t" fields,
a fresh random "s", "i" one higher than the request's, and "e":0. This one rule
covers dtu_prop_post, dev_prop_post and dev_event_post identically, because the
capture showed all three acked exactly that way with nothing topic-specific in the
reply's shape.

Deliberately not implemented: the periodic dev_rpc poll the real cloud sends every
~26s. The capture shows dev_prop_post already carries the same data on its own
schedule, so the dongle does not structurally need to be asked -- but whether it
tolerates never being asked at all across hours/days of runtime is untested. If
dongles are ever observed reconnecting in a loop or degrading after a long local-
cloud session, start here.
"""

import json
import os
import random
import re
import string
import time
from typing import Optional

from . import tcpstack
from .config import (
    LOG_MQTT_PAYLOAD_PREVIEW,
    LOG_MQTT_TOPICS,
    LOG_PACKETS,
    LOG_UNPARSED_PUBLISH,
    TELEMETRY_POLL_INTERVAL_SEC,
)
from .loggers import log, log_kv, log_payload_preview
from .parsers import SolarParser

MQTT_CONNECT = 1
MQTT_PUBLISH = 3
MQTT_SUBSCRIBE = 8
MQTT_PINGREQ = 12
MQTT_DISCONNECT = 14

_CONNECT_FLAG_USERNAME = 0x80
_CONNECT_FLAG_PASSWORD = 0x40
_CONNECT_FLAG_WILL = 0x04

_TOPIC_RE = re.compile(r"^dtu/([^/]+)/pub/([^/]+)/([^/]+)$")
_TOKEN_ALPHABET = string.ascii_letters + string.digits


def _random_token(length: int = 9) -> str:
    return "".join(random.choice(_TOKEN_ALPHABET) for _ in range(length))


def _decode_remaining_length(buf: bytes, offset: int):
    """Standard MQTT variable-byte integer. None if buf does not yet hold it all."""
    multiplier = 1
    value = 0
    i = offset
    while True:
        if i >= len(buf):
            return None
        b = buf[i]
        value += (b & 0x7F) * multiplier
        i += 1
        if not (b & 0x80):
            return value, i
        multiplier *= 128
        if multiplier > 128 ** 3:
            raise ValueError("MQTT remaining length field exceeds 4 bytes")


def _encode_remaining_length(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n % 128
        n //= 128
        if n > 0:
            b |= 0x80
        out.append(b)
        if n == 0:
            return bytes(out)


def _build_frame(control_byte: int, body: bytes) -> bytes:
    return bytes([control_byte]) + _encode_remaining_length(len(body)) + body


def extract_mqtt_frames(buffer: bytes):
    """(control_byte, body) for every complete frame at the front of buffer, plus
    whatever incomplete trailing bytes remain."""
    frames = []
    offset = 0
    while offset < len(buffer):
        if offset + 1 >= len(buffer):
            break
        control = buffer[offset]
        decoded = _decode_remaining_length(buffer, offset + 1)
        if decoded is None:
            break
        remaining_len, body_start = decoded
        frame_end = body_start + remaining_len
        if frame_end > len(buffer):
            break
        frames.append((control, buffer[body_start:frame_end]))
        offset = frame_end
    return frames, buffer[offset:]


def _log_connect(body: bytes) -> Optional[str]:
    """Parses and logs the CONNECT variable header. Returns the username -- which
    every capture so far shows is exactly the dtu_id -- so the caller can remember
    which device this connection belongs to (needed to address a dev_rpc poll at
    it later); None if parsing failed or no username was present."""
    try:
        proto_len = int.from_bytes(body[0:2], "big")
        offset = 2 + proto_len
        offset += 1  # protocol level
        connect_flags = body[offset]
        offset += 1
        offset += 2  # keep-alive

        client_id_len = int.from_bytes(body[offset:offset + 2], "big")
        offset += 2
        client_id = body[offset:offset + client_id_len].decode("utf-8", errors="replace")
        offset += client_id_len

        if connect_flags & _CONNECT_FLAG_WILL:
            will_topic_len = int.from_bytes(body[offset:offset + 2], "big")
            offset += 2 + will_topic_len
            will_payload_len = int.from_bytes(body[offset:offset + 2], "big")
            offset += 2 + will_payload_len

        username = None
        if connect_flags & _CONNECT_FLAG_USERNAME:
            user_len = int.from_bytes(body[offset:offset + 2], "big")
            offset += 2
            username = body[offset:offset + user_len].decode("utf-8", errors="replace")
            offset += user_len

        has_password = bool(connect_flags & _CONNECT_FLAG_PASSWORD)
        log(f"[LOCAL CLOUD] CONNECT client_id={client_id!r} username={username!r} password_present={has_password}")
        return username
    except Exception:
        log("[LOCAL CLOUD] CONNECT (could not parse variable header)", level="warning")
        return None


def _handle_subscribe(body: bytes) -> bytes:
    if len(body) < 2:
        return b""
    packet_id = body[0:2]
    offset = 2
    granted = bytearray()
    while offset + 2 <= len(body):
        topic_len = int.from_bytes(body[offset:offset + 2], "big")
        offset += 2 + topic_len
        if offset >= len(body):
            break
        offset += 1  # requested QoS byte, ignored -- everything here is QoS 0
        granted.append(0x00)
    if not granted:
        granted.append(0x00)
    return _build_frame(0x90, bytes(packet_id) + bytes(granted))


def _extract_envelope(payload: bytes) -> Optional[dict]:
    """The vendor's own PUBLISH payloads carry a stray leading byte before the JSON
    on every message observed so far (e.g. b'\\x00{"c":1,...' -- confirmed in a real
    capture's payload_hex, 2026-09-11), regardless of topic or QoS. parsers.py's
    SolarParser.parse_payload already tolerates this by searching for the first '{'
    rather than assuming payload[0] is one; mirrored here rather than assuming a
    clean payload, which is what silently produced an empty reply -- no ack, no
    error -- for every PUBLISH before this was found.
    """
    idx = payload.find(b"{")
    if idx == -1:
        return None
    raw = payload[idx:].decode("utf-8", errors="ignore")
    end = raw.rfind("}")
    if end != -1:
        raw = raw[: end + 1]
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


def _build_ack_publish(topic: str, payload: bytes) -> bytes:
    match = _TOPIC_RE.match(topic)
    if not match or not payload:
        return b""
    dtu_id, kind, name = match.groups()
    if name.endswith("_reply"):
        return b""

    envelope = _extract_envelope(payload)
    if envelope is None:
        return b""

    c = envelope.get("c")
    t = envelope.get("t")
    i = envelope.get("i")
    if c is None or t is None or not isinstance(i, int):
        return b""

    reply_topic = f"dtu/{dtu_id}/sub/{kind}/{name}_reply"
    reply_json = json.dumps(
        {"c": c, "t": t, "s": _random_token(), "i": i + 1, "e": 0},
        separators=(",", ":"),
    ).encode("utf-8")
    # The same leading \x00 every PUBLISH from this firmware carries before its own
    # JSON (see _extract_envelope) -- a real capture of the vendor cloud's own reply
    # to this exact message (2026-09-12) showed it one byte longer than ours and
    # carrying that same byte, confirming the dongle expects it symmetrically on
    # replies too. Without it here, the dongle retried dtu_prop_post three times
    # with backoff and never progressed to dev_prop_post.
    reply_body = b"\x00" + reply_json
    topic_bytes = reply_topic.encode("utf-8")
    publish_body = len(topic_bytes).to_bytes(2, "big") + topic_bytes + reply_body
    return _build_frame(0x30, publish_body)


_FORCE_RECONNECT_TEST = False  # Tested 2026-09-12: forcing a fresh MQTT session right
# after a dev_prop_post does NOT make the next one arrive sooner. First forced cycle
# came back in 51s, but that was residual from the dongle's own power-on boot
# sequence (a real physical power-cycle preceded it) -- the SECOND forced cycle, on an
# already-running dongle, took 355s, slower than the unforced steady-state cadence
# (exactly 300s, confirmed separately). The dongle's ~5 min report timer runs
# independently of the MQTT session and is not reset by reconnecting it. Left in place
# (default off) rather than deleted, in case a future firmware version changes this.


def _handle_publish(control: int, body: bytes, conn: tcpstack.Connection) -> bytes:
    qos = (control >> 1) & 0x03
    if len(body) < 2:
        return b""
    topic_len = int.from_bytes(body[0:2], "big")
    topic = body[2:2 + topic_len].decode("utf-8", errors="replace")
    offset = 2 + topic_len
    if qos > 0:
        offset += 2  # packet id -- never seen in the capture this is modelled on
    payload = body[offset:]

    if LOG_MQTT_TOPICS:
        log_kv("[LOCAL CLOUD PUBLISH]", topic=topic, payload_len=len(payload))
    if payload and LOG_MQTT_PAYLOAD_PREVIEW:
        log_payload_preview("[LOCAL CLOUD PAYLOAD]", payload, topic=topic)

    if payload:
        parsed_ok = SolarParser.parse_payload(payload, source_topic=topic)
        if not parsed_ok and LOG_UNPARSED_PUBLISH:
            log_payload_preview("[LOCAL CLOUD PAYLOAD NOT PARSED]", payload, topic=topic)
        if (
            _FORCE_RECONNECT_TEST
            and parsed_ok
            and topic.endswith("/dev_prop_post")
            and not topic.endswith("_reply")
        ):
            conn.force_close_after_reply = True
        if topic.endswith("/dev_rpc_reply"):
            # DIAGNOSTIC (2026-09-13): pairs with poll_due_connections' stall
            # detection -- marks this poll as answered so the next stall (if any)
            # gets logged fresh instead of being suppressed by an old flag.
            conn.poll_reply_count = getattr(conn, "poll_reply_count", 0) + 1
            conn.last_reply_ts = time.monotonic()
            conn.poll_stall_logged = False

    return _build_ack_publish(topic, payload)


def _handle_frame(control: int, body: bytes, conn: tcpstack.Connection) -> bytes:
    ptype = (control >> 4) & 0x0F

    if ptype == MQTT_CONNECT:
        dtu_id = _log_connect(body)
        if dtu_id and TELEMETRY_POLL_INTERVAL_SEC > 0:
            # Attributes on tcpstack.Connection itself rather than a side dict --
            # simplest way to remember which device a poll needs to be addressed
            # to without a second lookup structure to keep in sync with
            # tcpstack.CONNECTIONS' own lifecycle (creation, FIN/RST, sweep_stale).
            conn.dtu_id = dtu_id
            conn.next_poll_ts = time.monotonic() + TELEMETRY_POLL_INTERVAL_SEC
            # DIAGNOSTIC (2026-09-13): counters for the "poll stall" seen in
            # production on 2026-09-13 -- dev_rpc kept going out every 15s, cleanly
            # TCP-ACKed (no loss, no desync -- ruled out the 2026-09-12 TCP fix), but
            # dev_rpc_reply stopped after the very first success of the session. Not
            # yet known whether this is poll-count-triggered or time-triggered; these
            # let poll_due_connections log whichever it turns out to be, once, the
            # next time it happens, instead of re-diagnosing from raw captures again.
            conn.poll_sent_count = 0
            conn.poll_reply_count = 0
            conn.connect_ts = time.monotonic()
            conn.last_reply_ts = conn.connect_ts
            conn.poll_stall_logged = False
        return _build_frame(0x20, b"\x00\x00")  # CONNACK: no session present, accepted

    if ptype == MQTT_SUBSCRIBE:
        return _handle_subscribe(body)

    if ptype == MQTT_PUBLISH:
        return _handle_publish(control, body, conn)

    if ptype == MQTT_PINGREQ:
        return _build_frame(0xD0, b"")

    if ptype == MQTT_DISCONNECT:
        tcpstack.CONNECTIONS.pop((conn.local_ip, conn.local_port, conn.peer_ip, conn.peer_port), None)
        conn.closed = True
        return b""

    if LOG_PACKETS:
        log(f"[LOCAL CLOUD] unhandled MQTT type={ptype} body_len={len(body)}")
    return b""


def _build_dev_rpc_request(dtu_id: str, rpc_id: int) -> bytes:
    """The same request the real cloud sends every ~26s in a real capture
    (dtu/<id>/sub/service/dev_rpc, {"c":5,...,"i":501,"b":{}}) to force a full
    reading. The dongle's own dev_rpc_reply is a normal PUBLISH handled by
    _handle_publish like any other -- it is not acked further (name.endswith
    "_reply" in _build_ack_publish already excludes replying to a reply), so no
    other wiring is needed once this is sent.

    rpc_id: a live test sending every request with "i":501 got a bare TCP ACK
    and nothing else past the first 1-2 polls, which was first blamed on the
    dongle deduplicating by "i". The real capture refutes that: the vendor cloud
    sends "i":501 on EVERY poll (dongle_reboot.pcap, t=0s and t=25.4s, same id,
    answered both times). The silent-ACK symptom was our own missing TCP
    retransmission (tcpstack.py) -- one lost poll desynchronised our send stream
    and every later poll sat beyond the hole, never delivered to the dongle's
    application. Kept constant at 501 to match the real cloud byte-for-byte.
    What WAS real: the leading \x00 byte _build_ack_publish already prefixes onto
    every reply (see _extract_envelope's docstring -- confirmed on every
    PUBLISH this firmware sends or accepts, regardless of topic or QoS). This
    request path was the one place in the file still building a payload
    without it.
    """
    topic = f"dtu/{dtu_id}/sub/service/dev_rpc"
    payload = json.dumps(
        {"c": 5, "t": _random_token(8), "s": _random_token(), "i": rpc_id, "b": {}},
        separators=(",", ":"),
    ).encode("utf-8")
    body_with_marker = b"\x00" + payload
    topic_bytes = topic.encode("utf-8")
    body = len(topic_bytes).to_bytes(2, "big") + topic_bytes + body_with_marker
    return _build_frame(0x30, body)


# Confirmed working control commands (2026-09-13/14 investigation, see
# protocole-cloud-dongle/README.md section 6 for how each was captured from a real
# vendor-cloud session and validated by replaying it from this bridge with a
# physically-observed effect on the inverter). Each value is the raw serial command
# base64-encoded exactly as the real cloud sends it: `<mnemonic><state>` for the
# short (6-byte) family, or `PDAULC0<n>` for dual output, each with its own
# checksum baked in -- there is no need to compute one here.
CONTROL_COMMANDS = {
    "backlight": {"on": "UEV4U2gN", "off": "UER4YFkN"},
    "buzzer": {"on": "UEVh0HAN", "off": "UERh40EN"},
    # "Sortie double" (dual output): the real cloud has sent a DIFFERENT, broken
    # format (`DAULREMOTESW<n>`) since 2026-09-13, which fails even for the
    # official app -- always retried, never a real effect. This is the OLDER
    # format that worked once (2026-09-12) and still does when sent from here.
    "dual_output": {"on": "UERBVUxDMDGILw0=", "off": "UERBVUxDMDCYDg0="},
}
_CLEAR_FAULT_CODE_CI = "RkFVTFRDR6YN"

# The one thing that changed between a failed replay (2026-09-13, "i" picked
# arbitrarily) and a working one (2026-09-14, "i":503): every real capture of a
# command dev_rpc uses exactly this "i", never anything else. An arbitrary "i" got
# a bare TCP ACK and no further effect, identical in shape to the telemetry-poll
# dedup theory that _build_dev_rpc_request's docstring already found was false for
# polls -- but for commands specifically, "i" does appear to matter.
_CONTROL_RPC_ID = 503


def _first_established_connection() -> Optional[tcpstack.Connection]:
    for conn in list(tcpstack.CONNECTIONS.values()):
        if getattr(conn, "dtu_id", None) and not conn.closed:
            return conn
    return None


def _send_control_ci(ci_b64: str) -> bool:
    conn = _first_established_connection()
    if conn is None:
        log("[CONTROL] no established local-cloud connection to send on", level="warning")
        return False
    topic = f"dtu/{conn.dtu_id}/sub/service/dev_rpc"
    payload = json.dumps(
        {
            "c": 5,
            "t": _random_token(8),
            "s": _random_token(),
            "i": _CONTROL_RPC_ID,
            "b": {"ci": ci_b64, "no": 0, "rs": 0},
        },
        separators=(",", ":"),
    ).encode("utf-8")
    topic_bytes = topic.encode("utf-8")
    body = len(topic_bytes).to_bytes(2, "big") + topic_bytes + b"\x00" + payload
    conn.reply(_build_frame(0x30, body))
    return True


def send_control_switch(setting: str, turn_on: bool) -> bool:
    """Send one of CONTROL_COMMANDS. Returns False (logged) if there is no
    established local-cloud connection right now -- the caller (mqtt.py's command
    handler) is expected to leave the HA switch's state untouched in that case
    rather than report a success that did not happen."""
    commands = CONTROL_COMMANDS.get(setting)
    if commands is None:
        log(f"[CONTROL] unknown setting {setting!r}", level="error")
        return False
    ci = commands["on" if turn_on else "off"]
    ok = _send_control_ci(ci)
    if ok:
        log(f"[CONTROL] {setting} -> {'on' if turn_on else 'off'}")
    return ok


def send_clear_fault_code() -> bool:
    ok = _send_control_ci(_CLEAR_FAULT_CODE_CI)
    if ok:
        log("[CONTROL] clear fault code sent")
    return ok


def send_manual_refresh() -> bool:
    """On-demand telemetry poll -- the exact same i=501, empty-body dev_rpc
    request poll_due_connections sends on its own timer. Confirmed byte-for-byte
    identical to what the vendor app's own "refresh" pull sends (packet capture
    2026-09-14, see protocole-cloud-dongle/README.md): {"i":501,"b":{}}. Exposed
    as a button so a stalled poll cycle can be kicked without waiting for the
    next scheduled tick, and to let the state_diff/state_snapshot debug logs show
    exactly what a manual refresh actually returns."""
    conn = _first_established_connection()
    if conn is None:
        log("[CONTROL] no established local-cloud connection to send on", level="warning")
        return False
    try:
        conn.reply(_build_dev_rpc_request(conn.dtu_id, 501))
        log(f"[CONTROL] manual refresh sent to {conn.peer_ip}:{conn.peer_port} i=501")
        return True
    except Exception as exc:
        log(f"[CONTROL] manual refresh failed: {exc}", level="error")
        return False


_PROBE_FILE = "/tmp/probe.json"


def _send_probe_if_present() -> None:
    """One-shot experimental probe (2026-09-12): drop a JSON file at /tmp/probe.json
    inside the container -- {"service": "dev_rpc", "payload": {...}} -- and the next
    1 s tick publishes it once to dtu/<id>/sub/service/<service> on the first
    established connection, then deletes the file. Replies (any topic) appear via
    the normal PUBLISH logging. Read-only investigation tool; never ships enabled
    because nothing creates the file."""
    try:
        with open(_PROBE_FILE, "r", encoding="utf-8") as fh:
            spec = json.load(fh)
    except FileNotFoundError:
        return
    except Exception as exc:
        log(f"[PROBE] unreadable {_PROBE_FILE}: {exc}", level="error")
        try:
            os.remove(_PROBE_FILE)
        except OSError:
            pass
        return
    try:
        os.remove(_PROBE_FILE)
    except OSError:
        pass
    service = spec.get("service", "dev_rpc")
    payload_obj = spec.get("payload", {})
    for conn in list(tcpstack.CONNECTIONS.values()):
        dtu_id = getattr(conn, "dtu_id", None)
        if dtu_id is None or conn.closed:
            continue
        topic = f"dtu/{dtu_id}/sub/service/{service}"
        body = json.dumps(payload_obj, separators=(",", ":")).encode("utf-8")
        topic_bytes = topic.encode("utf-8")
        frame = _build_frame(0x30, len(topic_bytes).to_bytes(2, "big") + topic_bytes + b"\x00" + body)
        conn.reply(frame)
        log(f"[PROBE] sent topic={topic} payload={body.decode()}")
        return
    log("[PROBE] no established connection to send on", level="warning")


def poll_due_connections() -> None:
    """Called periodically from core.py's health_logger tick. Sends a dev_rpc
    request to every established local-cloud connection whose interval has
    elapsed. A no-op when TELEMETRY_POLL_INTERVAL_SEC is 0 (nothing ever sets
    conn.next_poll_ts in that case, so the getattr below finds nothing to do)."""
    _send_probe_if_present()
    if TELEMETRY_POLL_INTERVAL_SEC <= 0:
        return
    now = time.monotonic()
    for conn in list(tcpstack.CONNECTIONS.values()):
        dtu_id = getattr(conn, "dtu_id", None)
        next_poll_ts = getattr(conn, "next_poll_ts", None)
        if dtu_id is None or next_poll_ts is None or conn.closed:
            continue
        if now < next_poll_ts:
            continue
        conn.next_poll_ts = now + TELEMETRY_POLL_INTERVAL_SEC

        # DIAGNOSTIC (2026-09-13): if every poll sent so far has NOT been answered
        # (sent > replied), the previous poll(s) stalled -- log it once, with the
        # numbers needed to tell a count-triggered stall from a time-triggered one,
        # rather than re-deriving this from a tcpdump capture each time it recurs.
        sent = getattr(conn, "poll_sent_count", 0)
        replied = getattr(conn, "poll_reply_count", 0)
        if sent > replied and not getattr(conn, "poll_stall_logged", False):
            since_reply = now - getattr(conn, "last_reply_ts", now)
            since_connect = now - getattr(conn, "connect_ts", now)
            log(
                f"[LOCAL CLOUD DIAG] poll stall {conn.peer_ip}:{conn.peer_port}: "
                f"{sent - replied} poll(s) unanswered since last dev_rpc_reply, "
                f"{since_reply:.0f}s since last reply, {since_connect:.0f}s since "
                f"CONNECT, {sent} sent total, {replied} replied total",
                level="warning",
            )
            conn.poll_stall_logged = True

        conn.poll_sent_count = sent + 1
        try:
            # "i":501 on every poll, exactly like the real cloud (see
            # _build_dev_rpc_request's docstring).
            conn.reply(_build_dev_rpc_request(dtu_id, 501))
            if LOG_PACKETS:
                log(f"[LOCAL CLOUD] dev_rpc poll sent to {conn.peer_ip}:{conn.peer_port} i=501")
        except Exception as exc:
            log(f"[LOCAL CLOUD ERROR] dev_rpc poll failed: {exc}", level="error")


def _on_established(conn: tcpstack.Connection) -> None:
    log(f"[LOCAL CLOUD] TCP connected from {conn.peer_ip}:{conn.peer_port}")


def _on_data(conn: tcpstack.Connection) -> None:
    frames, conn.recv_buffer = extract_mqtt_frames(conn.recv_buffer)
    if not frames:
        return
    reply = b"".join(_handle_frame(control, body, conn) for control, body in frames)
    if not conn.closed:
        conn.reply(reply)
    if _FORCE_RECONNECT_TEST and getattr(conn, "force_close_after_reply", False) and not conn.closed:
        # TEMPORARY test (2026-09-12): does forcing a fresh MQTT session right after a
        # spontaneous dev_prop_post make the dongle's NEXT one arrive sooner than its
        # own steady 5 min cadence (confirmed exactly 300s apart, unforced, same
        # session)? One data point so far (connect-to-first-push = 53s once) is not
        # enough to tell a connect-reset timer from a lucky phase coincidence.
        key = (conn.local_ip, conn.local_port, conn.peer_ip, conn.peer_port)
        log(f"[LOCAL CLOUD TEST] forcing reconnect after dev_prop_post to measure recovery time ({conn.peer_ip}:{conn.peer_port})")
        conn.close(reason="forced test reconnect")
        tcpstack.CONNECTIONS.pop(key, None)


def handle_packet(pkt, local_ip: str, local_port: int, peer_mac: str, iface: Optional[str]) -> None:
    tcpstack.handle_tcp(
        pkt,
        local_ip=local_ip,
        local_port=local_port,
        peer_mac=peer_mac,
        iface=iface,
        on_established=_on_established,
        on_data=_on_data,
    )
