# Architecture

Anchors: a bare `core.py:N` means `siseli_bridge/src/siseli_bridge/core.py`; `tests/x.py:N` means `siseli_bridge/tests/x.py`. Symbol anchors — the ones the test below checks — track HEAD. The rest were written against 2.6.19 (commit `2851d8e`) and are refreshed only where their passage is rewritten; at 2.6.23 that was the module table, the forwarding description, and risks #5 and #10.

`TestArchitectureDocAnchors` in `siseli_bridge/tests/test_packaging.py` checks these: every cited file must exist, every line must be inside it, and wherever the prose names a backticked symbol before a citation, that symbol must be defined on the cited line. What it cannot check is a citation pointing at a statement rather than a definition — roughly half of them — so treat those as approximate and re-read before relying on one. Half the anchors here had drifted within a day of first being written, which is why the test exists.

## What this is

A Home Assistant add-on that ARP-spoofs a Siseli-platform solar inverter and its router so both send their frames through the bridge (`core.py:172-207`).
It passively reassembles the inverter's TCP stream to the vendor MQTT cloud (`parsers.py:505`), extracts the MQTT PUBLISH packets (`parsers.py:363`), and decodes the base64 "blocks" inside each one into sensor values (`parsers.py:1274`).
Values merge into one shared dict (`state.py:12`) and are republished to the local broker under HA MQTT auto-discovery (`mqtt.py:124`, `mqtt.py:278`).
The inverter's connection to the cloud broker is relayed (`core.py:347-359`), and so is everything the router sends to the inverter (`core.py:381-393`), so the vendor app keeps its session. The inverter's other traffic — DNS, NTP, other endpoints — is dropped unless `FORWARD_ALL_INVERTER_TRAFFIC` is set (`core.py:361-379`), and with `AUTO_INTERCEPT` off nothing is relayed at all.
The bridge observes; it never terminates or answers a connection. Only the inverter-to-cloud direction is parsed; router-to-inverter frames are relayed untouched and unparsed.
Block positions were reverse-engineered from one device with no schema, so the governing rule of the parser is: publish a value only when this payload contains evidence for it.

## Top-level layout

| Path | What it is | One non-obvious fact |
|---|---|---|
| `siseli_bridge/` | The add-on: Docker build context, manifest, runtime, tests | It is the *only* thing that reaches the image (`siseli_bridge/Dockerfile:24` `COPY . .`, `scripts/smoke-test.sh:30`); root-level docs and `captures/` never ship |
| `siseli_bridge/src/siseli_bridge/` | The runtime, eight modules | The package path is `src.siseli_bridge` because `run.sh:46` execs `python3 -m src.siseli_bridge.core`; tests patch `src.siseli_bridge.<module>` |
| `siseli_bridge/tests/` | 11 test files, `helpers.py`, `captures.py`, `conftest.py` | Excluded from the image (`siseli_bridge/.dockerignore:10`); `conftest.py` puts `siseli_bridge/` on `sys.path` so imports mirror the runtime path |
| `siseli_bridge/config.yaml`, `Dockerfile`, `run.sh`, `requirements.txt`, `.dockerignore`, `icon.png`, `logo.png` | Add-on manifest and build inputs | `config.yaml` has no `image:` key and there is no `build.yaml`, so Supervisor builds locally from the Dockerfile (`tests/test_packaging.py:428-433`) |
| `siseli_bridge/DOCS.md`, `siseli_bridge/CHANGELOG.md` | The HA Documentation tab and Changelog tab | Only `CHANGELOG.md` ships in the image (`.dockerignore:28-29`); Supervisor reads `DOCS.md` from the repo checkout |
| `siseli_bridge/translations/en.yaml` | UI labels for every option | Its key set must equal the schema's (`tests/test_packaging.py:94`) |
| `.github/` | CI workflow, dependabot, issue and PR templates | The seven CI check names are branch-protection requirements on `main`; nothing in the checkout says so except `tests/test_packaging.py:263-305` |
| `scripts/smoke-test.sh` | Build the image and wait for a running sniffer | Bypasses `run.sh` (`:46` overrides the entrypoint) and runs with `AUTO_INTERCEPT=false` (`:39`), so no ARP frame is ever sent under CI |
| `captures/` | Paired bridge-log / vendor-portal readings of the same device at the same second | `tests/captures.py` proves the parser does not regress; `captures/` is the only evidence that a decode is *correct* (`captures/README.md:20-25`) |
| `sensor_mapping_verified.md`, `sensor_mapping.md` | Per-token decode map for HPVINV04; the superseded 2.6.0 map | The verified file supersedes its own tables: Section 0 (`:25-124`) is the simultaneous reading, the later tables were 17 minutes apart (`:18-21`) |
| `README.md`, `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md` | GitHub landing page, dev procedure, security posture, conduct | `README.md` may use relative links; `DOCS.md` may not (`tests/test_packaging.py:227-234`). `SECURITY.md:30-32` states that a wrong `INVERTER_IP` poisons the wrong host by design |
| `CHANGELOG.md` (root) | A nine-line pointer | Must not carry any `## [N` heading (`tests/test_packaging.py:51-56`); the canonical history is `siseli_bridge/CHANGELOG.md` |
| `LICENSE`, `NOTICE` | MIT; scope statement | Upstream `yuraantonov11/siseli-ha` carries no licence, so the MIT grant covers only work done here (`NOTICE:3-16`) |
| `pyproject.toml`, `repository.yaml` | Dev/CI packaging; the add-on repository manifest | `pyproject.toml:30-36` declares the top-level package as literally `src` because `siseli_bridge/` has no `__init__.py` |
| `CLAUDE.md` | Local agent notes | Untracked by design (`.gitignore:39`) |
| `docs/` | This file; `DTU_PROTOCOL.md`, what is known of the DTU's MQTT envelope and cloud protocol | This file's anchors are checked by `TestArchitectureDocAnchors`; `DTU_PROTOCOL.md` separates what captures here show from what PR #43 reported |

