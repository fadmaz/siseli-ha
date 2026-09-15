"""Tests for the stale-discovery sweep.

A key's sensor group is baked into both its discovery topic and its unique_id, so
changing the grouping orphans the user's existing entity: the old retained config
stays on the broker and Home Assistant keeps the orphan alive alongside the new one,
frozen at its last value. This already happened in v2.5.21, when the calculated
sensors moved onto the main device.

The sweep publishes an empty retained payload to every topic this configuration will
never write to again. Because that is a mass delete of retained data, the first test
here is the safety property: the sweep and the live set must be disjoint.
"""

import json
import os
import tempfile
import unittest
from unittest import mock

from src.siseli_local_bridge import mqtt as mqtt_mod
from src.siseli_local_bridge import state as shared_state
from src.siseli_local_bridge.sensors import SENSOR_GROUP_TITLES, SENSORS, get_sensor_group
from tests.helpers import FakeMqttClient, isolated_state, patch_consts

PREFIX = "homeassistant"
DEVICE = "inv1"


class _SweepTestCase(unittest.TestCase):
    def setUp(self):
        self.client = FakeMqttClient()
        patcher = mock.patch("src.siseli_local_bridge.mqtt.client", self.client)
        patcher.start()
        self.addCleanup(patcher.stop)

        ctx = isolated_state()
        ctx.__enter__()
        self.addCleanup(lambda: ctx.__exit__(None, None, None))
        shared_state.DISCOVERY_CLEANED = False
        self.addCleanup(lambda: setattr(shared_state, "DISCOVERY_CLEANED", False))

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.marker = os.path.join(self.tmp.name, "discovery_state.json")

        consts = patch_consts(
            "src.siseli_local_bridge.mqtt",
            DEVICE_ID=DEVICE,
            MQTT_DISCOVERY_PREFIX=PREFIX,
            AVAILABILITY_TOPIC="siseli/inv1/availability",
            STATE_TOPIC="siseli/inv1/state",
            DISCOVERY_MARKER_FILE=self.marker,
            DISCOVERY_CLEANUP=True,
        )
        consts.__enter__()
        self.addCleanup(lambda: consts.__exit__(None, None, None))

    def _live_topics(self):
        return {
            f"{PREFIX}/sensor/{mqtt_mod.device_id_for_group(get_sensor_group(key))}/{key}/config"
            for key in SENSORS
        }


class TestSweepSafety(_SweepTestCase):
    def test_the_sweep_can_never_target_a_live_entity(self):
        """The property that makes a mass retained-delete safe."""
        stale = mqtt_mod.stale_discovery_topics()
        self.assertEqual(stale & self._live_topics(), set())

    def test_the_sweep_size_is_bounded_and_known(self):
        stale = mqtt_mod.stale_discovery_topics()
        expected = len(SENSORS) * len(SENSOR_GROUP_TITLES) - len(SENSORS)
        self.assertEqual(len(stale), expected)

    def test_undecodable_sensors_keep_their_configs(self):
        """They stay registered and read unknown. Deleting them instead would remove
        the entities outright, which is a different policy than the one chosen."""
        from src.siseli_local_bridge.sensors import UNDECODED_SENSOR_KEYS

        stale = mqtt_mod.stale_discovery_topics()
        for key in sorted(UNDECODED_SENSOR_KEYS):
            live = f"{PREFIX}/sensor/{mqtt_mod.device_id_for_group(get_sensor_group(key))}/{key}/config"
            with self.subTest(key=key):
                self.assertNotIn(live, stale)

    def test_a_renamed_device_sweeps_every_topic_under_the_old_id(self):
        stale = mqtt_mod.stale_discovery_topics("old_device")
        self.assertEqual(len(stale), len(SENSORS) * len(SENSOR_GROUP_TITLES))
        self.assertEqual(stale & self._live_topics(), set())


class TestSweepExecution(_SweepTestCase):
    def test_the_sweep_clears_topics_and_records_a_marker(self):
        cleared = mqtt_mod.cleanup_stale_discovery()

        self.assertGreater(cleared, 0)
        emptied = self.client.cleared_topics()
        self.assertEqual(len(emptied), cleared)
        self.assertEqual(set(emptied) & self._live_topics(), set())

        with open(self.marker) as handle:
            marker = json.load(handle)
        self.assertEqual(marker["device_id"], DEVICE)
        self.assertEqual(marker["discovery_prefix"], PREFIX)

    def test_legacy_group_availability_topics_are_cleared(self):
        """Every entity now watches one availability topic; the per-group ones would
        otherwise linger retained as online forever."""
        mqtt_mod.cleanup_stale_discovery()
        emptied = set(self.client.cleared_topics())
        self.assertIn("siseli/inv1/battery/availability", emptied)
        self.assertNotIn("siseli/inv1/availability", emptied)

    def test_it_does_not_run_twice_in_one_process(self):
        mqtt_mod.cleanup_stale_discovery()
        self.client.published.clear()
        self.assertEqual(mqtt_mod.cleanup_stale_discovery(), 0)
        self.assertEqual(self.client.published, [])

    def test_a_matching_marker_skips_the_sweep_entirely(self):
        with open(self.marker, "w") as handle:
            json.dump(
                {"schema": 1, "device_id": DEVICE, "discovery_prefix": PREFIX}, handle
            )
        self.assertEqual(mqtt_mod.cleanup_stale_discovery(), 0)
        self.assertEqual(self.client.published, [])

    def test_a_changed_device_id_triggers_a_wider_sweep(self):
        with open(self.marker, "w") as handle:
            json.dump(
                {"schema": 1, "device_id": "old_device", "discovery_prefix": PREFIX}, handle
            )
        cleared = mqtt_mod.cleanup_stale_discovery()

        emptied = set(self.client.cleared_topics())
        self.assertGreater(cleared, len(SENSORS) * len(SENSOR_GROUP_TITLES))
        self.assertTrue(any("old_device" in t for t in emptied))
        self.assertEqual(emptied & self._live_topics(), set())

    def test_the_kill_switch_disables_it(self):
        with patch_consts("src.siseli_local_bridge.mqtt", DISCOVERY_CLEANUP=False):
            self.assertEqual(mqtt_mod.cleanup_stale_discovery(), 0)
        self.assertEqual(self.client.published, [])

    def test_an_unwritable_marker_does_not_abort_the_sweep(self):
        with patch_consts(
            "src.siseli_local_bridge.mqtt",
            DISCOVERY_MARKER_FILE=os.path.join(self.tmp.name, "no", "such", "dir", "x.json"),
        ), mock.patch(
            "src.siseli_local_bridge.state.atomic_write_json", side_effect=OSError("read-only")
        ):
            cleared = mqtt_mod.cleanup_stale_discovery()
        self.assertGreater(cleared, 0)

    def test_a_corrupt_marker_is_treated_as_absent(self):
        with open(self.marker, "w") as handle:
            handle.write("{not json")
        self.assertGreater(mqtt_mod.cleanup_stale_discovery(), 0)


class TestSweepRunsBeforeDiscovery(_SweepTestCase):
    def test_on_connect_sweeps_then_republishes(self):
        mqtt_mod.on_connect(self.client, None, {}, 0)

        emptied = set(self.client.cleared_topics())
        configs = set(self.client.discovery_configs())

        self.assertTrue(emptied)
        self.assertEqual(len(configs), len(SENSORS))
        self.assertEqual(emptied & configs, set(), "a swept topic must not be republished")


if __name__ == "__main__":
    unittest.main()
