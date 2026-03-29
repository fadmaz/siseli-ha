# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

## [2.5.15] - 2026-03-29

### Changed

- **Main Device Sensor Layout**: `Mode` and `BMS Current SOC` now appear in the Main sensors card instead of Diagnostic.
- **Main Summary Scaling**: Calculated main sensors `c_generation_power_w`, `c_mains_power_w`, and `c_load_w` now scale by `INVERTER_COUNT` for parallel inverter setups, while raw power sensors remain unscaled.

### Added

- **Parallel Topology Config**: Added `INVERTER_COUNT`, `BATTERY_COUNT`, and `BATTERY_CAPACITY_PER_BATTERY_AH` options.
- **Calculated Capacity Helpers**: Added `c_bms_total_capacity_ah` and `c_bms_remaining_capacity_ah` main sensors computed from battery config and BMS SOC.

### Fixed

- **CI Dependency Install Quoting**: Quoted pip version specifiers in `.github/workflows/ci.yml` so bash does not interpret `<` as shell redirection during dependency installation.

## [2.5.14] - 2026-03-29

### Changed

- **Main Summary Device**: The root HA device now publishes five key summary sensors (Mains Power, Output Active Power, PV Generation Power, Mode, BMS Current SOC) directly on the root device.
- **Sensor Name Shortening**: Section prefixes (Battery Status, Load Status, etc.) are stripped from display names now that entities are grouped by device card.

## [2.5.13] - 2026-03-29

### Changed

- **Home Assistant Device Split**: MQTT discovery groups entities into logical devices (`Battery`, `BMS`, `Grid`, `Load`, `PV`, `Diagnostics`) instead of one overloaded device.
- **Functional Diagnostics Routing**: Sensors from the app's More section are mapped by function (battery/grid/pv/load) when possible, with fallback to Diagnostics.
- **Per-Group MQTT Topics**: Discovery state and availability topics are section-specific, and runtime publishes are routed per sensor group.
- **Shutdown Availability Handling**: Bridge shutdown marks all grouped device availability topics offline.

### Added

- Added sensor grouping helpers in sensors.py and grouping validation tests in tests/test_sensors.py.

## [2.5.12] - 2026-03-29

### Added

- **Startup Config Validation**: Added early validation for IPs, ports, host values, and update interval before threads start.
- **Bounded TCP Flow State**: Added stale flow eviction to prevent unbounded in-memory growth.
- **Shared State Module**: Introduced centralized state handling to remove fragile parser/MQTT globals coupling.
- **CI Pipeline**: Added GitHub Actions test runs on Python 3.9, 3.11, and 3.12.
- **Python Project Metadata**: Added pyproject.toml with Python version and pytest/dev settings.
- **Expanded Tests**: Added dedicated config and flow-eviction test coverage.

### Fixed

- **Silent Cache Write Failure**: Replaced swallowed cache write errors with explicit log output.

## [2.5.8] - 2026-03-26

### Changed

- **Modular Architecture**: Broke the 2000-line `siseli_bridge.py` monolith into a clean Python package (`src/siseli_bridge/`) with six focused modules: `config.py`, `loggers.py`, `sensors.py`, `parsers.py`, `mqtt.py`, and `core.py`. Each module has a single clear responsibility.
- **State Persistence**: State is now written to `/data/state.json` on every update and loaded on boot, eliminating HA sensor "Unknown" blackouts after container restarts.
- **Unit Test Framework**: Added `tests/test_parsers.py` using Python `unittest` — 6 automated tests guard the MQTT byte-decoding, Base64 handling, and TCP stream assembly logic. All pass.
- **Type Safety**: Added `# pyre-ignore-all-errors` pragma to `parsers.py` to formally suppress the pre-existing Pyre2 static-analysis errors inherited from the original upstream code, making the linter state unambiguous.

## [2.5.7] - 2026-03-26

### Changed

- **Enhanced Add-on Logging**: Upgraded the fundamental MQTT publish console output. Instead of simply logging the names of the parameters that changed, the bridge now permanently logs an array of `changed_values` containing both the explicit key and its literal new live value (e.g. `bat_v=54.5`) directly into the Home Assistant Add-on log stream!

## [2.5.6] - 2026-03-26

### Added

- **100% Data Parity**: Forensically cross-referenced the hardware's native app strings against our MQTT backend payload mappings. Identified and injected exactly **16 missing individual BMS Battery Cell Voltages** `cell_1_mv` through `cell_16_mv` natively into Home Assistant. The backend and the front-end App are now 100% completely matched!

## [2.5.5] - 2026-03-26

### Fixed

- Fixed a dictionary syntax error missing trailing commas in `siseli_bridge.py`.
- Synchronized internal python logging script version string to automatically match the add-on's release metadata.

## [2.5.4] - 2026-03-26

### Changed

