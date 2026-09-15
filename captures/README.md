# Captures

Paired readings of the same device at the same moment, from two sources:

- **The bridge** — a debug log with `DEBUG_FLAGS` set wide, giving every block and every
  token position, plus the values the add-on decoded from them.
- **The vendor portal** — `solar.siseli.com`, device detail page, which shows the
  manufacturer's own name and value for each field.

Each file is a full comparison of the two, covering **every parameter from both sides**,
including the ones present in only one.

| Capture | Device state | Notes |
|---|---|---|
| [2026-08-21 13:41 — charging](2026-08-21_1341_charging.md) | Off grid, charging, PV 2.055 kW, SOC 57% | Simultaneous to the second. 202 parameters, none disagree. |
| [2026-08-22 — Device B, unsupported](2026-08-22_device-b-modbus.md) | Beve Mega 6kW L1PE-ECO, from issue #30 | Not a decode. Records that this device speaks binary Modbus RTU inside the same DTU envelope, and what supporting it would take. |
| [2026-08-21 23:41 — discharging](2026-08-21_2341_discharging.md) | Off grid, discharging 27.3 A, PV 0, SOC 38% | Simultaneous to the second. Pairs with the row above: retires two hypotheses, confirms three decodes. |
| [2026-09-02 — Device C, Voltronic PI30](2026-09-02_device-c-voltronic-pi30.md) | Falcon VMIII-4000, from issue #32; battery mode, discharging 6 A, SOC 85% | Not a decode yet. Every frame CRC-verified and about 80 values paired to the portal to the second; support is planned. |

## Why these exist

Block positions in this project were reverse-engineered from one device with no published
schema. A capture paired with the vendor's own display is the only way to check a decode
against something other than itself — and the only way to find a field that is *wrong*
rather than merely missing. Several were found exactly this way.

## Taking one

1. Set **Debug Flags** to at least `blocks` and `unparsed_publish` (all of them is better
   for a reference capture), and **Log Level** to `info`. Turn them back off afterwards —
   the output is per-packet.
2. Open the device's *Data Overview* page on the portal.
3. Capture both **within the same minute**. The bridge's `[... ] Published to HA` line and
   the portal's `UpdateTime` should agree, and both should report the same inverter clock.
4. Scrub the log before sharing it: the `topic=` value contains the device serial.

## What makes a capture worth taking

45 fields have a value in the portal and read `unknown` in the bridge. They are
almost all flags, and in a healthy machine **every one of them reads its safe value** —
`No`, `Off`, `Close`. A capture in that state cannot distinguish them no matter how
carefully it is compared.

The captures that would move things forward:

| State | What it would settle |
|---|---|
| **One switch toggled alone** | Which `eo8w[2]` position is the charging light and which is the charging main switch. Three positions move together today, so nothing can be assigned. Toggle exactly one in the vendor app. |
| **On grid** | The mains flags and the grid-import path, which has never been exercised |
| **A fan at 0%** | Whether `V4W3[7]` is the fan status, and what `Abnormal Fan Speed` reads |
| **During any fault** | The sixteen fault flags, all of which read `No` in every capture so far |
| **Second output capacity set to a single digit** (e.g. 5%) | Whether `dHrK`[16] is a fixed two-digit field or a variable-width number in a five-character slot. `05000` means the current decode is right and 100% is simply unrepresentable; `50000` means the field is variable-width and every value above 99 is being misread |
| **A different device** | A 120 V / 60 Hz unit would separate several fields that happen to share a value on this install |

Two entries that used to be on this list are done, and one was never going to work:

- **Discharging** — taken 2026-08-21 23:41. It did *not* move the `Yavb` flag word, and it
  could never have: *Allow Charging* and *Allow Discharge* are permissions the pack grants,
  not a description of what it is doing, and both read `Yes` in every state.
- **After dark, PV = 0** — same capture. `Solar Charging Switch` reads `Close` with the
  string dark, and `noeP[3]` moved `2` → `0`, so that token tracks PV activity rather than
  topology. Nothing there is a settings field.
- **A single-inverter capture** is not needed for `total_number_of_grid_connection`; the
  dark-PV reading already settled it.

The one open question that no capture will answer is the `INVERTER_COUNT` scaling. It
needs a photograph of an inverter's rating plate, or a clamp meter on the AC output —
see the discharging capture for why.

See [`../sensor_mapping_verified.md`](../sensor_mapping_verified.md) for the running
analysis these captures feed.
