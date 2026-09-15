"""Coverage for mqtt.py, which had none.

Two kinds of test live here. Most pin behaviour that must not change. A few, marked
CURRENT BEHAVIOUR, pin behaviour that is known to be wrong and is scheduled to be
fixed -- they exist so the fix shows up as a deliberate, reviewed diff rather than
a silent change.
"""

import json
import unittest
from unittest import mock

from src.siseli_local_bridge import mqtt as mqtt_mod
from src.siseli_local_bridge import state as shared_state
from src.siseli_local_bridge.sensors import SENSORS, get_grouped_sensor_keys, get_sensor_group
from tests.helpers import FakeMqttClient, isolated_state, patch_consts

TOPICS = dict(
    DEVICE_ID="inv1",
    DEVICE_NAME="Siseli Local Inverter 1",
    MODEL_NAME="Siseli Local Inverter 1",
    MANUFACTURER="Siseli Compatible",
    ENTITY_PREFIX="Siseli",
    MQTT_DISCOVERY_PREFIX="homeassistant",
    STATE_TOPIC="siseli/inv1/state",
    AVAILABILITY_TOPIC="siseli/inv1/availability",
)


class _MqttTestCase(unittest.TestCase):
    """Swaps the module-level client for a fake and isolates shared state."""

    def setUp(self):
        self.client = FakeMqttClient()
        patcher = mock.patch("src.siseli_local_bridge.mqtt.client", self.client)
        patcher.start()
        self.addCleanup(patcher.stop)

        ctx = isolated_state()
        ctx.__enter__()
        self.addCleanup(lambda: ctx.__exit__(None, None, None))
        # publish_discovery now re-asserts this rather than a literal True, so the
        # availability assertions must not depend on another file's cleanup.
        shared_state.AVAILABILITY_ONLINE = True

        consts = patch_consts("src.siseli_local_bridge.mqtt", **TOPICS)
        consts.__enter__()
        self.addCleanup(lambda: consts.__exit__(None, None, None))


class TestWireIdentityOnTheDeviceCard(_MqttTestCase):
    """The bridge decodes a firmware version the vendor portal itself leaves blank, and
    it was reachable only as a diagnostic sensor. Home Assistant shows sw_version and
    serial_number on the device page header, which is where a user looks."""

    def test_the_main_device_carries_what_the_wire_reports(self):
        shared_state.LAST_STATE.update({
            "firmware_version": "0010.11",
            "model_code": "HPVINV04",
            "dtu_id": "12345678901234567890",
        })
        info = mqtt_mod.device_info("main")
        self.assertEqual(info["sw_version"], "0010.11")
        self.assertEqual(info["hw_version"], "HPVINV04")
        self.assertEqual(info["serial_number"], "12345678901234567890")
        # model stays the user's configured value; nothing configured is overridden.
        self.assertEqual(info["model"], TOPICS["MODEL_NAME"])

    def test_unknown_identity_is_omitted_rather_than_guessed(self):
        """A first-ever start has no cache, so discovery goes out without them and the
        next reconnect republishes it with them."""
        shared_state.LAST_STATE.clear()
        info = mqtt_mod.device_info("main")
        for key in ("sw_version", "hw_version", "serial_number"):
            with self.subTest(key=key):
                self.assertNotIn(key, info)


class TestTopicDerivation(_MqttTestCase):
    def test_main_group_uses_the_configured_topics_verbatim(self):
        self.assertEqual(mqtt_mod.device_id_for_group("main"), "inv1")
        self.assertEqual(mqtt_mod.state_topic_for_group("main"), "siseli/inv1/state")
        self.assertEqual(mqtt_mod.availability_topic_for_group("main"), "siseli/inv1/availability")

    def test_subgroup_is_inserted_before_the_suffix(self):
        self.assertEqual(mqtt_mod.device_id_for_group("bms"), "inv1_bms")
        self.assertEqual(mqtt_mod.state_topic_for_group("bms"), "siseli/inv1/bms/state")
        self.assertEqual(mqtt_mod.availability_topic_for_group("bms"), "siseli/inv1/bms/availability")

    def test_topic_without_the_expected_suffix_falls_back_to_appending(self):
        with patch_consts(
            "src.siseli_local_bridge.mqtt",
            STATE_TOPIC="custom/root",
            AVAILABILITY_TOPIC="custom/avail",
        ):
            self.assertEqual(mqtt_mod.state_topic_for_group("pv"), "custom/root/pv")
            self.assertEqual(mqtt_mod.availability_topic_for_group("pv"), "custom/avail/pv")


