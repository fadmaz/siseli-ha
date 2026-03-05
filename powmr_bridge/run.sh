#!/usr/bin/with-contenv bashio

echo "--- PowMr Bridge 1.2.3 DEBUG START ---"
echo "Date: $(date)"
echo "User context: $(id)"
echo "Kernel version: $(uname -r)"

# Перевірка IP Forwarding (критично для ARP Spoofing)
echo -n "Checking IP Forwarding: "
cat /proc/sys/net/ipv4/ip_forward
echo "Attempting to enable IP Forwarding..."
echo 1 > /proc/sys/net/ipv4/ip_forward || echo "FAILED to enable IP Forwarding. This is required for ARP Spoofing!"

# Перевірка мережевих інтерфейсів
echo "Network Interfaces:"
ip addr show

# Детальна перевірка iptables
echo "Testing iptables-legacy..."
iptables-legacy -t nat -L -n || echo "iptables-legacy NOT working"

echo "Testing iptables (standard)..."
iptables -t nat -L -n || echo "iptables (standard) NOT working"

echo "Configuring Port Redirection (1883 -> $LISTEN_PORT)..."
# Спроба 1: Legacy
iptables-legacy -t nat -A PREROUTING -p tcp --dport 1883 -j REDIRECT --to-port 18899 2>&1 | tee /tmp/ipt_err1
# Спроба 2: Standard
iptables -t nat -A PREROUTING -p tcp --dport 1883 -j REDIRECT --to-port 18899 2>&1 | tee /tmp/ipt_err2

echo "--- Diagnostic Summary ---"
if [ -s /tmp/ipt_err1 ] && [ -s /tmp/ipt_err2 ]; then
    echo "CRITICAL: All iptables attempts FAILED!"
    echo "Error 1: $(cat /tmp/ipt_err1)"
    echo "Error 2: $(cat /tmp/ipt_err2)"
else
    echo "SUCCESS: Port redirection should be active."
fi
echo "--- DEBUG END ---"

# Export variables
export MQTT_HOST=$(bashio::config 'mqtt_host' 'core-mosquitto')
export MQTT_PORT=$(bashio::config 'mqtt_port' '1883')
export MQTT_USER=$(bashio::config 'mqtt_user' '')
export MQTT_PASSWORD=$(bashio::config 'mqtt_password' '')
export TARGET_HOST=$(bashio::config 'TARGET_HOST' '8.212.18.157')
export TARGET_PORT=$(bashio::config 'TARGET_PORT' '1883')
export LISTEN_PORT=$(bashio::config 'LISTEN_PORT' '18899')
export INVERTER_IP=$(bashio::config 'INVERTER_IP' '')
export ROUTER_IP=$(bashio::config 'ROUTER_IP' '')
export INVERTER_MAC=$(bashio::config 'INVERTER_MAC' '')
export ROUTER_MAC=$(bashio::config 'ROUTER_MAC' '')

echo "Launching Python Bridge..."
python3 -u /app/powmr_bridge.py
