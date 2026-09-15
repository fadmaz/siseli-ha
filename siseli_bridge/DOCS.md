# Siseli Inverter Bridge

Full reference for installing, configuring and troubleshooting the add-on.

This page is rendered by Home Assistant on the add-on's **Documentation** tab, and is
also readable [on GitHub](https://github.com/fadmaz/siseli-ha/blob/main/siseli_bridge/DOCS.md).
For what the add-on is and how it decodes telemetry, see the
[project README](https://github.com/fadmaz/siseli-ha).

## Contents

- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [What you get](#what-you-get)
- [Parallel inverters and battery banks](#parallel-inverters-and-battery-banks)
- [Network setup](#network-setup)
- [Troubleshooting](#troubleshooting)

---

## Requirements

| | |
|---|---|
| **Architecture** | `aarch64` or `amd64`. Those are the two listed in the add-on's `arch:` key, so on a 32-bit system (`armv7`, `armhf`, `i386`) it does not appear in the store at all. |
| **Home Assistant** | Supervised or Home Assistant OS. The add-on needs `host_network`, `NET_ADMIN` and `NET_RAW` to capture and inject frames, and runs with AppArmor disabled. |
| **MQTT broker** | Any broker Home Assistant already uses. The [Mosquitto add-on](https://github.com/home-assistant/addons/tree/master/mosquitto) is the usual choice. |
| **Network** | The inverter and Home Assistant must be on the same layer-2 network for ARP interception to work. |

---

## Installation

### 1. Set up MQTT

Install the **Mosquitto broker** add-on if you have not already, and create a Home
Assistant user for the bridge to log in with (**Settings → People → Users → Add user**).
You will need that username and password in step 3.

### 2. Add this repository

**Settings → Add-ons → Add-on Store → ⋮ → Repositories**, then add:

```
https://github.com/fadmaz/siseli-ha
```

### 3. Install and configure

Install **Siseli Inverter Bridge**, open its **Configuration** tab, and set at minimum:

| Option | What to enter |
|---|---|
| `MQTT_USER` / `MQTT_PASSWORD` | The credentials you created in step 1 |
| `INVERTER_IP` | Your inverter's local IP — find it in your router's DHCP client list |
| `ROUTER_IP` | Your router / gateway IP |
| `INVERTER_COUNT` | How many inverters you have, if running in parallel |
| `BATTERY_COUNT` and `BATTERY_CAPACITY_PER_BATTERY_AH` | If you want a configured bank-capacity sensor |

Leave `AUTO_INTERCEPT` on unless you have arranged the traffic yourself — see
[Network setup](#network-setup).

### 4. Start it

Enable **Watchdog** and **Start on boot**, then start the add-on. Within a couple of
minutes the log should show:

```
--- Siseli Inverter Bridge 2.6.x ---
[ARP] Interception ACTIVE: 192.168.x.x <-> 192.168.x.x
[HA MQTT] Connected to ...
[HA MQTT] Discovery published
[Bridge] Sniffer started
```

Entities appear under **Settings → Devices & Services → MQTT** once the first telemetry
payload arrives. Inverters typically report every few minutes, so give it up to ten
minutes before concluding something is wrong.

---

## Configuration

Every option, with its shipped default. Most installations only need the handful listed
in step 3 above.

### Connection

| Option | Default | Notes |
|---|---|---|
| `MQTT_HOST` | `core-mosquitto` | Use the default with the official Mosquitto add-on |
| `MQTT_PORT` | `1883` | |
| `MQTT_USER` / `MQTT_PASSWORD` | *(blank)* | Leave blank only if your broker allows anonymous access |
| `TARGET_HOST` | `8.212.18.157` | The Siseli cloud. Must be an IPv4 address — it is compared with each packet's destination, so a hostname never matches and the add-on refuses to start. Do not change unless the cloud IP changes |
| `TARGET_PORT` | `1883` | |
| `INVERTER_IP` | `192.168.1.139` | **Must be set to your inverter's real IP** |
| `ROUTER_IP` | `192.168.1.1` | **Must be set to your gateway** |
| `INVERTER_MAC` / `ROUTER_MAC` | *(blank)* | Optional. Pin these if auto-detection picks the wrong device |
| `AUTO_INTERCEPT` | `true` | ARP interception. Turn off only if you route the traffic yourself |
| `SNIFF_IFACE` | *(blank)* | Advanced. Pin the capture interface if auto-detection fails |
| `FORWARD_ALL_INVERTER_TRAFFIC` | `false` | See [the caveat below](#a-caveat-on-forwarding) |

### Identity and scaling

| Option | Default | Notes |
|---|---|---|
| `DEVICE_ID` | `siseli_inverter_1` | Letters, digits, `_` and `-` only. Changing it renames every entity |
| `DEVICE_NAME` | `Siseli Inverter 1` | Shown in Home Assistant |
| `MODEL_NAME` / `MANUFACTURER` | `Siseli Inverter 1` / `Siseli Compatible` | Cosmetic |
| `ENTITY_PREFIX` | `Siseli` | Prefixed to every entity name |
| `INVERTER_COUNT` | `1` | Scales the calculated power sensors — see below |
| `BATTERY_COUNT` | `1` | |
| `BATTERY_CAPACITY_PER_BATTERY_AH` | `0.0` | `0` disables the configured bank-capacity sensor |
| `MQTT_DISCOVERY_PREFIX` | `homeassistant` | Only change for a custom discovery setup |
| `STATE_TOPIC` / `AVAILABILITY_TOPIC` | *(blank)* | Blank derives both from `DEVICE_ID` |
| `MQTT_RETAIN` | `true` | Keeps sensor states across a Home Assistant restart |

### Timing

| Option | Default | Notes |
|---|---|---|
| `UPDATE_INTERVAL_SEC` | `10` | Publish throttle. Raising it saves database storage |
| `EXPIRE_AFTER_SEC` | `1800` | How long a value stays valid before Home Assistant marks it unavailable. `0` disables |
| `TELEMETRY_TIMEOUT_SEC` | `1800` | How long without a decoded reading before the bridge marks sensors unavailable |

These three interact, and the add-on **refuses to start** if they contradict each other:

- `UPDATE_INTERVAL_SEC` must be less than `EXPIRE_AFTER_SEC`
- `TELEMETRY_TIMEOUT_SEC` must not exceed `EXPIRE_AFTER_SEC`, or Home Assistant would
  expire the sensors before the bridge decided they were stale

`TELEMETRY_TIMEOUT_SEC` is also raised automatically at runtime if the bridge measures
your inverter reporting less often than the configured value, so entities cannot flap.

> **If you installed a version before 2.6.6:** Home Assistant pins an option's value the
> first time you save the Configuration page, so an old default can outlive the release
> that changed it. If your entities cycle between available and unavailable, check that
> `TELEMETRY_TIMEOUT_SEC` and `EXPIRE_AFTER_SEC` both read `1800`.

### Diagnostics and maintenance

| Option | Default | Notes |
|---|---|---|
| `LOG_LEVEL` | `info` | `debug` for deep troubleshooting, `warning` for quiet logs |
| `DEBUG_FLAGS` | *(none)* | See [Troubleshooting](#troubleshooting). Requires `LOG_LEVEL` of `info` or `debug` |
| `DISCOVERY_CLEANUP` | `true` | Clears entities left behind by earlier versions that grouped sensors differently |
| `RESET_ENERGY_COUNTERS` | `false` | Zeroes the calculated kWh totals. Turn on, restart once, turn back off |

### Deprecated

`LISTEN_PORT` and `LOG_VERBOSE` are **ignored**. They stay in the schema for good:
Supervisor validates the options an installation has stored before it installs an
update, so removing a key that installations still have on disk would block every
upgrade. `LISTEN_PORT` in particular never did anything — the bridge has never opened a
socket. You can ignore the `[CONFIG WARNING]` about it.

---

## What you get

**207 sensors across 7 devices.** 143 are enabled on a fresh install; the rest are
disabled by default and can be switched on individually in Home Assistant.

| Device | Sensors | Covers |
|---|---|---|
| **Main** | 14 | The calculated power and energy sensors, state of charge, mode |
| **Battery** | 45 | Voltage, current, capacity, charge/discharge state, charging setpoints |
| **BMS** | 25 | Per-cell voltages (16), pack min/max/delta, nominal and remaining Ah, limits |
| **Grid** | 30 | Voltage, frequency, flow direction, mains loss thresholds, relay status |
| **Load** | 22 | Active and apparent power, load percentage, output voltage and frequency |
| **PV** | 18 | Per-string voltage/current/power, temperatures, daily/monthly/yearly/total energy |
| **Diagnostics** | 53 | Fan speeds, temperatures, firmware, settings echoes, raw block dumps |

The Battery, BMS, Grid, Load, PV and Diagnostics devices are nested under Main in Home
Assistant, so they appear together on one page.

**Cell Voltages Decoded** counts the per-cell voltages the payload carried, not the cells
in your pack. On the reference install the block holds 16 cells of a 32-cell bank, and the
list stops at the first out-of-range reading, so a failed cell 3 makes it read 2. No field
on the wire states the pack size.

**Calculated sensors** are prefixed `c_` and are derived rather than read from the wire —
battery charge/discharge power and energy, grid import power and energy, generation power
and energy, load power and energy, and the configured bank capacity. The five `kWh`
counters are `total_increasing`, so they feed the Home Assistant Energy Dashboard directly.

### Which sensors are per-inverter and which are system totals

This matters on a parallel installation. Sensors prefixed **`c_`** are calculated and
scaled by `INVERTER_COUNT`; everything else is published unscaled, exactly as the
inverter reported it.

| | |
|---|---|
| `generation_power_w`, `load_w`, `pv_today_kwh`, `pv_total_kwh` | unscaled, as the vendor app shows them |
| `c_generation_power_w`, `c_load_w`, `c_generation_energy_kwh`, `c_load_energy_kwh` | system total |

The `c_*` energy counters are integrated from the `c_*` power sensors, so each is on the
same basis as its power partner. The device's own `pv_*_kwh` counters are left exactly as
the inverter reports them, so they continue to match the vendor app.

**If you are on a single inverter**, `INVERTER_COUNT` is 1 and the two columns are
identical.

For a value-by-value map against the vendor portal — every block, every token position,
and the exact list of fields the bridge cannot yet decode — see
[`sensor_mapping_verified.md`](https://github.com/fadmaz/siseli-ha/blob/main/sensor_mapping_verified.md). An earlier map is kept at
[`sensor_mapping.md`](https://github.com/fadmaz/siseli-ha/blob/main/sensor_mapping.md).

---

## Parallel inverters and battery banks

Set `INVERTER_COUNT` to the number of inverters sharing the dongle. The bridge treats the
figures on the wire as one inverter's and multiplies them:

```
c_load_w             = load_w            × INVERTER_COUNT
c_generation_power_w = generation_power_w × INVERTER_COUNT
c_mains_power_w      = mains_power_w     × INVERTER_COUNT
```

Battery power is handled differently: the BMS reports the **whole bank** already, so it is
used unscaled. When the bridge has to fall back to the inverter's own ammeter it scales
that by `INVERTER_COUNT` instead, so both sources stay on one basis.

> **This factor is inferred, not documented.** There is no vendor schema for this device.
> The evidence is one installation's night-time energy balance, which is *consistent with*
> per-inverter figures without establishing them. If the blocks turn out to carry system
> totals already, every `c_*` power — and the kWh counters integrated from them — is high
> by `INVERTER_COUNT`. A photograph of one inverter's rating plate, or a clamp meter on the
> AC output compared against `c_load_w`, would settle it.
>
> **At the default `INVERTER_COUNT` of 1 the multiplication is a no-op**, so this affects
> only parallel installations that have raised it.

A sanity check on your own data, but **only run it at night, off grid, with PV at zero**:
battery discharge power should exceed load by the inverter's conversion loss — expect a
ratio around 1.1, and treat anything near 2.0 as a sign `INVERTER_COUNT` is wrong.

Do not run it while PV is producing. Tried against a real capture at 13:41 with 2 kW of
sun, the same comparison is 40% out on a correctly configured install, because
conversion losses and the twin inverter's own array both land in the gap. It reports a
fault that is not there. The only measurement that settles the scaling outright is a
clamp meter on the AC output compared against `c_load_w`.

For the configured bank capacity sensor, set `BATTERY_COUNT` and
`BATTERY_CAPACITY_PER_BATTERY_AH` to the number of packs and the Ah printed on one of
them. Leaving the capacity at `0` disables that sensor. Note it is a **configuration
echo**, not a measurement — your BMS reports its own figure separately.

---

## Network setup

### Method A — ARP interception (default, recommended)

With `AUTO_INTERCEPT: true` the add-on tells the inverter that Home Assistant is the
gateway, and tells the router that Home Assistant is the inverter. The inverter's frames
then pass through the Home Assistant host, where its cloud connection is decoded and
forwarded on. The caveat below says what else is, and is not, relayed.

Nothing else is required. On shutdown the add-on restores both ARP caches so the inverter
goes straight back to the real gateway.

> **Some networks fight this.** UniFi, pfSense/OPNsense and enterprise switches may have
> ARP inspection or IP-source-guard features that block spoofed replies. If interception
> never establishes, use Method B.

#### A caveat on forwarding

By default the bridge relays two things: the inverter's connection to the cloud broker at
`TARGET_HOST:TARGET_PORT`, and everything the router sends to the inverter. Everything
else the inverter sends — DNS lookups (often addressed to the router itself), NTP, HTTP,
DHCP renewals, MQTT to any other address — is **dropped**, because the add-on is now the
inverter's gateway but is not a router.

The reference install keeps its broker session this way, but that is not a guarantee for
yours. [PR #43](https://github.com/fadmaz/siseli-ha/pull/43) reported, and this project has
not verified, that the dongle looks its broker up over DNS and HTTP when it reconnects
from scratch — after a power cut, say — and in the default mode those lookups are dropped.
If your inverter fails to reconnect, or the health line reports dropped packets:

```
[HEALTH] broker=up; avail=online; Last packet seen 12s ago; inverter_macs=[...]; router_macs=[...]; dropped_non_broker={'UDP:53': 40}
```

set `FORWARD_ALL_INVERTER_TRAFFIC: true`. It relays the inverter's other unicast traffic
that is addressed to the Home Assistant host. Broadcast and multicast are counted too, but
they were never lost: they reach the router directly.

`TCP:1883` in that counter means the inverter is talking MQTT to an address other than
`TARGET_HOST`, so its cloud connection is neither decoded nor relayed. Turn the `xray`
debug flag on briefly to see the address in the `[X-RAY]` lines, and set `TARGET_HOST` to
it.

With `AUTO_INTERCEPT: false` nothing is relayed at all.

### Method B — router-side redirect (advanced, unsupported)

Set `AUTO_INTERCEPT: false` and arrange for the inverter's traffic to reach the Home
Assistant host yourself. This works only if all three hold:

1. **The destination IP is preserved.** The bridge matches on
   `TARGET_HOST:TARGET_PORT`, so a NAT or DNS rewrite that changes the destination is
   never matched.
2. **The host forwards packets.** The add-on does not enable `net.ipv4.ip_forward` and
   will not relay anything in this mode. Without forwarding, the inverter loses its cloud
   connection entirely.
3. **The traffic is actually on the wire.** A **switch port mirror (SPAN)** to the Home
   Assistant host is the cleanest way to satisfy all of this, and is fully passive.

> **A DNS override does not work.** Pointing the Siseli domain at Home Assistant produces
> nothing, because there is no listener — the bridge observes traffic, it does not
> terminate it. The inverter's connection simply fails.

---

## Troubleshooting

### The log says packet capture stopped

```
[HEALTH] Packet capture stopped (1/3); the inverter is ARP-poisoned toward a bridge
that cannot forward. cause=...
[HEALTH] Packet capture restarted
```

The packet capture ended and the add-on started it again. That pair of lines means it
recovered on its own; the gap in readings is at most a few seconds. It is worth knowing
about but needs nothing from you.

The `cause=` value is the underlying error where one was reported. Capture ending without
any error is normal for a closed socket, in which case it reads `not reported`.

If capture fails three times in a row the add-on gives up:

```
[HEALTH] Packet capture will not stay up; restoring ARP and stopping so the add-on is
not left redirecting traffic it cannot forward
```

It undoes the ARP redirection first, so your inverter goes straight back to talking to
the real gateway, then exits with an error status. **This is where Watchdog matters** —
with it enabled Supervisor restarts the add-on; with it disabled the add-on stays stopped
until you start it.

Capture that keeps dying usually means the interface went away. Check `SNIFF_IFACE` if
you have pinned one, and see [Network setup](#network-setup).

With `AUTO_INTERCEPT` off the message says the inverter's own path is unaffected, because
in that mode the bridge only listens — nothing it does can interrupt your inverter.

### The log says "Decoded but NOT published"

```
[12:34:56] Decoded but NOT published -- broker unreachable clean_value_count=131 ...
```

The inverter's data was read correctly and the MQTT broker did not accept it, so nothing
reached Home Assistant. That is a broker problem, not an inverter one — see
[No entities appear](#no-entities-appear) and check `broker=` on the health line.

`Decoded but NOT published -- no broker connection yet` means the bridge has never
managed to connect at all, rather than having lost a working connection. Look for
`[HA MQTT] Cannot reach the broker` or `[HA MQTT ERROR] Broker refused the connection`
above it: the first is a wrong host, port or a stopped broker, the second is credentials.

`Decoded, publish throttled` is different and normal: the reading was fine and the
publish was skipped because nothing had changed since the last one.

### Every sensor reads Unknown

Not a few sensors — *all* of them, right from the first start. That means the bridge is
seeing your inverter's traffic but does not recognise a single one of its data blocks.

Check the add-on log for this line. It appears once, on its own, with no debug flags
needed:

```
[UNSUPPORTED PROTOCOL] note="none of this device's blocks are ones this add-on decodes; ..."
    block_count=13 recognised=0 names=[...] body="binary"
    body_shapes="ascii=1,binary=11" modbus_crc_ok="10/12" looks_like="modbus_rtu"
```

```
[UNSUPPORTED PROTOCOL] ... block_count=14 recognised=0 names=[...]
    body="ascii+binary_tail" body_shapes="ascii=3,ascii+binary_tail=11"
    voltronic_crc_ok="14/14" looks_like="voltronic_pi30"
```

**`recognised=0` is the verdict.** Your inverter speaks a protocol this add-on does not
decode, whatever the other fields say. This is not a fault in the add-on or your
configuration — the bridge deliberately publishes nothing rather than guessing at values
it cannot read.

`body` describes the *shape* of the blocks, not whether they are supported. A device can
be perfectly readable text and still be a protocol this add-on knows nothing about:

| `body` | Meaning |
|---|---|
| `ascii` | Plain text blocks, the same shape supported devices use |
| `ascii+binary_tail` | Text with a short checksum appended — common in the Voltronic/PI30 family |
| `binary` | Not text at all |

`looks_like` is the only protocol *claim*, and it is printed only when a checksum computed
over the blocks actually verifies against the bytes on the wire — so it is evidence rather
than a guess. `body_shapes` breaks down a mixed payload; a device whose data blocks are one
family and whose acknowledgements are another will show both.

Please open an [unsupported inverter issue](https://github.com/fadmaz/siseli-ha/issues/new?template=unsupported_inverter.yml)
with that line and the `[BLOCK RAW]` output described below. Adding a protocol is real
work, but it starts with a capture.

> The entities still appear because Home Assistant creates them from the add-on's
> discovery messages, which are published before any inverter data arrives. Entities
> existing is not evidence that anything was decoded.

### No entities appear

- Check the log for `[HA MQTT] Connected` and `[HA MQTT] Discovery published`. Two
  different failures look different: `[HA MQTT] Cannot reach the broker at HOST:PORT`
  means the broker is not answering at all — check `MQTT_HOST`, `MQTT_PORT`, and that
  the broker is running. A connection that is refused rather than unanswered means the
  credentials are wrong.
- Check for `[ARP] Interception ACTIVE`. If it never appears, the MAC addresses could not
  be resolved — set `INVERTER_MAC` and `ROUTER_MAC` manually.
- The health line every 30 seconds reports the broker and which MACs the bridge is
  seeing: `[HEALTH] broker=up; avail=online; Last packet seen 12s ago; inverter_macs=[...]`.
  `broker=DOWN` means nothing is reaching Home Assistant however healthy the rest looks. If `inverter_macs` is empty,
  no inverter traffic is reaching the capture — check `INVERTER_IP`, or pin `SNIFF_IFACE`.

### Entities go unavailable and come back

Check that `TELEMETRY_TIMEOUT_SEC` and `EXPIRE_AFTER_SEC` are both `1800` on the
Configuration page. Values stored by an older release are not updated by an upgrade.

### Energy Dashboard totals look wrong

If the totals are inflated or were accumulated by a version before 2.6.7, set
`RESET_ENERGY_COUNTERS` to `true`, restart the add-on once, then set it back to `false`.

### The log says [GRID DIRECTION CONFLICT]

The inverter reported grid power with one sign while its flow-direction code said the
opposite, or said idle. The two come from different fields, and no capture has yet recorded
the grid path while power was actually flowing, so the add-on cannot tell which one is
right. Nothing changes because of it: grid import is still credited from the power sign, as
before. The line is logged once per start.

That line is the evidence that settles the question. Please open an issue with it and, if
you can, a screenshot of the vendor app's grid page taken in the same minute.

### PV1 reads zero on a single-string system

Expected. Some inverters report the live string on the second MPPT input, and the official
app shows the same split. `c_generation_power_w` sums both, so the total is still correct.

### Your inverter is not decoded

Set **Debug Flags** to `blocks` and `unparsed_publish`, and **Log Level** to `info`, for
about two minutes — then turn them back off, because the output is per-packet. Open an
issue with the [unsupported inverter template](https://github.com/fadmaz/siseli-ha/blob/main/.github/ISSUE_TEMPLATE/unsupported_inverter.yml)
and attach the `[BLOCK RAW]` lines.

> **Scrub your log before posting it.** The `topic=` values contain your device serial.

---