## Runtime

### Modules

| Module | Lines | Owns |
|---|---|---|
| `core.py` | 803 | Capture, ARP spoofing, L2 forwarding, lifecycle, availability watchdog, cache restore. The `__main__` block (`:762-803`) is the only place threads and the sniffer start |
| `parsers.py` | 2278 | TCP reassembly (`:65`, `:549`), MQTT framing (`:193`, `:407`, `:452`), `SolarParser` (`:688`) with `parse_payload` (`:2070`) and the positional decoder `_try_ascii_schema` (`:1457`), energy integrators (`:870`), heartbeat (`:631`, `:646`) |
| `mqtt.py` | 401 | paho client (constructed at import, `:121`), discovery payloads (`:124`), topic derivation (`:45`, `:53`), grouped state publish (`:278`), stale-discovery sweep (`:226`), `on_connect` (`:323`) |
| `sensors.py` | 394 | `SENSORS` registry (`:9`), `UNDECODED_SENSOR_KEYS` (`:244`), `get_sensor_group` (`:356`). Imports nothing; everything imports it |
| `state.py` | 114 | `LAST_STATE` (`:12`), `STATE_LOCK` (`:21`), every cross-thread flag (`:26`, `:35`, `:42`, `:52`, `:62`), cadence measurement (`:65-82`), `atomic_write_json` (`:98`) |
| `config.py` | 340 | Every option via `os.getenv` at import (`:9-119`), internal tuning constants (`:5-7`, `:74-116`, `:186-197`), `validate_config` (`:200`) |
| `loggers.py` | 91 | `print`-based logger; level bound at import (`:24`); `log` (`:31`), `log_kv` (`:48`), `log_error_always` (`:36`), `hex_preview` (`:70`) |
| `version.py` | 7 | `__version__` (`:7`), the single source for the version string |

### Packet-to-publish data path

```
run.sh:46  exec python3 -m src.siseli_bridge.core
   |
core.py:580-622  validate_config -> signal handlers -> load_cached_state -> seed SENSORS to None
   |             -> start_mqtt -> ARP thread -> health_logger thread -> AsyncSniffer(filter "ip host INVERTER_IP")
   v
core.packet_callback (:277)        stamp LAST_PACKET_TS, drop own re-emitted frames (:291-292),
   |                               learn INV_MAC/RTR_MAC (:295-298), reject spoofed IPs (:306-307)
   |-- inverter -> TARGET_HOST:TARGET_PORT? (:314)
   |      |
   |      v
   |   core.handle_inverter_tcp_packet (:221)   flow key (src,sport,dst,dport); RST/FIN drop, SYN reset (:229-235)
   |      |
   |      v
   |   parsers.append_stream_data (:505)        RFC1982 wraparound, out-of-order parking, bounded gap recovery
   |      |                                     (config.py:167-169: 64 segs / 64 KiB / 5 s, then resync)
   |      v
   |   parsers.extract_mqtt_packets_from_stream (:363)   byte-slide resync on any implausible header
   |      |
   |      v  type nibble 3 only (core.py:260)
   |   parsers.extract_publish_payload (:408)   topic + body; dtu_id from topic (:178)
   |      |
   |      v
   |   SolarParser.parse_payload (:1869)        find JSON, _walk_for_blocks (:964), _safe_b64decode (:895),
   |      |                                     _try_ascii_schema (:1274) reads tokens BY POSITION per block name,
   |      |                                     _apply_energy_dashboard_calculations (:803), _derive_battery_status (:781)
   |      v
   |   state.update_state (:81) + record_telemetry (:65)      one merge, one snapshot, under STATE_LOCK (parsers.py:1971-1972)
   |      |
   |      |-- _write_state_cache (parsers.py:617)  /data/state.json at most every 30 s (config.py:162)
   |      |-- publish_sensor_discovery for new keys (parsers.py:1989-1991)
   |      '-- publish_grouped_state (mqtt.py:278) if a change is pending AND UPDATE_INTERVAL_SEC elapsed (parsers.py:1993-2007)
   |             one retained JSON object per HA device group -> siseli/<id>/<group>/state (mqtt.py:45-50)
   |
   '-- broker frames: re-emit pkt[IP] to the router MAC if AUTO_INTERCEPT and RTR_MAC are set (core.py:353-358);
       router->inverter frames re-emitted to INV_MAC (:388-393)
```

Non-broker inverter traffic (`core.py:361-379`) is counted in `DROPPED_NON_TARGET` (`core.py:181`). It is forwarded only when `FORWARD_ALL_INVERTER_TRAFFIC` and `AUTO_INTERCEPT` are both set, the router's MAC and our own are known, and the frame was L2-addressed to us. Broadcast and multicast are never re-emitted; they reach the router directly.

