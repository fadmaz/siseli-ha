import json
import logging
import os
import signal
import sys
import threading
import time
import warnings
from typing import Optional

from scapy.all import (  # type: ignore
    ARP,
    IP,
    TCP,
    UDP,
    AsyncSniffer,
    Ether,
    Raw,
    conf,
    get_if_hwaddr,
    getmacbyip,
    sendp,
)

from .config import *
from .loggers import log, log_kv, log_payload_preview
from .sensors import SENSORS, UNDECODED_SENSOR_KEYS
from . import state as _state
from .mqtt import broker_is_connected, client, publish_availability, start_mqtt
from .parsers import (
    SEEN_MQTT_TOPICS,
    SolarParser,
    append_stream_data,
    drop_flow,
    extract_publish_payload,
    heartbeat_due,
    mqtt_type_name,
    republish_state,
    reset_flow,
    restore_energy_clocks,
)
from .version import __version__ as VERSION

warnings.filterwarnings("ignore", category=DeprecationWarning)
#: Consecutive capture restarts before the bridge gives up and stops. Consecutive, so a
#: rare transient is forgiven while a fault that recurs on the next 10 s tick is not.
CAPTURE_RESTART_LIMIT = 3
CAPTURE_FAILURES = 0


class _LastScapyWarning(logging.Handler):
    """Keeps the most recent scapy warning so a dead capture thread can say why.

    scapy handles an exception escaping the sniff callback by closing the capture
    socket and emitting a warning, then returning normally -- so AsyncSniffer.exception
    is None and that warning is the only account of the cause. Muting scapy.runtime
    entirely, which this module used to do, threw it away.
    """

    def __init__(self):
        super().__init__(level=logging.WARNING)
        self.last: Optional[str] = None

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.last = record.getMessage()
        except Exception:
            pass


SCAPY_WARNINGS = _LastScapyWarning()
_scapy_runtime = logging.getLogger("scapy.runtime")
# WARNING rather than ERROR so the handler above sees the message; the logger has no
# other handler of its own, so nothing extra reaches the add-on log.
_scapy_runtime.setLevel(logging.WARNING)
_scapy_runtime.addHandler(SCAPY_WARNINGS)
_scapy_runtime.propagate = False

def norm_mac(mac: Optional[str]) -> Optional[str]:
    if not mac:
        return None
    return mac.strip().lower().replace("-", ":")

def send_layer2(frame, iface: Optional[str] = None) -> None:
    if iface:
        sendp(frame, verbose=False, iface=iface)
    else:
        sendp(frame, verbose=False)

INV_MAC: Optional[str] = None
RTR_MAC: Optional[str] = None
sniffer: Optional[AsyncSniffer] = None

from .config import STATE_CACHE_FILE


ENERGY_COUNTER_KEYS = (
    "c_battery_charge_energy_kwh",
    "c_battery_discharge_energy_kwh",
    "c_generation_energy_kwh",
    "c_grid_import_energy_kwh",
    "c_load_energy_kwh",
)


