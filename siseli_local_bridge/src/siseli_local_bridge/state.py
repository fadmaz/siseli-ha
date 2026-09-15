import collections
import json
import os
import threading
import time
from typing import Deque, Dict, Optional

# Shared mutable state consumed by core.py, mqtt.py and parsers.py.
# Having these globals here breaks the circular import that previously required
# a deferred-import hack (_get_mqtt_globals) inside parsers.py.

LAST_STATE: Dict[str, object] = {}
DISCOVERY_PUBLISHED: bool = False
PUBLISHED_SENSOR_KEYS: set = set()

#: Guards LAST_STATE. It is written on the scapy capture thread and read on the paho
#: network thread; without this, a dict resize during on_connect's iteration raises
#: RuntimeError inside a paho callback, which kills the network thread silently while
#: the bridge keeps parsing and logging as though nothing were wrong.
#: Only ever held long enough to take a dict() copy -- never across I/O.
STATE_LOCK = threading.RLock()

#: Lifecycle flag. It lives here, not in mqtt.py, because core owns shutdown and
#: mqtt.py must observe it. Previously mqtt.py defined it and core imported it by
#: value, so core.shutdown() rebound only its own copy and mqtt's stayed True forever.
RUNNING: bool = True

#: Set by a background thread that wants the bridge to stop. It is NOT the same as
#: clearing RUNNING, and the difference is load-bearing: shutdown() returns early when
#: RUNNING is already False, so a helper thread that cleared it would neuter the very
#: teardown it was asking for. Worse, shutdown() must run on the MAIN thread -- it
#: spends a full second restoring ARP, and a daemon thread doing that is killed
#: mid-restore the moment main falls off the end of the module. The main loop watches
#: this flag and lets its own `finally: shutdown()` do the work.
STOP_REQUESTED: bool = False

#: time.monotonic() reading at the last *successfully parsed telemetry payload*. It is
#: meaningless across a process boundary and must never be persisted. This is the
#: only trustworthy liveness signal: core's LAST_PACKET_TS is set for any packet
#: matching the capture filter, bare ACKs included, so it stays fresh long after the
#: cloud stream has stopped carrying data.
LAST_TELEMETRY_TS: float = 0.0

#: Gaps, in seconds, between the last few decoded telemetry payloads -- newest last.
#: The availability watchdog floors its timeout on these. A timeout shorter than the
#: inverter's real reporting cadence marks every entity unavailable between payloads,
#: and raising the shipped default does not fix an install that already stored the old
#: one, so the floor has to be measured rather than configured.
TELEMETRY_INTERVALS: Deque[float] = collections.deque(maxlen=8)

#: Set once the stale-discovery sweep has run in this process.
DISCOVERY_CLEANED: bool = False

#: What the availability watchdog last told the broker. It lives here because two
#: threads write the availability topic: the watchdog on its own thread, and the paho
#: thread re-asserting it after a reconnect (the retained LWT fires on an unclean
#: drop, so something must restore it). The watchdog is edge-triggered, so when the
#: reconnect published a literal True instead of consulting this flag, the two
#: disagreed permanently and every entity read available with stale values.
#: Reach it as ``_state.AVAILABILITY_ONLINE``. A ``from .state import`` binds a copy
#: and reintroduces exactly the bug documented above for RUNNING.
AVAILABILITY_ONLINE: bool = True


def observed_telemetry_interval() -> float:
    """Largest recent gap between decoded payloads; 0.0 until two have arrived.

    Read by the availability watchdog and by the energy integrator. Both need a bound
    that tracks the device's real cadence rather than an unrelated config option.
    """
    intervals = list(TELEMETRY_INTERVALS)
    return max(intervals) if intervals else 0.0


def record_telemetry(now: Optional[float] = None) -> None:
    """Stamp a decoded payload and remember the gap since the previous one."""
    global LAST_TELEMETRY_TS
    now = now if now is not None else time.monotonic()
    previous = LAST_TELEMETRY_TS
    if previous and now > previous:
        TELEMETRY_INTERVALS.append(now - previous)
    LAST_TELEMETRY_TS = now


def snapshot_state() -> Dict[str, object]:
    """A consistent copy of LAST_STATE, safe to iterate off-thread."""
    with STATE_LOCK:
        return dict(LAST_STATE)


def update_state(values: Dict[str, object]) -> Dict[str, object]:
    """Merge values into LAST_STATE and return a snapshot taken under the same lock."""
    with STATE_LOCK:
        LAST_STATE.update(values)
        return dict(LAST_STATE)


def atomic_write_json(path: str, payload: object) -> None:
    """Write JSON so a reader never observes a half-written file.

    The previous truncate-then-write meant a kill mid-write left invalid JSON, and the
    loader's broad except turned that into an empty state -- silently zeroing every
    cumulative energy counter.
    """
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)

    tmp_path = path + ".tmp"
    with open(tmp_path, "w") as handle:
        json.dump(payload, handle)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp_path, path)
