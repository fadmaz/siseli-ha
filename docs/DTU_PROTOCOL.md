# The DTU's MQTT transport

What this project knows about how the inverter's Wi-Fi dongle — the DTU — talks to the
Siseli cloud. The add-on reads this traffic and forwards the DTU's cloud connection
unchanged; it never sends on it (see [`SECURITY.md`](../SECURITY.md)). This file exists so
the traffic it sees can be understood.

Two kinds of statement are kept apart here:

- **Observed** — seen in captures taken with this add-on.
- **Reported** — from [PR #43](https://github.com/fadmaz/siseli-ha/pull/43) by
  [@siselilocal](https://github.com/siselilocal), who built a local stand-in for the cloud.
  Not verified here. Corrections are welcome in an issue.

## Observed

### Topics

The DTU publishes on `dtu/<id>/pub/event/dev_prop_post` on every device seen so far, and on
Device C also on `dtu/<id>/pub/service/dev_rpc_reply`. `<id>` is the DTU's serial number,
so scrub it before sharing a log.

### The payload

On Device C — the only device whose raw payload bytes have been logged — each PUBLISH
payload is a single `0x00` byte followed by JSON:

```text
\x00{"c":1,"t":"<8 chars>","s":"<9 chars>","i":101,"e":0,
     "b":{"sa":"","ts":"2026-09-02T17:08:08.000+08:00","lf":0,"tf":2,"cf":1,
          "ct":[{"cn":"cCft","co":"KFBJMzCaCw0="}, …]}}
```

| Field | Seen | Meaning |
|---|---|---|
| `c` | `1` on `dev_prop_post`, `5` on `dev_rpc_reply` | message class (inferred) |
| `i` | `101` on `dev_prop_post`, `502` on `dev_rpc_reply` | message type (inferred; see *Reported*) |
| `t` | shared by both fragments of a set, new for each set | transaction id (inferred) |
| `s` | different in every message | nonce (inferred) |
| `e` | always `0` | status (inferred) |
| `b.sa` | always empty | — |
| `b.ts` | identical in both fragments, `+08:00` | the DTU's clock when it published — not when the inverter was read |
| `b.tf`, `b.cf`, `b.lf` | `2`; `1` then `2`; `0` then `1` | total fragments, 1-based fragment index, last-fragment flag |
| `b.ct[]` | `cn` a four-character block name, `co` base64 | one entry per inverter reply |

The patterns hold across all three Device C sets captured; the meanings are inference. The
leading `0x00` is consistent with an empty MQTT 5 property block, but the protocol level
of the CONNECT has never been logged, so that is a hypothesis.

### Fragmentation

A response set can be larger than one message. Device C's 24 blocks always arrive as 14
then 10. Blocks are self-contained, so each fragment decodes on its own. Device A's "two
payloads with zero overlap" ([capture](../captures/2026-08-21_1341_charging.md)) look like
the same thing: that capture's envelope appendix shows the same `ts` on both. It did not
record `tf`, `cf` or `lf`; the `raw_json` debug flag would show those next time. It
cannot show `c`, `t`, `s`, `i` or `e`, which the parser discards before logging.

### What a block is

Each `co` is the base64 of one inverter reply. For Devices B and C, whose frames carry a
checksum that verifies, that reply is demonstrably the raw serial frame. Device A's frames
have no checksum, so whether the DTU passes them on verbatim is unknown.

| Device | Body | Notes |
|---|---|---|
| A (supported) | ASCII, `(`-framed, space-separated tokens, no checksum | [`sensor_mapping_verified.md`](../sensor_mapping_verified.md) |
| B — [#30](https://github.com/fadmaz/siseli-ha/issues/30) | binary Modbus RTU with CRC16/Modbus | [Device B note](../captures/2026-08-22_device-b-modbus.md) |
| C — [#32](https://github.com/fadmaz/siseli-ha/issues/32) | ASCII Voltronic PI30 replies with CRC16-XMODEM | [Device C note](../captures/2026-09-02_device-c-voltronic-pi30.md) |

Block names are opaque codes. Device A's recur on a second unit of the same kind (the
reference unit behind `sensor_mapping.md`), so they are not unique per device, but no name
is shared between Devices A, B and C.

### `dev_rpc_reply` carries the same blocks

On Device C, the one `dev_rpc_reply` set captured carries, byte for byte, the same blocks as
the `dev_prop_post` set that followed it, except for the clock block. The envelopes differ.
The reply coincided with the vendor portal's device page being open — the page shows an
auto-refresh countdown — which suggests the portal triggers it. That part is inference.

## How the add-on uses it

`handle_inverter_tcp_packet` in `core.py` passes every PUBLISH on the inverter-to-cloud
stream to `SolarParser.parse_payload`, whatever its topic. The parser re-roots at the `"b":`
key, so the leading byte and `c`, `t`, `s`, `i` and `e` are discarded, and it never reads
`ts`, `tf`, `cf` or `lf`. Each fragment is parsed on its own.

## Reported by PR #43 — not verified here

PR #43 replaced the cloud with a local stand-in and recorded what the dongle expected of it,
tested against a Datouboss 11 kW inverter. The captures behind it were not included, so
these are reports, not observations. Where something can be checked against a capture
here, that is said.

- **Asking for a reading.** The cloud publishes `{"c":5,"t":…,"s":…,"i":501,"b":{}}` on
  `dtu/<id>/sub/service/dev_rpc`, and the DTU answers on
  `dtu/<id>/pub/service/dev_rpc_reply`. The author's capture shows the vendor cloud doing
  this about every 26 s. *Observed here: Device C's replies carry `c` = 5 and `i` = 502, one
  higher than the reported request.*
- **Acknowledgements.** For a message the DTU originates, the cloud replies on the same
  topic under `sub/` with `_reply` appended, echoing `c` and `t`, with a fresh `s`,
  `i` + 1 and `e` = 0. When that acknowledgement was missing or malformed, the DTU retried
  its identity message (`dtu_prop_post`) three times with backoff and never moved on to
  `dev_prop_post`. The PR saw the same symptom when the HTTP bootstrap below was
  unreachable.
- **The leading `0x00`** is on every PUBLISH the firmware sends, and it expects the same on
  every PUBLISH it receives. *Consistent with every Device C payload.*
- **Replies come from a cache.** `dev_rpc` returns what the DTU last read from the
  inverter, which it refreshes in staggered groups of blocks, each on a cycle of about 60 s
  (measured on one device).
- **Bootstrap.** Before its MQTT session the DTU calls `dtu.access.solar.siseli.com` over
  plain HTTP, in this order on one connection:
  1. `POST /dtu/checkin`, which returns the time and a collector-protocol configuration;
  2. `POST /dtu/devices/findSingle`;
  3. `GET /dtu/servers/mqtt`, whose `data.main.host` is the broker it then resolves and
     connects to. In the author's capture that was `hongkong.broker.mqtt.solar.siseli.com`,
     port 1883, no TLS; the region prefix suggests it can differ elsewhere.

  The HTTP calls carry the same credentials as the MQTT CONNECT. The DTU sometimes skips
  DNS and dials a cached address, `8.212.16.60`.
- **Commands.** The PR replays commands it captured from the vendor cloud, sent on
  `dev_rpc` with `i` = 503. Two are standard PI30 flag commands (backlight and buzzer,
  `PEx`/`PDx` and `PEa`/`PDa`). Two are not in the published PI30MAX document: a
  "dual output" switch and a fault-clear. This add-on never sends on `dev_rpc`.

## Why the add-on does not use any of this

Asking for a reading, or sending anything else, means modifying or terminating the DTU's
connection instead of forwarding it, and the add-on would become something that talks to
the inverter. This project's premise is the opposite: observe, forward, never inject.
PR #43 goes further still and replaces the cloud, at which point the vendor app loses its
data. The notes above are here to explain the traffic this add-on sees.
