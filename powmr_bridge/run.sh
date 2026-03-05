#!/usr/bin/with-contenv bashio

echo "--- PowMr Bridge 1.2.7 START ---"

# Експорт налаштувань
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

# Перехоплюємо ТІЛЬКИ пакет від інвертора (-s $INVERTER_IP)
if [ -n "$INVERTER_IP" ]; then
    echo "Redirecting ONLY traffic from $INVERTER_IP to $LISTEN_PORT..."
    iptables -t nat -I PREROUTING 1 -s $INVERTER_IP -p tcp --dport 1883 -j REDIRECT --to-port $LISTEN_PORT || echo "WARNING: iptables failed"
else
    echo "ERROR: INVERTER_IP is not set. Redirection might be too broad!"
    iptables -t nat -I PREROUTING 1 -p tcp --dport 1883 -j REDIRECT --to-port $LISTEN_PORT
fi

echo "Launching Python Bridge..."
python3 -u /app/powmr_bridge.py
