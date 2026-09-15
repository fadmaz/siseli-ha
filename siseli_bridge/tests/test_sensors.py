import pathlib
import re
import unittest

# Add parent directory to path to allow importing src

from src.siseli_bridge.sensors import (
    SENSORS,
    UNDECODED_SENSOR_KEYS,
    SENSOR_GROUP_TITLES,
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

    def test_energy_dashboard_calculated_sensors_metadata(self):
        self.assertEqual(SENSORS["c_battery_charge_power_w"].get("device_class"), "power")
        self.assertEqual(SENSORS["c_battery_charge_power_w"].get("state_class"), "measurement")
        self.assertEqual(SENSORS["c_battery_charge_power_w"].get("unit"), "W")

        self.assertEqual(SENSORS["c_battery_discharge_power_w"].get("device_class"), "power")
        self.assertEqual(SENSORS["c_battery_discharge_power_w"].get("state_class"), "measurement")
        self.assertEqual(SENSORS["c_battery_discharge_power_w"].get("unit"), "W")

        self.assertEqual(SENSORS["c_grid_import_power_w"].get("device_class"), "power")
        self.assertEqual(SENSORS["c_grid_import_power_w"].get("state_class"), "measurement")
        self.assertEqual(SENSORS["c_grid_import_power_w"].get("unit"), "W")

        self.assertEqual(SENSORS["c_battery_charge_energy_kwh"].get("device_class"), "energy")
        self.assertEqual(SENSORS["c_battery_charge_energy_kwh"].get("state_class"), "total_increasing")
        self.assertEqual(SENSORS["c_battery_charge_energy_kwh"].get("unit"), "kWh")

        self.assertEqual(SENSORS["c_battery_discharge_energy_kwh"].get("device_class"), "energy")
        self.assertEqual(SENSORS["c_battery_discharge_energy_kwh"].get("state_class"), "total_increasing")
        self.assertEqual(SENSORS["c_battery_discharge_energy_kwh"].get("unit"), "kWh")

        self.assertEqual(SENSORS["c_grid_import_energy_kwh"].get("device_class"), "energy")
        self.assertEqual(SENSORS["c_grid_import_energy_kwh"].get("state_class"), "total_increasing")
        self.assertEqual(SENSORS["c_grid_import_energy_kwh"].get("unit"), "kWh")

    def test_energy_dashboard_calculated_sensors_grouping(self):
        calculated_keys = [key for key in SENSORS.keys() if key.startswith("c_")]
        self.assertTrue(calculated_keys)
        for key in calculated_keys:
            with self.subTest(key=key):
                self.assertEqual(get_sensor_group(key), "main")

class TestUndecodableSensors(unittest.TestCase):
    """These sensors only ever held values invented by hardcoded presets. They stay
    declared so a future real decode reuses the same unique_id, but they must not be
    enabled on a fresh install."""

    def test_every_undecodable_key_exists_and_is_disabled(self):
        for key in sorted(UNDECODED_SENSOR_KEYS):
            with self.subTest(key=key):
                self.assertIn(key, SENSORS)
                self.assertIs(SENSORS[key].get("enabled_by_default"), False)

    def test_the_list_covers_the_fault_indicators_and_bms_flags(self):
        for key in (
            "mode", "overloaded", "machine_over_temperature", "low_battery_alarm",
            "bms_temperature_too_high_flag", "bms_communication_normal",
            "battery_not_connected", "util_chg",
        ):
            with self.subTest(key=key):
                self.assertIn(key, UNDECODED_SENSOR_KEYS)

    def test_decoded_sensors_are_not_in_the_list(self):
        for key in ("bat_v", "grid_v", "load_w", "bms_current_soc", "yavb_flags_raw"):
            with self.subTest(key=key):
                self.assertNotIn(key, UNDECODED_SENSOR_KEYS)

    def test_no_listed_key_is_written_by_the_parser(self):
        """The list means "nothing decodes this", and load_cached_state purges every
        key on it at startup. A key with a live writer would be purged on restart and
        rewritten on the next payload, which is neither state.

        Checked against the source rather than a hardcoded sample, because the sample
        is what let battery_type sit on the wrong side of this for several releases.
        Every state write in parsers.py uses a double-quoted literal key (152 of them,
        none single-quoted), so this pattern sees all of them.
        """
        parsers_src = (
            pathlib.Path(__file__).resolve().parents[1]
            / "src" / "siseli_bridge" / "parsers.py"
        ).read_text(encoding="utf-8")

        written = set(re.findall(r'state\["([a-z0-9_]+)"\]\s*=', parsers_src))
        self.assertGreater(len(written), 100, "the pattern stopped matching state writes")

        overlap = sorted(written & set(UNDECODED_SENSOR_KEYS))
        self.assertEqual(
            overlap, [], f"written by the parser yet listed as undecoded: {overlap}"
        )

    def test_every_key_the_parser_writes_is_registered(self):
        """This is what makes the cache filter in load_cached_state safe. That filter
        drops any cached key not in SENSORS, on the reasoning that "not registered"
        means "not produced by this build". If the parser ever wrote an unregistered
        key, the filter would silently discard a live value on every restart -- and
        publish_grouped_state would have been publishing it with no entity behind it.
        """
        parsers_src = (
            pathlib.Path(__file__).resolve().parents[1]
            / "src" / "siseli_bridge" / "parsers.py"
        ).read_text(encoding="utf-8")

        written = set(re.findall(r'state\["([a-z0-9_]+)"\]\s*=', parsers_src))
        self.assertGreater(len(written), 100, "the pattern stopped matching state writes")

        unregistered = sorted(written - set(SENSORS))
        self.assertEqual(
            unregistered,
            [],
            f"the parser writes keys with no registry entry: {unregistered}",
        )

    def test_configured_capacity_is_named_as_a_configuration_echo(self):
        """It is BATTERY_COUNT x BATTERY_CAPACITY_PER_BATTERY_AH, not a BMS reading,
        and it sat next to bms_nominal_ah contradicting it 2x on a live install."""
        self.assertEqual(
            SENSORS["c_bms_total_capacity_ah"]["name"],
            "Battery Status - Configured Battery Bank Capacity",
        )


class TestCellCountIsNamedForWhatItCounts(unittest.TestCase):
    """bms_cell_count is the number of voltages v09K carried -- 16 of a 32-cell pack on
    the reference install, 2 when cell 3 collapses -- not the pack size. The friendly
    name said "BMS Cell Count". Only the name moves: the key and its "BMS Status - "
    prefix fix the unique_id, so the entity and its history are unchanged."""

    def test_the_name_says_what_it_counts(self):
        self.assertEqual(
            SENSORS["bms_cell_count"]["name"], "BMS Status - Cell Voltages Decoded"
        )

    def test_it_stays_on_the_bms_device(self):
        self.assertEqual(get_sensor_group("bms_cell_count"), "bms")


if __name__ == '__main__':
    unittest.main()
