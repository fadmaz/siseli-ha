import importlib
import os
import unittest
from unittest import mock

from tests.helpers import reload_config



class TestValidateConfig(unittest.TestCase):
    """Tests for config.validate_config() startup validation."""

    _BASE_ENV = {
        "INVERTER_IP": "192.168.1.139",
        "ROUTER_IP": "192.168.1.1",
        "TARGET_HOST": "8.212.18.157",
        "TARGET_PORT": "1883",
        "MQTT_HOST": "core-mosquitto",
        "MQTT_PORT": "1883",
        "LISTEN_PORT": "18899",
        "UPDATE_INTERVAL_SEC": "10",
        "INVERTER_COUNT": "1",
        "BATTERY_COUNT": "1",
        "BATTERY_CAPACITY_PER_BATTERY_AH": "0.0",
    }

    def _reload_config(self, overrides=None):
        """Reload config module so that patched env vars take effect."""
        env = dict(self._BASE_ENV)
        if overrides:
            env.update(overrides)
        import src.siseli_bridge.config as cfg_mod

        with mock.patch.dict(os.environ, env, clear=False):
            importlib.reload(cfg_mod)
        return cfg_mod

    @mock.patch("src.siseli_bridge.config.os.makedirs")
    def test_valid_config_passes(self, _mock_makedirs):
        cfg = self._reload_config()
        # Should not raise or call sys.exit
        cfg.validate_config()

    @mock.patch("src.siseli_bridge.config.os.makedirs")
    def test_invalid_inverter_ip_fails(self, _mock_makedirs):
        cfg = self._reload_config({"INVERTER_IP": "not-an-ip"})
        with self.assertRaises(SystemExit):
            cfg.validate_config()

    @mock.patch("src.siseli_bridge.config.os.makedirs")
    def test_invalid_router_ip_fails(self, _mock_makedirs):
        cfg = self._reload_config({"ROUTER_IP": "999.999.999.999"})
        with self.assertRaises(SystemExit):
            cfg.validate_config()

    @mock.patch("src.siseli_bridge.config.os.makedirs")
    def test_invalid_mqtt_port_high_fails(self, _mock_makedirs):
        cfg = self._reload_config({"MQTT_PORT": "99999"})
        with self.assertRaises(SystemExit):
            cfg.validate_config()

    @mock.patch("src.siseli_bridge.config.os.makedirs")
    def test_zero_target_port_fails(self, _mock_makedirs):
        cfg = self._reload_config({"TARGET_PORT": "0"})
        with self.assertRaises(SystemExit):
            cfg.validate_config()

    @mock.patch("src.siseli_bridge.config.os.makedirs")
    def test_empty_mqtt_host_fails(self, _mock_makedirs):
        cfg = self._reload_config({"MQTT_HOST": ""})
        with self.assertRaises(SystemExit):
            cfg.validate_config()

    @mock.patch("src.siseli_bridge.config.os.makedirs")
    def test_empty_target_host_fails(self, _mock_makedirs):
        cfg = self._reload_config({"TARGET_HOST": ""})
        with self.assertRaises(SystemExit):
            cfg.validate_config()

    @mock.patch("src.siseli_bridge.config.os.makedirs")
    def test_a_target_host_that_can_never_match_fails(self, _mock_makedirs):
        """TARGET_HOST is compared as a string with each packet's IPv4 destination. A
        hostname, an IPv6 address or stray spaces never match, so nothing was ever
        decoded. Refusing to start is strictly better, in every forwarding mode."""
        for value in ("broker.mqtt.solar.siseli.com", "2001:db8::1", " 8.212.18.157"):
            with self.subTest(value=value):
                cfg = self._reload_config({"TARGET_HOST": value})
                with self.assertRaises(SystemExit):
                    cfg.validate_config()

    @mock.patch("src.siseli_bridge.config.os.makedirs")
    def test_the_shipped_target_host_is_accepted(self, _mock_makedirs):
        cfg = self._reload_config({"TARGET_HOST": "8.212.18.157"})
        cfg.validate_config()

    @mock.patch("src.siseli_bridge.config.os.makedirs")
    def test_update_interval_zero_fails(self, _mock_makedirs):
        cfg = self._reload_config({"UPDATE_INTERVAL_SEC": "0"})
        with self.assertRaises(SystemExit):
            cfg.validate_config()

    @mock.patch("src.siseli_bridge.config.os.makedirs")
    def test_update_interval_negative_fails(self, _mock_makedirs):
        cfg = self._reload_config({"UPDATE_INTERVAL_SEC": "-5"})
        with self.assertRaises(SystemExit):
            cfg.validate_config()

    @mock.patch("src.siseli_bridge.config.os.makedirs")
    def test_multiple_errors_all_reported(self, _mock_makedirs):
        """All errors should be collected before aborting, not fail on first."""
        cfg = self._reload_config(
            {"INVERTER_IP": "bad-ip", "MQTT_PORT": "0", "MQTT_HOST": ""}
        )
        with self.assertRaises(SystemExit) as ctx:
            cfg.validate_config()
        # Exit message should mention the error count
        self.assertIn("3", str(ctx.exception))

    @mock.patch("src.siseli_bridge.config.os.makedirs")
    def test_inverter_count_zero_fails(self, _mock_makedirs):
        cfg = self._reload_config({"INVERTER_COUNT": "0"})
        with self.assertRaises(SystemExit):
            cfg.validate_config()

    @mock.patch("src.siseli_bridge.config.os.makedirs")
    def test_battery_count_zero_fails(self, _mock_makedirs):
        cfg = self._reload_config({"BATTERY_COUNT": "0"})
        with self.assertRaises(SystemExit):
            cfg.validate_config()

    @mock.patch("src.siseli_bridge.config.os.makedirs")
    def test_negative_battery_capacity_per_battery_fails(self, _mock_makedirs):
        cfg = self._reload_config({"BATTERY_CAPACITY_PER_BATTERY_AH": "-1"})
        with self.assertRaises(SystemExit):
            cfg.validate_config()