### Threads and the lock

`CLAUDE.md` says three threads; there are five threads of execution, three of which touch `LAST_STATE`.

| Thread | Started | Touches `LAST_STATE` | Flags it writes |
|---|---|---|---|
| main | `core.py:617-618` sleep loop | before other threads exist (`:140`, `:587-588`) | `RUNNING`, `AVAILABILITY_ONLINE` via `shutdown` (`:518-542`) |
| ARP spoofer (daemon) | `core.py:536`, only when `AUTO_INTERCEPT` | no; reads `_state.RUNNING` (`:179`, `:200`) | none |
| scapy `AsyncSniffer` | `core.py:536` | sole runtime writer (`parsers.py:1971`) | `LAST_TELEMETRY_TS`, `TELEMETRY_INTERVALS`, `PUBLISHED_SENSOR_KEYS`, once-logged flags |
| paho network | `mqtt.py:323` `loop_start` | reads via `snapshot_state` (`mqtt.py:74`, `:298`) | `DISCOVERY_PUBLISHED` (`:175`), `PUBLISHED_SENSOR_KEYS` (`:164`); re-asserts `AVAILABILITY_ONLINE` (`:174`) |
| `health_logger` (daemon) | `core.py:601` | reads via `republish_state` -> `snapshot_state` (`:462-463`, `parsers.py:646`) | `AVAILABILITY_ONLINE` (`:436`) |

`STATE_LOCK` is an `RLock` (`state.py:21`) held only inside `snapshot_state` and `update_state` (`:75-85`), never across I/O. The comment at `state.py:85` records why: a dict resize during `on_connect` iteration killed the paho thread silently. `health_logger` ticks every 10 s (`core.py:512`), runs the watchdog each tick (`:454`), republishes retained state when `heartbeat_due` (`parsers.py:631`, interval `max(UPDATE_INTERVAL_SEC, EXPIRE_AFTER_SEC//3)` = 600 s at defaults), and prints `[HEALTH]` every third tick (`:467-469`). Availability is keyed on decoded telemetry (`state.py:32`, `core.py:411`), not on packet age, with a timeout of `max(TELEMETRY_TIMEOUT_SEC, min(3 x observed gap, 3600))` (`core.py:372-399`) and a startup grace measured from `PROCESS_START_TS` (`:412-415`).

### How configuration reaches the modules

```
Supervisor options -> run.sh:3-40  export X="$(bashio::config 'X' 'default')"
                   -> config.py:9-95  os.getenv at import
                   -> core.py:24 and mqtt.py:7   from .config import *        (bound copies)
                      parsers.py:11-20           from .config import (...)    (explicit list; still bound copies)
                      loggers.py:24              level bound at import
```

Consequences:

- Tests must patch the **consuming** module (`tests/helpers.py:98` `patch_consts`); reloading `config.py` changes nothing the runtime reads.
- Names starting with `_` are not exported by a star import. `core.py` and `mqtt.py` carry a ruff `F405` exemption (`pyproject.toml:64-66`), so a reference to one is caught by neither lint nor tests; `tests/test_core.py:577-600` greps for it, and the smoke test exists because this crash-looped a release. `config.py:146-147` defines `ACTIVE_DEBUG_FLAGS` for exactly this reason. `parsers.py` never had a star import despite the exemption at `pyproject.toml:66`.
- `validate_config` (`config.py:200`) is called only from `__main__` (`core.py:581-582`) and `sys.exit`s on error, so importing `config` in tests never aborts.
- Not env-driven: `STATE_CACHE_FILE` (`config.py:5`), `STATE_CACHE_INTERVAL_SEC` (`:162`), the reassembly bounds (`:167-175`). Read from env but deliberately not add-on options: `TELEMETRY_TIMEOUT_MULTIPLIER`, `TELEMETRY_TIMEOUT_CEILING_SEC`, `ENERGY_MAX_DT_SEC` (`:76-92`), because Supervisor pins a stored option forever and a default is not a fix.
- The state module is aliased two ways: `_state` in `core.py:27` and `mqtt.py:6`, `_shared_state` in `parsers.py:8`. Grep for both when tracing a flag.

## Tests and evidence

**Fixture contract** (`tests/captures.py:1-11`): `BLOCK_*` are verbatim bytes from a real device, reconstructed from a `[BLOCK RAW]` `hex_preview`, and may back golden assertions; `SYNTH_*` are hand-built and may only back structural or edge tests. Device A (HPVINV04, two parallel units, 32-cell bank) supplies `CAPTURE_TELEMETRY` (11 blocks, `:48-60`) and `CAPTURE_IDENTITY` (4 blocks, `:62-67`), which have zero overlap (`:22-24`); together they cover all 15 names in `KNOWN_BLOCK_NAMES` (`parsers.py:122`), which `tests/test_truthfulness.py:709-721` pins against the decoder's own source via `inspect.getsource`. `EXPECTED_*` golden dicts (`:69-139`) were taken from the add-on's own "Published to HA" line and are asserted key-for-key at `tests/test_truthfulness.py:554-574`. Two different devices are both labelled "Device B" (`:142` reference unit, `:155` Beve Mega foreign device); the `*_REF` literals at `:147-152` end in `)` rather than `\r` and were not built per the contract.