def load_cached_state(path: str = STATE_CACHE_FILE) -> None:
    """Restore LAST_STATE from disk. Called from __main__ after validate_config(),
    which is what creates the /data directory."""
    try:
        if os.path.exists(path):
            with open(path, "r") as f:
                cached = json.load(f)
            if not isinstance(cached, dict):
                log(f"[CACHE] Ignoring {path}: expected an object, got {type(cached).__name__}", level="error")
                return

            # The integrator's clocks travel in the same record as the counters they
            # gate. Taken out first, so the filters below never see the key.
            clocks_record = cached.pop(_state.ENERGY_CLOCKS_CACHE_KEY, None)

            # The energy counters are state_class: total_increasing, so a corrupt or
            # negative value can never correct itself downward. Drop those rather
            # than restoring them.
            dropped = []
            for key in ENERGY_COUNTER_KEYS:
                value = cached.get(key)
                if value is None:
                    continue
                if not isinstance(value, (int, float)) or value < 0 or value != value or value in (float("inf"), float("-inf")):
                    dropped.append(key)
                    cached.pop(key, None)
            if dropped:
                log(f"[CACHE] Dropped invalid energy counters: {', '.join(dropped)}", level="warning")

            # Values the parser can no longer produce. Without this they survive in
            # the cache indefinitely and are republished on every start, so removing
            # a fabricated sensor from the code does not remove it from anyone's
            # dashboard.
            stale = [key for key in UNDECODED_SENSOR_KEYS if cached.get(key) is not None]
            for key in stale:
                cached.pop(key, None)
            if stale:
                log(
                    f"[CACHE] Discarded {len(stale)} cached values with no decode path "
                    f"(e.g. {', '.join(sorted(stale)[:3])}); they will read unknown",
                    level="warning",
                )

            # Keys this build no longer defines at all. The purge above only covers
            # keys listed as undecodable, and that list is required to name registered
            # sensors -- so a key deleted from SENSORS outright had no purge path: it
            # was restored, merged into LAST_STATE, and republished in the retained
            # group payload forever, because publish_grouped_state iterates the payload
            # rather than the registry. c_bms_remaining_capacity_ah was deleted in
            # 2.5.17 and was still being republished thirteen releases later, and the
            # dbg_*_raw family still carried block text from a months-old session.
            # Every key the parser writes is registered, so "not in SENSORS" is exactly
            # "not produced by this build" -- no maintained list is needed.
            removed = [key for key in cached if key not in SENSORS]
            for key in removed:
                cached.pop(key, None)
            if removed:
                log(
                    f"[CACHE] Dropped {len(removed)} cached values for sensors this build "
                    f"no longer defines (e.g. {', '.join(sorted(removed)[:3])})",
                    level="warning",
                )

            if RESET_ENERGY_COUNTERS:
                for key in ENERGY_COUNTER_KEYS:
                    cached.pop(key, None)
                log(
                    "[CACHE] RESET_ENERGY_COUNTERS is on: calculated energy totals zeroed. "
                    "Turn the option back off so they are not zeroed again on the next restart.",
                    level="warning",
                )

            _state.LAST_STATE.update(cached)

            # Its own try, after the counters are in: nothing about the clocks may cost
            # the totals they gate.
            try:
                outcomes = restore_energy_clocks(
                    clocks_record, restored_keys=set(cached), reset=RESET_ENERGY_COUNTERS
                )
                if outcomes:
                    log(
                        "[CACHE] Energy clocks: "
                        + ", ".join(f"{domain} {outcome}" for domain, outcome in sorted(outcomes.items())),
                        level="info",
                    )
                elif clocks_record is None:
                    log("[CACHE] Energy clocks: none saved; each domain starts a new baseline", level="info")
            except Exception as exc:
                log(f"[CACHE] Could not restore energy clocks: {exc}", level="error")
    except Exception as e:
        log(f"[CACHE] Error loading state: {e}", level="error")

OWN_MAC: Optional[str] = None
#: Inverter packets dropped because they were not broker traffic, by protocol.
#: Surfaced in the health line so the need for FORWARD_ALL_INVERTER_TRAFFIC can be
#: judged from evidence rather than guessed at.
DROPPED_NON_TARGET = {}


def resolve_own_mac() -> Optional[str]:
    """Our own MAC on the capture interface, or None if it cannot be determined.

    Used to recognise the frames we ourselves re-emitted. Without it those frames are
    indistinguishable from inverter traffic, which is why the health line reported the
    bridge's own MAC as both an inverter and a router address.
    """
    global OWN_MAC
    if OWN_MAC is not None:
        return OWN_MAC
    try:
        OWN_MAC = norm_mac(get_if_hwaddr(SNIFF_IFACE or conf.iface))
    except Exception:
        OWN_MAC = None
    # scapy answers an interface with no address with all zeros rather than raising.
    # Stamped into an ARP reply that would tell both peers the other lives at
    # 00:00:00:00:00:00, so it counts as unresolved and is retried next time.
    if OWN_MAC == "00:00:00:00:00:00":
        OWN_MAC = None
    return OWN_MAC


def route_mac_for(ip: str) -> Optional[str]:
    """The MAC scapy would stamp on a frame to `ip` if the source were left unset.

    An unset Ether.src or ARP hwsrc is filled from the interface the routing table
    picks for the destination -- not from the interface the frame is sent on. On a
    host with two interfaces on this network the two differ, which is the defect
    stamping the source explicitly fixes. Logged beside our own MAC at startup, so a
    user's log shows whether the fix changed anything on their host.
    """
    try:
        return norm_mac(get_if_hwaddr(conf.route.route(ip)[0]))
    except Exception:
        return None


KNOWN_INVERTER_MACS = set()
KNOWN_ROUTER_MACS = set()
LAST_PACKET_TS = 0.0

