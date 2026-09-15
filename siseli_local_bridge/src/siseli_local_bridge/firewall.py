"""Blocks the kernel's own sockets from claiming traffic bound for LOCAL_CLOUD_IP.

Home Assistant's local MQTT broker listens on 0.0.0.0:1883, which matches every
local address including LOCAL_CLOUD_IP once that address exists on the interface
(see the network_ip_alias add-on). Without this, the real broker -- not our
userspace fake-cloud responder -- would accept the dongle's connection.

Deliberately narrow: one dedicated nftables table holding a single static INPUT
DROP rule for traffic addressed to this host itself. This is NOT the NAT/FORWARD
approach this project's own history (see CHANGELOG.md, versions 1.6.0-1.8.0) found
unreliable under this base image's kernel -- ip_forward and iptables NAT were
dropped precisely because they lost transit packets meant for a third party (the
real cloud). Nothing here forwards anything to anyone; it only tells the kernel not
to deliver certain locally-destined packets to a locally-bound socket, a different
and much simpler code path.

nft, not legacy iptables: a live install on this add-on's target (6.18-haos-raspi)
showed nf_tables already loaded and in active use by the host (694 references,
likely Docker's own networking) while the legacy ip_tables/iptable_filter modules
could not be loaded inside the container at all (no /lib/modules to modprobe from).
The legacy iptables binary talks to those legacy modules specifically and has no
nf_tables fallback, so it fails outright here; nft speaks nf_tables natively.

raw/PREROUTING, not filter/INPUT: a live test showed Docker itself publishes the
MQTT broker add-on's port 1883 via a DNAT rule in its own `ip nat` table's DOCKER
chain (`iifname != "hassio" tcp dport 1883 ... DNAT`), reached from PREROUTING at
priority dstnat. That hook runs before filter/INPUT in the packet's path, so by the
time a filter/INPUT rule would see the packet its destination address has already
been rewritten to the broker container's bridge IP -- "ip daddr LOCAL_CLOUD_IP"
never matches there no matter how correct the rule is. raw/PREROUTING (priority
"raw", canonically -300) runs before nat/PREROUTING, which is early enough to drop
the packet before Docker's DNAT ever touches it. HAOS's own Supervisor networking
already uses this same raw/PREROUTING hook for its own address protections, visible
in the same `nft list ruleset` output that revealed the DNAT rule in the first
place.
"""

import subprocess

from .config import HTTP_STUB_PORT, LOCAL_CLOUD_IP, LOCAL_CLOUD_PORT
from .loggers import log

_TABLE = "siseli_local_cloud"
_CHAIN = "prerouting"
#: Every port this add-on impersonates a real listener on. dtu_prop_post's HTTP
#: bootstrap (httpstub.py) needed the same treatment as the MQTT broker once it got
#: its own userspace responder -- see httpstub.py's module docstring.
_PORTS = (LOCAL_CLOUD_PORT, HTTP_STUB_PORT)


def _run(*args: str):
    return subprocess.run(["nft", *args], capture_output=True, text=True)


def _table_exists() -> bool:
    return _run("list", "table", "inet", _TABLE).returncode == 0


def install_local_cloud_block() -> bool:
    """Idempotent: safe to call on every startup, including after a crash that
    skipped teardown_local_cloud_block(). Returns False (and logs) on any failure
    so the caller can decide whether that is fatal."""
    if not LOCAL_CLOUD_IP:
        return False

    if _table_exists():
        log(f"[Firewall] nft table {_TABLE!r} already present ({LOCAL_CLOUD_IP}: {_PORTS})")
        return True

    steps = [
        ("add", "table", "inet", _TABLE),
        ("add", "chain", "inet", _TABLE, _CHAIN, "{ type filter hook prerouting priority raw; }"),
    ]
    for port in _PORTS:
        steps.append(
            (
                "add", "rule", "inet", _TABLE, _CHAIN,
                "ip", "daddr", LOCAL_CLOUD_IP, "tcp", "dport", str(port), "drop",
            )
        )
    for step in steps:
        result = _run(*step)
        if result.returncode != 0:
            log(f"[Firewall] Could not set up nft table: {result.stderr.strip()}", level="error")
            # Best-effort cleanup of whatever partial state the failed sequence left,
            # so a later retry starts clean rather than hitting "already exists".
            _run("delete", "table", "inet", _TABLE)
            return False

    log(f"[Firewall] Added INPUT DROP for {LOCAL_CLOUD_IP} ports {_PORTS} (nft table {_TABLE!r})")
    return True


def teardown_local_cloud_block() -> None:
    """Best-effort. Deleting the table removes its chain and rule with it, so there
    is nothing left to leak even if install_local_cloud_block() partially failed."""
    if not LOCAL_CLOUD_IP:
        return
    if not _table_exists():
        return
    result = _run("delete", "table", "inet", _TABLE)
    if result.returncode != 0:
        log(f"[Firewall] Could not remove nft table {_TABLE!r}: {result.stderr.strip()}", level="error")
    else:
        log(f"[Firewall] Removed INPUT DROP for {LOCAL_CLOUD_IP} ports {_PORTS}")