class TestDebugFlags(unittest.TestCase):
    """Ten fine-grained switches were env-only with no add-on option, so from the UI
    they were all-on (LOG_LEVEL=debug) or all-off. There was no way to enable just the
    unparsed-payload dump, which is what someone with an unsupported inverter needs."""

    def _flags(self, **env):
        cfg = reload_config(**env)
        return [name for name in cfg.DEBUG_FLAG_NAMES if cfg._debug(name)]

    def test_nothing_is_enabled_by_default(self):
        """The add-on shipped with LOG_VERBOSE true, so a fresh install wrote a line
        for every captured frame without anyone asking."""
        self.assertEqual(self._flags(), [])

    def test_a_single_flag_can_be_enabled_on_its_own(self):
        self.assertEqual(self._flags(DEBUG_FLAGS="unparsed_publish"), ["unparsed_publish"])

    def test_several_flags_are_comma_separated(self):
        self.assertEqual(
            sorted(self._flags(DEBUG_FLAGS="blocks,unparsed_publish")),
            ["blocks", "unparsed_publish"],
        )

    def test_newline_separation_is_accepted(self):
        """bashio emits one array element per line."""
        self.assertEqual(sorted(self._flags(DEBUG_FLAGS="blocks\nxray")), ["blocks", "xray"])

    def test_debug_log_level_enables_everything(self):
        cfg = reload_config(LOG_LEVEL="debug")
        self.assertEqual(len(self._flags(LOG_LEVEL="debug")), len(cfg.DEBUG_FLAG_NAMES))

    def test_unknown_flags_are_reported_not_silently_dropped(self):
        cfg = reload_config(DEBUG_FLAGS="blocks,nonsense")
        self.assertEqual(cfg.UNKNOWN_DEBUG_FLAGS, ["nonsense"])

    def test_verbose_covers_frames_and_packets_separately(self):
        """LOG_VERBOSE conflated 'no data arriving' with 'data arrives but does not
        parse', so diagnosing either meant enabling both."""
        self.assertEqual(self._flags(DEBUG_FLAGS="xray"), ["xray"])
        self.assertEqual(self._flags(DEBUG_FLAGS="packets"), ["packets"])

    def test_legacy_log_verbose_is_ignored(self):
        """Honouring it would preserve exactly the output it was meant to remove."""
        cfg = reload_config(LOG_VERBOSE="true")
        self.assertFalse(cfg.LOG_VERBOSE)
        self.assertTrue(cfg.LOG_VERBOSE_DEPRECATED)