class ArpSpoofer:
    def resolve_macs(self) -> None:
        global INV_MAC, RTR_MAC

        INV_MAC = norm_mac(INVERTER_MAC_CFG) or INV_MAC
        RTR_MAC = norm_mac(ROUTER_MAC_CFG) or RTR_MAC

        while _state.RUNNING and (not INV_MAC or not RTR_MAC):
            if not INV_MAC:
                INV_MAC = norm_mac(getmacbyip(INVERTER_IP))
            if not RTR_MAC:
                RTR_MAC = norm_mac(getmacbyip(ROUTER_IP))

            if not INV_MAC or not RTR_MAC:
                log("[ARP] Waiting for MAC addresses...", level="info")
                time.sleep(2)

        if _state.RUNNING:
            log(f"[ARP] Inverter MAC: {INV_MAC}", level="info")
            log(f"[ARP] Router MAC:   {RTR_MAC}", level="info")

    def report_source_mac(self) -> None:
        """Say once which MAC the bridge's frames carry, and whether scapy's own choice
        would have differed. The only on-host evidence that the source fix matters."""
        own_mac = resolve_own_mac()
        iface = SNIFF_IFACE or conf.iface
        if not own_mac:
            log(
                f"[ARP] Could not read this host's MAC on {iface}; scapy will choose the "
                f"source from the routing table, which is wrong on a host with two "
                f"interfaces on this network -- pin SNIFF_IFACE",
                level="warning",
            )
            return
        route_mac = route_mac_for(INVERTER_IP)
        if route_mac and route_mac != own_mac:
            log(
                f"[ARP] Frames are sent from {own_mac} on {iface}; scapy's route to "
                f"{INVERTER_IP} would have stamped {route_mac} -- before 2.6.24 that was "
                f"the MAC your inverter and router were told",
                level="warning",
            )
        else:
            log(f"[ARP] Frames are sent from {own_mac} on {iface}", level="info")

    def run(self) -> None:
        self.resolve_macs()
        if not _state.RUNNING:
            return

        log(f"[ARP] Interception ACTIVE: {INVERTER_IP} <-> {ROUTER_IP}", level="info")
        self.report_source_mac()

        while _state.RUNNING:
            # Stamped explicitly: left unset, scapy fills both fields from the route to
            # the destination, which on a host with two interfaces on this network is
            # the wrong interface's MAC. None (unresolvable) sends today's frames.
            own_mac = resolve_own_mac()
            try:
                send_layer2(
                    Ether(src=own_mac, dst=INV_MAC)
                    / ARP(op=2, hwsrc=own_mac, pdst=INVERTER_IP, psrc=ROUTER_IP, hwdst=INV_MAC),
                    SNIFF_IFACE,
                )
                send_layer2(
                    Ether(src=own_mac, dst=RTR_MAC)
                    / ARP(op=2, hwsrc=own_mac, pdst=ROUTER_IP, psrc=INVERTER_IP, hwdst=RTR_MAC),
                    SNIFF_IFACE,
                )
            except Exception as exc:
                log(f"[ARP ERROR] {exc}", level="error")

            time.sleep(2)


arp_spoofer = ArpSpoofer()


# TCP flag bits we care about. Checked numerically -- scapy exposes flags as a
# FlagValue, and comparing it to strings silently never matches.
TCP_FIN = 0x01
TCP_SYN = 0x02
TCP_RST = 0x04
TCP_ACK = 0x10


def handle_inverter_tcp_packet(pkt) -> None:
    flow_key = (pkt[IP].src, int(pkt[TCP].sport), pkt[IP].dst, int(pkt[TCP].dport))
    flags = int(pkt[TCP].flags)

    # Connection lifecycle is handled before the payload guard, because SYN, FIN and
    # RST carry no payload. Without this, a reconnect that reused the same socket
    # pair inside the stale window inherited the dead connection's next_seq and every
    # segment looked like a giant gap.
    if flags & TCP_RST or flags & TCP_FIN:
        drop_flow(flow_key)
        return
    if (flags & TCP_SYN) and not (flags & TCP_ACK):
        # The SYN itself consumes one sequence number.
        reset_flow(flow_key, initial_seq=int(pkt[TCP].seq) + 1)
        return

    if Raw not in pkt:
        return

    payload = bytes(pkt[Raw].load)
    if not payload:
        return

    seq = int(pkt[TCP].seq)

    packets = append_stream_data(flow_key, seq, payload)

    if not packets:
        return

    for packet in packets:
        if LOG_PACKETS:
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
            if LOG_PACKETS and topic is not None:
                log(f"[MQTT PUBLISH] topic={topic} payload_len={len(publish_payload or b'')}")
            if publish_payload and LOG_MQTT_PAYLOAD_PREVIEW:
                log_payload_preview("[MQTT PAYLOAD]", publish_payload, topic=topic)
            if publish_payload:
                parsed_ok = SolarParser.parse_payload(publish_payload, source_topic=topic)
                if not parsed_ok and LOG_UNPARSED_PUBLISH:
                    log_payload_preview("[MQTT PAYLOAD NOT PARSED]", publish_payload, topic=topic)


