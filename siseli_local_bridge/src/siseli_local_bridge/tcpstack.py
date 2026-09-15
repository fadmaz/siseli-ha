"""A minimal userspace TCP responder, built on raw L2 send/capture.

Exists because firewall.py's INPUT DROP rule means no normal bind()/listen() socket
can ever see traffic addressed to LOCAL_CLOUD_IP:LOCAL_CLOUD_PORT -- the kernel drops
it before any socket layer runs. The only way left to answer it is to speak just
enough TCP ourselves, the same way core.py already speaks just enough ARP.

Deliberately not a general-purpose TCP stack. What it does not do, on purpose:
  - No out-of-order handling. A segment that does not extend the buffer exactly at
    its expected sequence number is treated as a duplicate: re-ACK the current
    cumulative position, do not touch the buffer. If it was genuinely new data
    arriving early, the peer's own retransmission timer resends it once earlier
    bytes are missing for long enough, and it will then land in order.
  - No congestion control, no window scaling, no SACK. Nothing here ever needs to
    move more than a few hundred bytes per connection.

One Connection per (peer_ip, peer_port); LOCAL_CLOUD_IP:LOCAL_CLOUD_PORT is constant
and implicit.
"""

import random
import threading
import time
from typing import Callable, Dict, List, Optional, Tuple

from scapy.all import IP, TCP, Ether, Raw, sendp  # type: ignore

from .loggers import log

TCP_FIN = 0x01
TCP_SYN = 0x02
TCP_RST = 0x04
TCP_PSH = 0x08
TCP_ACK = 0x10

STALE_CONNECTION_SEC = 300

# Retransmission of our own data segments. Originally omitted ("one LAN hop, tiny
# payloads, losses are rare"), but the dongle end is Wi-Fi and a real consequence was
# observed (2026-09-12): one lost dev_rpc poll permanently desynchronises our send
# stream -- every later poll sits beyond the hole, the dongle's stack never delivers
# it to its application and only re-ACKs the old position, which looked exactly like
# "the dongle stops answering polls after the first 1-2" in every live test.
RETRANSMIT_TIMEOUT_SEC = 1.0
RETRANSMIT_MAX_TRIES = 5


def _seq_lte(a: int, b: int) -> bool:
    """a <= b in RFC 1982 32-bit serial arithmetic."""
    return ((b - a) & 0xFFFFFFFF) < 0x80000000


