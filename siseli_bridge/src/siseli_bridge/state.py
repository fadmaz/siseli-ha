from typing import Dict

# Shared mutable state consumed by both mqtt.py and parsers.py.
# Having these globals here breaks the circular import that previously required
# a deferred-import hack (_get_mqtt_globals) inside parsers.py.

LAST_STATE: Dict[str, object] = {}
DISCOVERY_PUBLISHED: bool = False
PUBLISHED_SENSOR_KEYS: set = set()