def packet_callback(pkt) -> None:
    global INV_MAC, RTR_MAC, LAST_PACKET_TS

    LAST_PACKET_TS = time.monotonic()

    if IP not in pkt or Ether not in pkt:
        return

    src_mac = norm_mac(pkt[Ether].src)
    src_ip = pkt[IP].src
    dst_ip = pkt[IP].dst

    # Frames we re-emitted ourselves carry our MAC but the inverter's IP. Recognising
    # them keeps the learned-MAC sets honest and prevents a forwarding loop.
    own_mac = resolve_own_mac()
    if own_mac and src_mac == own_mac:
        return

    if src_ip == INVERTER_IP and not INV_MAC:
        INV_MAC = src_mac
    if dst_ip == INVERTER_IP and not RTR_MAC:
        RTR_MAC = src_mac

    if LOG_VERBOSE and (src_ip == INVERTER_IP or dst_ip == INVERTER_IP):
        proto = "TCP" if TCP in pkt else ("UDP" if UDP in pkt else "OTHER")
        port = f":{pkt[TCP].dport}" if TCP in pkt else ""
        log(f"[X-RAY] {src_ip} ({src_mac}) -> {dst_ip}{port} [{proto}]")

    if src_ip == INVERTER_IP:
        if INV_MAC and src_mac != INV_MAC:
            return

        # Recorded only after the identity guard. Doing it before meant every
        # rejected frame still polluted the set the health line reports.
        if src_mac:
            KNOWN_INVERTER_MACS.add(src_mac)

        if TCP in pkt and dst_ip == TARGET_HOST and int(pkt[TCP].dport) == TARGET_PORT:
            try:
                handle_inverter_tcp_packet(pkt)
            except Exception as exc:
                log(f"[TCP PARSE ERROR] {exc}", level="error")

            if AUTO_INTERCEPT and RTR_MAC:
                try:
                    fwd_pkt = Ether(src=own_mac, dst=RTR_MAC) / pkt[IP]
                    send_layer2(fwd_pkt, SNIFF_IFACE)
                except Exception as exc:
                    log(f"[FWD ERROR] inverter->router {exc}", level="error")
            return

        # Everything else the inverter sends -- DNS, NTP, ICMP, any secondary
        # endpoint. ARP interception made us its gateway for all of it, but only
        # broker traffic was ever relayed, so the rest was silently blackholed.
        proto = "TCP" if TCP in pkt else ("UDP" if UDP in pkt else "OTHER")
        port = int(pkt[TCP].dport) if TCP in pkt else (int(pkt[UDP].dport) if UDP in pkt else 0)
        bucket = f"{proto}:{port}" if port else proto
        DROPPED_NON_TARGET[bucket] = DROPPED_NON_TARGET.get(bucket, 0) + 1

        if FORWARD_ALL_INVERTER_TRAFFIC and AUTO_INTERCEPT and RTR_MAC:
            # Only frames addressed to us at layer 2 were actually routed here.
            # Without this guard the inverter's broadcast and multicast traffic gets
            # re-emitted, duplicating what the real router already received.
            if own_mac and norm_mac(pkt[Ether].dst) == own_mac:
                try:
                    send_layer2(Ether(src=own_mac, dst=RTR_MAC) / pkt[IP], SNIFF_IFACE)
                    DROPPED_NON_TARGET[bucket] -= 1
                except Exception as exc:
                    log(f"[FWD ERROR] inverter->router (non-broker) {exc}", level="error")
        return

    if dst_ip == INVERTER_IP:
        if RTR_MAC and src_mac != RTR_MAC:
            return

        if src_mac:
            KNOWN_ROUTER_MACS.add(src_mac)

        if AUTO_INTERCEPT and INV_MAC:
            try:
                fwd_pkt = Ether(src=own_mac, dst=INV_MAC) / pkt[IP]
                send_layer2(fwd_pkt, SNIFF_IFACE)
            except Exception as exc:
                log(f"[FWD ERROR] router->inverter {exc}", level="error")


