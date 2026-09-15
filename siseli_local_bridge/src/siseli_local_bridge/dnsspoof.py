"""Answers the dongle's own DNS query for the vendor cloud's hostname with
LOCAL_CLOUD_IP, so it connects to fakecloud.py instead of the real internet.

Runs entirely in userspace via the same raw capture/injection scapy already uses for
ARP -- no dnsmasq, no router-level override. This project's own history already
tried a DNS-override once and rejected it (see siseli-ha's CHANGELOG/README:
"a DNS-override method that cannot work -- the bridge observes traffic rather than
terminating it, so there is no listener for a redirected connection to reach"). That
objection does not apply here: LOCAL_CLOUD_IP now has a real userspace MQTT server
answering at it (fakecloud.py, via firewall.py's redirect-proof INPUT block), so
sending DNS there points at something that actually picks up.

Only A records for names under one of DNS_SPOOF_DOMAINS are answered; every other
query -- including AAAA for a matched name -- passes through untouched, so a
resolver that tries AAAA first still falls back to the real A behaviour it would
normally see rather than getting a half-spoofed, inconsistent answer.
"""

from typing import Optional

from scapy.all import DNS, DNSRR, IP, UDP, Ether  # type: ignore

from .config import DNS_SPOOF_DOMAINS, LOCAL_CLOUD_IP

_TYPE_A = 1


def _matches(qname: str) -> bool:
    name = qname.rstrip(".").lower()
    return any(name == d or name.endswith("." + d) for d in DNS_SPOOF_DOMAINS)


def query_name(pkt) -> Optional[str]:
    """The queried name if this is a spoofable A-record query, else None."""
    if not (LOCAL_CLOUD_IP and DNS_SPOOF_DOMAINS):
        return None
    if DNS not in pkt or pkt[DNS].qr != 0 or not pkt[DNS].qdcount or pkt[DNS].qd is None:
        return None
    qd = pkt[DNS].qd
    if int(qd.qtype) != _TYPE_A:
        return None
    try:
        qname = qd.qname.decode("ascii", errors="replace")
    except Exception:
        return None
    return qname if _matches(qname) else None


def build_reply(pkt, peer_mac: str):
    """The A-record answer, as a ready-to-send Ether/IP/UDP/DNS frame.

    Spoofed to look like it came from whatever server the dongle actually asked
    (pkt[IP].dst), which is what its resolver expects a reply's source to be.
    """
    qd = pkt[DNS].qd
    answer = DNSRR(rrname=qd.qname, type="A", ttl=60, rdata=LOCAL_CLOUD_IP)
    dns = DNS(id=pkt[DNS].id, qr=1, aa=0, rd=pkt[DNS].rd, ra=1, qdcount=1, ancount=1, qd=qd, an=answer)
    return (
        Ether(dst=peer_mac)
        / IP(src=pkt[IP].dst, dst=pkt[IP].src)
        / UDP(sport=pkt[UDP].dport, dport=pkt[UDP].sport)
        / dns
    )
