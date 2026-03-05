#!/usr/bin/with-contenv bashio
echo "Initializing PowMr Network Interceptor..."

# Enable IP Forwarding (required for ARP spoofing to work)
echo 1 > /proc/sys/net/ipv4/ip_forward

# Redirect incoming traffic on port 1883 (intercepted from inverter) to our local bridge
iptables -t nat -A PREROUTING -p tcp --dport 1883 -j REDIRECT --to-port 1883

# Export HA MQTT settings
export MQTT_HOST=$(bashio::config 'mqtt_host' 'core-mosquitto')
export MQTT_PORT=$(bashio::config 'mqtt_port' '1883')
export MQTT_USER=$(bashio::config 'mqtt_user' '')
export MQTT_PASSWORD=$(bashio::config 'mqtt_password' '')

# Export user options
export TARGET_HOST=$(bashio::config 'TARGET_HOST' '8.212.18.157')
export TARGET_PORT=$(bashio::config 'TARGET_PORT' '1883')
export LISTEN_PORT=$(bashio::config 'LISTEN_PORT' '1883')
export INVERTER_IP=$(bashio::config 'INVERTER_IP' '')
export ROUTER_IP=$(bashio::config 'ROUTER_IP' '')
export AUTO_INTERCEPT=$(bashio::config 'AUTO_INTERCEPT' 'true')

echo "Starting PowMr HA Bridge with ARP Spoofing..."
python3 /app/powmr_bridge.py