class TestDisplayName(_MqttTestCase):
    def test_section_prefix_is_stripped_and_entity_prefix_applied(self):
        self.assertEqual(
            mqtt_mod.display_sensor_name("Battery Status - Battery Voltage"),
            "Siseli Battery Voltage",
        )

    def test_name_without_a_known_section_prefix_is_left_alone(self):
        self.assertEqual(mqtt_mod.display_sensor_name("Something Else"), "Siseli Something Else")

    def test_empty_entity_prefix_yields_the_bare_name(self):
        with patch_consts("src.siseli_local_bridge.mqtt", ENTITY_PREFIX=""):
            self.assertEqual(
                mqtt_mod.display_sensor_name("Grid Status - AC Input Voltage"),
                "AC Input Voltage",
            )


class TestDeviceInfo(_MqttTestCase):
    def test_main_device_has_no_via_device(self):
        info = mqtt_mod.device_info("main")
        self.assertEqual(info["identifiers"], ["inv1"])
        self.assertEqual(info["name"], "Siseli Local Inverter 1")
        self.assertNotIn("via_device", info)

    def test_subgroup_device_hangs_off_the_main_device(self):
        info = mqtt_mod.device_info("bms")
        self.assertEqual(info["identifiers"], ["inv1_bms"])
        self.assertEqual(info["name"], "Siseli Local Inverter 1 BMS")
        self.assertEqual(info["via_device"], "inv1")


class TestPublishSensorDiscovery(_MqttTestCase):
    def test_payload_shape(self):
        mqtt_mod.publish_sensor_discovery("bat_v")

        published = self.client.last("homeassistant/sensor/inv1_battery/bat_v/config")
        self.assertTrue(published.retain)
        payload = json.loads(published.payload)
        self.assertEqual(payload["unique_id"], "inv1_battery_bat_v")
        self.assertEqual(payload["state_topic"], "siseli/inv1/battery/state")
        self.assertEqual(payload["value_template"], "{{ value_json.bat_v }}")
        self.assertEqual(payload["payload_available"], "online")
        self.assertEqual(payload["payload_not_available"], "offline")
        self.assertEqual(payload["unit_of_measurement"], "V")
        self.assertEqual(payload["device_class"], "voltage")
        self.assertEqual(payload["state_class"], "measurement")
        self.assertEqual(payload["device"]["via_device"], "inv1")

    def test_optional_metadata_is_omitted_when_absent(self):
        mqtt_mod.publish_sensor_discovery("mode")
        payload = self.client.json_at("homeassistant/sensor/inv1/mode/config")
        for absent in ("unit_of_measurement", "device_class", "state_class", "entity_category"):
            self.assertNotIn(absent, payload)

    def test_enabled_by_default_is_forwarded_when_declared(self):
        mqtt_mod.publish_sensor_discovery("mains_eo8w_code")
        payload = self.client.json_at(
            f"homeassistant/sensor/inv1_{get_sensor_group('mains_eo8w_code')}/mains_eo8w_code/config"
        )
        self.assertIs(payload["enabled_by_default"], False)

    def test_unknown_key_publishes_nothing_and_is_not_recorded(self):
        mqtt_mod.publish_sensor_discovery("no_such_sensor")
        self.assertEqual(self.client.published, [])
        self.assertNotIn("no_such_sensor", shared_state.PUBLISHED_SENSOR_KEYS)

    def test_published_key_is_recorded(self):
        mqtt_mod.publish_sensor_discovery("bat_v")
        self.assertIn("bat_v", shared_state.PUBLISHED_SENSOR_KEYS)