**Isolation helper**: `helpers.isolated_state` (`tests/helpers.py:121-175`) saves and restores 19 module globals across `parsers.py` and `state.py`, including every once-logged flag — `TestEveryOnceFlagIsIsolated` fails on any `*_LOGGED` flag in either module it does not restore, and an autouse fixture in `tests/conftest.py` keeps every test out of `/data`. It does **not** cover `core.py` globals (`INV_MAC`, `KNOWN_*_MACS`, `LAST_PACKET_TS`, `DROPPED_NON_TARGET`), `state.RUNNING` or `state.DISCOVERY_CLEANED`; `test_core._CoreTestCase` (`tests/test_core.py:44-93`) restores those by hand. `BASE_ENV` (`:22-57`) is pinned to `config.yaml` by `tests/test_packaging.py:645-676` because `reload_config` (`:73`) reloads `config.py` in place and never restores it; `tests/test_packaging.py:645-649` records the order-dependent false pass that caused. `FakeMqttClient` (`:176`) records publishes, retained topics and the will.

**Coverage**: 323 tests + 1132 subtests, ~7 s. Measured 84 % overall and 74 % for `mqtt.py`+`core.py` against floors of 78 and 65 (`.github/workflows/ci.yml:53-54`); the "~2 points under" comment at `:50-51` is stale. `core.py` alone sits at 65 %, carried by `mqtt.py` at 94 %; nothing gates `core.py` individually.

**What only the smoke test proves**: the `__main__` body (`core.py:580-622`), `start_mqtt` (`mqtt.py:393-325`), and that the image starts at all. **What nothing proves**: the ARP send path (`core.py:172-207`, `:50-54`, always mocked at `tests/test_core.py:63-68`, disabled in smoke at `scripts/smoke-test.sh:39`); `SIGTERM -> shutdown` (`core.py:572-577` is only asserted callable at `tests/test_core.py:373-374`, and smoke tears down with `docker rm -f`, `scripts/smoke-test.sh:25`); the `health_logger` loop body (`core.py:601`); `run.sh` itself (entrypoint overridden at `scripts/smoke-test.sh:46`; only grepped by `tests/test_packaging.py:97-100`). One test is vacuous: `tests/test_core.py:309-314` patches `core.client` but `shutdown` publishes through `mqtt.py`'s own client (`core.py:694` -> `mqtt.py:179-181`), so the `except` at `core.py:536` never runs.

**Source-as-data tests**: five tests parse source text rather than executing it and will fail on a purely stylistic rewrite: `tests/test_sensors.py:147-190` (regex over `parsers.py` for `state["key"] =`), `tests/test_core.py:577-600`, `tests/test_truthfulness.py:709-721`, `tests/test_packaging.py:285-305` (ci.yml -> check names), `tests/test_packaging.py:102-105` (`os.getenv("KEY"` literals).

**`captures/`**: two reference captures and two unsupported-device notes. `2026-08-21_1341_charging.md` (2.6.10, 202 parameters, none disagree); `2026-08-21_2341_discharging.md` (2.6.15, same device ten hours later, refutes three candidate decodes at `:66-86`); `2026-08-22_device-b-modbus.md` (issue #30, binary Modbus RTU inside the same DTU envelope, not a decode); `2026-09-02_device-c-voltronic-pi30.md` (issue #32, Voltronic PI30 inside the same envelope, every frame CRC-verified and paired to the portal to the second, not yet a decode). `captures/README.md:37-53` lists which device states would settle which open question; every flag has read its safe value in every capture so far.

## Packaging and CI

**How HA installs it**: the user adds the repo URL; Supervisor reads `repository.yaml` and `siseli_bridge/config.yaml`. With no `image:` key and no `build.yaml` (`tests/test_packaging.py:428-433`), Supervisor builds `siseli_bridge/Dockerfile` on the user's machine: `ARG BUILD_FROM=ghcr.io/hassio-addons/base:14.0.0` (`:1-2`, a multi-arch manifest, which is what makes a local aarch64 build work), `apk add python3 py3-pip libpcap-dev dos2unix iproute2` (`:14`), `COPY . .` (`:24`), `dos2unix run.sh` (`:27`, the only CRLF defence), `CMD ["./run.sh"]` (`:29`). `run.sh` turns every option into an env var and `exec`s Python (`:46`) so signals reach `core.py`'s handlers; `init: false` (`config.yaml:6`). Privileges: `host_network`, `NET_ADMIN`+`NET_RAW`, `apparmor: false` (`config.yaml:10-14`; reasons in `SECURITY.md:45-50`).

**Supervisor pins stored options.** Every option's value is stored the first time the user saves the configuration page and shadows the shipped default forever (`config.py:76-79`). Supervisor also validates *stored* options against the *new* schema before an automatic update, and again at start, so a stored value that fails a tightened type or pattern blocks the upgrade; a trailing `?` means the key may be absent, not that `''` passes (`tests/test_packaging.py:570-642` replays a real stored configuration). A stored key the schema no longer lists is only warned about and skipped (Supervisor's `apps/options.py`), so removing an option does not block anything — the belief that it did came from 2.6.2, whose only quoted error was the MAC pattern. `LISTEN_PORT` and `LOG_VERBOSE` are still in the schema and read only to warn (`config.py:157`, `:279-291`; `tests/test_packaging.py:474-502`).