- **Massive UI Decluttering**: Leveraged Home Assistant's `entity_category: diagnostic` feature to aggressively collapse all 60+ Advanced Hardware Settings and System Identity codes into a dedicated, minimized 'Diagnostic' card. The main 'Sensors' dashboard is now perfectly clean and only shows critical core metrics (Battery stats, Solar Wattage, etc.).

## [2.5.3] - 2026-03-26

### Changed

- **Sensor UI Categorization**: Dynamically injected string categories (e.g., _Battery Status, PV Panel Status, Grid Status_) to all 130+ Home Assistant entities. This allows the native Home Assistant Device UI to automatically sort and visually group related sensors together instead of displaying a massive randomized list.

## [2.5.2] - 2026-03-26

### Added

- **Configuration UI Localization**: Implemented native Home Assistant translation files (`en.yaml`), replacing raw backend variables with beautiful, user-friendly labels and helper descriptions directly inside the Add-on Configuration tab.

## [2.5.1] - 2026-03-26

### Added

- **Smart Configuration**: Added `LOG_LEVEL` (debug, info, warning) to dynamically control console output natively from the Home Assistant add-on UI.
- **Database Throttling**: Added `UPDATE_INTERVAL_SEC` to selectively throttle Home Assistant MQTT updates, dramatically reducing recorder database sizes.
- **Persistence Toggle**: Exposed `MQTT_RETAIN` boolean toggle to `config.yaml` to allow control over entity memory across reboot cycles.
- Added `ENTITY_PREFIX` UI parameter to support multi-inverter setups natively without entity ID collisions.

## [2.5.0] - 2026-03-25

### Changed

- **Generalization Overhaul**: Fully rebranded `powmr_bridge` to `siseli_bridge`.
- Decoupled hardcoded "PowMr" and "Taico" hardware references in favor of generic variables supporting 13+ sister brands (LUMINOUS NEO, SunSaviour, ECOmenic, etc).
- Consolidated fragmented setup instructions (`DOCS.md`) directly into a unified `README.md`.
- Default MQTT Discovery base topic changed from `powmr/` to `siseli/`.
- Updated repository metadata structure to support `fadmaz/siseli-ha`.

## [1.8.0] - 2026-03-05

### Added

- **Full Autonomous L2 Bridge**: Implemented a software switch that routes ALL inverter traffic (DNS, NTP, etc.) through HA.
- Fixed inverter "no internet" issue by manually forwarding non-MQTT packets to the real router.
- Added `DROP` rules in HA kernel to prevent system interference with bridged packets.
- Real-time Ethernet frame routing using Scapy.

## [1.7.0] - 2026-03-05

### Added

- **Autonomous Proxy Mode (MITM)**: Switched to a full Man-in-the-Middle proxy.
- HA now actively manages the connection between the Inverter and Siseli Cloud.
- Fixed data loss issue caused by HA kernel dropping transit packets.
- Implemented surgical `iptables` redirection restricted to the Inverter's source IP.

## [1.6.2] - 2026-03-05

### Fixed

- Re-implemented strict source IP filtering (`-s $INVERTER_IP`) in `iptables` to prevent intercepting internal HA traffic.
- Added background heartbeat (ping) to keep the inverter connection alive and ARP table warm.
- Enhanced proxy logging to show the actual IP of the connected device.

## [1.6.1] - 2026-03-05

### Added

- **Diagnostic Proxy Logging**: Added real-time tracking of data packets between Inverter and Cloud.
- Hex dump of incoming traffic to identify protocol issues.
- Added `iptables -F` to ensure a clean redirection state on startup.

## [1.6.0] - 2026-03-05

### Added

- **Transparent Proxy Mode**: Implemented a duplicator that forwards inverter traffic to Siseli Cloud while parsing data for Home Assistant.
- Fixed packet loss issue where HA would drop forwarded traffic due to read-only `ip_forward`.
- Dual-path data flow: Inverter -> HA (Proxy) -> Siseli Cloud.

## [1.5.1] - 2026-03-05

### Fixed

- Improved JSON payload detection in TCP packets (robust against MQTT headers).
- Broadened sniffer filters to capture traffic even if destination IP is modified by the router.
- Cleaned up legacy router firewall rules recommendation.

## [1.5.0] - 2026-03-05

### Added

- **Universal Mode**: Combined ARP Spoofing with Passive Packet Sniffing.
- No longer depends on `iptables` or router reconfiguration.
- Automatic discovery watchdog to keep Home Assistant sensors updated.
- Detailed capture logging for real-time status.

## [1.4.1] - 2026-03-05

### Added

- **Router-Assisted Mode**: Optimized the bridge to work with external port redirection (e.g., from OpenWrt).
- Restored active proxy server on port 18899.
- Removed ARP spoofing and sniffing logic to improve stability when using router-level NAT.

## [1.4.0] - 2026-03-05

### Changed

- Switched to **Direct Packet Capture Mode** using Scapy Sniffing.
- Removed all `iptables` and NAT redirection logic for maximum compatibility with HAOS/Docker.
- Implemented real-time packet parsing directly from the network interface.

