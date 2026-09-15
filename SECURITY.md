# Security

This add-on does something unusual, and you should understand it before installing.

**It ARP-spoofs a device on your LAN.** That is the mechanism by which it works, not an
optional mode. This document says exactly what it does, what privileges it holds, and
what it does not do.

## Reporting a vulnerability

Use GitHub's private reporting: **[Security → Report a
vulnerability](https://github.com/fadmaz/siseli-ha/security/advisories/new)**. That opens
a private thread visible only to the maintainer.

Please do not open a public issue for a vulnerability. There is no published contact
email — the private advisory form is the only channel.

This is a hobby project with one maintainer. Expect a first response within a week or
two, not within hours.

## What it does

Your inverter's WiFi dongle opens an MQTT connection to the vendor cloud at a fixed
address. The add-on inserts itself into that path so it can read the telemetry that is
already flowing.

1. **ARP interception.** It sends unsolicited ARP replies (`op=2`) to exactly two hosts:
   the inverter, telling it that the router's IP is at the MAC of the interface the
   add-on captures on; and the router, telling it the same about the inverter's IP. Both
   addresses are configured by you, in `INVERTER_IP` and `ROUTER_IP`. **It never scans, sweeps or discovers** — if
   you put the wrong IP in, it poisons the wrong host, and nothing in the add-on will
   notice. While `INVERTER_MAC` or `ROUTER_MAC` is blank it also sends ordinary ARP
   requests (`who-has`) for those two IPs to learn their MACs; setting both suppresses
   them.
2. **Passive capture.** A scapy `AsyncSniffer` reads the frames that now arrive.
3. **Forwarding.** Two kinds of traffic are re-emitted toward their real destination by
   default: the inverter's connection to the cloud broker (`TARGET_HOST:TARGET_PORT`),
   and everything the router sends to the inverter. The IP datagram goes out as
   captured; only the Ethernet header is rebuilt. The broker connection is never
   terminated, never proxied, never modified. **Everything else the inverter sends —
   DNS, NTP, HTTP, any other endpoint — is dropped**, unless you enable
   `FORWARD_ALL_INVERTER_TRAFFIC`. On a clean stop the add-on sends corrective ARP
   replies, so both peers go back to talking directly at once; only after a crash do
   they wait for their ARP caches to expire.
4. **Publishing.** Decoded values go to your own MQTT broker.

`AUTO_INTERCEPT: false` turns off steps 1 and 3, for people who have arranged the
traffic another way (a port mirror, a router-side rule). Nothing is relayed in that mode;
steps 2 and 4 are unaffected.

## Privileges it holds, and why

| Privilege | Why |
|---|---|
| `NET_RAW` | The `AsyncSniffer` needs a raw socket to read frames, and `sendp()` needs one to emit the ARP replies and forwarded packets. Every frame the add-on builds goes out through one helper, `send_layer2` in `core.py`, which holds the only two `sendp()` calls. The ARP requests of step 1 are sent by scapy's `getmacbyip` through the same raw socket. |
| `NET_ADMIN` | Putting the interface into promiscuous mode, so frames addressed to another MAC are delivered. |
| `host_network: true` | Both the spoofing and the capture happen on the host's LAN segment. Inside a bridged container namespace there is nothing to see and nobody to spoof. |
| `apparmor: false` | See below. This is the weakest part of the posture. |

**The historical `iptables` and NAT redirection are gone.** An earlier design rewrote
packet destinations with `iptables`; nothing does that now, and as of 2.6.13 the
`iptables` and `libcap` packages are no longer installed in the image. The remaining
network activity is raw send and raw capture, both of which come from the capabilities
above rather than from a binary.

### `apparmor: false` is a known gap

The add-on ships with AppArmor confinement disabled, which means it is constrained only
by the two capabilities above rather than by a profile that says which files and
operations it may use.

This is not defensible as a permanent state; it is a gap that has not been closed. Doing
so means writing an `apparmor.txt` that permits `network packet raw`, `network packet
packet`, the `/data` writes described below, and nothing else. Contributions welcome.

## What it does not do

- **No listening socket.** Nothing binds a port. The `LISTEN_PORT` option is a
  deprecated no-op.
- **No outbound connections of its own**, other than to the MQTT broker you configure.
  It never contacts the vendor cloud on its own behalf — it only relays the inverter's
  own packets, and by default only its broker connection (see step 3).
- **It never writes toward the inverter.** No command, no setting, no control frame. The
  decoder is read-only by construction; there is no code path that constructs an inverter
  MQTT message.
- **It never terminates a TCP connection.** It observes a stream it is not an endpoint
  of, so there is no TLS to break and no session to hijack.

## What leaves the machine, and what is stored

**Leaves:** decoded sensor values, to the MQTT broker you configure. Nothing else. There
is no telemetry, no analytics, no crash reporting, no update check.

**Stored,** in the add-on's private `/data` volume:

| File | Contents |
|---|---|
| `/data/state.json` | The last decoded value of every sensor, so entities survive a restart, plus the energy integrator's clock readings and this host's boot id, so a restart does not lose an interval of energy. |
| `/data/discovery_state.json` | Which discovery topics have been published, so stale ones can be swept. |

Neither contains credentials. Your MQTT password lives in the add-on's Supervisor
configuration, like every other add-on's.

**Your logs contain your device serial.** It appears in the MQTT `topic=` values, which
are printed when debug flags are on. Scrub them before pasting a log into an issue.

## Two risks stated plainly

**ARP spoofing is indistinguishable from an attack.** To a managed switch with dynamic
ARP inspection, an IDS, or a router with ARP-spoofing protection, this add-on looks
exactly like a man-in-the-middle attempt — because mechanically it is one, aimed at a
device you own with your permission. Expect alerts. On networks with DAI enabled, expect
the frames to be dropped and the add-on not to work.

**A hard power loss skips the cleanup.** `restore_arp()` sends five rounds of corrective
replies to both peers on `SIGTERM` and `SIGINT`, so a normal stop or restart puts both
ARP caches back immediately. A pulled plug or a killed container does not run it. The
caches then age out on their own — usually a minute or two — during which the inverter
cannot reach the cloud.

## Scope

Reports about the following are in scope: anything that lets a third party influence what
the bridge publishes, anything that widens the two-host targeting, credential handling,
and the contents of `/data`.

Out of scope: the fact that the add-on ARP-spoofs at all, and the fact that it requires
`NET_ADMIN`/`NET_RAW`. Those are the design, documented above.
