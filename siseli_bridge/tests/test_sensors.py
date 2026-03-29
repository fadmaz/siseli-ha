import unittest
import sys
import os

# Add parent directory to path to allow importing src
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.siseli_bridge.sensors import (
    SENSORS,
    SENSOR_GROUP_TITLES,
    MAIN_SENSOR_KEYS,
    get_group_title,
    get_grouped_sensor_keys,
    get_sensor_group,
)

class TestSensors(unittest.TestCase):
    def test_sensors_schema(self):
        """Ensure all sensors have a name and valid configuration."""
        for key, config in SENSORS.items():
            with self.subTest(key=key):
                self.assertIn("name", config, f"Sensor {key} is missing a name")
                self.assertIsInstance(config["name"], str)
                
                # Check for common optional keys type correctness
                if "unit" in config:
                    self.assertIsInstance(config["unit"], str)
                if "icon" in config:
                    self.assertIsInstance(config["icon"], str)
                    self.assertTrue(config["icon"].startswith("mdi:"), f"Icon for {key} should start with mdi:")
                if "device_class" in config:
                    self.assertIsInstance(config["device_class"], str)
                if "state_class" in config:
                    self.assertIsInstance(config["state_class"], str)

    def test_unique_sensor_names(self):
        """Ensure no two sensors share the same Home Assistant name to avoid collisions."""
        names = {}
        for key, config in SENSORS.items():
            name = config["name"]
            if name in names:
                self.fail(f"Duplicate sensor name '{name}' found for keys '{names[name]}' and '{key}'")
            names[name] = key

    def test_entity_categories(self):
        """Ensure entity categories are valid."""
        valid_categories = {None, "diagnostic", "config"}
        for key, config in SENSORS.items():
            category = config.get("entity_category")
            self.assertIn(category, valid_categories, f"Invalid entity_category '{category}' for sensor {key}")

    def test_sensor_grouping_prefixes(self):
        """Ensure primary app sections map to dedicated logical devices."""
        self.assertEqual(get_sensor_group("bat_v"), "battery")
        self.assertEqual(get_sensor_group("cell_1_mv"), "bms")
        self.assertEqual(get_sensor_group("grid_v"), "grid")
        self.assertEqual(get_sensor_group("out_v"), "load")
        self.assertEqual(get_sensor_group("pv_v"), "pv")
        self.assertEqual(get_sensor_group("mode"), "main")
        self.assertEqual(get_sensor_group("mains_power_w"), "grid")
        self.assertEqual(get_sensor_group("c_mains_power_w"), "main")

    def test_sensor_grouping_settings_split(self):
        """Ensure diagnostics on the More page are functionally distributed."""
        self.assertEqual(get_sensor_group("bms_avg_temp_c"), "battery")
        self.assertEqual(get_sensor_group("grid_connection_sign"), "grid")
        self.assertEqual(get_sensor_group("pv_energy_feeding_priority"), "pv")
        self.assertEqual(get_sensor_group("parallel_mode"), "load")

    def test_grouping_covers_all_sensors(self):
        grouped = get_grouped_sensor_keys()
        regrouped = set()
        for keys in grouped.values():
            regrouped.update(keys)
        self.assertEqual(regrouped, set(SENSORS.keys()))

    def test_group_titles(self):
        for group in SENSOR_GROUP_TITLES:
            self.assertEqual(get_group_title(group), SENSOR_GROUP_TITLES[group])
        self.assertEqual(get_group_title("main"), "Main")
        self.assertEqual(get_group_title("unknown-group"), "Diagnostics")

    def test_main_mode_and_soc_are_not_diagnostic(self):
        self.assertNotIn("entity_category", SENSORS["mode"])
        self.assertNotIn("entity_category", SENSORS["bms_current_soc"])

    def test_debug_helpers_stay_hidden_by_default(self):
        self.assertEqual(SENSORS["mains_eo8w_code"].get("entity_category"), "diagnostic")
        self.assertFalse(SENSORS["mains_eo8w_code"].get("enabled_by_default", True))

if __name__ == '__main__':
    unittest.main()