PROCESS_START_TS = time.monotonic()
ADAPTIVE_TIMEOUT_LOGGED = False


def observed_telemetry_interval() -> float:
    """Largest recent gap between decoded payloads; 0.0 until two have arrived."""
    return _state.observed_telemetry_interval()


def effective_telemetry_timeout() -> float:
    """How long without a decoded reading before the sensors stop being trustworthy.

    Never shorter than the configured timeout, and never shorter than a few of the
    inverter's own reporting intervals. The second half is the load-bearing one:
    Supervisor pins an option's value the first time the configuration page is saved,
    and a pinned value shadows every later change to the shipped default. An install
    that stored the original 180 s therefore keeps it no matter what a release ships,
    and every entity flaps unavailable between payloads. A floor measured from the
    device's own cadence cannot be shadowed that way.
    """
    global ADAPTIVE_TIMEOUT_LOGGED
    observed = observed_telemetry_interval()
    adaptive = min(
        observed * TELEMETRY_TIMEOUT_MULTIPLIER,
        float(TELEMETRY_TIMEOUT_CEILING_SEC),
    )
    if adaptive <= TELEMETRY_TIMEOUT_SEC:
        return float(TELEMETRY_TIMEOUT_SEC)
    if not ADAPTIVE_TIMEOUT_LOGGED:
        ADAPTIVE_TIMEOUT_LOGGED = True
        log(
            f"[HEALTH] Telemetry arrives up to {int(observed)}s apart, which the "
            f"configured TELEMETRY_TIMEOUT_SEC of {TELEMETRY_TIMEOUT_SEC}s would flap "
            f"against; using {int(adaptive)}s instead",
            level="warning",
        )
    return adaptive


def telemetry_is_fresh(now: Optional[float] = None) -> bool:
    """Whether a decoded reading has arrived recently enough to trust the sensors.

    Deliberately keyed on parsed telemetry rather than LAST_PACKET_TS, which is set
    for any packet matching the capture filter -- bare ACKs included -- and so stays
    fresh long after the cloud stream has stopped carrying data.

    Measured on time.monotonic(), which on Linux excludes time spent suspended. After a
    host suspend the watchdog therefore sees little elapsed and keeps the entities
    available until the timeout passes in running time. That is accepted rather than
    overlooked: Home Assistant is suspended alongside the bridge, so nobody is being
    shown a stale value while it lasts, and a second clock just for this branch would
    reintroduce the two-writers-one-value shape state.py exists to record.
    """
    now = now if now is not None else time.monotonic()
    timeout = effective_telemetry_timeout()
    last = _state.LAST_TELEMETRY_TS
    if not last:
        # Startup grace: do not mark 200 entities unavailable for three minutes every
        # time the add-on restarts. Bounded by STARTUP_GRACE_SEC rather than by the
        # telemetry timeout it used to borrow -- that made the grace 1800 s, long
        # enough to present a restored cache as live data for half an hour.
        return (now - PROCESS_START_TS) < STARTUP_GRACE_SEC
    return (now - last) < timeout


def availability_watchdog_tick(now: Optional[float] = None) -> Optional[bool]:
    """Publish availability when it changes. Returns the new state, or None.

    The verdict is kept in state.py because the paho thread re-asserts it on every
    reconnect. Edge-triggering against a core-private copy meant that once the
    reconnect overwrote the retained topic, this function saw no transition and never
    corrected it -- every entity read available with stale values indefinitely.
    """
    # Shutdown is terminal for availability. It clears RUNNING, then spends about a
    # second restoring ARP before publishing offline, so without this an in-flight
    # tick could republish online afterwards -- onto a client that then disconnects
    # cleanly, which suppresses the LWT that would otherwise correct it.
    if not _state.RUNNING:
        return None
    fresh = telemetry_is_fresh(now)
    if fresh == _state.AVAILABILITY_ONLINE:
        return None
    _state.AVAILABILITY_ONLINE = fresh
    publish_availability(fresh)
    log(
        "[HEALTH] Telemetry resumed; sensors available again"
        if fresh
        # Name the bound that actually fired. Before any payload has been decoded the
        # startup grace governs, not the telemetry timeout, and printing the wrong one
        # is the silent-inconsistency shape this project keeps getting bitten by.
        else (
            f"[HEALTH] No decoded telemetry for {int(effective_telemetry_timeout())}s; "
            "marking sensors unavailable"
            if _state.LAST_TELEMETRY_TS
            else f"[HEALTH] No telemetry decoded within {STARTUP_GRACE_SEC}s of startup; "
            "marking sensors unavailable"
        ),
        level="info" if fresh else "warning",
    )
    return fresh