**The six-place option rule.** A new option touches `config.yaml` `options:` (`:16`) and `schema:` (`:56`), `translations/en.yaml`, `run.sh` (`bashio::config 'KEY'`), `config.py` (`os.getenv("KEY"`), and `DOCS.md` as a backticked `KEY`. `tests/test_packaging.py:81-105` pins the first five and `:209-215` the sixth. There is effectively a seventh: `tests/helpers.py` `BASE_ENV` (`tests/test_packaging.py:670-676`). `.github/PULL_REQUEST_TEMPLATE.md:21-23` still says five.

**CI** (`.github/workflows/ci.yml`): five jobs, seven check runs.

| Job | Runner | Produces |
|---|---|---|
| `test` (`:17`) | `ubuntu-latest` x Python 3.9/3.11/3.12 (`:20-21`) | `test (3.9)`, `test (3.11)`, `test (3.12)`; coverage floors at `:53-54` |
| `lint` (`:65`) | `ubuntu-latest` | `lint` (`ruff check .`) |
| `addon-lint` (`:82`) | `ubuntu-latest` | `addon-lint` via `frenck/action-app-linter@v2` (`:92`; renamed from action-addon-linter, warnings do not fail) |
| `smoke` (`:99`) | `ubuntu-24.04` / `ubuntu-24.04-arm` (`:111-117`), `fail-fast: false` | `smoke (amd64, ubuntu-24.04)`, `smoke (aarch64, ubuntu-24.04-arm)`; runs `scripts/smoke-test.sh` (`:126`) |

Every job runs with a read-only token (`permissions: contents: read`, `:13-14`), pinned by `TestCiTokenIsReadOnly` (`tests/test_packaging.py:803`).

There is no separate docker build job; smoke builds first (`:105`). **Branch protection on `main` requires exactly those seven check names**, derived by GitHub from job id plus every matrix value, so bumping `runner: ubuntu-24.04` alone renames a check and blocks every PR with no error naming the cause. `tests/test_packaging.py:263-305` pins the derived set; the protection rule must be edited in the same change.

**Pins are written twice**: `pyproject.toml:17-20` (what CI installs) and `siseli_bridge/requirements.txt` (what the image installs) must agree, and `Dockerfile:1` must equal `scripts/smoke-test.sh:18` (`tests/test_packaging.py:436-471`). Dependabot is registered for both pip roots and for docker (`.github/dependabot.yml:8-26`), so each single-file bot PR fails CI until a human lands the pair; this is the documented intent.

**Version** lives in four files that must agree in one commit: `version.py:7`, `config.yaml:3`, the `README.md:3` badge, and the first `## [x.y.z]` heading in `siseli_bridge/CHANGELOG.md` (`tests/test_packaging.py:24-50`). `run.sh` may not print one (`:57-67`).

## Documentation

The split is by render target, not topic (`CONTRIBUTING.md:3-7`): `README.md` is the GitHub landing page only; `siseli_bridge/DOCS.md` is what Supervisor renders on the add-on's Documentation tab; `CONTRIBUTING.md` is developer procedure. It was created in 2.6.13 by reversing 2.5.0, which had merged `DOCS.md` into the README and left the tab empty (`siseli_bridge/CHANGELOG.md:155-160`).

Tests holding it (`tests/test_packaging.py`):

| Pair | Test | Rule |
|---|---|---|
| README <-> DOCS.md | `TestDocumentationSplit` (`:177-234`) | Requirements/Installation/Configuration/Network setup/Troubleshooting live only in DOCS.md; README has no option table rows; README names DOCS.md |
| DOCS.md <-> config.yaml | `:209-215` | every schema key appears backticked |
| DOCS.md alone | `:220-234` | no *current* version literal (earlier ones allowed); every link is `http(s)` or `#anchor`, because a relative link resolves against the HA origin inside the frontend |
| README <-> sensor registry | `TestDocumentedCountsAreCurrent` (`:237-261`) | "207 sensors", "143 enabled", "45 of the 207" are derived from `SENSORS` and `UNDECODED_SENSOR_KEYS` |
| root CHANGELOG <-> add-on CHANGELOG | `:41-56` | root is a pointer with no headings; the add-on file's first heading equals `version.py` |

Nothing tests `CONTRIBUTING.md`, the per-device counts in `DOCS.md:164-172`, or any option *description*. Known stale text: `DOCS.md:179-180` says "three kWh counters" and `translations/en.yaml:91` says battery and grid totals, while `core.py:63-69` resets five; `tests/captures.py:13` still says to ask reporters for `LOG_LEVEL: debug` while every other place says Debug Flags at info; `sensor_mapping_verified.md:375` and `:388` still name the `eo8w[1]`/`Yavb[1]` candidates that `captures/2026-08-21_2341_discharging.md:66-86` refuted.

## The 10 biggest risks and half-finished areas

