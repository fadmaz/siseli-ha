import importlib
import os
import unittest
from unittest import mock



class TestLoggingLevels(unittest.TestCase):
    def _reload_loggers(self, level: str):
        env = {
            "LOG_LEVEL": level,
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

        import src.siseli_local_bridge.config as cfg_mod
        import src.siseli_local_bridge.loggers as log_mod

        with mock.patch.dict(os.environ, env, clear=False):
            importlib.reload(cfg_mod)
            importlib.reload(log_mod)

        return log_mod

    def test_info_logs_suppressed_at_error_level(self):
        log_mod = self._reload_loggers("error")

        with mock.patch("builtins.print") as mock_print:
            log_mod.log("info message")
            mock_print.assert_not_called()

    def test_error_logs_allowed_at_error_level(self):
        log_mod = self._reload_loggers("error")

        with mock.patch("builtins.print") as mock_print:
            log_mod.log("error message", level="error")
            mock_print.assert_called_once()

    def test_log_kv_respects_level(self):
        log_mod = self._reload_loggers("warning")

        with mock.patch("builtins.print") as mock_print:
            log_mod.log_kv("[TAG]", key=1)
            mock_print.assert_not_called()

        with mock.patch("builtins.print") as mock_print:
            log_mod.log_kv("[TAG]", level="warning", key=1)
            mock_print.assert_called_once()

    def test_log_payload_preview_respects_level(self):
        log_mod = self._reload_loggers("error")

        with mock.patch("builtins.print") as mock_print:
            log_mod.log_payload_preview("[PAYLOAD]", b"abc")
            mock_print.assert_not_called()

        with mock.patch("builtins.print") as mock_print:
            log_mod.log_payload_preview("[PAYLOAD]", b"abc", level="error")
            mock_print.assert_called_once()

    def test_log_error_always_bypasses_filter(self):
        log_mod = self._reload_loggers("error")

        with mock.patch("builtins.print") as mock_print:
            log_mod.log_error_always("must print")
            mock_print.assert_called_once()


if __name__ == "__main__":
    unittest.main()