class Connection:
    def __init__(self, peer_ip: str, peer_port: int, local_ip: str, local_port: int, peer_mac: str, iface: Optional[str]):
        self.peer_ip = peer_ip
        self.peer_port = peer_port
        self.local_ip = local_ip
        self.local_port = local_port
        self.peer_mac = peer_mac
        self.iface = iface

        self.our_seq = random.randint(0, 0xFFFFFFFF)
        self._syn_seq: Optional[int] = None  # set by accept(); lets resend_synack() be exact
        self.their_isn: Optional[int] = None  # the exact SYN.seq this connection was accept()ed with
        self.their_next_seq: Optional[int] = None  # set once the SYN is seen
        self.established = False
        self.closed = False
        self.recv_buffer = b""
        self.last_activity = time.monotonic()
        # Sniffer thread, telemetry poll thread and retransmit tick all send on the
        # same connection; our_seq/_inflight updates must not interleave.
        self.lock = threading.RLock()
        # Unacked data segments: [seq, payload, tries, next_retx_ts]
        self._inflight: List[list] = []

    def _emit(self, flags: int, payload: bytes, seq: int, options=None) -> None:
        """Put one segment on the wire at an explicit sequence number."""
        tcp_kwargs = dict(
            sport=self.local_port,
            dport=self.peer_port,
            seq=seq,
            ack=self.their_next_seq or 0,
            flags=flags,
            window=65535,
        )
        if options:
            tcp_kwargs["options"] = options
        frame = Ether(dst=self.peer_mac) / IP(src=self.local_ip, dst=self.peer_ip) / TCP(**tcp_kwargs)
        if payload:
            frame = frame / Raw(load=payload)
        try:
            if self.iface:
                sendp(frame, verbose=False, iface=self.iface)
            else:
                sendp(frame, verbose=False)
        except Exception as exc:
            log(f"[LOCAL CLOUD] send failed {self.peer_ip}:{self.peer_port}: {exc}", level="error")

    def _send(self, flags: int, payload: bytes = b"", options=None) -> None:
        with self.lock:
            seq = self.our_seq
            if payload:
                # Queued before the send attempt: a sendp() failure is then just the
                # first "loss" and the retransmit tick covers it like any other.
                self._inflight.append(
                    [seq, payload, 0, time.monotonic() + RETRANSMIT_TIMEOUT_SEC]
                )
                self.our_seq = (self.our_seq + len(payload)) & 0xFFFFFFFF
            self._emit(flags, payload, seq, options=options)

    def note_ack(self, ack: int) -> None:
        """Drop inflight segments fully covered by the peer's cumulative ACK."""
        with self.lock:
            self._inflight = [
                seg for seg in self._inflight
                if not _seq_lte((seg[0] + len(seg[1])) & 0xFFFFFFFF, ack)
            ]

    def retransmit_due(self, now: float) -> None:
        """Resend unacked segments past their deadline; give up after MAX_TRIES."""
        with self.lock:
            if self.closed or not self._inflight:
                return
            for seg in self._inflight:
                seq, payload, tries, next_ts = seg
                if now < next_ts:
                    continue
                if tries >= RETRANSMIT_MAX_TRIES:
                    # The peer is gone or unreachable; keeping the connection alive
                    # just hides it. Drop state so its next SYN starts clean.
                    log(
                        f"[LOCAL CLOUD] {self.peer_ip}:{self.peer_port} gave up "
                        f"retransmitting {len(payload)}B seq={seq} after {tries} tries",
                        level="warning",
                    )
                    self._inflight.clear()
                    self.close("retransmit exhausted")
                    CONNECTIONS.pop((self.local_ip, self.local_port, self.peer_ip, self.peer_port), None)
                    return
                seg[2] = tries + 1
                seg[3] = now + RETRANSMIT_TIMEOUT_SEC * (2 ** seg[2])
                log(
                    f"[LOCAL CLOUD] {self.peer_ip}:{self.peer_port} retransmit "
                    f"{len(payload)}B seq={seq} (try {seg[2]})",
                    level="warning",
                )
                self._emit(TCP_ACK | TCP_PSH, payload, seq)

    def accept(self, their_isn: int) -> None:
        """Handle an inbound SYN: reply SYN-ACK. their_isn is the SYN's seq field."""
        self.their_isn = their_isn
        self.their_next_seq = (their_isn + 1) & 0xFFFFFFFF
        self._syn_seq = self.our_seq  # remembered so resend_synack() can be exact
        # MSS matches what the real cloud advertised in the captured session
        # (docs/... capture, 2026-09-11): informational only, since every payload we
        # send here is far smaller than either side's MSS.
        self._send(TCP_SYN | TCP_ACK, options=[("MSS", 1412)])
        self.our_seq = (self.our_seq + 1) & 0xFFFFFFFF  # the SYN itself consumes one

    def resend_synack(self) -> None:
        """Retransmit the exact SYN-ACK already sent for this connection -- same
        sequence number, not a fresh one -- without disturbing our_seq/
        their_next_seq. Used for a duplicate SYN carrying the same ISN as the one
        accept() already handled (see handle_tcp) -- every dongle capture so far
        sends its SYN twice, and handling the duplicate as a brand new connection
        would hand it a second, different random ISN. If the dongle then ACKs the
        first one it saw, our own bookkeeping is left tracking a their_next_seq
        that no longer matches what accept() actually recorded, which does not
        fail loudly: receive() just treats every following segment as stale and
        silently re-ACKs instead of processing it, forever. A live dongle capture
        on port 80 (2026-09-12) matched this exactly: full 3-way handshake, then
        only zero-length keepalive probes and never the request payload itself,
        for every connection attempt, until it gave up.
        """
        with self.lock:
            self._emit(TCP_SYN | TCP_ACK, b"", self._syn_seq, options=[("MSS", 1412)])

    def ack_only(self) -> None:
        self._send(TCP_ACK)

    def reply(self, payload: bytes) -> None:
        """Send application data, ACKing everything received so far in the same segment."""
        if not payload:
            self.ack_only()
            return
        self._send(TCP_ACK | TCP_PSH, payload=payload)

    def receive(self, seq: int, payload: bytes) -> bool:
        """Feed an inbound data segment. Returns True if it extended recv_buffer."""
        self.last_activity = time.monotonic()
        if self.their_next_seq is None:
            return False
        if seq != self.their_next_seq:
            # Duplicate (old) or a gap (early) -- see module docstring. Either way,
            # re-assert what we actually have; do not touch the buffer.
            if payload:
                # DIAGNOSTIC (2026-09-12): a real payload landing here, not just a
                # bare/duplicate ACK, is exactly what the SYN-replacement scenario
                # documented in handle_tcp would produce -- the peer's actual HTTP/
                # MQTT request, silently re-ACKed instead of read, every time, until
                # it gives up. Empty-payload mismatches are normal traffic and not
                # logged; this branch should stay quiet unless that bug is real.
                log(
                    f"[LOCAL CLOUD DIAG] {self.peer_ip}:{self.peer_port} dropped "
                    f"{len(payload)}B: seq={seq} != expected={self.their_next_seq} "
                    "-- payload discarded, peer will see only a re-ACK",
                    level="warning",
                )
            self.ack_only()
            return False
        self.recv_buffer += payload
        self.their_next_seq = (self.their_next_seq + len(payload)) & 0xFFFFFFFF
        return True

    def close(self, reason: str = "") -> None:
        if self.closed:
            return
        self.closed = True
        self._inflight.clear()
        try:
            self._send(TCP_FIN | TCP_ACK)
        except Exception:
            pass
        if reason:
            log(f"[LOCAL CLOUD] closed {self.peer_ip}:{self.peer_port} ({reason})")