def capture_thread_is_dead() -> bool:
    """True once the scapy capture thread has ended while the bridge still runs.

    Read from the thread object rather than AsyncSniffer.running or .exception, because
    neither is reliable here. scapy wraps the sniff loop in a try/except that stores an
    exception on the instance, so .exception is None whenever _run simply returns --
    which is what a closed capture socket produces -- and .running is cleared on that
    path too, indistinguishably from a deliberate stop. Thread liveness covers both
    endings without assuming which happened.

    Both None checks are real startup windows, not padding: health_logger is started
    before the sniffer is constructed, and AsyncSniffer.__init__ leaves .thread None
    until start() builds it.
    """
    if sniffer is None:
        return False
    thread = getattr(sniffer, "thread", None)
    if thread is None:
        return False
    return not thread.is_alive()


def build_sniffer() -> AsyncSniffer:
    """The capture configuration, in one place so a restart cannot drift from a start."""
    kwargs = {"filter": f"ip host {INVERTER_IP}", "prn": packet_callback, "store": False}
    if SNIFF_IFACE:
        kwargs["iface"] = SNIFF_IFACE
    return AsyncSniffer(**kwargs)


def restart_capture() -> bool:
    """Replace the dead sniffer with a fresh one. True if the new thread is alive.

    A new instance rather than start() on the old one: after the socket-setup failure
    path scapy leaves .running stale-True, and .exception/.results hold the previous
    run's values.
    """
    global sniffer
    try:
        sniffer = build_sniffer()
        sniffer.start()
        return not capture_thread_is_dead()
    except Exception as exc:
        log(f"[HEALTH] Could not restart packet capture: {exc}", level="error")
        return False


def check_capture_thread() -> bool:
    """Watch the capture thread and act when it dies. True when it acted.

    The capture thread ending is the worst state this add-on can be in. The ARP spoofer
    poisons on RUNNING alone and forwarding lives inside packet_callback, so a dead
    thread leaves the inverter redirected at a bridge that no longer forwards: its route
    to the vendor cloud is gone, not degraded. Nothing noticed before, and the first
    symptom was sensors going stale up to half an hour later, which reads exactly like a
    quiet inverter.

    Restarting in place rather than stopping, because stopping cannot be relied on to
    recover: config.yaml declares no watchdog, so whether the add-on comes back is a
    toggle this code cannot enforce, and Supervisor caps its restart attempts anyway. A
    fresh sniffer costs milliseconds, so the outage is the detection latency either way,
    and it needs nothing from the user. Persistent faults still end loudly.
    """
    global CAPTURE_FAILURES

    if not _state.RUNNING or _state.STOP_REQUESTED:
        return False
    if not capture_thread_is_dead():
        CAPTURE_FAILURES = 0
        return False

    CAPTURE_FAILURES += 1
    cause = getattr(sniffer, "exception", None) or SCAPY_WARNINGS.last or "not reported"
    # In passive mode the bridge never poisoned anything, so the inverter's own path to
    # the cloud is untouched and saying otherwise would send the reader to their router.
    impact = (
        "the inverter is ARP-poisoned toward a bridge that cannot forward"
        if AUTO_INTERCEPT
        else "no telemetry can be decoded; the inverter's own path is unaffected"
    )
    log(
        f"[HEALTH] Packet capture stopped ({CAPTURE_FAILURES}/{CAPTURE_RESTART_LIMIT}); "
        f"{impact}. cause={cause}",
        level="error",
    )

    if CAPTURE_FAILURES < CAPTURE_RESTART_LIMIT and restart_capture():
        log("[HEALTH] Packet capture restarted", level="warning")
        return True

    log(
        "[HEALTH] Packet capture will not stay up; restoring ARP and stopping so the "
        "add-on is not left redirecting traffic it cannot forward",
        level="error",
    )
    # Hand the stop to the main thread. Calling shutdown() here would set RUNNING False,
    # then spend a second in restore_arp on a daemon thread that the interpreter kills
    # as soon as main's own loop notices and falls through -- truncating the one action
    # that ends the blackhole.
    _state.STOP_REQUESTED = True
    return True