class TestPublishDiscovery(_MqttTestCase):
    def test_every_sensor_gets_exactly_one_config(self):
        mqtt_mod.publish_discovery()
        configs = self.client.discovery_configs()
        self.assertEqual(len(configs), len(SENSORS))

    def test_every_unique_id_is_distinct(self):
        mqtt_mod.publish_discovery()
        ids = [c["unique_id"] for c in self.client.discovery_configs().values()]
        self.assertEqual(len(ids), len(set(ids)))

    def test_one_availability_topic_is_marked_online(self):
        """Availability was published per group, but paho supports one will, so only
        the main group could ever be marked offline by the broker."""
        mqtt_mod.publish_discovery()

        published = self.client.last(TOPICS["AVAILABILITY_TOPIC"])
        self.assertEqual(published.payload, "online")
        self.assertTrue(published.retain)
        self.assertTrue(shared_state.DISCOVERY_PUBLISHED)

        for group in get_grouped_sensor_keys():
            if group == "main":
                continue
            with self.subTest(group=group):
                self.assertNotIn(
                    mqtt_mod.availability_topic_for_group(group), self.client.topics()
                )

    def test_every_entity_references_the_single_availability_topic(self):
        mqtt_mod.publish_discovery()
        for topic, payload in self.client.discovery_configs().items():
            with self.subTest(topic=topic):
                self.assertEqual(payload["availability_topic"], TOPICS["AVAILABILITY_TOPIC"])


class TestPublishGroupedState(_MqttTestCase):
    def test_keys_are_routed_to_their_group_topic(self):
        mqtt_mod.publish_grouped_state({"bat_v": 53.4, "grid_v": 232.7, "mode": "Battery Mode"})

        self.assertEqual(self.client.json_at("siseli/inv1/battery/state"), {"bat_v": 53.4})
        self.assertEqual(self.client.json_at("siseli/inv1/grid/state"), {"grid_v": 232.7})
        self.assertEqual(self.client.json_at("siseli/inv1/state"), {"mode": "Battery Mode"})

    def test_none_values_are_published_as_json_null(self):
        """A null is what tells Home Assistant the value is unknown rather than
        leaving the previous value on screen."""
        mqtt_mod.publish_grouped_state({"bat_v": None})
        self.assertEqual(self.client.json_at("siseli/inv1/battery/state"), {"bat_v": None})

    def test_retain_follows_the_configured_flag(self):
        with patch_consts("src.siseli_local_bridge.mqtt", MQTT_RETAIN=False):
            mqtt_mod.publish_grouped_state({"bat_v": 1.0})
        self.assertFalse(self.client.last("siseli/inv1/battery/state").retain)


class TestConnectionCallbacks(_MqttTestCase):
    def test_successful_connect_publishes_discovery(self):
        mqtt_mod.on_connect(self.client, None, {}, 0)
        self.assertTrue(shared_state.DISCOVERY_PUBLISHED)
        self.assertTrue(self.client.discovery_configs())

    def test_failed_connect_publishes_nothing(self):
        mqtt_mod.on_connect(self.client, None, {}, 5)
        self.assertEqual(self.client.published, [])
        self.assertFalse(shared_state.DISCOVERY_PUBLISHED)

    def test_connect_replays_state_only_when_something_is_known(self):
        shared_state.LAST_STATE.clear()
        shared_state.LAST_STATE.update({"bat_v": None})
        mqtt_mod.on_connect(self.client, None, {}, 0)
        self.assertNotIn("siseli/inv1/battery/state", self.client.topics())

        self.client.published.clear()
        shared_state.LAST_STATE["bat_v"] = 53.4
        mqtt_mod.on_connect(self.client, None, {}, 0)
        self.assertEqual(self.client.json_at("siseli/inv1/battery/state"), {"bat_v": 53.4})

    def test_clean_disconnect_is_quiet(self):
        with mock.patch("src.siseli_local_bridge.mqtt.log") as logged:
            mqtt_mod.on_disconnect(self.client, None, 0)
        logged.assert_not_called()


