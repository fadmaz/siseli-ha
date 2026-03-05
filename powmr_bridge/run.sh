#!/usr/bin/with-contenv bashio

echo "--- PowMr Bridge 1.2.5 START ---"

# 1. Експортуємо налаштування з інтерфейсу HA
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

# 2. Налаштування iptables (використовуємо стандартний, бо legacy не знайдено)
echo "Configuring Port Redirection (1883 -> $LISTEN_PORT)..."
iptables -t nat -A PREROUTING -p tcp --dport 1883 -j REDIRECT --to-port $LISTEN_PORT || echo "WARNING: iptables failed. Make sure Protection Mode is OFF."

echo "Launching Python Bridge..."
python3 -u /app/powmr_bridge.py
