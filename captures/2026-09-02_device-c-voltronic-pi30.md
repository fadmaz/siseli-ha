# Device C — Falcon VMIII-4000 — Voltronic PI30

Reported in [issue #32](https://github.com/fadmaz/siseli-ha/issues/32) against add-on 2.6.17
with every sensor reading `Unknown`, then re-captured on 2.6.20 on 2026-09-02 together with
four screenshots of the vendor portal. **This device is not supported yet and this file is
not a decode.** It records what the captures prove, so the decoder is built from evidence
rather than rediscovered.

Unlike [Device B](2026-08-22_device-b-modbus.md), this one clears the bar a decode needs:
the re-capture is byte-faithful, every frame carries its own checksum, and the portal
screenshots pair with it to the second. Support is planned and tracked in #32; what is
still missing is listed at the end.

## What is shared, and what is not

| | Device A (supported) | Device C |
|---|---|---|
| MQTT topics | `dtu/<id>/pub/event/dev_prop_post` | **same**, and also `dtu/<id>/pub/service/dev_rpc_reply` carrying the same data |
| Envelope | JSON, base64 blocks under `b.ct`, `cn`/`co` keys | **same** — see [`../docs/DTU_PROTOCOL.md`](../docs/DTU_PROTOCOL.md) |
| Block names | `2ONL 2l0E 93VQ COST Mpod V4W3 WdRR Yavb dHrK eo8w noeP` | 24 names, **no overlap** with Device A or B |
| Block body | ASCII, `(`-framed, space-separated tokens, no checksum | ASCII, `(`-framed, space-separated tokens, **then a two-byte CRC** before the CR — Voltronic/Axpert **PI30** query replies |

`cCft` answers `(PI30`: the protocol names itself.

## Every frame verifies

CRC16-XMODEM — poly `0x1021`, init `0x0000`, stored big-endian — computed over the frame
from the leading `(` up to, but not including, the two CRC bytes. **All 48 blocks in the
four payloads verify**: 25 distinct bodies, since the clock block differs between the two
sets. No frame is truncated; since 2.6.18 `hex_preview` logs up to 4096 bytes and marks a
cut. The 22 complete frames of the first report verify too; its `G4WT` and `MrfS` were cut
at 64 bytes by the old preview limit, which is why this re-capture was needed.

The CRC bytes are often printable, so the logged `text` of a block is **not** its value:
`G4WT` ends in a stray `~`, `MrfS` in `Z`, `sJqt` in `x2`, and `EMu5`'s CRC happens to be
valid UTF-8. A decoder must strip the two bytes before tokenising. `_parse_ascii_text`
does not, because Device A frames have none. mpp-solar bumps a CRC byte that would collide
with `(`, CR, LF or NUL; no CRC byte in either capture hit that case, so how this device
handles it is untested.

## Which query each block answers

Basis: **S** matches the public PI30/PI30MAX response format; **P** is paired to a portal
value; **B** is byte-identical to a published mpp-solar test frame.

| Block | Fragment | Query | Carries | Basis |
|---|---|---|---|---|
| `cCft` | 1 | QPI | protocol id `PI30` | S B P |
| `G5E9` | 1 | QSID | serial number, long form | S |
| `ahLb` | 1 | QID | serial number | S P |
| `o2lC` | 1 | QVFW | main CPU firmware `00060.10` | S P |
| `ag5g` | 1 | QVFW3 (QVFW2 not excluded) | secondary firmware `00025.12` | S P |
| `MrfS` | 1 | QPIRI | ratings and settings, 25 fields | S P |
| `sJqt` | 1 | QFLAG | enable/disable flags `EakxyzDbdjuv` | S P |
| `G4WT` | 1 | QPIGS | live status, 24 fields | S P |
| `Ezgh` | 1 | — | `NAK` | S |
| `zZ3K` | 1 | QMOD | mode `B`, battery (it was `L`, line, on 08-31) | S B P |
| `DB48` | 1 | QMCHGCR | selectable max charging currents, 10–120 A | S B P |
| `wb83` | 1 | QMUCHGCR | selectable max utility charging currents, 2–100 A | S P |
| `oTLG` | 1 | QOPPT | output-priority schedule, hourly array | S |
| `48rR` | 1 | QCHPT | charger-priority schedule, hourly array | S |
| `lCMp` | 2 | QT | inverter clock, `YYYYMMDDhhmmss` | S P |
| `7v9T` | 2 | QBEQI | equalisation settings | S P |
| `EMu5` | 2 | QMN | model `VMIII-4000` | S P |
| `9gbt` | 2 | QGMN | general model `055` | S P |
| `cT7S` | 2 | QET | lifetime PV energy, Wh | S P |
| `mA9W` | 2 | QLT | lifetime load energy, Wh | S P |
| `UefO` | 2 | QBMS | BMS link, reporting itself disconnected | S P |
| `u51Q` | 2 | **unresolved** | `1` — QBOOT fits; QOPM contradicts `MrfS` field 22 | — |
| `TWfA` | 2 | — | `NAK` | S |
| `W7EX` | 2 | — | `NAK` | S |

Twenty identified, three NAKs (queries this firmware does not answer), one unresolved.

The name-to-query map held across all three sets seen (08-31 `dev_prop_post`, 09-02
`dev_rpc_reply`, 09-02 `dev_prop_post`), and the same 14 names always sit in fragment 1.
It is proven for **one device**: Devices A and B share no names with it, and nothing yet
shows whether another PI30 dongle reuses these names. Shape alone cannot tell QET from QLT
(both eight digits), QOPPT from QCHPT when their values are equal, or QVFW from QVFW3 (both
`VERFW:`), so a decoder needs the name map, not just pattern matching.

## `G4WT` is QPIGS — paired with the portal

| # | Wire | Field | Portal |
|---|---|---|---|
| 1 | `219.4` | grid voltage, V | 219.4 V |
| 2 | `49.7` | grid frequency, Hz | 49.7 Hz |
| 3 | `230.0` | AC output voltage, V | 230 V |
| 4 | `49.7` | AC output frequency, Hz | 49.7 Hz |
| 5 | `0368` | AC output apparent power, VA | 368 VA |
| 6 | `0258` | AC output active power, W | 0.258 kW |
| 7 | `009` | output load, % | 9 % |
| 8 | `436` | bus voltage, V | 436 V |
| 9 | `27.60` | battery voltage, V | 27.6 V |
| 10 | `000` | battery charging current, A | 0 A |
| 11 | `085` | battery capacity, % | 85 % |
| 12 | `0049` | heat-sink temperature | 49 °C |
| 13 | `01.2` | PV1 input current, A | 1.2 A |
| 14 | `184.1` | PV1 input voltage, V | 184.1 V |
| 15 | `00.00` | battery voltage from SCC, V | 0 V |
| 16 | `00006` | battery discharge current, A | 6 A |
| 17 | `00010000` | status bits b7…b0 | all eight paired: load on, everything else off or unchanged |
| 18 | `00` | fan-on voltage offset, 10 mV units | 0 V |
| 19 | `00` | EEPROM version | 0 |
| 20 | `00151` | PV1 charging power, W | 151 W |
| 21 | `011` | status bits b10 b9 b8 | b9 paired ("Switch On: yes"); b10 (float) and b8 not shown |
| 22 | `0` | reserved, solar feed to grid | "Solar feed to grid: disable" — may come from QFLAG instead |
| 23 | `01` | reserved, country regulation | not shown |
| 24 | `0054` | reserved, feed-in power | not shown; unexplained on an off-grid unit in battery mode |

Field 20 is the device's own figure; 184.1 V × 1.2 A would be 221 W. Publish what the
device states, never a derived value. PI30MAX describes field 12 as a raw NTC reading on
1–3 kW models; the vendor displays it as °C for this 4 kW unit.

## `MrfS` is QPIRI — all 25 fields paired

| # | Wire | Field | Portal |
|---|---|---|---|
| 1 | `230.0` | grid rating voltage | 230 V |
| 2 | `17.3` | grid rating current | 17.3 A |
| 3 | `230.0` | AC output rating voltage | 230 V |
| 4 | `50.0` | AC output rating frequency | 50 Hz |
| 5 | `17.3` | AC output rating current | 17.3 A |
| 6 | `4000` | AC output rating apparent power | 4000 VA |
| 7 | `4000` | AC output rating active power | 4000 W |
| 8 | `24.0` | battery rating voltage | 24 V |
| 9 | `25.0` | battery re-charge voltage | 25 V |
| 10 | `24.0` | battery under voltage | 24 V |
| 11 | `28.8` | battery bulk voltage | 28.8 V |
| 12 | `27.6` | battery float voltage | 27.6 V |
| 13 | `2` | battery type | User |
| 14 | `060` | max AC charging current | 60 A |
| 15 | `030` | max charging current | 30 A |
| 16 | `1` | input voltage range | UPS |
| 17 | `2` | output source priority | "Photovoltaic → Battery → mains" (SBU) |
| 18 | `2` | charger source priority | Solar + Utility |
| 19 | `1` | parallel max number | 1 |
| 20 | `10` | machine type | 10 |
| 21 | `0` | topology | Transformerless |
| 22 | `0` | output mode | single machine |
| 23 | `27.0` | battery re-discharge voltage | 27 V |
| 24 | `0` | PV OK condition for parallel | "One inverter has connect PV" |
| 25 | `1` | PV power balance | Max Power |

Field 14 has three digits where the spec shows two; the portal settles the order of 14 and
15. The portal's "Maximum charging current 120 A" and "Maximum utility charging current
100 A" are not settings — they are the largest entries in `DB48` and `wb83`.

These are **live settings, not constants**. Between the two captures the owner changed the
output priority, input range, re-charge voltage, float voltage and equalisation voltage.

## The system, as the wire describes it

A 24 V bank (rating 24.0, cut-off 24.0, bulk 28.8, float 27.6) on a 4000 VA / 4000 W,
230 V / 50 Hz hybrid, machine type 10. Single unit, single PV input, battery type "User",
no BMS link, equalisation off. At the capture it was in battery mode, discharging 6 A at
85 %, on 151 W of PV, with the grid present (219.4 V) but unused under SBU priority.
Lifetime PV is 253.8 kWh and lifetime load 114.1 kWh (`00253800` and `00114100` Wh; every
sample so far ends in `00`, which suggests 100 Wh resolution). The "4200W" in the issue
title is not on the wire; `EMu5`'s `-4000` is QMN's rated-VA suffix.

## Pairing to the second

- The screenshot reading "UpdateTime 2026-09-02 17:07:53" is 2 s after the
  `dev_rpc_reply` set's `ts` of 17:07:51.
- The screenshot reading "System Time 17:05:54" is exactly the `dev_prop_post` set's QT
  block. The portal renders the month as `8`, apparently counting from zero.
- Every block except QT is byte-identical between the two sets, so each portal value
  pairs with both.
- About 80 wire values have a portal counterpart: roughly 28 from `G4WT`, 25 from `MrfS`,
  10 QFLAG flags and 8 QBEQI fields, plus model, firmware, QMOD, QT, QET and QLT.

Some portal values have **no wire source** and must not be published: "PV2 … 0" (there is
no QPIGS2 on the wire), and "Battery percentage 0 %", cut-off and C.V. voltages of 0 V,
which match the QBMS zeros of a BMS reporting itself disconnected.

## Envelope and fragmentation

One 24-query response set is split across two MQTT messages (`tf=2`): 14 blocks in
`cf=1`, 10 in `cf=2`, the same split every time. A `dev_rpc_reply` set and the next
`dev_prop_post` set carry the same data. `ts` is the DTU's clock (+08:00); the inverter's
own clock runs 134–178 s behind it, and two QT readings 61 s apart arrived in sets whose
`ts` differ by 17 s. So the DTU polls the inverter on its own cycle and publishes cached
replies: `ts` is a publish time, not a sample time. The envelope itself is described in
[`../docs/DTU_PROTOCOL.md`](../docs/DTU_PROTOCOL.md).

## What a decoder has to get right

1. **An explicit name map, per device.** Recognise the protocol from `QPI` = `PI30` and the
   CRC, then map names to queries; do not infer a query from a block's shape.
2. **Strip the CRC** before splitting tokens.
3. **Count response sets, not messages.** Two fragments per set, plus replies triggered by
   an open portal page, would otherwise look like extra readings. The cadence measurement
   takes the largest recent gap, so this is a correctness point for a decoder rather than
   a fault today.
4. **Use the device's own energy counters.** QET and QLT are Wh totals, so they become
   `total_increasing` sensors directly and nothing needs integrating.
5. **Treat settings as live.** QPIRI, QFLAG and QBEQI change when the owner changes them.
6. **Publish only what the wire says** — see the portal-only values above.

## Still missing

1. **Cadence.** Each capture holds a single `dev_prop_post` set, and timing bounds come
   from measured cadence. A 30–60 minute log is needed first.
2. **Other states,** each paired with the portal: solar charging (battery current above
   0), line mode with AC charging, and after dark.
3. **Warnings and faults.** QPIWS is not among the queries, and three are NAKs whose query
   is unknown; the `raw_json` debug flag would log the block order and let them be
   attributed. No fault entity is possible yet.
4. **Unpaired fields:** `G4WT` 21 (b10, b8) and 22–24, `u51Q`, the QOPPT/QCHPT arrays, and
   the reserved QBEQI fields.

## Fixtures

`CAPTURE_DEVICE_C_VOLTRONIC` in `tests/captures.py` comes from the first report. It merges
blocks from both fragments — a payload this device never sends — and exists to test the
unsupported-protocol diagnostic, not as a decode target. Decode fixtures should be
per-fragment and taken from the paired set below.

## Sources

- Voltronic, *PI30MAX Communication Protocol* (2021-02-17), in
  [BMBIT-oss/Various-Solar-Protocols-Docs](https://github.com/BMBIT-oss/Various-Solar-Protocols-Docs)
- mpp-solar's [`pi30.py`, `pi30max.py` and `protocol_helpers.py`](https://github.com/jblance/mpp-solar/tree/master/mppsolar/protocols)
- The reporter's capture and screenshots, in
  [this comment on #32](https://github.com/fadmaz/siseli-ha/issues/32)

## Appendix — the portal-paired `dev_prop_post` set, verbatim

Each block's `hex_preview` exactly as the bridge logged it on 2026-09-02 (DTU `ts`
17:08:08 +08:00), every one CRC-verified when this file was written. `ahLb` and `G5E9`
are omitted because they carry the owner's serial number; the payload-level log lines
are left out for the same reason, since their base64 contains those blocks too.

```text
fragment 1 of 2
  48rR  2832203220322032203220322032203220322032203220322032203220322032203220322032203220322032203220322032203020302030407f0d
  DB48  2830313020303230203033302030343020303530203036302030373020303830203039302031303020313130203132300ccb0d
  Ezgh  284e414b73730d
  G4WT  283231392e342034392e37203233302e302034392e372030333638203032353820303039203433362032372e3630203030302030383520303034392030312e32203138342e312030302e30302030303030362030303031303030302030302030302030303135312030313120302030312030303534e77e0d
  G5E9  (omitted: serial number)
  MrfS  283233302e302031372e33203233302e302035302e302031372e33203430303020343030302032342e302032352e302032342e302032382e382032372e36203220303630203033302031203220322031203130203020302032372e3020302031915a0d
  ag5g  2856455246573a30303032352e3132ab390d
  ahLb  (omitted: serial number)
  cCft  28504933309a0b0d
  o2lC  2856455246573a30303036302e3130be380d
  oTLG  2832203220322032203220322032203220322032203220322032203220322032203220322032203220322032203220322032203020302030407f0d
  sJqt  2845616b78797a4462646a757678320d
  wb83  28303032203031302030323020303330203034302030353020303630203037302030383020303930203130305c460d
  zZ3K  2842e7c90d

fragment 2 of 2
  7v9T  2830203036302030333020303330203033302032372e363020303030203132302030203030303061ee0d
  9gbt  28303535ebbe0d
  EMu5  28564d4949492d34303030de930d
  TWfA  284e414b73730d
  UefO  2831203030302030203020302030303020303030203030302030303030203030303080860d
  W7EX  284e414b73730d
  cT7S  283030323533383030f2d00d
  lCMp  28323032363039303231373035353416340d
  mA9W  2830303131343130307a8a0d
  u51Q  2831a93d0d
```