def health_logger() -> None:
    ticks = 0
    while _state.RUNNING:
        # Ten seconds so availability reacts promptly; the health line still prints
        # every 30 so log volume is unchanged.
        time.sleep(10)
        try:
            # Before availability: a dead capture thread is the cause of the staleness
            # the watchdog would otherwise misreport as a quiet inverter. No early
            # return -- a successful restart should leave the loop running, and a
            # give-up sets STOP_REQUESTED, which this call then ignores on later ticks.
            check_capture_thread()
        except Exception as exc:
            log(f"[HEALTH ERROR] {exc}", level="error")

        try:
            availability_watchdog_tick()
        except Exception as exc:
            log(f"[HEALTH ERROR] {exc}", level="error")

        try:
            # Timer-driven, so the retained state stays fresh while the inverter is
            # quiet. Doing this from parse_payload meant it could only fire when a
            # payload arrived, which is exactly when it was not needed.
            if heartbeat_due():
                republish_state()
        except Exception as exc:
            log(f"[HEARTBEAT ERROR] {exc}", level="error")

        ticks += 1
        if ticks % 3:
            continue

        age = time.monotonic() - LAST_PACKET_TS if LAST_PACKET_TS else -1
        if age < 0:
            log(
                f"[HEALTH] No packets captured yet; broker="
                f"{'up' if broker_is_connected() else 'DOWN'}; "
                f"avail={'online' if _state.AVAILABILITY_ONLINE else 'OFFLINE'}",
                level="info",
            )
        else:
            inv_list = sorted(x for x in KNOWN_INVERTER_MACS if x)
            rtr_list = sorted(x for x in KNOWN_ROUTER_MACS if x)
            dropped = {k: v for k, v in sorted(DROPPED_NON_TARGET.items()) if v > 0}
            extra = f"; dropped_non_broker={dropped}" if dropped else ""
            log(
                # broker= included because a down broker used to be invisible here:
                # capture keeps working, this line keeps printing, and nothing reaches
                # Home Assistant.
                # Both silence sources, because they are different faults with the
                # same symptom: broker=DOWN is the transport, avail=OFFLINE is the
                # bridge declaring its own data stale while the broker is fine.
                f"[HEALTH] broker={'up' if broker_is_connected() else 'DOWN'}; "
                f"avail={'online' if _state.AVAILABILITY_ONLINE else 'OFFLINE'}; "
                f"Last packet seen {int(age)}s ago; inverter_macs={inv_list}; "
                f"router_macs={rtr_list}{extra}",
                level="info",
            )


def restore_arp() -> None:
    """Undo the ARP poisoning so the inverter goes straight back to the real gateway.

    The spoofer only ever emits poisoning replies, so stopping the add-on used to
    leave both caches wrong until they aged out -- minutes during which the inverter
    could not reach the cloud at all. hwsrc carries each peer's real MAC -- that is the
    correction -- while the Ethernet source is ours, as on every frame we send. It
    reads the cached OWN_MAC rather than resolving: this runs in a signal handler.

    Runs inside a signal handler, so it is hard-bounded at about a second and every
    failure is swallowed -- it must never block the MQTT teardown that follows.
    """
    if not (AUTO_INTERCEPT and INV_MAC and RTR_MAC):
        return
    try:
        for _ in range(5):
            send_layer2(
                Ether(src=OWN_MAC, dst=INV_MAC)
                / ARP(op=2, psrc=ROUTER_IP, hwsrc=RTR_MAC, pdst=INVERTER_IP, hwdst=INV_MAC),
                SNIFF_IFACE,
            )
            send_layer2(
                Ether(src=OWN_MAC, dst=RTR_MAC)
                / ARP(op=2, psrc=INVERTER_IP, hwsrc=INV_MAC, pdst=ROUTER_IP, hwdst=RTR_MAC),
                SNIFF_IFACE,
            )
            time.sleep(0.2)
        log("[ARP] Restored both peers to their real MAC addresses", level="info")
    except Exception as exc:
        log(f"[ARP] Could not restore ARP caches: {exc}", level="error")