**1. The sniffer thread can die silently while ARP poisoning continues, blackholing the inverter.**
`core.py:612-614` starts the `AsyncSniffer` and logs `[Bridge] Sniffer started` unconditionally; the main loop at `:617-618` polls only `_state.RUNNING`, and nothing in `src/` reads `sniffer.running` or `sniffer.exception`. In the installed scapy 2.6.1 the capture socket is opened inside the sniffer thread, and a recv error *or* an exception escaping `packet_callback` closes the socket and ends the thread with `exception` still `None`; the only report goes to the `scapy.runtime` logger, which `core.py:310` sets to ERROR. `ArpSpoofer.run` (`:200-207`) keeps poisoning every 2 s on `_state.RUNNING` alone, and forwarding exists only inside `packet_callback` (`:320-325`, `:355-360`), so the inverter loses its cloud path until a manual restart. `health_logger` (`:447-483`) prints packet age and takes no action; the watchdog flips availability only after 1800-3600 s (`:433-437`), and Supervisor's Watchdog (`DOCS.md:66`) reacts to container exit only.
Status: **FIXED in 2.6.20.** The health loop checks capture liveness every 10 s, restarts a
dead sniffer in place, and after three consecutive failures restores ARP and exits non-zero.
Kept here because the first attempt at the fix called `shutdown()` from the daemon health
thread, where the interpreter killed it mid-restore — the reusable lesson is that a teardown
which needs a second of wall time has to run on the main thread.
Fix: have `health_logger` or the main loop check `sniffer.running` and either restart the sniffer or clear `RUNNING` so the container exits and Supervisor restarts it. The smoke test cannot catch this; its ready marker is the same log line (`scripts/smoke-test.sh:20`).

**2. The `INVERTER_COUNT` scaling basis is unproven.**
`_scale_main_power` (`parsers.py:1294`) multiplies load, mains and generation power by `INVERTER_COUNT` (`:1316`, `:1387`, `:1581`), the factor also enters grid import (`:863`) and the legacy battery current (`:744`, `:768`), and all of it feeds five monotonic kWh counters (`:772-778`) that persist across restarts (`:1974` -> `core.py:140`). Whether the inverter's blocks carry per-unit or system figures is recorded as the top open question in `captures/README.md:66-68` and `captures/2026-08-21_2341_discharging.md:108-122`, yet `DOCS.md:209` says "per-unit figures" flatly and `siseli_bridge/CHANGELOG.md:259` calls the 11 kW nameplate confirmed. The proposed 24-hour `c_generation_energy_kwh` vs `pv_today_kwh` ratio (`CHANGELOG.md:237`) cannot discriminate, because both derive from the same device's blocks (`parsers.py:1572-1581`, `:1187-1188`). The shipped default `INVERTER_COUNT: 1` (`config.yaml:38`) is unaffected, and the night-time efficiency figure (89.6 %) leans the code's way.
Status: **open question — still open.** The docs no longer state the basis as fact (2.6.19); the
question itself is unresolved and only a rating plate or a clamp meter settles it.
Settle: a rating-plate photo or a clamp-meter reading on the maintainer's install at night with PV = 0; soften `DOCS.md:209` until then.

**3. Battery-current guards reject anything over 300 A, below the BMS's own 390 A limit, and say nothing.**
`parsers.py:1468` and `:1473` (2ONL) and `:1769`, `:1771` (Yavb) drop currents outside `0..300` with no log, while the same device reports `bms_charge_current_limit_a = 390` (`captures/2026-08-21_2341_discharging.md:27`). A rejected reading leaves its key absent, `[ENERGY SOURCE DISAGREEMENT]` fires only when *both* sources are present (`:743`), and `_battery_current` silently uses the survivor (`:765-768`), which on the reference install is the scaled inverter ammeter that the captures show disagreeing with the BMS by 1.6x to ~10x. If both are absent, charge/discharge power become 0 (`:837-846`) and `battery_status` reads `Idle` (`:795-800`). The only test of the guard uses 9999 A (`tests/captures.py:220`, `tests/test_truthfulness.py:299-304`).
Status: **FIXED in 2.6.21.** The bound is 1000 A, clear of the 390 A the hardware
declares for itself, and a rejection now logs once as `[BATTERY CURRENT REJECTED]`.
Fix: raise the bound to a physical ceiling or the device's declared limit, and log the rejection once, as the grid guard does at `:1396`.

**4. An unreachable broker logs nothing, while every payload still prints "Published to HA".**
`mqtt.py:322-323` uses `connect_async` + `loop_start`; paho 1.6.1 (`requirements.txt:1`) retries forever and reports connect failure only through `on_connect_fail` or `on_log`, which nothing in `src/` sets. `on_connect` (`:288`) needs a CONNACK, so the `rc != 0` branch at `:302` covers bad credentials only. `parse_payload`'s "Published to HA" line (`parsers.py:1955`) sits outside the `DISCOVERY_PUBLISHED` gate (`:1987`), so it prints when nothing was published; `health_logger` prints no client state (`core.py:512`); `DOCS.md:322-323` tells the user a failed connection means wrong credentials.
Status: **FIXED in 2.6.21.** `on_connect_fail` is registered, `publish_grouped_state`
returns whether the broker accepted the publish, the per-payload line reports which of
three things happened, and the health line opens with `broker=up`/`broker=DOWN`.
Fix: register `on_connect_fail`, print `client.is_connected()` in the `[HEALTH]` line, and gate the "Published" line on an actual publish.