class TestClientConstruction(unittest.TestCase):
    def test_will_is_registered_and_credentials_applied(self):
        fake = FakeMqttClient()
        with mock.patch("src.siseli_local_bridge.mqtt.mqtt.Client", return_value=fake), patch_consts(
            "src.siseli_local_bridge.mqtt",
            MQTT_USER="bob",
            MQTT_PASSWORD="secret",
            AVAILABILITY_TOPIC="siseli/inv1/availability",
        ):
            built = mqtt_mod.create_mqtt_client()

        self.assertIs(built, fake)
        self.assertEqual(fake.will, ("siseli/inv1/availability", "offline", True))
        self.assertEqual(fake.credentials, ("bob", "secret"))
        self.assertEqual(fake.delays, (5, 30))

    def test_no_credentials_are_set_when_user_is_blank(self):
        fake = FakeMqttClient()
        with mock.patch("src.siseli_local_bridge.mqtt.mqtt.Client", return_value=fake), patch_consts(
            "src.siseli_local_bridge.mqtt", MQTT_USER=""
        ):
            mqtt_mod.create_mqtt_client()
        self.assertIsNone(fake.credentials)


class TestAvailabilityAndIdentity(_MqttTestCase):
    """These began as CURRENT BEHAVIOUR tests pinning known defects. Stage C fixed
    them, and each assertion was inverted in the same commit."""

    def test_the_will_topic_is_the_one_every_entity_watches(self):
        """The single will now covers all ~203 entities rather than the 12 in the
        main group."""
        fake = FakeMqttClient()
        with mock.patch("src.siseli_local_bridge.mqtt.mqtt.Client", return_value=fake), patch_consts(
            "src.siseli_local_bridge.mqtt", AVAILABILITY_TOPIC="siseli/inv1/availability"
        ):
            mqtt_mod.create_mqtt_client()

        self.assertEqual(fake.will, ("siseli/inv1/availability", "offline", True))
        main_keys = [k for k in SENSORS if get_sensor_group(k) == "main"]
        self.assertLess(len(main_keys), len(SENSORS) // 2, "most entities are not in main")

    def test_sensor_group_is_baked_into_the_unique_id(self):
        """Because the group is part of unique_id and the discovery topic, moving a
        key between groups orphans the user's existing entity."""
        mqtt_mod.publish_sensor_discovery("bat_v")
        payload = self.client.json_at("homeassistant/sensor/inv1_battery/bat_v/config")
        self.assertIn("battery", payload["unique_id"])

    def test_device_id_is_sanitised_before_it_reaches_a_topic(self):
        """Home Assistant's discovery matcher accepts only [a-zA-Z0-9_-] for the node
        id, so an unsanitised value with a space created zero entities with nothing
        logged. Case is preserved so working ids are unaffected."""
        from src.siseli_local_bridge.config import sanitize_device_id

        self.assertEqual(sanitize_device_id("Siseli Local Inverter 1"), "siseli_local_inverter_1")
        self.assertEqual(sanitize_device_id("inverter#1"), "inverter_1")
        self.assertEqual(sanitize_device_id("siseli/inv1"), "siseli_inv1")
        self.assertEqual(sanitize_device_id(""), "siseli_local_inverter_1")
        # Already-valid ids, including mixed case, must pass through untouched.
        for value in ("siseli_local_inverter_1", "Inv-2", "ABC123"):
            with self.subTest(value=value):
                self.assertEqual(sanitize_device_id(value), value)

    def test_expire_after_is_declared_so_stale_values_age_out(self):
        """Without it, a bridge that stops receiving data leaves every entity showing
        its last reading indefinitely, with availability still online."""
        mqtt_mod.publish_discovery()
        configs = self.client.discovery_configs()
        self.assertTrue(configs)
        for topic, payload in configs.items():
            with self.subTest(topic=topic):
                self.assertEqual(payload["expire_after"], mqtt_mod.EXPIRE_AFTER_SEC)


if __name__ == "__main__":
    unittest.main()


class TestBrokerReachabilityIsVisible(unittest.TestCase):
    """An unreachable broker produced no output at all, while the parser went on
    logging "Published to HA" for every payload.

    connect_async plus loop_start retries forever in paho's network thread, and
    on_connect fires only on a CONNACK -- so its rc != 0 branch covers a broker that
    answers and refuses (bad credentials) and never one that is not there. Meanwhile a
    QoS 0 publish on a disconnected client returns MQTT_ERR_NO_CONN and is dropped
    without raising, and every call site discarded that return.
    """

    def setUp(self):
        mqtt_mod.LAST_CONNECT_FAILURE = None
        self.addCleanup(setattr, mqtt_mod, "LAST_CONNECT_FAILURE", None)

    def test_a_failed_publish_is_reported_to_the_caller(self):
        fake = FakeMqttClient(publish_rc=4)  # MQTT_ERR_NO_CONN
        with mock.patch.object(mqtt_mod, "client", fake):
            self.assertFalse(mqtt_mod.publish_grouped_state({"bat_v": 53.4}))

    def test_a_good_publish_reports_success(self):
        fake = FakeMqttClient()
        with mock.patch.object(mqtt_mod, "client", fake):
            self.assertTrue(mqtt_mod.publish_grouped_state({"bat_v": 53.4}))

    def test_broker_state_is_readable(self):
        fake = FakeMqttClient()
        with mock.patch.object(mqtt_mod, "client", fake):
            self.assertTrue(mqtt_mod.broker_is_connected())
            fake.connected = False
            self.assertFalse(mqtt_mod.broker_is_connected())

    def test_an_unreachable_broker_says_so_once(self):
        """paho retries every few seconds; one line per outage, not per attempt."""
        with mock.patch("src.siseli_local_bridge.mqtt.log_error_always") as logged:
            for _ in range(5):
                mqtt_mod.on_connect_fail()
        self.assertEqual(len(logged.call_args_list), 1)
        said = str(logged.call_args_list[0].args[0])
        self.assertIn("Cannot reach the broker", said)

    def test_a_later_outage_is_reported_again(self):
        with mock.patch("src.siseli_local_bridge.mqtt.log_error_always"):
            mqtt_mod.on_connect_fail()
        self.assertEqual(mqtt_mod.LAST_CONNECT_FAILURE, "unreachable")
        with mock.patch.object(mqtt_mod, "client", FakeMqttClient()), mock.patch.object(
            mqtt_mod, "publish_discovery"
        ), mock.patch.object(mqtt_mod, "publish_availability"):
            mqtt_mod.on_connect(None, None, None, 0)
        self.assertIsNone(
            mqtt_mod.LAST_CONNECT_FAILURE, "a successful connect must re-arm the report"
        )

    def test_a_refusing_broker_is_not_logged_on_every_retry(self):
        """Wrong credentials produce a CONNACK on every reconnect. Ungated that is one
        error line per retry delay for as long as they stay wrong -- thousands a day."""
        with mock.patch.object(mqtt_mod, "client", FakeMqttClient()), mock.patch(
            "src.siseli_local_bridge.mqtt.log"
        ) as logged:
            for _ in range(6):
                mqtt_mod.on_connect(None, None, None, 5)
        refusals = [
            c for c in logged.call_args_list
            if c.args and "refused the connection" in str(c.args[0])
        ]
        self.assertEqual(len(refusals), 1)

    def test_a_change_of_failure_kind_is_still_reported(self):
        """A bool could not express this: an unreachable broker that later starts
        refusing credentials is new information and must not be swallowed."""
        with mock.patch("src.siseli_local_bridge.mqtt.log_error_always"):
            mqtt_mod.on_connect_fail()
        with mock.patch.object(mqtt_mod, "client", FakeMqttClient()), mock.patch(
            "src.siseli_local_bridge.mqtt.log"
        ) as logged:
            mqtt_mod.on_connect(None, None, None, 5)
        self.assertTrue(
            any("refused the connection" in str(c.args[0]) for c in logged.call_args_list if c.args)
        )

    def test_a_raising_publish_is_not_reported_as_a_parser_error(self):
        """It used to unwind into parse_payload's handler and print [PARSER ERROR] --
        a broker fault attributed to the decoder."""
        fake = FakeMqttClient()
        fake.raise_on_publish = OSError("broker went away")
        with mock.patch.object(mqtt_mod, "client", fake), mock.patch(
            "src.siseli_local_bridge.mqtt.log_error_always"
        ) as logged:
            self.assertFalse(mqtt_mod.publish_grouped_state({"bat_v": 53.4}))
        self.assertTrue(
            any("Publish to" in str(c.args[0]) for c in logged.call_args_list if c.args)
        )

    def test_the_callback_is_registered_on_the_client(self):
        """The defect was that nothing registered it, so nothing could report."""
        self.assertIs(mqtt_mod.client.on_connect_fail, mqtt_mod.on_connect_fail)
