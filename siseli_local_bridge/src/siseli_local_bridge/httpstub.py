"""A minimal HTTP/1.1 server for the dongle's cloud-discovery bootstrap API.

Discovered by capturing a real dongle's traffic (2026-09-12) after fakecloud.py's
MQTT side was already working end-to-end but the dongle kept retrying its identity
announcement (dtu_prop_post) and never progressed to telemetry: the health log
showed repeated failed connections to a THIRD-PARTY host on port 80, which turned
out to be "dtu.access.solar.siseli.com" -- called over plain HTTP, on every boot,
BEFORE the dongle even knows which MQTT broker hostname to resolve. Fully offline
operation needs this answered locally too, or the dongle never learns a broker to
connect to at all.

Three requests were observed, always in this order, over one keep-alive TCP
connection:

  1. POST /dtu/checkin        {"model":..., "firmwareVerCode":..., "firmwareVerText":...}
  2. POST /dtu/devices/findSingle   {"slaveAddr":"","cmdNo":...,"cmdOut":...,"gatherProtocolNo":...,"gatherProtocolVerCode":...}
  3. GET  /dtu/servers/mqtt   -- this response's "data.main.host" is what the dongle
                                 then resolves and connects its MQTT session to.

All three carry `Authorization: Basic base64(username:password)` where username and
password are the exact same dtu_id/password pair seen in the MQTT CONNECT
(fakecloud.py's _log_connect) -- not validated here, since this add-on IS the
authority being asked, not a client proving itself to one.

The JSON response bodies below are replayed near-verbatim from that real capture,
against the real vendor cloud, for this exact device. They are not derived from any
spec; anything the dongle does not appear to need for these three calls specifically
was left out or hardcoded rather than guessed at.
"""

import json
import time
from typing import Optional

from . import tcpstack
from .config import DEV_SN, MQTT_BROKER_HOSTNAME
from .loggers import log

_HEADER_END = b"\r\n\r\n"


def _extract_request(buffer: bytes):
    """One parsed HTTP request from the front of buffer, plus what is left over.
    Returns (None, buffer) if buffer does not yet hold a complete request."""
    header_end = buffer.find(_HEADER_END)
    if header_end == -1:
        return None, buffer

    lines = buffer[:header_end].split(b"\r\n")
    try:
        method, path, _version = lines[0].decode("ascii", errors="replace").split(" ", 2)
    except ValueError:
        # Not an HTTP request line at all -- discard the buffer rather than spin on
        # the same unparseable prefix forever.
        return None, b""

    headers = {}
    for line in lines[1:]:
        if b":" not in line:
            continue
        key, _, value = line.partition(b":")
        headers[key.decode("ascii", "ignore").strip().lower()] = value.decode("ascii", "ignore").strip()

    body_start = header_end + len(_HEADER_END)
    content_length = int(headers.get("content-length", "0") or "0")
    if len(buffer) < body_start + content_length:
        return None, buffer  # headers arrived, body still incomplete

    body = buffer[body_start:body_start + content_length]
    remainder = buffer[body_start + content_length:]
    return {"method": method, "path": path, "headers": headers, "body": body}, remainder


def _json_response(payload: dict) -> bytes:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    header = (
        "HTTP/1.1 200 OK\r\n"
        "Server: nginx/1.20.1\r\n"
        "Content-Type: application/json;charset=UTF-8\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Connection: keep-alive\r\n"
        "Cache-Control: no-cache\r\n"
        "\r\n"
    ).encode("ascii")
    return header + body


def _handle_checkin() -> bytes:
    now_ms = int(time.time() * 1000)
    return _json_response({
        "code": 0,
        "msg": "Success",
        "data": {
            "timeInText": time.strftime("%Y-%m-%dT%H:%M:%S.000+00:00", time.gmtime()),
            "timeInMills": str(now_ms),
            "utcOffset": "+00:00",
            "outbound": 1,
            "performanceReportPeriod": 180000,
            "firmware": None,
            "gatherProtocol": {
                "autoAdaptive": 0,
                "verCode": 42,
                "verText": "1.0.0",
                "verState": 10,
                "protocolNo": "MH2089635",
                "forceUpdate": 0,
            },
        },
    })


def _handle_find_single() -> bytes:
    # DEV_SN (config.py) replaces what a real capture showed for one specific
    # device. Never seen a second device to infer a general derivation rule from,
    # so this is a configurable literal rather than something computed from the
    # dtu_id -- fine for a single-inverter add-on; would need revisiting to
    # support more than one.
    return _json_response({"code": 0, "msg": "Success", "data": {"devSN": DEV_SN}})


def _handle_servers_mqtt() -> bytes:
    server = {"host": MQTT_BROKER_HOSTNAME, "port": 1883, "ssl": 0, "sslCertificate": None}
    return _json_response({"code": 0, "msg": "Success", "data": {"main": server, "mains": [server]}})


_ROUTES = {
    ("POST", "/dtu/checkin"): _handle_checkin,
    ("POST", "/dtu/devices/findSingle"): _handle_find_single,
    ("GET", "/dtu/servers/mqtt"): _handle_servers_mqtt,
}


def _handle_request(request: dict) -> bytes:
    key = (request["method"], request["path"])
    handler = _ROUTES.get(key)
    if handler is None:
        log(f"[HTTP STUB] unhandled {request['method']} {request['path']}", level="warning")
        return b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: keep-alive\r\n\r\n"
    log(f"[HTTP STUB] {request['method']} {request['path']}")
    return handler()


def _on_established(conn: tcpstack.Connection) -> None:
    log(f"[HTTP STUB] TCP connected from {conn.peer_ip}:{conn.peer_port}")


def _on_data(conn: tcpstack.Connection) -> None:
    replies = []
    while True:
        request, conn.recv_buffer = _extract_request(conn.recv_buffer)
        if request is None:
            break
        replies.append(_handle_request(request))
    if replies and not conn.closed:
        conn.reply(b"".join(replies))


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
