import importlib
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


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


if __name__ == "__main__":
    unittest.main()