ConnHandler = Callable[[Connection], None]
DataHandler = Callable[[Connection], None]

CONNECTIONS: Dict[Tuple[str, int], Connection] = {}


def handle_tcp(
    pkt,
    local_ip: str,
    local_port: int,
    peer_mac: str,
    iface: Optional[str],
    on_established: ConnHandler,
    on_data: DataHandler,
) -> None:
    """Dispatch one inbound TCP segment addressed to local_ip:local_port.

    on_established(conn) fires once, right after the handshake completes.
    on_data(conn) fires whenever receive() actually extended recv_buffer -- the
    caller (fakecloud.py) is responsible for consuming whatever complete MQTT
    frames that buffer now holds and calling conn.reply(...) with any response.
    """
    peer_ip = pkt[IP].src
    peer_port = int(pkt[TCP].sport)
    flags = int(pkt[TCP].flags)
    # Includes local_ip AND local_port: fakecloud.py (1883) and httpstub.py (80) share
    # this same CONNECTIONS dict, several local IPs can serve the same port
    # (LOCAL_CLOUD_IP, HTTP_STUB_REAL_IPS, TARGET_HOST for cached-IP MQTT), and
    # nothing stops a dongle from reusing the same ephemeral source port toward two
    # of them at once. A narrower key would let one flow overwrite the other's state.
    key = (local_ip, local_port, peer_ip, peer_port)

    if flags & TCP_RST:
        existing = CONNECTIONS.get(key)
        if existing is None:
            return
        # RFC 5961-style validation: only honour an RST whose seq matches exactly
        # what we expect next from the peer. Without this, a stale RST -- e.g. the
        # peer's answer to an old SYN-ACK of a connection it already abandoned --
        # tears down the CURRENT connection for the same (port, peer) key, and the
        # peer's live request is then silently ignored until its own ~75s timeout.
        rst_seq = int(pkt[TCP].seq)
        if existing.their_next_seq is not None and rst_seq != existing.their_next_seq:
            log(
                f"[LOCAL CLOUD] {peer_ip}:{peer_port} stale RST ignored: "
                f"seq={rst_seq} != expected={existing.their_next_seq}",
                level="warning",
            )
            return
        CONNECTIONS.pop(key, None)
        # Previously silent: a live session (2026-09-12) went dark for ~7 minutes
        # between its last decoded reading and the dongle's next reconnect, with
        # no FIN/RST ever logged for the old connection -- impossible to tell
        # apart from "the dongle just stopped talking" without this. If it is
        # an RST killing sessions early, this is the only place that would show it.
        log(f"[LOCAL CLOUD] {peer_ip}:{peer_port} sent RST (was established={existing.established})")
        return

    if (flags & TCP_SYN) and not (flags & TCP_ACK):
        incoming_isn = int(pkt[TCP].seq)
        existing = CONNECTIONS.get(key)
        if existing is not None and not existing.closed and existing.their_isn == incoming_isn:
            # The same SYN, seen again -- every dongle capture so far sends it
            # twice. Resend the identical SYN-ACK rather than accept()ing a fresh
            # Connection with a new random ISN (see resend_synack's docstring).
            existing.resend_synack()
            return
        if existing is not None and not existing.closed and existing.their_isn != incoming_isn:
            if existing.established:
                # RFC 793/5961: a SYN with a new ISN on an ESTABLISHED connection must
                # not silently replace it -- the peer may still be speaking on the old
                # handshake, and discarding their_next_seq would turn every one of its
                # following segments into a "duplicate" forever (bootstrap attempts
                # observed RST-ing after a fixed ~75s, 2026-09-12). Send a challenge
                # ACK instead: a peer that genuinely restarted its socket answers with
                # a valid RST (seq matching their_next_seq), which frees the slot above
                # and lets its retransmitted SYN through cleanly.
                log(
                    f"[LOCAL CLOUD] {peer_ip}:{peer_port} SYN isn={incoming_isn} on "
                    f"established isn={existing.their_isn} -- challenge-ACK sent, "
                    "state kept",
                    level="warning",
                )
                existing.ack_only()
                return
            # Not yet established: the old handshake never completed, so nothing is
            # lost by starting over with the new ISN (the dongle abandoned it).
            log(
                f"[LOCAL CLOUD] {peer_ip}:{peer_port} new SYN isn={incoming_isn} "
                f"replaces unestablished isn={existing.their_isn}",
                level="warning",
            )
        conn = Connection(peer_ip, peer_port, local_ip, local_port, peer_mac, iface)
        CONNECTIONS[key] = conn
        conn.accept(incoming_isn)
        return

    conn = CONNECTIONS.get(key)
    if conn is None:
        # Nothing we opened -- ignore rather than RST, which would just invite the
        # peer to retry immediately.
        return

    if flags & TCP_FIN:
        conn.receive(int(pkt[TCP].seq), b"")  # advance past the FIN's own sequence slot if in order
        conn.their_next_seq = ((conn.their_next_seq or int(pkt[TCP].seq)) + 1) & 0xFFFFFFFF
        conn.close("peer FIN")
        CONNECTIONS.pop(key, None)
        return

    if flags & TCP_ACK:
        conn.note_ack(int(pkt[TCP].ack))

    if not conn.established:
        conn.established = True
        on_established(conn)

    if Raw in pkt:
        payload = bytes(pkt[Raw].load)
        if payload and conn.receive(int(pkt[TCP].seq), payload):
            on_data(conn)


def retransmit_tick() -> None:
    """Resend every overdue unacked segment across all connections. Called every
    second from core.py's local-cloud maintenance thread."""
    now = time.monotonic()
    for conn in list(CONNECTIONS.values()):
        conn.retransmit_due(now)


def sweep_stale(max_age_sec: int = STALE_CONNECTION_SEC) -> int:
    """Drop connections that have gone quiet. Returns how many were removed.

    Nothing here ever sends a keepalive-triggered close, so this is the only thing
    that reclaims state for a dongle that vanished (power loss, Wi-Fi drop) without
    a FIN/RST ever arriving.
    """
    now = time.monotonic()
    stale = [key for key, conn in CONNECTIONS.items() if now - conn.last_activity > max_age_sec]
    for key in stale:
        CONNECTIONS.pop(key, None)
    return len(stale)
