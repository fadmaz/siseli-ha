# Voltronic PI30 support — design

**Status: Proposed — awaiting the maintainer's approval. Nothing here is implemented.**

This is the design for decoding the second protocol family this add-on has met: Voltronic
PI30, spoken by the Falcon VMIII-4000 in
[issue #32](https://github.com/fadmaz/siseli-ha/issues/32). The evidence is in
[`captures/2026-09-02_device-c-voltronic-pi30.md`](../captures/2026-09-02_device-c-voltronic-pi30.md)
and the transport in [`DTU_PROTOCOL.md`](DTU_PROTOCOL.md). Three rounds of adversarial
review shaped it; the alternatives they rejected are listed at the end so they are not
re-proposed. It builds on 2.6.24 (the deferred-publish flush, `publish_tick`,
`PUBLISH_LOCK` and the saved energy clocks).

**Terms.** A **set** is one response set: the DTU's replies to all 24 queries of one polling
cycle. The DTU splits each set into two MQTT messages, **fragment 1** (14 blocks) and
**fragment 2** (10 blocks), with the same `b.ts`. A set arrives on `dev_prop_post`, the DTU's
own cycle, or on `dev_rpc_reply`, when an open portal page asks for one. A **registry** is a
sensor dict: `SENSORS` (Device A, 207 keys) or the new `PI30_SENSORS`. They share one key,
`dtu_id`, with identical metadata.

## Goals and non-goals

- Decode the PI30 fields the evidence supports, for this device's block names, under the
  project's rule: publish a value only when this payload carries evidence for it.
- **Leave every Device A install, fresh or existing, byte-for-byte unchanged**, the
  maintainer's included: MQTT publishes, `state.json` and log lines. This is tested, not
  asserted (§10 item 1). The one deliberate exception is in the table below.
- No new option. A new option costs six places (CLAUDE.md) and, once stored, shadows every
  later change to its default.
- Not a goal: PI30 devices whose block names differ from the proven map. They get a clear
  diagnostic, not a guess. Not a goal: any write path to the inverter.

**Implementation waits for two things:** approval of this document, and the cadence log
already requested from the #32 reporter.

### What changes for Device A

| Situation | Today | With this design |
|---|---|---|
| Existing install, readable cache | Device A discovery at connect | Identical. The cache holds Device A values, so the protocol resolves to Device A. |
| Fresh install, no cache | Device A discovery at connect; entities read Unknown until the first decode | Identical. An undetermined protocol behaves as Device A. |
| Corrupt or unreadable cache | Same as a fresh install | Identical. |
| Broker down at the first decode | "no broker connection yet" per payload; discovery at the first connect | Identical. A Device A decode matches the protocol, so nothing switches. |
| `RESET_ENERGY_COUNTERS` on | Its warning and the energy-clock line | Identical. Both are suppressed only for a PI30 cache. |
| Marker names a previous `DEVICE_ID` | The sweep clears Device A's topics under the old id | Also clears PI30's topics under the old id. Those are empty retained publishes to topics no Device A install ever wrote. **This is the one byte difference.** |
| Unsupported non-PI30 device (#30) | 207 Unknown entities, `[UNSUPPORTED PROTOCOL]` | Identical. This design does not remove them (see Rejected). |
| Hardware swapped, PI30 → Device A | — | Switches back. Energy clocks are cleared, so the gap is not credited. |

## 1. Which protocol an install speaks

**No persisted setting.** The protocol is inferred at startup from evidence that already
exists: `/data/state.json`, which is written only after a decode that produced values.

**Startup leaves `__main__`.**
- `__main__` makes one call, to a module-level `prepare_startup_state(path)` in `core.py`.
  It comes after `validate_config()` and `install_signal_handlers()`, and before
  `start_mqtt()`.
- `prepare_startup_state(path)` runs, in this order:
  1. `record = load_cached_state(path)`;
  2. `resolve_startup_protocol(record)`;
  3. `log_startup_configuration()`;
  4. `seed_state()`, today's `__main__` loop, moved, now iterating `sensors_for_protocol()`.
- It takes `path` because `load_cached_state`'s default argument binds `STATE_CACHE_FILE`
  when the function is defined.
- No startup ordering is left in `__main__`, which no test executes.

**`resolve_startup_protocol()`**
- It reads `_state.LAST_STATE`. It returns PI30 only when `LAST_STATE` holds a non-null
  `pi30_*` value and no non-null Device A value other than `dtu_id`. Anything else is
  Device A: no cache, a corrupt cache, a cache holding both registries, or one whose only
  value is `dtu_id`.
- It removes the inactive registry's keys other than `dtu_id` from `LAST_STATE`. It
  deletes them in place, so the remaining keys keep their order: that order is the byte
  order of every replayed state payload. `dtu_id`'s cached value is the device card's
  serial number and the Collector ID reading.
- It never raises. On any error it logs and leaves Device A.
- `log_startup_configuration()` prints `[Config] PROTOCOL=pi30` when `_state.PROTOCOL` is
  PI30, and nothing new for Device A.

**Reserved keys.** `load_cached_state()` pops `_energy_clocks` and the new `_protocol_switch`
(§2) before its registry filter runs, so neither reaches `LAST_STATE`.
- It returns the `_protocol_switch` record, and `prepare_startup_state()` hands it to
  `resolve_startup_protocol(record)`.
- `resolve_startup_protocol(record)` sets `_state.SWITCH_PENDING` from it only when all
  three hold:
  - the record is exactly `{"clear": R}`;
  - `R` is a registry this build defines;
  - `R` is not the protocol just resolved.
- Anything else is dropped with a `[CACHE]` warning. A record this build cannot act on, for
  instance one written by a later build, is therefore dropped rather than left to block
  publishing.

**Other startup behaviour.**
- `load_cached_state()` filters cached keys against `SENSORS | PI30_SENSORS`, instead of
  `SENSORS` alone.
- It treats the raw cache as PI30 by the same test `resolve_startup_protocol()` applies: a
  non-null `pi30_*` value and no non-null Device A value other than `dtu_id`. It checks this
  before any key is removed. For a PI30 cache it prints neither its
  `RESET_ENERGY_COUNTERS` warning nor its energy-clock line. For any other cache both print
  exactly as today: with the option on or off, with a clock record or none, whether or not
  counters were dropped.
- `_state.PROTOCOL` defaults to Device A at import, so every existing test that drives
  `on_connect` is unchanged.

**An undetermined protocol behaves as Device A at connect.** Discovery, the sweep and the
replay run exactly as today. A fresh PI30 install therefore shows Device A entities until
its first PI30 decode, when the switch clears them. That is the same path the #32 upgrader
takes.

## 2. The switch

A decode switches the protocol when it contradicts `_state.PROTOCOL`: a PI30 decode while
the protocol is Device A, or the reverse. The switch has two halves. The capture thread
must not block on about 200 publishes, and Home Assistant must see the old entities removed
before the new ones arrive.

**The capture-thread half** runs in `parse_payload`, after `update_state(clean_state)` and
before `record_telemetry`, `_write_state_cache` and the publish gate. `snapshot` is taken
again after it. Steps 1–4 run under `STATE_LOCK`, in this order:

1. Remove the other registry's keys, except `dtu_id`, from `LAST_STATE` and
   `PUBLISHED_SENSOR_KEYS`, in place.
2. Seed the active registry's missing keys with `None`. A null is what tells Home Assistant a
   value is unknown.
3. When leaving Device A, also clear `LAST_ENERGY_TS` and `RESTORED_ENERGY_DOMAINS`.
   Otherwise a later switch back would credit the stale interval into counters that
   restarted at 0.
4. Reset the MQTT half's state: `SWITCH_CLEAR_INFOS`, `SWITCH_CLEARED_TS` and
   `SWITCH_CLEAR_FAILED_LOGGED`. Then set `SWITCH_PENDING` to the registry to clear, and
   `PROTOCOL` to the active one, last. An exception before this point leaves the old
   protocol, and the next payload retries the whole half. Every step is idempotent.

After the lock is released, it logs `[PROTOCOL]` once, naming both protocols.

**Pending clears survive a restart.** While `SWITCH_PENDING` is set, `_write_state_cache`
adds `_protocol_switch: {"clear": "<registry>"}` to the same atomic record as the values.
§1 restores and validates it. Without this, a restart after the switching payload's cache
write, but before the clears landed, would resolve to PI30 and never clear the Device A
configs. Older builds drop the unknown key through the existing filter, as they do
`_energy_clocks`.

**The MQTT half** is `apply_pending_switch()`, in `mqtt.py`, because `on_connect` calls it
and `core` imports `mqtt`.
- **Locking and callers.** It runs under `_state.DISCOVERY_LOCK`, a `threading.RLock`:
  `on_connect` calls it from inside its own `DISCOVERY_LOCK` block, which a plain lock
  would deadlock.
- It returns at once unless `SWITCH_PENDING` is set and `broker_is_connected()`.
- It is called by `on_connect`, and by `publish_tick` first thing inside its existing `try`.
  So a failure is logged as `[PUBLISH TICK ERROR]` and retried at the next tick. It is
  never called by the capture thread.

It works in two phases.

**Phase 1: clear.** Publish an empty retained payload at **QoS 1** to every topic the other
registry's keys map to under the current `DEVICE_ID` and prefix. That is
`device_id_for_group(get_sensor_group(k))` for each key `k` except `dtu_id`, whether or not
this process published it. This does not depend on `DISCOVERY_CLEANED`, the marker or
`DISCOVERY_CLEANUP`, because those configs describe sensors this device cannot have.
- Each clear's `MQTTMessageInfo` is kept in `_state.SWITCH_CLEAR_INFOS`.
- **When a clear has landed.** Only when its `rc` is 0 and `is_published()` is true, which
  paho sets for QoS 1 when the broker's PUBACK arrives. An `rc` of 0 means only that paho
  queued the packet, and `reconnect()` discards that queue.
- **Checking.** Check `rc` before calling `is_published()`, which raises when `rc` is
  non-zero. Later calls poll the stored infos. `wait_for_publish()` is never used: from
  `on_connect` it would block the network thread that has to read the PUBACK.
- **Retrying.** A clear whose publish returned a non-zero `rc` is published again at the
  next call. paho resends unacknowledged QoS 1 clears itself after a reconnect. A failure is
  logged once per switch (`SWITCH_CLEAR_FAILED_LOGGED`).
- **Done.** When the last clear is acknowledged, record `_state.SWITCH_CLEARED_TS`
  (monotonic) and log `[PROTOCOL] cleared=N`.

**Phase 2: publish.** This is the first call at least 10 s after `SWITCH_CLEARED_TS`,
whether it comes from `publish_tick` or from a reconnect's `on_connect`.
- It runs `cleanup_stale_discovery(registry)` if the sweep has not run, then
  `publish_discovery(registry)` (which publishes availability).
- It then takes `PUBLISH_LOCK`, and in one `STATE_LOCK` acquisition takes the snapshot and
  checks that `SWITCH_PENDING` still names the registry it cleared.
  - Only then does it reset `SWITCH_PENDING`, `SWITCH_CLEAR_INFOS`, `SWITCH_CLEARED_TS` and
    `SWITCH_CLEAR_FAILED_LOGGED`, and publish the snapshot.
  - A newer switch is left pending.
- `parse_payload` reads `SWITCH_PENDING` inside its own `PUBLISH_LOCK` block. So a payload
  decoded during the replay is either in the snapshot or goes through the normal gate
  after it.

The 10 s gap exists because Home Assistant removes an entity and its registry entry
asynchronously. A PI30 sensor sharing a display name with a Device A sensor on the same
group device, "Battery Voltage" for instance, could otherwise register before the old entry
is gone and keep a permanent `_2` entity_id.

**One read decides what `on_connect` publishes.** At the top of its discovery block,
`on_connect` reads `PROTOCOL` and `SWITCH_PENDING` together under `STATE_LOCK`.
- **A switch is pending:** it calls `apply_pending_switch()` and publishes nothing else.
- **No switch is pending:**
  - It passes the registry it read to `cleanup_stale_discovery(registry)` and
    `publish_discovery(registry)`, which do not read `PROTOCOL` again.
  - For its replay it takes the snapshot and reads `SWITCH_PENDING` again, in one
    `STATE_LOCK` acquisition inside `PUBLISH_LOCK`. It skips the replay if a switch has
    become pending.
- `republish_state` takes its snapshot and reads `SWITCH_PENDING` the same way.

A Device A burst that overlaps a switch is then removed by the pending clears. Those wait
for `DISCOVERY_LOCK`, so they always come after it.

**While `SWITCH_PENDING` is set, no state is published.**
- Late discovery in `parse_payload` is skipped, and `republish_state` returns early.
- The old entities' keys have been purged. If state were published, each of them would
  render a missing `value_json.<key>` and Home Assistant would log a template warning for
  it. The replay covers everything deferred.
- `on_connect`'s success branch sets `_state.BROKER_HAS_CONNECTED`. While `SWITCH_PENDING`
  is set, the per-payload outcome is:
  - "no broker connection yet" while that flag is False;
  - otherwise the new outcome `switch-pending`, logged as "Decoded; publishing after the
    protocol switch completes".

  With no switch pending, the existing `DISCOVERY_PUBLISHED` gate decides, unchanged. A
  pending switch restored from `state.json` therefore never logs "no broker connection yet"
  on a connected broker, which is the misdiagnosis 2.6.22 split out.
- Nothing sets `DISCOVERY_PUBLISHED` before the broker has connected.

**Other rules.**
- `dtu_id` is never cleared and never purged.
- **Switching is symmetric.** The block names are disjoint, so a switch back cannot fire
  on PI30 data. It covers hardware being swapped, which a user cannot otherwise reset,
  because `/data` is not reachable from the UI.

## 3. Detection

Detection runs only when the Device A decoder returned nothing.

- **Frame check.** Remove exactly one trailing `0x0D` if the body ends in one, never more.
  Then accept the frame if its last two bytes are the CRC16-XMODEM
  (`binascii.crc_hqx(frame, 0)`) of the bytes from `(` to just before them. Accept the CRC
  raw, or with each of its two bytes incremented by one when it equals `(`, CR, LF or NUL,
  as mpp-solar does. The existing diagnostic's `rstrip(b"\r\n")` would eat a CRC byte of
  `0x0D` or `0x0A`, so it must not be reused for decoding.
- **The first switch to PI30** needs all of:
  - at least 3 verified frames, making up at least half the blocks (the existing
    `_describe_foreign_blocks` rule);
  - at least 2 names in `PI30_BLOCK_MAP` whose bodies pass that query's shape check.

  `cCft` (QPI) is not required, since fragment 2 carries no QPI. When it is present it
  must read `PI30`, and any other answer vetoes the switch. A false positive needs three
  simultaneous 16-bit CRC collisions. Device B's single valid frame already fails, and
  that is pinned.
- **Once the protocol is PI30,** each frame is validated on its own (CRC, mapped name,
  shape), so a small payload is never thrown away for missing the thresholds.
- **Unknown names.** Frames that pass the CRC and threshold test but carry fewer than 2
  mapped names log `[PI30 BLOCK NAMES UNKNOWN]` once, in place of `[UNSUPPORTED PROTOCOL]`.
  They publish nothing and do not switch. `[UNSUPPORTED PROTOCOL]` never fires while the
  protocol is PI30.

## 4. Registry

- **A separate `PI30_SENSORS` dict** of `pi30_*` keys, plus the shared `dtu_id`. It is
  appended after `SENSORS` in `sensors.py`. Groups come from the same display-name prefixes
  Device A uses ("Battery Status - ", "Grid Status - ", …).
  - `pi30_mode` and `pi30_battery_capacity_pct` are added to `MAIN_SENSOR_KEYS`, which is
    the only other way into the main group.
  - Keeping it separate means a forgotten membership check fails closed, in a PI30 test.
  - The iterating consumers (`publish_discovery`, seeding, the sweep) fail open, and each
    gets a PI30 test.
- **A key's group depends only on the key.** `get_sensor_group` looks it up in
  `SENSORS | PI30_SENSORS`. It never reads `_state.PROTOCOL`, so the switch's clears and a
  snapshot published mid-switch route every key to its own group.
- **Membership goes through the active registry:** what is published, seeded, replayed and
  cleared.
  - Callers that read the protocol themselves use `sensors_for_protocol()`: late discovery in
    `parse_payload`, `seed_state()` and `IMPORTANT_DEBUG_KEYS`.
  - Everything on `on_connect`'s and `apply_pending_switch()`'s path takes the registry
    they pass down, so nothing there reads `PROTOCOL` a second time:
    - `publish_discovery(registry)`;
    - `publish_sensor_discovery(key, registry)`, which checks membership in, and reads
      metadata from, the registry its caller passes. Today's early return on
      `key not in SENSORS` would otherwise drop every PI30 key.
    - the sweep's same-id candidates and live set, from the registry passed to
      `cleanup_stale_discovery(registry)`.

  For Device A, the registry is `SENSORS` itself.
- **The sweep's previous-`DEVICE_ID` candidates** cover `SENSORS | PI30_SENSORS`. No topic
  under the old id can be live, so sweeping both there is safe. The same-id candidates
  keep the active registry, which leaves the Device A golden's fresh-marker set unchanged.
- **Device card.** QMN and QVFW are registered as the diagnostic sensors `pi30_model` and
  `pi30_firmware_version`, exactly as Device A registers `model_code` and
  `firmware_version`, so they survive the restart filter. `wire_identity` maps `hw_version`
  to `model_code` or `pi30_model` and `sw_version` to `firmware_version` or
  `pi30_firmware_version`. At most one of each pair is present after §1 and §2. As for
  Device A, a first-ever start has no identity until the next discovery. For a switch it is
  phase 2, which normally follows both fragments. A switch triggered by fragment 2 alone
  lacks `pi30_firmware_version` until the next discovery.
- **Energy options.** `RESET_ENERGY_COUNTERS` leaves the PI30 totals alone, because they are
  the inverter's own counters. On a PI30 install with the option on,
  `resolve_startup_protocol()` logs
  "[CACHE] RESET_ENERGY_COUNTERS does nothing on PI30; its energy totals are the inverter's
  own". §1 keeps every Device A line as it is.
- `SENSORS` stays Device A's 207, so README's pinned counts and `UNDECODED_SENSOR_KEYS` keep
  their meaning. A separate PI30 count gets its own test.

## 5. Fields (v1)

The rule for v1: publish only fields that are paired to the vendor portal, or defined
identically in PI30 (2014/2015) and PI30MAX (2021). Where only one document defines a
field, the table names it. PI30 2014's QPIGS ends at field 17. Fields 18–21 come from PI30
2015 (which numbers them 19–22, counting `(`) and PI30MAX.

| Source | Publish | Exclude, and why |
|---|---|---|
| QPIGS (`G4WT`) | Fields 1–11, 13, 14, 16 and 20. Field 12 (heat-sink temperature, °C) only when QPIRI field 6 (rated apparent power) in the same payload reads above 3000 VA; `MrfS` and `G4WT` are both always in fragment 1. Status bits b6, b5, b4, b2, b1 and b0 of field 17, one per bit (MSB first, which the single `1` at b4 pins). Field 21: b9 "Switch On" (paired), and b10 "charging to floating mode" (PI30 2015 b104 and PI30MAX b10 agree; it read 1 in the unpaired 08-31 capture, with the battery at its 28.8 V float setting, 100 % and 0 A). | 12 when QPIRI field 6 reads 3000 VA or less, or the payload carries no verified QPIRI: all three specs define it as an NTC A/D value on 1–3 kVA models, and °C is paired only on this 4000 VA unit. The gate only withholds, so it needs none of the rating trust §6 declines. 15: reads 00.00 in both captured states (09-02 battery mode on 151 W of PV; 08-31 line mode, AC charging, no PV), each with the SCC-charging bit off; whether it is live or a placeholder needs a capture with SCC charging on. 17 b7 and b3: PI30MAX reserves them on Axpert, PI30 defines them, and both read 0. 18 (fan-on offset) and 19 (EEPROM version): constants of no use as sensors. 21 b8: reserved in PI30 2015 (b106), "dustproof, V series only" in PI30MAX; reads 1 in both captures. 22–24: reserved features in PI30MAX. |
| QPIRI (`MrfS`) | All 25 fields, as diagnostics. Their order is defined identically in PI30 2014/2015 and PI30MAX. The portal pins the 8 whose value is unique in the reply (4, 9, 11, 12, 14, 15, 20, 23). The other 17 agree with it but share a value with another field (1/3, 2/5, 6/7, 8/10, 13/17/18, 16/19/25, 21/22/24), so their positions rest on the specs. Enums map the paired codes plus the codes both documents give the same meaning; output priority 0/1/2 count as identical despite the different wording. Machine type raw. | Published raw, not mapped: charger priority 0 (PI30 only), battery types 3–9 and output modes 05–07 (PI30MAX only). |
| QMOD (`zZ3K`) | Mode enum. B paired; L captured 08-31 (unpaired); P, S and F defined identically in PI30 and PI30MAX. H (PI30 only), D (PI30MAX only) and any other code are published as the raw code. | — |
| QFLAG (`sJqt`) | a b j k u v x y z, as diagnostics. a b k u v x y z are defined in both specs; j (power saving) in PI30 only. `a` is named "Buzzer", never "Silence buzzer" (§11). | d: defined only in PI30MAX, as a reserved feature, and its portal field may be QPIGS field 22 instead. It is neither paired nor in both specs. |
| QBEQI (`7v9T`) | The 8 non-reserved fields of PI30MAX §2.20: 3 pinned by the portal, 5 by spec order. QBEQI is in neither PI30 document. | The 2 reserved fields. |
| QET / QLT (`cT7S` / `mA9W`) | Lifetime PV and load energy, kWh (Wh ÷ 1000), `total_increasing` (§6). QET's unit is confirmed by the portal's 253.8 kWh. QLT's unit comes from PI30MAX §2.27 alone; the portal shows it unitless. Both are PI30MAX only. | — |
| QMN (`EMu5`), QVFW (`o2lC`) | Diagnostic sensors `pi30_model` and `pi30_firmware_version`, also shown on the device card. | — |
| — | — | QVFW3 (ambiguous with QVFW2). QMCHGCR/QMUCHGCR (the portal's "120 A" is just the list's maximum). QGMN (a model-family number; QMN already names the model). QID/QSID (the serial). QT (the clock). QOPPT/QCHPT (hourly arrays, unpaired). QBMS (placeholders while the BMS reports itself disconnected). PV2 (no QPIGS2 on the wire). `u51Q` (unresolved). The three NAKs. |

QFLAG is split on the uppercase separators only: `E` (0x45) opens the enabled list and `D`
(0x44) the disabled one. `d` (0x64) is itself a flag letter in this reply.

## 6. Energy counters

QET and QLT are device-side lifetime totals, so nothing is integrated. The one danger is a
transient low reading. Home Assistant treats a drop of more than about 10 % as a new meter
cycle, so a single `00000000` followed by `00253800` would book about 254 kWh into one
hour, permanently.

- **No baseline** (a fresh install, a lost cache, the first reading after a switch):
  nothing is published until two `dev_prop_post` sets agree without decreasing, and 0 is
  never accepted as a baseline.
- **A lower reading** is accepted as a reset only after it has persisted across N = 3
  `dev_prop_post` sets and at least 2 × `state.observed_telemetry_interval()` of monotonic
  time.
  - `dev_rpc_reply` sets never count toward N. PR #43 reports the cloud requesting them
    about every 26 s on another device, served from the DTU's cache (`DTU_PROTOCOL.md`,
    not verified here); no capture here holds two of them. Counted, they could meet N
    within about a minute of a DTU restart.
  - The cadence log turns N into a hold time of N `dev_prop_post` periods. It cannot
    change N, because no capture has shown a decrease; both counters rose between 08-31
    and 09-02. N changes only if a capture shows a low reading persisting.
- **Logging.** Holding a reading logs `[PI30 ENERGY HELD]` once, with the old and new
  values. Accepting a reset logs a one-shot warning. A held reading is a bound that fires in
  normal operation, so it is never silent.
- Rises are not bounded. The CRC makes a corrupted digit unlikely, and a bound would need a
  rated power the bridge cannot trust.
- The guard's hold state is process-local. After a restart the baseline is the cached
  value and the count starts again, which is the conservative direction.
- Wh → kWh exactly. Both counters are in fragment 2.

## 7. Freshness and cadence

- **Freshness** (what availability means) is stamped by `state.stamp_freshness()` on any
  payload carrying QPIGS, on either topic. Fragment 2 carries settings and totals, and must
  not keep frozen live values marked available.
- **Cadence** (the measured floor for timeouts) is recorded by `state.record_cadence()` only
  for QPIGS arriving on `dev_prop_post`, one gap per set, on its own clock
  `_state.LAST_CADENCE_TS`. Portal-triggered `dev_rpc_reply` sets carry QPIGS too (reported
  about every 26 s). Counting them would empty the measured floor, which is what the
  capture note warns about.
- Device A keeps `record_telemetry()` unchanged, stamping and recording together.
- `STARTUP_GRACE_SEC` (1200 s) assumes a cadence under 20 minutes. The requested timing log
  decides whether that holds for PI30.

## 8. Fragment 2 and the throttle

Fragment 2 arrives right behind fragment 1 (same `b.ts`), inside the `UPDATE_INTERVAL_SEC`
throttle window. 2.6.24 flushes a deferred change at the first 10 s tick after the window
ends. So fragment 2 reaches Home Assistant within `UPDATE_INTERVAL_SEC` + 10 s, which is
20 s at the default.

## 9. Shared state

- **In `state.py`,** always reached as `_state.NAME`, never imported by value:
  - `PROTOCOL`;
  - `DISCOVERY_LOCK` (an `RLock`);
  - `SWITCH_PENDING`;
  - `SWITCH_CLEAR_INFOS`, the clears' `MQTTMessageInfo` objects;
  - `SWITCH_CLEARED_TS`, monotonic, 0.0 when unset;
  - `SWITCH_CLEAR_FAILED_LOGGED`;
  - `BROKER_HAS_CONNECTED`;
  - `LAST_CADENCE_TS`, monotonic.

  They live there rather than in `mqtt.py` because `TestEveryOnceFlagIsIsolated` scans
  `state.py`, though only for bool `*_LOGGED` names. Every other value must be added to
  `isolated_state` by hand.
- `stamp_freshness()` and `record_cadence()` are added next to `record_telemetry()`.
- `PROTOCOL_SWITCH_CACHE_KEY = "_protocol_switch"` is a public constant beside
  `ENERGY_CLOCKS_CACHE_KEY`.
- PI30 decoding lives in a new module, `pi30.py`, holding the block map, the frame check
  and the field table. `parsers.py` calls into it.
- `isolated_state` saves and restores every new value and every new `*_LOGGED` flag.

## 10. Tests

1. **Written first, as its own test-only PR** (CHANGELOG `[Unreleased]`), on `main` after
   2.6.24 has merged and before any PI30 change. The PI30 PR changes no expected value in it
   except the one named in (d).
   - **Its first commit** extracts `prepare_startup_state(path)` and `seed_state()` from
     `__main__` as a pure move, so the golden runs the startup that ships.
   - **What every step records and patches.** Each step patches `time.monotonic`,
     `parsers.datetime` and `_state.host_boot_id`. It records the ordered
     `FakeMqttClient.published` list (topic, payload, retain, qos) and the ordered log
     lines.
   - **The runs,** in the order production runs them:
     - (f) `prepare_startup_state(path)` with no `state.json`, then `on_connect` with no
       marker file.
     - (c) Continuing from (f): `parse_payload` over `CAPTURE_TELEMETRY`, then over
       `CAPTURE_IDENTITY` inside the `UPDATE_INTERVAL_SEC` window. Both run with
       `source_topic="dtu/34545375423553743260/pub/event/dev_prop_post"`, because the topic
       is `dtu_id`'s only source and no existing test passes one. Then `publish_tick` after
       the window. The golden includes the bytes of the `state.json` this step writes.
     - (s) From reset state, `prepare_startup_state(path)` on that `state.json`.
       `LAST_STATE` is compared as `list(LAST_STATE.items())`, because its insertion order
       is the byte order of every replayed state payload.
     - (b) `on_connect` after (s), with a matching marker.
     - (d) The same, with a marker naming a previous `DEVICE_ID`. The PI30 PR's only edit
       to the golden adds exactly the PI30 topics under the old id to (d), in sorted
       position, with the new count in its "Cleared N" line.
2. Per-fragment `BLOCK_PI30_*` fixtures, verbatim from the capture note's appendix. The two
   serial-bearing blocks use `SYNTH_` stand-ins with recomputed CRCs, so fragment 1 is the
   full 14-block payload the device actually sends.
3. **Field goldens** from the portal pairing, one assertion per published field, including:
   - the QFLAG split on uppercase separators;
   - field 12 reading 49 °C with the captured `MrfS`, and absent with a `SYNTH_` `MrfS`
     whose field 6 reads 3000 (CRC recomputed). A `SYNTH_` block may assert absence, never
     value parity.
4. **Detection:**
   - positives on each fragment alone and on `dev_rpc_reply`;
   - negatives on Device A, Device B's single ACK and arbitrary ASCII;
   - the `cCft` veto;
   - the unknown-names diagnostic;
   - the bumped CRC;
   - a CRC whose low byte is `0x0D`;
   - `[UNSUPPORTED PROTOCOL]` stays silent once the protocol is PI30.
5. **Startup and the switch:**
   - *resolve.*
     - Both registries resolve to Device A, and so does `dtu_id` alone.
     - A raise logs and leaves Device A.
     - `dtu_id` and its value keep their position in both directions.
     - `_protocol_switch` records that are malformed, name an unknown registry, or name the
       resolved one are each dropped with a warning.
   - *Capture half.* An exception before `PROTOCOL` is set leaves the old protocol, and the
     next payload completes the switch. `[PROTOCOL]` appears once.
   - *Broker not connected.* With `FakeMqttClient.connected=False` nothing is published and
     the line is "no broker connection yet". The next `on_connect` performs the clears.
   - *Clears.*
     - A Device A → PI30 switch empties exactly the 206 Device A live topics at QoS 1,
       computed as `test_discovery_migration.py` computes them.
     - A clear that is queued but never acknowledged keeps `SWITCH_PENDING` set across a
       reconnect, and no discovery is published. For this, `FakeMqttClient.publish` returns
       an info object with `rc` and an `is_published()` the test controls; today it returns
       a `PublishResult(rc, mid)` tuple.
     - A non-zero `rc` is retried.
   - *Phase 2.* Discovery is not published within 10 s of the last acknowledgement, and a
     reconnect after 10 s publishes it.
   - *During the switch.*
     - No state is published while it is pending.
     - After `on_connect` has run, a pending switch restored from `state.json` logs the
       switch outcome, not "no broker connection yet".
     - A snapshot published mid-switch routes every key to its own group.
     - A capture-thread switch injected between `on_connect`'s read and its
       `publish_discovery` publishes no PI30 config and no state before the clears.
   - *Restart.* A restart from the `state.json` written by the switching payload infers PI30
     and restores the pending clears.
   - *Round trip.* Device A, then PI30, then Device A in one process: nothing is credited on
     the first Device A payload, and the second switch empties every PI30 live topic before
     any Device A config is republished.
6. **Energy and cadence:**
   - the energy guard's no-baseline, hold and reset cases, with their log lines;
   - cadence ignores `dev_rpc_reply`, while a `dev_rpc_reply` QPIGS still stamps freshness;
   - fragment 2 does not stamp freshness;
   - a Device A cache with `RESET_ENERGY_COUNTERS` on and no `_energy_clocks` still prints
     "none saved", and a PI30 cache prints neither line.
7. **Registry:**
   - A PI30 test asserts every key in `pi30.py`'s field table is in `PI30_SENSORS`, and
     `PI30_SENSORS` passes `test_sensors.py`'s schema, category and unique-name checks. A
     duplicate display name inside one group device is itself a `_2` entity_id.
   - `SENSORS` and `PI30_SENSORS` share only `dtu_id`, with identical metadata.
   - `wire_identity` reads `pi30_model` and `pi30_firmware_version`.
   - The old-id sweep equals the Device A set plus the PI30 set under the old id.
     `test_discovery_migration.py`'s exact count under the old id changes to that.
   - Seeding: `prepare_startup_state(path)` on a PI30 cache holds every `PI30_SENSORS` key
     and no Device A key other than `dtu_id`.
   - Device A's regex guards in `test_sensors.py` stay on `parsers.py` unchanged.
   - `pi30.py` is added to the module lists in `test_no_runtime_module_calls_time_time`,
     the star-import guard, and `TestArchitectureDocAnchors.BARE`.
8. `isolated_state` covers the new state, and `TestEveryOnceFlagIsIsolated` covers
   `pi30.py`. `test_every_outcome_has_a_label` gains `switch-pending`.
9. Mutation checks on each.

## 11. Docs and release

- **DOCS.md:**
  - supported devices and the PI30 entity list;
  - the Energy dashboard: PV production from QET, load from QLT, and no grid or battery
    energy;
  - the options that do nothing on PI30: `INVERTER_COUNT`, `BATTERY_*` and
    `RESET_ENERGY_COUNTERS`;
  - a note in `DISCOVERY_CLEANUP`'s description that the switch's clears ignore it;
  - the "Every sensor reads Unknown" section, whose Voltronic example becomes wrong;
  - the new outcome in "The log says Decoded but NOT published";
  - troubleshooting entries for `[PI30 BLOCK NAMES UNKNOWN]`, `[PROTOCOL]` and
    `[PI30 ENERGY HELD]`.
- **README:** supported hardware. "45 of the 207 sensors read", "207 sensors" and
  "143 enabled" stay verbatim. The PI30 count is its own sentence, with its own pin.
- **SECURITY.md:** the stored-data table gains `_protocol_switch`.
- **CLAUDE.md:** the decoding section (PI30 frames are CRC-checked and name-mapped, not read
  by position with no schema), the registry split, the switch, its locks and QoS 1.
- **The capture note, `DTU_PROTOCOL.md`'s device table, the architecture map and its
  anchors, the unsupported-inverter issue template, and the CHANGELOG.**
- **The QFLAG `a` entity** is named "Buzzer" (E = sounds, per mpp-solar). The spec wording,
  "silence buzzer or open buzzer", leaves the direction open.
- **Release** as **2.7.0**, a new capability. Before the release, confirm on the #32 install
  that no entity_id ends in `_2`. Then comment on #32 asking the reporter to compare
  against the portal.

## 12. Evidence still needed

- **The cadence log from #32.** It sets whether `STARTUP_GRACE_SEC` fits and how long the
  energy guard's 3 sets take.
- **Other states,** each paired with the portal.
  - Solar charging has never been captured. It is the only state that can set field 17 b1
    and show whether field 15 is live.
  - Line mode with AC charging and no PV was captured unpaired on 08-31 (QMOD L, field 17
    `00010101`, field 21 `111`). A paired capture of it would pin b2, b0 and b10.
- **Unobserved codes.** Codes that are neither paired nor defined identically in PI30 and
  PI30MAX are published raw until a capture pairs them.
- **Whether this firmware byte-bumps its CRC.** It is untested, so the decoder accepts both
  forms.
- **Optional:** toggling a single QFLAG setting on the LCD and re-capturing would pin one
  letter to the portal. Today the letters rest on the specs.

## 13. Top risks

1. **A transient QET/QLT decrease** spiking the Energy dashboard. §6 exists for this.
2. **The switch:** ordering, the `on_connect` race, a restart mid-switch, a connection lost
   mid-clear, and leftover retained configs. Covered by:
   - the two halves;
   - `on_connect`'s single read of the protocol and pending switch;
   - the persisted, validated pending clears;
   - exact-topic clears at QoS 1, counted only once acknowledged.
3. **`_2` entity_ids** from a name reused across registries. Covered by the 10 s gap after
   the last acknowledgement, and checked on the #32 install before release.
4. **Unmeasured cadence plus portal duplicates** making availability flap. Covered by §7.
5. **A registry consumer that silently keeps reading Device A's dict.** Covered by the
   separation and the consumer tests.
6. **A change to a shared function regressing Device A.** Covered by the golden written
   first.

## Rejected alternatives

- **Storing the protocol in `/data/discovery_state.json`.** The sweep rewrites that marker
  wholesale, dropping the key. `DISCOVERY_CLEANUP=false` returns before reading it. The
  sweep cannot rerun in one process. And two threads would share its fixed `.tmp` path.
- **Holding back discovery until the first decode.** It would spare an unsupported install
  its 207 Unknown entities, but 2.6.17 declined it for two reasons (df52d95), and both
  still hold. It inverts the behaviour DOCS.md documents for "No entities appear". And a
  supported device that has not yet sent a payload would show nothing at all. It would also
  not clear the configs existing unsupported installs already carry. If it is wanted, it is
  a separate change with its own DOCS.md rewrite.
- **One merged registry with a protocol tag.** A forgotten filter would publish or sweep
  200 wrong topics instead of failing a test.
- **`get_sensor_group` reading the active registry.** After the switch set the protocol, 140
  of Device A's keys would map to a diagnostics topic that never existed, and their real
  configs would survive.
- **Clearing only `PUBLISHED_SENSOR_KEYS`.** That set is per-process. It is empty for the
  #32 upgrader, whose configs were published by an earlier run.
- **Clears and discovery in one burst,** or clears on the capture thread. The first risks
  `_2` entity_ids. The second blocks capture on about 200 publishes.
- **Counting a clear as landed when `publish()` returns rc 0.** At QoS 0 that means only
  "queued", and a reconnect discards the queue. The Device A configs would stay retained
  for good.
- **Printing the protocol on every start.** It would change every Device A banner. It is
  printed only when it is PI30.