**5. Grid import has never been captured non-zero, and its label and integrator consult different sources.**
Import is integrated only when WdRR[6] is positive (`parsers.py:929`) and feeds a monotonic `total_increasing` counter (`:955-957`, `sensors.py:73`); every recorded token is `+00000` (`captures/README.md:49`, `captures/2026-08-21_1341_charging.md:347`), yet the reference install carries 56 kWh of import (`captures/2026-08-21_1341_charging.md:114`). Git shows the `> 0` rule unchanged since the counter was introduced (`2d8b3e6`), so the token has been positive at some point, presumably below the 35 % return-to-mains SOC (`captures/2026-08-21_1341_charging.md:68`) that neither capture reached. The direction label is decided by a single-digit flow code before the sign is read (`:1619-1633`; a two-digit code such as `01` falls through to the sign): running the parser with token `-01500` and code `0` yields "Mains To Inverter" and 0 W, so label and integrator can contradict.
Status: **detected and logged in 2.6.23; the rule itself is still open.** `_grid_direction_conflicts` compares the sign with the direction the flow code states — one digit or two, via `_flow_code_direction` — or with the published label when there is no usable code, and logs `[GRID DIRECTION CONFLICT]` once with both raw tokens. Crediting is deliberately unchanged: making the integrator follow the label was designed and then rejected in review, because the reference install has credited 56 kWh of import from positive tokens whose flow code was never recorded — a rule that trusts the code could silently flatten a counter that was right. The sign convention and codes 1/2 remain unverified.
Settle: the first logged conflict on a real install, or the reference install's Home Assistant history (what "Mains Current Flow Direction" read when "Grid Import Energy" last rose), then choose one source for both.

**6. A pinned `SNIFF_IFACE` on a dual-homed host stamps ARP replies and forwarded frames with the wrong MAC.**
`core.py:202-203` builds `Ether(dst)/ARP(op=2, ...)` with no `hwsrc` and no `Ether.src`; forwarded frames at `:322`, `:342`, `:357` likewise; `send_layer2` (`:50-54`) passes `iface` only to `sendp`, which selects the send socket and rewrites nothing. scapy fills the source fields from the interface its routing table picks for the *destination IP*, not from `iface`; executing the exact frame from `:202` confirms it. `DOCS.md:101` and `:328` recommend pinning `SNIFF_IFACE` precisely on hosts where capture is not seeing traffic, i.e. multi-homed ones. No test sends an unmocked frame (`tests/test_core.py:63-68`) and smoke runs `AUTO_INTERCEPT=false`.
Status: live defect under one configuration; end-to-end effect on a real dual-homed HA host unverified.
Fix: set `hwsrc` and `Ether.src` explicitly from `resolve_own_mac()` (`:151`), which already reads `SNIFF_IFACE`'s MAC for the own-frame guard (`:291-292`).