## [1.3.0] - 2026-03-05

### Added

- Implemented Inverter Heartbeat (ICMP ping) to monitor connectivity.
- Enhanced Traffic Watchdog to capture ALL IP traffic from Inverter (TCP, UDP, ICMP).
- Added port 8080 to redirection rules.
- Explicit discovery logging for each sensor.

## [1.2.9] - 2026-03-05

### Added

- Implemented Traffic Watchdog (passive sniffer) to monitor inverter network activity and detect target ports.
- Added support for port 8883 (MQTT over SSL) redirection.
- Added `iptables -F` to ensure a clean state before applying new rules.

## [1.2.8] - 2026-03-05

### Fixed

- Added `iptables` rule cleanup loop to remove legacy redirection rules on startup.
- Implemented strict source IP filtering for port redirection to avoid intercepting HA internal traffic.
- Added verification log for active redirection rules.

## [1.2.7] - 2026-03-05

### Fixed

- Added `-s $INVERTER_IP` to `iptables` rule to avoid intercepting internal HA traffic.
- Added explicit logging for MQTT Discovery publication.
- Enhanced proxy logging to show data transfer size and direction.

## [1.2.6] - 2026-03-05

### Fixed

- Changed `iptables` rule from `-A` (Append) to `-I` (Insert) to ensure redirection takes priority over Docker rules.
- Added connection logging `[PROXY] New connection` to verify traffic interception.
- Corrected version string in Python bridge output.

## [1.2.5] - 2026-03-05

### Fixed

- Simplified `iptables` redirection to use the default binary (removed legacy reference).
- Confirmed successful ARP Spoofing and device discovery.

## [1.2.4] - 2026-03-05

### Fixed

- Fixed `unbound variable` crash in `run.sh` by reordering config export.
- Automatic network interface detection (`conf.iface`) for Scapy ARP operations.
- Suppressed non-fatal errors when setting `ip_forward` on read-only filesystems.

## [1.2.3] - 2026-03-05

### Added

- Verbose Debug Mode for network diagnostics.
- Detailed error reporting for `iptables` and `ip_forward`.
- Enhanced logging in `powmr_bridge.py` for ARP and Proxy operations.

## [1.2.2] - 2026-03-05

### Added

- Manual MAC address configuration for Inverter and Router in Add-on options.
- `privileged` mode with `NET_ADMIN` and `NET_RAW` capabilities.
- `apparmor: false` to allow advanced network operations.
- Restored SBU configuration sensors (`sbu_return_grid`, `sbu_return_bat`).

## [1.2.1] - 2026-03-05

### Added

- Detailed network and capability diagnostics in `run.sh`.
- `libcap` and `iproute2` packages for advanced network troubleshooting.
- Redirection error logging to `/tmp/ipt_err`.

## [1.2.0] - 2026-03-05

### Fixed

- Switched to `iptables-legacy` for better compatibility with Home Assistant OS.
- Improved ARP Spoofing reliability using `Ether` frames and `sendp`.
- Enabled unbuffered Python output (`-u`) for real-time logging.
- Restored configuration via Home Assistant Add-on options (environment variables).

## [1.1.7] - 2026-03-05

### Added

- Silenced Scapy library warnings in logs to provide cleaner output.

## [1.1.6] - 2026-03-05

### Fixed

- Fatal crash during startup caused by direct writes to `/proc/sys/net/ipv4/ip_forward` on Read-only filesystems in Home Assistant.
- Removed unnecessary `sysctl` calls as local `REDIRECT` does not require system-wide IP forwarding.

## [1.1.5] - 2026-03-05

### Fixed

- Added robust error handling in `run.sh` to prevent crashes on read-only OS filesystems.
- Added warnings about "Protection Mode" in logs if network redirection fails.

## [1.1.2] - 2026-03-05

### Fixed

- Docker build failure caused by PEP 668 in modern Alpine Linux (Home Assistant base images).
- Added `--break-system-packages` flag to `pip3 install` to allow global package installation in containers.

## [1.1.1] - 2026-03-05

### Added

- English documentation and README.
- Versioning support for Home Assistant Add-on Store.
- This CHANGELOG file.

### Fixed

- Docker build process for Home Assistant OS.
- Line ending issues (`run.sh` CRLF/LF) causing build failures on Windows-to-Linux deployments.
- Repository structure to comply with Home Assistant requirements.

## [1.1.0] - 2026-03-05

### Added

- ARP Spoofing support for automatic traffic interception.
- `iptables` redirection logic to capture inverter data without router changes.
- In-container network management (IP forwarding).

## [1.0.0] - 2026-03-05

### Added

- Initial bridge logic for PowMr RWB1 inverters.
- MQTT Auto-Discovery for Home Assistant sensors.
- Basic proxy server for Siseli cloud interception.