class TestDerivedTopics(unittest.TestCase):
    """The defaults shipped a literal naming siseli_inverter_1, so Supervisor always
    materialised the key, the DEVICE_ID-derived fallback was unreachable, and changing
    the device id did not move the topics."""

    def test_topics_follow_the_device_id_when_left_blank(self):
        cfg = reload_config(DEVICE_ID="solar_shed", STATE_TOPIC="", AVAILABILITY_TOPIC="")
        self.assertEqual(cfg.STATE_TOPIC, "siseli/solar_shed/state")
        self.assertEqual(cfg.AVAILABILITY_TOPIC, "siseli/solar_shed/availability")

    def test_an_explicit_topic_still_wins(self):
        cfg = reload_config(DEVICE_ID="solar_shed", STATE_TOPIC="custom/root/state")
        self.assertEqual(cfg.STATE_TOPIC, "custom/root/state")

    def test_whitespace_only_counts_as_blank(self):
        cfg = reload_config(DEVICE_ID="solar_shed", STATE_TOPIC="   ")
        self.assertEqual(cfg.STATE_TOPIC, "siseli/solar_shed/state")

    @mock.patch("src.siseli_bridge.config.os.makedirs")
    def test_wildcards_in_a_topic_are_fatal(self, _makedirs):
        """Publishing to a topic containing + or # is a protocol violation; the broker
        closes the connection and paho retries in a loop."""
        for bad in ("siseli/+/state", "siseli/#", "/siseli/state"):
            with self.subTest(topic=bad):
                cfg = reload_config(STATE_TOPIC=bad)
                with self.assertRaises(SystemExit):
                    cfg.validate_config()


class TestDeviceIdSanitisation(unittest.TestCase):
    def test_invalid_characters_are_replaced(self):
        cfg = reload_config(DEVICE_ID="Siseli Inverter 1")
        self.assertEqual(cfg.DEVICE_ID, "Siseli_Inverter_1")
        self.assertEqual(cfg.DEVICE_ID_RAW, "Siseli Inverter 1")

    def test_case_is_preserved(self):
        """Lowercasing would rename the topics of every user whose id has a capital,
        turning a safety fix into a breaking change."""
        for value in ("siseli_inverter_1", "Inv-2", "ABC123"):
            with self.subTest(value=value):
                self.assertEqual(reload_config(DEVICE_ID=value).DEVICE_ID, value)

    def test_an_empty_id_falls_back_to_the_default(self):
        self.assertEqual(reload_config(DEVICE_ID="").DEVICE_ID, "siseli_inverter_1")

    @mock.patch("src.siseli_bridge.config.os.makedirs")
    def test_sanitisation_is_a_warning_not_a_failure(self, _makedirs):
        cfg = reload_config(DEVICE_ID="Siseli Inverter 1")
        cfg.validate_config()  # must not raise


class TestExpiryAndTimeout(unittest.TestCase):
    @mock.patch("src.siseli_bridge.config.os.makedirs")
    def test_update_interval_must_be_shorter_than_the_expiry_window(self, _makedirs):
        """Otherwise the publish throttle outlasts the expiry and every entity flaps
        to unavailable between updates."""
        cfg = reload_config(UPDATE_INTERVAL_SEC="700", EXPIRE_AFTER_SEC="600")
        with self.assertRaises(SystemExit):
            cfg.validate_config()

    @mock.patch("src.siseli_bridge.config.os.makedirs")
    def test_expiry_can_be_disabled(self, _makedirs):
        cfg = reload_config(UPDATE_INTERVAL_SEC="700", EXPIRE_AFTER_SEC="0")
        cfg.validate_config()  # must not raise

    @mock.patch("src.siseli_bridge.config.os.makedirs")
    def test_a_too_short_telemetry_timeout_is_rejected(self, _makedirs):
        cfg = reload_config(TELEMETRY_TIMEOUT_SEC="5")
        with self.assertRaises(SystemExit):
            cfg.validate_config()


if __name__ == "__main__":
    unittest.main()
