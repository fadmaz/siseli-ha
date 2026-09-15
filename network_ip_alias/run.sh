#!/usr/bin/with-contenv bashio

INTERFACE="$(bashio::config 'INTERFACE')"
IP_ADDRESS="$(bashio::config 'IP_ADDRESS')"
ADDED=false

cleanup() {
    if [ "$ADDED" = true ]; then
        ip addr del "$IP_ADDRESS" dev "$INTERFACE" 2>/dev/null || true
        echo "[Network IP Alias] Removed $IP_ADDRESS from $INTERFACE"
    fi
}

trap cleanup TERM INT EXIT

if ! ip link show dev "$INTERFACE" >/dev/null 2>&1; then
    echo "[Network IP Alias] Interface '$INTERFACE' does not exist. Available interfaces:"
    ip -br link
    exit 1
fi

case "$IP_ADDRESS" in
    */*) ;;
    *)
        echo "[Network IP Alias] IP_ADDRESS must include a CIDR prefix, for example 192.168.1.20/24"
        exit 1
        ;;
esac

if ip -o -4 addr show dev "$INTERFACE" | awk '{print $4}' | grep -Fxq "$IP_ADDRESS"; then
    echo "[Network IP Alias] $IP_ADDRESS is already present on $INTERFACE"
else
    if ! ip addr add "$IP_ADDRESS" dev "$INTERFACE"; then
        echo "[Network IP Alias] Could not add $IP_ADDRESS to $INTERFACE"
        exit 1
    fi
    ADDED=true
    echo "[Network IP Alias] Added $IP_ADDRESS to $INTERFACE"
fi

while true; do
    sleep 3600
done