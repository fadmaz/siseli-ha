# Changelog

All notable changes to this project will be documented in this file.

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