**7. The unsupported-protocol diagnostic mis-triages an ASCII device as binary (issue #32).**
`_describe_foreign_blocks` (`parsers.py:1091`) labels a body `binary` if any byte fails the printable test (`:931-934`, `:955`). Issue #32 (Falcon VMIII 4200W on 2.6.17) reports `recognised=0 body="binary"`; running the function on its 12 complete blocks, 8 fail on exactly one or two checksum bytes before `\r`, and the bodies are `(`-framed space-separated ASCII tokens, the shape `_try_ascii_schema` consumes (`:996-1011`). `DOCS.md:308` and `.github/ISSUE_TEMPLATE/unsupported_inverter.yml:43-46` call `body="binary"` the strongest signal of a different protocol family. `tests/test_truthfulness.py:702-707` pins only that ASCII is not labelled Modbus.
Status: **FIXED in 2.6.18.** Bodies are classified individually as `ascii`, `ascii+binary_tail`
or `binary`, and the device is now identified as Voltronic PI30 on 22-of-22 CRC16-XMODEM
verification. Kept here because the shape of the mistake — one boolean folding two axes — is
the reusable lesson.
Fix: exclude trailing checksum bytes from the printable test and emit `looks_like="ascii_with_checksum"`; support would need a block-name map plus checksum stripping, not a second parser family.

**8. `hex_preview` truncates at 64 bytes, so the documented fixture pipeline cannot produce the fixtures.**
`parsers.py:1930` emits `blocks[name][:64].hex()` with no truncation marker; `loggers.py:70` already has a `hex_preview(limit=240)` helper with a marker that is not used here. The fixture contract (`tests/captures.py:5-8`) and `CONTRIBUTING.md:94-96` say `BLOCK_*` are decoded verbatim from that field, yet `BLOCK_93VQ_SETTINGS`, `BLOCK_DHRK_SETTINGS`, `BLOCK_YAVB_CHARGING` and `BLOCK_V09K_CELLS_16` are 70-90 bytes. The cap bit issue #30 (`captures/2026-08-22_device-b-modbus.md:36-37`) and #32 (both telemetry-bearing blocks cut at 64), and the `text=`/`tokens=` fields (`:1928-1929`) are lossy for non-ASCII tails.
Status: **FIXED in 2.6.18.** `hex_preview` is no longer capped at 64 bytes; it uses the helper in
`loggers.py` that marks truncation. The two telemetry blocks it destroyed in issue #32 still
have to be re-requested from the reporter.
Fix: call `loggers.hex_preview` or raise the cap to the full block.

**9. Energy clocks are not persisted; every restart drops one interval and a failing cache write desyncs HA.**
`LAST_ENERGY_TS` is process-local (`parsers.py:178`) and `_energy_dt_seconds` returns 0 on the first call per domain (`:679-688`), pinned as intended by `tests/test_parsers.py:205-224`. The cache holds only `LAST_STATE` (`:1974`, `core.py:140`) and `shutdown` writes none (`core.py:694`), so at the 300-600 s cadence and ~5 kW each restart silently loses 0.4-0.9 kWh per domain. If `atomic_write_json` fails, `[CACHE WRITE ERROR]` (`:632-634`) is logged but publishing continues, so the broker and `/data/state.json` diverge until a write succeeds; a torn file then restores as empty state (`tests/test_core.py:158-169`, a CURRENT BEHAVIOUR pin).
Status: **half fixed in 2.6.19.** Durations now use `time.monotonic()`, so a clock step can no
longer fabricate a block of kWh — that was the sharp edge. The clocks are still not persisted,
so each restart still drops up to one interval per domain; that part remains known and accepted.
Fix: persist the per-domain clocks next to the snapshot and clamp `dt` on restore with the existing `_energy_max_dt` (`:712-727`).

**10. `bms_cell_count` reports cells decoded from the v09K block, not cells in the pack.**
`parsers.py:1434` sets `bms_cell_count = len(cell_values)`, where v09K carries at most 16 cells of a 32-cell pack (`sensor_mapping_verified.md:235-238`), and the cell list stops at the first out-of-range token (`:1408-1432`), so `tests/test_truthfulness.py:336-343` pins that a collapsed cell 3 makes the count read 2. The registry named it "BMS Cell Count" (`sensors.py:46`), and the `>16` warning (`:1437-1445`) had no test. Min/max/delta come from uxJp and are pack-wide (`:1375-1405`), so only the count was wrong.
Status: **FIXED in 2.6.23 by naming it for what it counts.** The friendly name is now "Cell Voltages Decoded"; the key and its `BMS Status - ` prefix are unchanged, so the unique_id and history stay. The count itself is unchanged, because no token carries the pack size — uxJp's positions give only a lower bound. The >16 overflow warning is now one-shot and tested.
Fix: rename the entity to what it measures or derive the count from uxJp's positions.

## Conventions that are load-bearing

- **Publish only what this payload evidences; one writer per key.** Every branch of `_try_ascii_schema` is `if len(vals) >= n` (`parsers.py:1476`, `:1358`, `:1454`); no defaults, no constants, no cross-block aliases. The only cross-payload reads are the `bat_v` cache fallback (`:827-829`) and previous energy totals (`:773`). Enforced by `tests/test_truthfulness.py:35-94` (no fabrication), `:97-179` (single writer), `:554-574` (golden parity).
- **Timing bounds come from measured cadence, not from another option.** `state.observed_telemetry_interval` (`state.py:55-62`) feeds both `effective_telemetry_timeout` (`core.py:405-399`) and `_energy_max_dt` (`parsers.py:758`); `UPDATE_INTERVAL_SEC` is a publish throttle and says nothing about the inverter (`config.py:68`). The measured cadence is a fixture (`tests/test_core.py:603-692`, `tests/test_truthfulness.py:394-470`).
- **A bound that fires during normal operation logs, once, and its once-flag is registered for isolation.** `[ENERGY GAP CLAMPED]` (`parsers.py:703`), `[GRID VALUE REJECTED]` (`:1396`), `[UNSUPPORTED PROTOCOL]` (`:2031`); each flag is saved and restored by `tests/helpers.py:121-175`. Add a flag to `parsers.py` or `state.py` without registering it and `TestEveryOnceFlagIsIsolated` fails; before that test existed, later tests silently inherited the fired state.
- **Shared flags live in `state.py` and are reached through the module alias.** `state.py:23-26` and `:44-52` record the two bugs that came from private copies (`RUNNING`, `AVAILABILITY_ONLINE`). Access is `_state.NAME` (`core.py:27`, `mqtt.py:6`) or `_shared_state.NAME` (`parsers.py:8`), never `from .state import NAME`. Regression pinned at `tests/test_core.py:421-485`.
- **Patch constants on the consuming module; never reference `_private` config names across a star import.** `core.py:24` and `mqtt.py:7` hold bound copies; `tests/helpers.py:98` `patch_consts` is the tool; `tests/test_core.py:577-600` greps for underscore names because `pyproject.toml:64-66` exempts `F405`.
- **Anything that must be verified lives in a module-level function called from `__main__`.** `log_startup_configuration` (`core.py:563`), `load_cached_state` (`:72`), `install_signal_handlers` (`:572`); the `__main__` body (`:580-622`) is executed by no test.
- **A removed sensor goes into `UNDECODED_SENSOR_KEYS`, never just deleted.** `/data/state.json` outlives the code and is merged wholesale at `core.py:590`; `:101-109` purges the listed keys. `tests/test_sensors.py:147-190` greps `parsers.py` to prove no listed key is written and every written key is registered, which is what makes the "not in SENSORS" purge at `core.py:121-129` safe.
- **The registry derives everything on the wire.** Group from `get_sensor_group` (`sensors.py:356`) fixes the discovery topic and `unique_id` (`mqtt.py:131`) and the state topic (`:45-50`); the value template is `{{ value_json.<key> }}` (`:136`). Regrouping a sensor orphans its HA entity (`tests/test_mqtt.py:298-303`), and a stale-discovery sweep exists for exactly that (`mqtt.py:184-268`).