def shutdown(*_args) -> None:
    global sniffer

    if not _state.RUNNING:
        return

    _state.RUNNING = False

    try:
        if sniffer is not None:
            sniffer.stop()
    except Exception:
        pass

    restore_arp()

    try:
        _state.AVAILABILITY_ONLINE = False
        publish_availability(False)
        client.disconnect()
        client.loop_stop()
    except Exception:
        pass

    log("[Bridge] Stopped")


def log_startup_configuration() -> None:
    """Print the effective configuration.

    A module-level function rather than inline in __main__, so a test can
    execute it. This block previously used a private helper that
    `from .config import *` does not export, and the resulting NameError was
    unreachable by any test because nothing ran the __main__ body.
    """
    log(f"--- Siseli Inverter Bridge {VERSION} ---")
    log(f"[Config] INVERTER_IP={INVERTER_IP} ROUTER_IP={ROUTER_IP}")
    log(f"[Config] TARGET={TARGET_HOST}:{TARGET_PORT} MQTT={MQTT_HOST}:{MQTT_PORT}")
    # Both, because together they decide what is relayed: nothing in passive mode, the
    # broker connection by default, everything addressed to us with FORWARD_ALL on.
    # No log -- the reference captures included -- recorded which mode was running.
    log(
        f"[Config] AUTO_INTERCEPT={AUTO_INTERCEPT} "
        f"FORWARD_ALL_INVERTER_TRAFFIC={FORWARD_ALL_INVERTER_TRAFFIC}"
    )
    log(f"[Config] INVERTER_COUNT={INVERTER_COUNT}")
    log(f"[Config] BATTERY_COUNT={BATTERY_COUNT} BATTERY_CAPACITY_PER_BATTERY_AH={BATTERY_CAPACITY_PER_BATTERY_AH}")
    log(f"[Config] DEVICE_NAME={DEVICE_NAME} MANUFACTURER={MANUFACTURER}")
    log(f"[Config] STATE_TOPIC={STATE_TOPIC}")
    log(f"[Config] SNIFF_IFACE={SNIFF_IFACE or 'auto'}")
    # Printed because these are the options Supervisor pins on first save, so the
    # running value can differ from the shipped default and nothing else reveals it.
    log(
        f"[Config] UPDATE_INTERVAL_SEC={UPDATE_INTERVAL_SEC} "
        f"EXPIRE_AFTER_SEC={EXPIRE_AFTER_SEC} "
        f"TELEMETRY_TIMEOUT_SEC={TELEMETRY_TIMEOUT_SEC}"
    )
    log(f"[Config] DEBUG_FLAGS={list(ACTIVE_DEBUG_FLAGS) or 'none'}")


def install_signal_handlers() -> None:
    """Called from __main__ only. At module scope this would hijack the signal
    handlers of any process that merely imports core (e.g. the test runner), and
    raises ValueError when imported off the main thread."""
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)


if __name__ == "__main__":
    from .config import validate_config
    validate_config()
    install_signal_handlers()
    load_cached_state()
    log_startup_configuration()

    for key in SENSORS.keys():
        _state.LAST_STATE.setdefault(key, None)

    start_mqtt()

    if AUTO_INTERCEPT:
        threading.Thread(target=arp_spoofer.run, daemon=True).start()
        wait_start = time.monotonic()
        while _state.RUNNING and time.monotonic() - wait_start < 15 and (not INV_MAC or not RTR_MAC):
            time.sleep(1)
    else:
        INV_MAC = norm_mac(INVERTER_MAC_CFG)
        RTR_MAC = norm_mac(ROUTER_MAC_CFG)
        log("[ARP] AUTO_INTERCEPT disabled; relying on existing network redirection")

    threading.Thread(target=health_logger, daemon=True).start()

    sniffer = build_sniffer()
    sniffer.start()
    log("[Bridge] Sniffer started", level="info")

    try:
        while _state.RUNNING and not _state.STOP_REQUESTED:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        # On the main thread, with RUNNING still set, so restore_arp gets its full
        # second instead of being killed with the daemon threads.
        shutdown()

    if _state.STOP_REQUESTED:
        # Non-zero, because exit 0 is what a user-requested stop looks like and is the
        # least likely status to prompt a supervisor to restart anything.
        sys.exit(1)
