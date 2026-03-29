#!/usr/bin/with-contenv bashio

echo "--- Siseli Inverter Bridge 2.5.20 ---"

export MQTT_HOST="$(bashio::config 'MQTT_HOST' 'core-mosquitto')"
export MQTT_PORT="$(bashio::config 'MQTT_PORT' '1883')"
export MQTT_USER="$(bashio::config 'MQTT_USER' '')"
export MQTT_PASSWORD="$(bashio::config 'MQTT_PASSWORD' '')"

export TARGET_HOST="$(bashio::config 'TARGET_HOST' '8.212.18.157')"
export TARGET_PORT="$(bashio::config 'TARGET_PORT' '1883')"
export LISTEN_PORT="$(bashio::config 'LISTEN_PORT' '18899')"

export INVERTER_IP="$(bashio::config 'INVERTER_IP')"
export ROUTER_IP="$(bashio::config 'ROUTER_IP')"
export INVERTER_MAC="$(bashio::config 'INVERTER_MAC' '')"
export ROUTER_MAC="$(bashio::config 'ROUTER_MAC' '')"
export AUTO_INTERCEPT="$(bashio::config 'AUTO_INTERCEPT' 'true')"

export MQTT_DISCOVERY_PREFIX="$(bashio::config 'MQTT_DISCOVERY_PREFIX' 'homeassistant')"
export DEVICE_ID="$(bashio::config 'DEVICE_ID' 'siseli_inverter_1')"
export DEVICE_NAME="$(bashio::config 'DEVICE_NAME' 'Siseli Inverter')"
export MODEL_NAME="$(bashio::config 'MODEL_NAME' 'Siseli Inverter')"
export MANUFACTURER="$(bashio::config 'MANUFACTURER' 'Siseli Compatible')"
export STATE_TOPIC="$(bashio::config 'STATE_TOPIC' "siseli/$(bashio::config 'DEVICE_ID' 'siseli_inverter_1')/state")"
export AVAILABILITY_TOPIC="$(bashio::config 'AVAILABILITY_TOPIC' "siseli/$(bashio::config 'DEVICE_ID' 'siseli_inverter_1')/availability")"
export SNIFF_IFACE="$(bashio::config 'SNIFF_IFACE' '')"
export LOG_VERBOSE="$(bashio::config 'LOG_VERBOSE' 'true')"
export ENTITY_PREFIX="$(bashio::config 'ENTITY_PREFIX' 'Siseli')"
export LOG_LEVEL="$(bashio::config 'LOG_LEVEL' 'info')"
export UPDATE_INTERVAL_SEC="$(bashio::config 'UPDATE_INTERVAL_SEC' '10')"
export MQTT_RETAIN="$(bashio::config 'MQTT_RETAIN' 'true')"
export INVERTER_COUNT="$(bashio::config 'INVERTER_COUNT' '1')"
export BATTERY_COUNT="$(bashio::config 'BATTERY_COUNT' '1')"
export BATTERY_CAPACITY_PER_BATTERY_AH="$(bashio::config 'BATTERY_CAPACITY_PER_BATTERY_AH' '0.0')"

echo "[Config] INVERTER_IP=${INVERTER_IP} ROUTER_IP=${ROUTER_IP}"
echo "[Config] TARGET=${TARGET_HOST}:${TARGET_PORT} MQTT=${MQTT_HOST}:${MQTT_PORT}"
echo "[Config] AUTO_INTERCEPT=${AUTO_INTERCEPT}"

exec python3 -u -m src.siseli_bridge.core