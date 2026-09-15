ARG BUILD_FROM=ghcr.io/hassio-addons/base:14.0.0
FROM $BUILD_FROM

# Install system dependencies.
#
# nftables (not iptables) is back, unlike upstream siseli-ha (which dropped iptables
# in 2.6.13 after its own NAT-redirection history turned out to be unreliable under
# this base image's kernel -- see CHANGELOG.md 1.6.0-1.8.0, "ip_forward" read-only /
# dropped transit packets). This add-on's usage is deliberately narrower: a single
# static INPUT DROP rule for locally-destined traffic (firewall.py), never
# FORWARD/NAT/ip_forward, so that history does not apply here.
#
# nft specifically, not the legacy `iptables` binary: a live install on
# 6.18-haos-raspi showed nf_tables already loaded and in active use (694 refs, likely
# Docker's own networking) while the legacy ip_tables/iptable_filter modules were not
# loadable at all inside the container (no /lib/modules to modprobe from). The legacy
# iptables binary talks to those legacy modules specifically and cannot fall back to
# nf_tables, so it fails outright in exactly the environment this add-on runs in;
# nft speaks nf_tables natively. setcap/getcap are still unused; raw send/capture
# still comes from the NET_RAW/NET_ADMIN capabilities rather than a binary.
#
# iproute2 stays. Nothing here calls `ip` either, but scapy can, and the smoke test
# runs with AUTO_INTERCEPT=false and so never exercises sendp(). Removing it would be
# unverifiable short of a real installation.
RUN apk add --no-cache python3 py3-pip libpcap-dev dos2unix iproute2 nftables

# Install Python libraries with the flag for modern Alpine (PEP 668)
COPY requirements.txt /tmp/requirements.txt
RUN pip3 install --no-cache-dir --break-system-packages -r /tmp/requirements.txt

# Set working directory
WORKDIR /app

# Copy all files from the add-on directory
COPY . .

# Fix line endings for shell scripts and make them executable
RUN dos2unix run.sh && chmod a+x run.sh

CMD [ "./run.sh" ]
