"""Regression tests for the rule that a value is published only when this payload
contains evidence for it.

Each class here pins the absence of something the parser used to invent: hardcoded
presets keyed on one inverter's settings, a second writer overwriting a key with a
different quantity, a range guard that discarded the alarm condition it was meant to
catch, or a calculated value derived from a payload that carried no inputs.

Fixtures are real captures from two devices -- see tests/captures.py.
"""

import inspect
import os
import pathlib
import re
import tempfile
import unittest
from unittest import mock

from src.siseli_bridge import parsers as parser_module
from src.siseli_bridge import state as shared_state
from src.siseli_bridge.parsers import SolarParser
from tests import captures
from tests.helpers import envelope, isolated_state, patch_consts


class _ParserTestCase(unittest.TestCase):
    def setUp(self):
        ctx = isolated_state()
        ctx.__enter__()
        self.addCleanup(lambda: ctx.__exit__(None, None, None))
        shared_state.LAST_STATE.clear()
        parser_module.LAST_ENERGY_TS.clear()


class TestNoFabricatedValues(_ParserTestCase):
    def test_yavb_does_not_fabricate_bms_fault_flags(self):
        """Twelve BMS alarm flags appeared whenever the 16-bit flag word equalled one
        exact all-clear pattern, and nothing else ever wrote them -- so they were
        structurally incapable of reporting a fault."""
        state = SolarParser._try_ascii_schema({"Yavb": captures.BLOCK_YAVB_CHARGING})

        for key in (
            "bms_allow_charging_flag", "bms_allow_discharge_flag", "bms_communication_normal",
            "bms_communication_control_function", "bms_charging_overcurrent_sign",
            "bms_discharge_overcurrent_flag", "bms_low_battery_alarm_flag",
            "bms_low_power_fault_flag", "bms_low_temperature_flag",
            "bms_temperature_too_high_flag", "battery_not_connected", "battery_voltage_higher",
        ):
            with self.subTest(key=key):
                self.assertNotIn(key, state)

        # The raw bit word is the honest artefact and is still published.
        self.assertEqual(state["yavb_flags_raw"], "1001100000000000")
        self.assertEqual(state["bms_current_soc"], 58)
        self.assertEqual(state["bms_charging_current_a"], 29.1)

    def test_battery_type_is_not_guessed_from_a_block_name(self):
        """battery_type was defaulted to "LIA" whenever a Yavb block existed."""
        state = SolarParser._try_ascii_schema({"Yavb": captures.BLOCK_YAVB_CHARGING})
        self.assertNotIn("battery_type", state)

    def test_battery_type_is_not_guessed_from_a_non_numeric_token(self):
        """After the preset was removed the key kept a narrower guess: publish 2ONL
        token 6 as the pack chemistry if it happens not to be a number. On the one
        device with byte-faithful captures that token is 110007200000, a twelve-digit
        status field, so the guard never fired and nothing supported the reading it
        would have produced. The official app does report LIA for this device, so the
        old constant was right here -- and would have claimed LIA for every inverter."""
        state = SolarParser._try_ascii_schema({"2ONL": captures.BLOCK_2ONL_CHARGING})
        self.assertNotIn("battery_type", state)
        # The neighbouring token keeps its real meaning.
        self.assertEqual(state["bus_voltage"], 420.0)

    def test_battery_status_is_not_guessed_from_the_bus_voltage_token(self):
        """2ONL token 5 had the same shape -- publish it as the battery status if it
        is not numeric -- for a token that is the bus voltage."""
        state = SolarParser._try_ascii_schema({"2ONL": captures.BLOCK_2ONL_CHARGING})
        self.assertEqual(state["battery_status"], "Charge")  # from the calculated power
        self.assertEqual(state["bus_voltage"], 420.0)

    def test_battery_status_needs_battery_data(self):
        """A payload carrying only the grid block reported "Charge"."""
        state = SolarParser._try_ascii_schema({"WdRR": captures.BLOCK_WDRR_NO_GRID_FLOW})
        self.assertNotIn("battery_status", state)

    def test_battery_status_reports_idle_from_arithmetic(self):
        state = SolarParser._try_ascii_schema({"2ONL": captures.SYNTH_2ONL_IDLE})
        self.assertEqual(state["battery_status"], "Idle")

    def test_unknown_blocks_decode_to_nothing(self):
        """_try_ascii_schema always returned six calculated keys, so every payload
        reported success and the unparsed diagnostics were unreachable."""
        self.assertEqual(SolarParser._try_ascii_schema({}), {})
        self.assertEqual(SolarParser._try_ascii_schema({"ZZZZ": captures.SYNTH_UNKNOWN_BLOCK}), {})


class TestSingleWriterPerKey(_ParserTestCase):
    def test_dc_rectification_temperature_comes_from_v4w3_only(self):
        """2l0E decoded it with a `>100 -> /10` rescale that turned the live token
        01175 into 117.5 C; V4W3 carries the same reading unscaled at 51.0 C."""
        only_2l0e = SolarParser._try_ascii_schema({"2l0E": captures.BLOCK_2L0E_LOADED})
        self.assertNotIn("dc_rectification_temperature_c", only_2l0e)

        with_v4w3 = SolarParser._try_ascii_schema({
            "2l0E": captures.BLOCK_2L0E_LOADED,
            "V4W3": captures.BLOCK_V4W3_TEMPS,
        })
        self.assertEqual(with_v4w3["dc_rectification_temperature_c"], 51.0)

    def test_dhrk_does_not_write_grid_connected_current(self):
        """dHrK token 2 is a state-of-charge percentage; it was written into a sensor
        declared with unit A and device_class current."""
        state = SolarParser._try_ascii_schema({"dHrK": captures.BLOCK_DHRK_SETTINGS})
        self.assertEqual(state["parallel_mode_turn_off_soc"], 20)
        self.assertNotIn("grid_connected_current_a", state)

    def test_dhrk_does_not_write_the_mains_charging_times(self):
        """One token was written to both the start and the end time."""
        state = SolarParser._try_ascii_schema({"dHrK": captures.BLOCK_DHRK_SETTINGS})
        self.assertNotIn("mains_charging_starting_time", state)
        self.assertNotIn("mains_charging_ending_time", state)

    def test_bat_series_count_comes_from_2onl_only(self):
        from_2onl = SolarParser._try_ascii_schema({"2ONL": captures.BLOCK_2ONL_CHARGING})
        self.assertEqual(from_2onl["bat_series_count"], 4)
        from_yavb = SolarParser._try_ascii_schema({"Yavb": captures.BLOCK_YAVB_CHARGING})
        self.assertNotIn("bat_series_count", from_yavb)

    def test_low_electric_lock_voltage_comes_from_93vq_only(self):
        from_yavb = SolarParser._try_ascii_schema({"Yavb": captures.BLOCK_YAVB_CHARGING})
        self.assertNotIn("low_electric_lock_voltage_v", from_yavb)
        self.assertEqual(from_yavb["bms_discharge_voltage_limit_v"], 42.0)

    def test_float_and_bulk_voltage_come_from_93vq_only(self):
        """Both fell back to dHrK settings that mean something else entirely, so a
        payload carrying dHrK without 93VQ published the parallel-mode turn-off
        voltage as float charging voltage -- 44.0 V against a true 56.4 V."""
        dhrk_only = SolarParser._try_ascii_schema({"dHrK": captures.BLOCK_DHRK_SETTINGS})
        self.assertNotIn("float_v", dhrk_only)
        self.assertNotIn("bulk_v", dhrk_only)
        # The genuine dHrK readings are still published under their own names.
        self.assertEqual(dhrk_only["parallel_mode_turn_off_voltage_v"], 44.0)
        self.assertEqual(dhrk_only["return_to_mains_mode_voltage_v"], 46.0)

        with_93vq = SolarParser._try_ascii_schema({
            "dHrK": captures.BLOCK_DHRK_SETTINGS,
            "93VQ": captures.BLOCK_93VQ_SETTINGS,
        })
        self.assertEqual(with_93vq["float_v"], 56.4)
        self.assertEqual(with_93vq["bulk_v"], 56.4)

    def test_max_chg_carries_the_charge_limit_not_the_grid_current(self):
        """The alias fell back to grid_connected_current_a, a different quantity, when
        a short 93VQ token list omitted the charge limit. Both live in 93VQ and read
        50 A and 20 A respectively, so the value proves which source is in use."""
        state = SolarParser._try_ascii_schema({"93VQ": captures.BLOCK_93VQ_SETTINGS})
        self.assertEqual(state["maximum_total_charging_current_a"], 50)
        self.assertEqual(state["grid_connected_current_a"], 20)
        self.assertEqual(state["max_chg"], 50)

        # Absent source, absent alias -- no borrowing from another block.
        without_93vq = SolarParser._try_ascii_schema({"dHrK": captures.BLOCK_DHRK_SETTINGS})
        self.assertNotIn("max_chg", without_93vq)

    def test_cell_summary_comes_from_uxjp_only(self):
        """v09K carries at most 16 cells. This bank has 32 -- the BMS reports its
        minimum at position 32 -- so any summary derived from that list describes a
        subset of the pack."""
        cells = SolarParser._try_ascii_schema({"v09K": captures.BLOCK_V09K_CELLS_16})
        self.assertEqual(cells["bms_cell_count"], 16)
        self.assertEqual(cells["cell_1_mv"], 3321)
        for key in ("bms_min_cell_mv", "bms_max_cell_mv", "bms_min_cell_pos",
                    "bms_max_cell_pos", "bms_cell_delta_mv"):
            with self.subTest(key=key):
                self.assertNotIn(key, cells)

        summary = SolarParser._try_ascii_schema({"uxJp": captures.BLOCK_UXJP_BMS_CAPACITY})
        self.assertEqual(summary["bms_min_cell_pos"], 32)
        self.assertEqual(summary["bms_cell_delta_mv"], 7)


class TestNoQuantityIsRelabelledAsAnother(_ParserTestCase):
    """Each of these published one quantity under the name of a different one. They
    are not missing decodes -- they were wrong ones, which is worse, because a wrong
    value looks like a working sensor."""

    def test_output_set_frequency_is_not_the_measured_frequency(self):
        """It was out_hz rounded. The portal carries Output Frequency 49.9 Hz and
        Output Set Frequency 50 Hz as two fields at one instant, and a sagging output
        at 49.4 Hz would have published the user's setting as 49."""
        state = SolarParser._try_ascii_schema({"2l0E": captures.BLOCK_2L0E_LOADED})
        self.assertIn("out_hz", state)
        self.assertNotIn("output_set_frequency", state)

    def test_solar_charging_switch_is_not_derived_from_pv_power(self):
        """It read "Open" if PV power was above zero, so it reported the switch closed
        every night."""
        state = SolarParser._try_ascii_schema({"Mpod": captures.BLOCK_MPOD_PV1_IDLE})
        self.assertEqual(state["pv_w"], 0)
        self.assertNotIn("solar_charging_switch", state)

    def test_fan_status_is_not_derived_from_fan_speed(self):
        """The portal carries Fan Speed, Fan Status and Abnormal Fan Speed as three
        separate fields."""
        state = SolarParser._try_ascii_schema({"V4W3": captures.BLOCK_V4W3_TEMPS})
        self.assertIn("fan_1_speed", state)
        self.assertNotIn("fan_1_status", state)
        self.assertNotIn("fan_2_status", state)

    def test_grid_connection_count_is_not_the_pv_channel_count(self):
        """noeP token 3 reads 2 in every capture, and so do the inverter count and
        uxJp token 2 on this device -- three unrelated quantities equalling two."""
        state = SolarParser._try_ascii_schema({"noeP": captures.BLOCK_NOEP_PV2_ACTIVE})
        self.assertNotIn("total_number_of_grid_connection", state)


class TestNoDecodeIsPinnedToOneDeviceConfiguration(_ParserTestCase):
    """A guard that only passes on the reference device is a memorised constant."""

    SETTINGS = (
        "ac_charging_switch", "charging_priority_order", "working_mode", "eco",
        "dual_output_mode", "does_machine_have_output", "grid_connection_function",
        "input_source_prompt_function", "output_set_voltage",
    )

    def _decode(self, tail):
        base = captures.BLOCK_93VQ_SETTINGS.decode("ascii")
        block = base.replace("13310110230", "13310110" + tail, 1).encode("ascii")
        return SolarParser._try_ascii_schema({"93VQ": block})

    def test_the_settings_word_decodes_at_any_mains_voltage(self):
        """The whole nine-field decode was gated on the word ending in "230", the
        reference device's output setting. On a 120 V or 240 V inverter every one of
        these vanished silently, with no log line."""
        for tail in ("230", "220", "240", "120", "100"):
            with self.subTest(output_voltage=tail):
                state = self._decode(tail)
                self.assertEqual(state["output_set_voltage"], int(tail))
                for key in self.SETTINGS:
                    self.assertIn(key, state)

    def test_a_tail_that_is_not_a_voltage_decodes_nothing(self):
        """The tail still gates the decode -- it is what confirms this is the word we
        think it is. It just must not be compared against one device's setting."""
        state = self._decode("999")
        for key in self.SETTINGS:
            with self.subTest(key=key):
                self.assertNotIn(key, state)

    def test_the_reference_values_are_unchanged(self):
        state = self._decode("230")
        self.assertEqual(state["working_mode"], "SBU")
        self.assertEqual(state["charging_priority_order"], "SNU")
        self.assertEqual(state["ac_charging_switch"], "Close")
        self.assertEqual(state["grid_connection_function"], "Off")


class TestDecodedFromMeasuredEvidence(_ParserTestCase):
    """The other direction: values that were missing and are now genuinely decoded."""

    def test_bms_average_temperature_comes_from_deci_kelvin(self):
        """Token 8 is tenths of a Kelvin. 3041/10 - 273.15 = 30.95, digit for digit
        what the vendor portal reports for this device."""
        state = SolarParser._try_ascii_schema({"Yavb": captures.BLOCK_YAVB_CHARGING})
        self.assertEqual(state["bms_avg_temp_c"], 30.95)

    def test_rated_apparent_power_is_the_load_percentage_denominator(self):
        """Constant 11000 on both devices. The owner confirms 11 kW is the maximum
        output, and apparent_va / 11000 reproduces load_pct on every capture."""
        state = SolarParser._try_ascii_schema({"2l0E": captures.BLOCK_2L0E_LOADED})
        self.assertEqual(state["rated_apparent_va"], 11000)
        self.assertEqual(
            int(state["apparent_va"] / state["rated_apparent_va"] * 100),
            state["load_pct"],
        )


class TestRangeGuards(_ParserTestCase):
    def test_relay_status_is_not_taken_from_a_numeric_token(self):
        """WdRR token 8 is a number -- it reads 11000, 08969 and 07969 across captures.
        Taking its leading character made the 07969 payload report the relay Off while
        the same payload reported 4055 W delivered to the load."""
        state = SolarParser._try_ascii_schema({"WdRR": captures.BLOCK_WDRR_NO_GRID_FLOW})
        self.assertNotIn("main_output_relay_status", state)
        # The raw token is still published as the artefact to work from.
        self.assertEqual(state["wdrr_status_bits"], "11000")

    def test_overload_percentage_is_published(self):
        """Above 100% is exactly the reading a user needs, and it was discarded --
        leaving the last sub-100 value on screen."""
        state = SolarParser._try_ascii_schema({"2l0E": captures.SYNTH_2L0E_OVERLOADED})
        self.assertEqual(state["load_pct"], 115)

    def test_parse_garbage_is_still_rejected(self):
        """Widening the guard must not let a mis-indexed token through."""
        state = SolarParser._try_ascii_schema({"2l0E": captures.BLOCK_2L0E_LOADED})
        self.assertEqual(state["load_pct"], 7)

    def test_absurd_bms_current_is_rejected(self):
        """Unbounded current times voltage, accumulated into a total_increasing
        sensor, can never be corrected downward."""
        state = SolarParser._try_ascii_schema({"Yavb": captures.SYNTH_YAVB_ABSURD_CURRENT})
        self.assertNotIn("bms_charging_current_a", state)
        self.assertEqual(state["bms_discharge_current_a"], 0.0)

    def test_absurd_grid_power_is_rejected(self):
        """The only unguarded input to a total_increasing counter. _accumulate_kwh is
        monotonic, so one malformed token latched the Energy Dashboard permanently."""
        good = SolarParser._try_ascii_schema({"WdRR": captures.BLOCK_WDRR_NO_GRID_FLOW})
        self.assertEqual(good["mains_wdrr_value"], 0)
        self.assertEqual(good["mains_power_w"], 0)

        with mock.patch("src.siseli_bridge.parsers.log_kv"):
            bad = SolarParser._try_ascii_schema({"WdRR": captures.SYNTH_WDRR_ABSURD_POWER})
        for key in ("mains_wdrr_value", "mains_wdrr_abs", "mains_power_w", "c_mains_power_w"):
            with self.subTest(key=key):
                self.assertNotIn(key, bad)
        # The raw token survives as a diagnostic, exactly as bat_series_count does.
        self.assertEqual(bad["mains_wdrr_token"], "+999999999")

    def test_a_rejected_grid_value_never_reaches_the_energy_counter(self):
        """Dropping the key must skip the grid domain rather than integrate a zero."""
        shared_state.LAST_STATE["c_grid_import_energy_kwh"] = 46.732669
        with mock.patch("src.siseli_bridge.parsers.log_kv"):
            state = SolarParser._try_ascii_schema({"WdRR": captures.SYNTH_WDRR_ABSURD_POWER})
        self.assertNotIn("c_grid_import_energy_kwh", state)
        self.assertNotIn("c_grid_import_power_w", state)

    def test_the_rejection_is_reported(self):
        with mock.patch("src.siseli_bridge.parsers.log_kv") as logged:
            SolarParser._try_ascii_schema({"WdRR": captures.SYNTH_WDRR_ABSURD_POWER})
        self.assertTrue(logged.called)
        self.assertEqual(logged.call_args[0][0], "[GRID VALUE REJECTED]")

    def test_cell_list_stops_at_the_first_out_of_range_cell(self):
        """Skipping it renumbered every later cell, so cell_3_mv reported physical
        cell 4's voltage."""
        state = SolarParser._try_ascii_schema({"v09K": captures.SYNTH_V09K_CELL_3_COLLAPSED})
        self.assertEqual(state["bms_cell_count"], 2)
        self.assertEqual(state["cell_1_mv"], 3321)
        self.assertEqual(state["cell_2_mv"], 3321)
        self.assertNotIn("cell_3_mv", state)


class TestCalculatedEnergyFamily(_ParserTestCase):
    """Battery and grid had integrated counters; generation and load did not, so two
    of the four scaled power sensors had no energy partner on the same basis. The
    device's own pv_*_kwh counters are per-inverter and cannot fill that role."""

    def _run_an_hour(self, **power):
        now = 1000.0
        with mock.patch("src.siseli_bridge.parsers.log_kv"):
            for _ in range(13):  # the first call only establishes the baseline
                state = dict(power)
                SolarParser._apply_energy_dashboard_calculations(state, now_ts=now)
                shared_state.LAST_STATE.update(state)
                now += 300.0
        return state

    def test_generation_energy_integrates_the_scaled_power(self):
        state = self._run_an_hour(c_generation_power_w=4110)
        self.assertAlmostEqual(state["c_generation_energy_kwh"], 4.110, places=3)

    def test_load_energy_integrates_the_scaled_power(self):
        state = self._run_an_hour(c_load_w=1796)
        self.assertAlmostEqual(state["c_load_energy_kwh"], 1.796, places=3)

    def test_each_domain_keeps_its_own_clock(self):
        """A shared clock plus per-domain gating loses energy: a payload carrying only
        one domain would consume the interval another was waiting for."""
        with mock.patch("src.siseli_bridge.parsers.log_kv"):
            SolarParser._apply_energy_dashboard_calculations({"c_load_w": 100}, now_ts=0.0)
            SolarParser._apply_energy_dashboard_calculations({"c_load_w": 100}, now_ts=60.0)
            self.assertEqual(parser_module.LAST_ENERGY_TS.get("load"), 60.0)
            self.assertIsNone(parser_module.LAST_ENERGY_TS.get("generation"))

            state = {"c_generation_power_w": 600}
            SolarParser._apply_energy_dashboard_calculations(state, now_ts=60.0)
            SolarParser._apply_energy_dashboard_calculations(state, now_ts=120.0)
        # 600 W across the full 60 s the generation clock waited, not a truncated slice.
        self.assertAlmostEqual(
            state["c_generation_energy_kwh"], 600 * 60 / 3_600_000, places=6
        )

    def test_a_payload_without_the_power_writes_no_energy(self):
        """Same gating rule as battery and grid: evidence in this payload or nothing."""
        with mock.patch("src.siseli_bridge.parsers.log_kv"):
            state = {"mains_wdrr_value": 100}
            SolarParser._apply_energy_dashboard_calculations(state, now_ts=0.0)
        self.assertNotIn("c_generation_energy_kwh", state)
        self.assertNotIn("c_load_energy_kwh", state)


class TestEnergyIntegrationWindow(_ParserTestCase):
    """The integrator must credit the interval the inverter actually reported over.

    _energy_dt_seconds used to bound dt at max(UPDATE_INTERVAL_SEC * 6, 60), which is
    60 s at the shipped default. Payloads arrive every ~300 s with observed 600 s
    gaps, so the bound fired on every single step and all three kWh counters accrued a
    fifth of the real energy -- silently, and with the whole suite passing, because no
    test ever drove an interval longer than the bound.
    """

    #: The live capture: 52.6 V x 104.3 A, exactly as published.
    DISCHARGE_W = 5486.0

    def setUp(self):
        super().setUp()
        parser_module.ENERGY_DT_CLAMP_LOGGED = False

    def test_a_real_300s_interval_is_credited_in_full(self):
        parser_module.LAST_ENERGY_TS["battery"] = 1000.0
        self.assertEqual(SolarParser._energy_dt_seconds("battery", 1300.0), 300.0)

    def test_the_observed_600s_gap_is_credited_in_full(self):
        parser_module.LAST_ENERGY_TS["grid"] = 1000.0
        self.assertEqual(SolarParser._energy_dt_seconds("grid", 1600.0), 600.0)

    def test_an_abnormal_jump_is_still_bounded(self):
        """A clock jump or a suspended process must not credit a fabricated block."""
        parser_module.LAST_ENERGY_TS["battery"] = 1000.0
        with mock.patch("src.siseli_bridge.parsers.log_kv"):
            dt = SolarParser._energy_dt_seconds("battery", 1000.0 + 6 * 3600)
        self.assertEqual(dt, float(parser_module.ENERGY_MAX_DT_SEC))

    def test_the_bound_rises_with_a_slower_measured_cadence(self):
        """An inverter reporting every 15 min must not be truncated at 1200 s."""
        shared_state.record_telemetry(1000.0)
        shared_state.record_telemetry(1000.0 + 900.0)
        self.assertEqual(SolarParser._energy_max_dt(), 1800.0)

    def test_the_bound_is_capped(self):
        shared_state.record_telemetry(1000.0)
        shared_state.record_telemetry(1000.0 + 8 * 3600)
        self.assertEqual(
            SolarParser._energy_max_dt(), float(parser_module.TELEMETRY_TIMEOUT_CEILING_SEC)
        )

    def test_an_hour_of_discharge_records_an_hour_of_energy(self):
        """The regression in the units a user reads.

        Twelve payloads at the measured 300 s cadence, at the discharge power from the
        live capture. The old 60 s bound produced 1.097 kWh for the same hour.
        """
        state = {}
        parser_module.LAST_ENERGY_TS.clear()
        now = 1000.0
        for _ in range(13):  # first call is the baseline, so 12 integration steps
            dt = SolarParser._energy_dt_seconds("battery", now)
            SolarParser._accumulate_kwh(state, "c_battery_discharge_energy_kwh", self.DISCHARGE_W, dt)
            shared_state.LAST_STATE.update(state)
            now += 300.0

        self.assertAlmostEqual(state["c_battery_discharge_energy_kwh"], 5.486, places=3)

    def test_an_abnormal_gap_is_reported_once(self):
        parser_module.LAST_ENERGY_TS["battery"] = 1000.0
        with mock.patch("src.siseli_bridge.parsers.log_kv") as logged:
            SolarParser._energy_dt_seconds("battery", 1000.0 + 6 * 3600)
            parser_module.LAST_ENERGY_TS["battery"] = 1000.0
            SolarParser._energy_dt_seconds("battery", 1000.0 + 6 * 3600)
        self.assertEqual(logged.call_count, 1)

    def test_the_window_does_not_depend_on_the_publish_throttle(self):
        """UPDATE_INTERVAL_SEC is an MQTT throttle. Deriving the integration window
        from it is what caused the undercount, and it is Supervisor-pinned, so no
        default change could have fixed an existing install."""
        with patch_consts("src.siseli_bridge.parsers", UPDATE_INTERVAL_SEC=1):
            parser_module.LAST_ENERGY_TS["battery"] = 1000.0
            self.assertEqual(SolarParser._energy_dt_seconds("battery", 1300.0), 300.0)


class TestEnergyDomainGating(_ParserTestCase):
    def test_grid_only_payload_does_not_zero_battery_power(self):
        """One combined gate would let this through, find no battery current, and
        write 0 W -- flapping battery power to zero on every grid-only payload."""
        state = SolarParser._try_ascii_schema({"WdRR": captures.BLOCK_WDRR_NO_GRID_FLOW})
        self.assertNotIn("c_battery_charge_power_w", state)
        self.assertNotIn("c_battery_discharge_power_w", state)
        self.assertEqual(state["c_grid_import_power_w"], 0)

    def test_identity_payload_writes_no_calculated_values(self):
        """The real second payload from a live install. It carries no battery voltage
        or current at all, yet it published a changed energy total.

        The PV keys are seeded too, and that is the point: this test seeded only the
        battery pair until 2.6.16, so it passed while generation quietly kept a
        LAST_STATE fallback and republished the previous payload's PV power on every
        identity payload. A gate is only worth what its test seeds.
        """
        shared_state.LAST_STATE.update(
            {"bat_v": 53.4, "bms_charging_current_a": 29.1, "pv_w": 0, "pv2_power_w": 1403}
        )
        parser_module.LAST_ENERGY_TS["battery"] = 100.0
        parser_module.LAST_ENERGY_TS["generation"] = 100.0

        state = SolarParser._try_ascii_schema(dict(captures.CAPTURE_IDENTITY))

        self.assertEqual([k for k in state if k.startswith("c_")], [])
        self.assertNotIn("generation_power_w", state)
        self.assertEqual(
            parser_module.LAST_ENERGY_TS.get("battery"), 100.0, "battery clock must not advance"
        )
        self.assertEqual(
            parser_module.LAST_ENERGY_TS.get("generation"), 100.0, "generation clock must not advance"
        )

    def test_the_two_clocks_are_independent(self):
        """A shared clock plus per-domain gating loses energy: a grid-only payload
        would consume the interval the battery integrator needed."""
        SolarParser._apply_energy_dashboard_calculations({"mains_wdrr_value": 100}, now_ts=0.0)
        SolarParser._apply_energy_dashboard_calculations({"mains_wdrr_value": 100}, now_ts=60.0)

        self.assertEqual(parser_module.LAST_ENERGY_TS.get("grid"), 60.0)
        self.assertIsNone(parser_module.LAST_ENERGY_TS.get("battery"))

        state = {"bat_v": 50.0, "bms_charging_current_a": 10.0}
        SolarParser._apply_energy_dashboard_calculations(state, now_ts=60.0)
        SolarParser._apply_energy_dashboard_calculations(state, now_ts=120.0)
        # 500 W across the full 60 s the battery clock waited, not a truncated slice.
        self.assertAlmostEqual(
            state["c_battery_charge_energy_kwh"], 500 * 60 / 3_600_000, places=6
        )

    def test_legacy_current_fallback_is_scaled_like_every_other_sensor(self):
        """BMS figures are whole-bank; 2ONL figures are per-inverter. Without scaling
        the fallback, reported power steps whenever the BMS block is absent."""
        state = {"bat_v": 48.0, "bat_charge_current": 5.0, "dischg_current": 4.0}
        with mock.patch("src.siseli_bridge.parsers.INVERTER_COUNT", 2):
            SolarParser._apply_energy_dashboard_calculations(state, now_ts=1.0)
        self.assertEqual(state["c_battery_charge_power_w"], 480)
        self.assertEqual(state["c_battery_discharge_power_w"], 384)

    def test_cached_current_no_longer_overrides_a_fresh_reading(self):
        """A cached BMS current used to win over a fresh inverter current in the same
        payload, integrating a stale rate indefinitely."""
        shared_state.LAST_STATE.update({"bms_charging_current_a": 29.1})
        state = {"bat_v": 53.4, "bat_charge_current": 0.0, "dischg_current": 0.0}
        with mock.patch("src.siseli_bridge.parsers.INVERTER_COUNT", 1):
            SolarParser._apply_energy_dashboard_calculations(state, now_ts=1.0)
        self.assertEqual(state["c_battery_charge_power_w"], 0)

    def test_disagreeing_current_sources_are_logged_not_silently_resolved(self):
        """No ground truth exists -- the official app displays both and they differ by
        about 2x on the reference unit and 4x on another."""
        state = {"bat_v": 53.4, "bms_charging_current_a": 29.1, "bat_charge_current": 7.0}
        with mock.patch("src.siseli_bridge.parsers.log_kv") as logged:
            SolarParser._apply_energy_dashboard_calculations(state, now_ts=1.0)
        tags = [call.args[0] for call in logged.call_args_list if call.args]
        self.assertIn("[ENERGY SOURCE DISAGREEMENT]", tags)
        self.assertEqual(state["c_battery_charge_power_w"], round(53.4 * 29.1))


class TestLiveCaptureParity(_ParserTestCase):
    """The full telemetry payload from a live 2x-parallel install must still decode to
    exactly the values that installation published."""

    def test_telemetry_capture_decodes_to_the_recorded_values(self):
        with mock.patch("src.siseli_bridge.parsers.INVERTER_COUNT", 2):
            state = SolarParser._try_ascii_schema(dict(captures.CAPTURE_TELEMETRY))

        for key, expected in captures.EXPECTED_TELEMETRY.items():
            with self.subTest(key=key):
                self.assertEqual(state.get(key), expected)

        for key, expected in captures.EXPECTED_TELEMETRY_SCALED.items():
            with self.subTest(key=key):
                self.assertEqual(state.get(key), expected)

    def test_identity_capture_decodes_to_the_recorded_values(self):
        state = SolarParser._try_ascii_schema(dict(captures.CAPTURE_IDENTITY))
        for key, expected in captures.EXPECTED_IDENTITY.items():
            with self.subTest(key=key):
                self.assertEqual(state.get(key), expected)


class TestEnergyIsImmuneToAClockStep(unittest.TestCase):
    """A Raspberry Pi has no RTC. It boots with a wrong clock and NTP steps it, which
    is routine on the reference platform -- and the integrator used to measure its
    interval on the wall clock, so a step was indistinguishable from elapsed time.

    _energy_max_dt bounded the damage at ENERGY_MAX_DT_SEC but did not prevent it: a
    step credited up to 1200 s of the current power into five total_increasing counters,
    which round(max(previous, total)) makes permanent. At 5 kW that is 1.67 kWh that can
    never come back down. Durations are measured on time.monotonic() now, so the step
    cannot be seen at all.
    """

    def setUp(self):
        self._ctx = isolated_state()
        self._ctx.__enter__()
        self.addCleanup(lambda: self._ctx.__exit__(None, None, None))
        parser_module.LAST_ENERGY_TS.clear()

    def test_a_wall_clock_jump_credits_nothing(self):
        with mock.patch.object(parser_module.time, "monotonic", return_value=1000.0):
            SolarParser._apply_energy_dashboard_calculations({"c_load_w": 5000})

        # Wall clock leaps four hours; the monotonic clock advances a normal interval.
        state = {"c_load_w": 5000}
        with mock.patch.object(parser_module.time, "time", return_value=1e9), mock.patch.object(
            parser_module.time, "monotonic", return_value=1300.0
        ):
            SolarParser._apply_energy_dashboard_calculations(state)

        expected = 5000 * 300 / 3_600_000.0
        self.assertAlmostEqual(state["c_load_energy_kwh"], expected, places=5)
        self.assertLess(
            state["c_load_energy_kwh"], 0.5, "a clock step is being credited as elapsed time"
        )

    def test_the_integrator_reads_the_monotonic_clock(self):
        """Pins the clock source itself. Measuring durations on the wall clock is the
        defect, and it is invisible in behaviour until a step happens."""
        seen = []
        with mock.patch.object(
            parser_module.time, "monotonic", side_effect=lambda: seen.append(1) or 500.0
        ):
            SolarParser._apply_energy_dashboard_calculations({"c_load_w": 100})
        self.assertTrue(seen, "the energy path no longer consults the monotonic clock")


class TestNoModuleMeasuresADurationOnTheWallClock(unittest.TestCase):
    """A source pin, because a partial migration is invisible in behaviour until a
    clock steps -- and the whole suite would still pass. Moving record_telemetry to
    monotonic while telemetry_is_fresh stayed on the wall clock would make now - last
    about 1.7e9 and read every entity Unavailable forever."""

    SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "siseli_bridge"

    def test_no_runtime_module_calls_time_time(self):
        for name in ("core.py", "parsers.py", "state.py", "mqtt.py"):
            with self.subTest(module=name):
                text = (self.SRC / name).read_text(encoding="utf-8")
                self.assertNotIn(
                    "time.time()",
                    text,
                    f"{name} measures a duration on a clock that can step; use time.monotonic()",
                )

    def test_the_human_readable_timestamp_is_still_a_wall_clock(self):
        """The one correct wall-clock use: the time a person reads in the log."""
        text = (self.SRC / "parsers.py").read_text(encoding="utf-8")
        self.assertIn("datetime.now().strftime", text)


class TestBatteryCurrentGuard(unittest.TestCase):
    """The guard used to reject anything over 300 A, silently.

    300 is below what this hardware declares for itself -- the reference device reports
    bms_charge_current_limit_a of 390 -- so a real reading between the two was thrown
    away. Silently is the worse half: the key simply went absent, _battery_current then
    picked whichever source survived without the [ENERGY SOURCE DISAGREEMENT] warning
    (which needs both present), and if both were dropped the powers read 0 and
    battery_status reported Idle. A high-current moment presented as an idle battery.
    """

    def setUp(self):
        self._ctx = isolated_state()
        self._ctx.__enter__()
        self.addCleanup(lambda: self._ctx.__exit__(None, None, None))
        parser_module.BATTERY_CURRENT_REJECTED_LOGGED = False

    def test_a_current_the_device_itself_allows_is_kept(self):
        """390 A is Yavb[4] on the reference device -- its own declared charge limit.
        Rejected by the old 300 A bound."""
        for amps in (301.0, 350.0, 390.0, 600.0):
            with self.subTest(amps=amps):
                self.assertEqual(
                    SolarParser._plausible_current(amps, "bms_charging_current_a"), amps
                )

    def test_an_impossible_current_is_still_rejected(self):
        for amps in (-1.0, 9999.0, 100000.0):
            with self.subTest(amps=amps):
                parser_module.BATTERY_CURRENT_REJECTED_LOGGED = False
                self.assertIsNone(SolarParser._plausible_current(amps, "field"))

    def test_the_synthetic_absurd_block_is_still_rejected(self):
        """The only existing test of these guards. 9999 A must not become a reading."""
        state = SolarParser._try_ascii_schema({"Yavb": captures.SYNTH_YAVB_ABSURD_CURRENT})
        self.assertNotIn("bms_charging_current_a", state)

    def test_a_rejection_is_reported_once_not_per_payload(self):
        with mock.patch("src.siseli_bridge.parsers.log_kv") as logged:
            for _ in range(4):
                SolarParser._plausible_current(9999.0, "bms_charging_current_a")
        tags = [c.args[0] for c in logged.call_args_list if c.args]
        self.assertEqual(tags.count("[BATTERY CURRENT REJECTED]"), 1)

    def test_the_rejection_names_the_field_and_the_bound(self):
        with mock.patch("src.siseli_bridge.parsers.log_kv") as logged:
            SolarParser._plausible_current(9999.0, "bms_discharge_current_a")
        call = next(c for c in logged.call_args_list if c.args and c.args[0] == "[BATTERY CURRENT REJECTED]")
        self.assertEqual(call.kwargs["level"], "warning")
        self.assertEqual(call.kwargs["field"], "bms_discharge_current_a")
        self.assertEqual(call.kwargs["max_a"], parser_module.BATTERY_CURRENT_MAX_A)

    def test_the_bound_clears_the_limit_the_hardware_declares(self):
        """Pins the reason for the number rather than the number. 390 A is the highest
        limit any capture has shown; a bound at or below it rejects real readings."""
        self.assertGreater(parser_module.BATTERY_CURRENT_MAX_A, 390)


class TestPublishOutcomeIsReportedHonestly(unittest.TestCase):
    """2.6.21 initialised the publish outcome INSIDE `if DISCOVERY_PUBLISHED:` and read
    it outside. On a broker that had never connected -- the exact install the change was
    written for -- every payload raised UnboundLocalError, which the handler swallowed as
    [PARSER ERROR], so a perfectly good decode was reported as a failure and
    record_telemetry never ran.
    """

    def setUp(self):
        self._ctx = isolated_state()
        self._ctx.__enter__()
        self.addCleanup(lambda: self._ctx.__exit__(None, None, None))

    def _parse(self):
        return SolarParser.parse_payload(envelope(captures.CAPTURE_TELEMETRY))

    def test_a_payload_decodes_when_the_broker_has_never_connected(self):
        shared_state.DISCOVERY_PUBLISHED = False
        with mock.patch("src.siseli_bridge.parsers.log_kv") as logged:
            self.assertTrue(self._parse(), "a good payload must not be reported as a failure")
        said = " ".join(str(c.args[0]) for c in logged.call_args_list if c.args)
        self.assertIn("no broker connection yet", said)

    def test_the_never_connected_case_is_not_called_throttled(self):
        """Hoisting the default above the gate would have swapped the crash for a new
        false statement: nothing is being throttled when nothing can be published."""
        shared_state.DISCOVERY_PUBLISHED = False
        with mock.patch("src.siseli_bridge.parsers.log_kv") as logged:
            self._parse()
        said = " ".join(str(c.args[0]) for c in logged.call_args_list if c.args)
        self.assertNotIn("throttled", said)

    def test_every_outcome_has_a_label(self):
        outcomes = set(parser_module.PUBLISH_OUTCOMES)
        self.assertEqual(
            outcomes, {"sent", "throttled", "broker-unreachable", "no-broker-yet"}
        )
        self.assertEqual(parser_module.PUBLISH_OUTCOMES["sent"], "Published to HA")


class TestPublishThrottle(unittest.TestCase):
    """UPDATE_INTERVAL_SEC never suppressed a publish: the gate was
    `changed or interval elapsed`, and something always changed."""

    def setUp(self):
        ctx = isolated_state()
        ctx.__enter__()
        self.addCleanup(lambda: ctx.__exit__(None, None, None))
        shared_state.LAST_STATE.clear()
        shared_state.DISCOVERY_PUBLISHED = True
        parser_module.LAST_ENERGY_TS.clear()
        parser_module.PENDING_PUBLISH = False

        self.publish_state = mock.Mock()
        self.publish_discovery = mock.Mock()
        p = mock.patch.object(
            parser_module, "_get_mqtt_publish",
            return_value=(self.publish_discovery, self.publish_state),
        )
        p.start()
        self.addCleanup(p.stop)

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        c = mock.patch.object(parser_module, "STATE_CACHE_FILE", os.path.join(self.tmp.name, "state.json"))
        c.start()
        self.addCleanup(c.stop)

    def _payload(self, bat_cap):
        # Derived from the real capture so the framing stays byte-faithful.
        return envelope(
            {"2ONL": captures.BLOCK_2ONL_CHARGING.replace(b"058", b"0%d" % bat_cap)}
        )

    def test_a_change_inside_the_window_is_deferred_not_dropped(self):
        parser_module.LAST_PUBLISH_TS = 1000.0
        with mock.patch.object(parser_module, "UPDATE_INTERVAL_SEC", 10),              mock.patch.object(parser_module, "EXPIRE_AFTER_SEC", 600),              mock.patch.object(parser_module.time, "monotonic", return_value=1002.0):
            SolarParser.parse_payload(self._payload(58))
            SolarParser.parse_payload(self._payload(59))
        self.publish_state.assert_not_called()
        self.assertTrue(parser_module.PENDING_PUBLISH, "the change must be remembered")

        with mock.patch.object(parser_module, "UPDATE_INTERVAL_SEC", 10),              mock.patch.object(parser_module, "EXPIRE_AFTER_SEC", 600),              mock.patch.object(parser_module.time, "monotonic", return_value=1011.0):
            SolarParser.parse_payload(self._payload(60))
        self.publish_state.assert_called_once()
        self.assertFalse(parser_module.PENDING_PUBLISH)

    def test_heartbeat_republishes_when_nothing_changes(self):
        """Without this, a steady inverter publishes nothing and expire_after marks
        every entity unavailable."""
        parser_module.LAST_PUBLISH_TS = 1000.0
        with mock.patch.object(parser_module, "UPDATE_INTERVAL_SEC", 10),              mock.patch.object(parser_module, "EXPIRE_AFTER_SEC", 600),              mock.patch.object(parser_module.time, "monotonic", return_value=1000.0):
            SolarParser.parse_payload(self._payload(58))
        self.publish_state.reset_mock()
        parser_module.PENDING_PUBLISH = False
        parser_module.LAST_PUBLISH_TS = 1000.0

        # Same values again, one full heartbeat interval later (600 // 3 = 200 s).
        with mock.patch.object(parser_module, "UPDATE_INTERVAL_SEC", 10),              mock.patch.object(parser_module, "EXPIRE_AFTER_SEC", 600),              mock.patch.object(parser_module.time, "monotonic", return_value=1201.0):
            SolarParser.parse_payload(self._payload(58))
        self.publish_state.assert_called_once()

    def test_payload_with_no_recognised_blocks_reports_failure(self):
        self.assertFalse(SolarParser.parse_payload(envelope({"ZZZZ": b"(1 2 3)"})))
        self.publish_state.assert_not_called()


class TestForeignProtocolIsDiagnosed(_ParserTestCase):
    """Issue #30: a Beve Mega 6kW published fifteen block names this add-on has never
    seen, carrying binary Modbus RTU instead of ASCII tokens. The parser did the right
    thing and decoded nothing -- but said so only through an info-level line that reads
    identically to a known block with a truncated token list, and only when a debug
    flag was on. The reporter saw ~200 entities reading Unknown and no cause.

    Decoding this device is out of scope. Being able to tell its owner what happened
    is not.
    """

    def setUp(self):
        super().setUp()
        parser_module.UNSUPPORTED_PROTOCOL_LOGGED = False

    def _warnings(self, tag, payload):
        with mock.patch("src.siseli_bridge.parsers.log_kv") as logged:
            result = SolarParser.parse_payload(payload)
        self.assertFalse(result)
        return [c for c in logged.call_args_list if c.args and c.args[0] == tag]

    def test_a_foreign_device_is_named_as_unsupported(self):
        calls = self._warnings(
            "[UNSUPPORTED PROTOCOL]", envelope(captures.CAPTURE_DEVICE_B_FOREIGN)
        )
        self.assertEqual(len(calls), 1)
        reported = calls[0].kwargs
        self.assertEqual(reported["level"], "warning", "a debug flag must not be needed to see this")
        self.assertEqual(reported["recognised"], 0)
        self.assertEqual(reported["block_count"], len(captures.CAPTURE_DEVICE_B_FOREIGN))
        self.assertEqual(reported["body"], "binary")
        self.assertEqual(reported["looks_like"], "modbus_rtu")

    def test_the_verdict_is_said_once_not_on_every_payload(self):
        """The device republishes every few seconds. A per-payload warning would bury
        the log it is meant to make readable."""
        payload = envelope(captures.CAPTURE_DEVICE_B_FOREIGN)
        with mock.patch("src.siseli_bridge.parsers.log_kv") as logged:
            for _ in range(5):
                SolarParser.parse_payload(payload)
        tags = [c.args[0] for c in logged.call_args_list if c.args]
        self.assertEqual(tags.count("[UNSUPPORTED PROTOCOL]"), 1)

    def test_recognised_blocks_that_yield_nothing_take_the_other_branch(self):
        """A truncated known block is a different fault from a foreign device, and the
        old message could not tell them apart."""
        calls = self._warnings("[NO VALUES DECODED]", envelope({"2l0E": b"("}))
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].kwargs["recognised"], 1)
        self.assertNotIn("looks_like", calls[0].kwargs)

    def test_a_supported_device_is_never_called_unsupported(self):
        with mock.patch("src.siseli_bridge.parsers.log_kv") as logged:
            SolarParser.parse_payload(envelope(captures.CAPTURE_TELEMETRY))
        tags = [c.args[0] for c in logged.call_args_list if c.args]
        self.assertNotIn("[UNSUPPORTED PROTOCOL]", tags)
        self.assertNotIn("[NO VALUES DECODED]", tags)

    def test_the_modbus_hint_needs_more_than_a_foreign_name(self):
        """An ASCII foreign block must not be labelled Modbus -- the CRC is the whole
        basis for naming a protocol in a log line, and without it this is a guess."""
        described = SolarParser._describe_foreign_blocks({"ZZZZ": b"(1 2 3"})
        self.assertEqual(described["body"], "ascii")
        self.assertNotIn("looks_like", described)

    def test_the_crc16_xmodem_helper_matches_known_frames(self):
        """Hand-computed from real wire bytes. Pins the polynomial, the init value and
        -- the part that is easy to tidy away -- that the CRC covers the leading paren.
        Excluding it matches nothing at all."""
        self.assertEqual(SolarParser._crc16_xmodem(b"(PI30"), 0x9A0B)
        self.assertEqual(SolarParser._crc16_xmodem(b"(NAK"), 0x7373)
        self.assertEqual(SolarParser._crc16_xmodem(b"(VMIII-4000"), 0xDE93)
        self.assertNotEqual(SolarParser._crc16_xmodem(b"PI30"), 0x9A0B)

    def test_an_ascii_device_with_a_checksum_is_not_called_binary(self):
        """Issue #32, the defect this fixes. 2.6.17 called a plainly-textual device
        binary because of a two-byte CRC, and the docs name binary as the strongest
        signal of a different protocol family."""
        described = SolarParser._describe_foreign_blocks(captures.CAPTURE_DEVICE_C_VOLTRONIC)
        self.assertEqual(described["body"], "ascii+binary_tail")
        self.assertNotEqual(described["body"], "binary")
        self.assertEqual(described["looks_like"], "voltronic_pi30")
        self.assertEqual(described["recognised"], 0)

    def test_the_modbus_device_does_not_regress_to_voltronic(self):
        """Issue #30's payload contains exactly one valid Voltronic frame -- its DTU
        emits an ACK in that framing while the data blocks are Modbus. That single
        frame is why both hints are counted and thresholded rather than any-match."""
        described = SolarParser._describe_foreign_blocks(captures.CAPTURE_DEVICE_B_FOREIGN)
        self.assertEqual(described["body"], "binary")
        self.assertEqual(described["looks_like"], "modbus_rtu")
        self.assertEqual(described["voltronic_crc_ok"], "1/12")

    def test_the_ack_frame_alone_never_names_a_protocol(self):
        """The threshold is set by observed data, not picked: one valid frame out of
        twelve must not label a payload."""
        described = SolarParser._describe_foreign_blocks(
            {"aRv4": captures.DEVB_BLOCK_ARV4}
        )
        self.assertNotIn("looks_like", described)

    def test_a_supported_device_gets_no_protocol_hint(self):
        described = SolarParser._describe_foreign_blocks(captures.CAPTURE_TELEMETRY)
        self.assertEqual(described["body"], "ascii")
        self.assertNotIn("looks_like", described)
        self.assertEqual(described["recognised"], len(captures.CAPTURE_TELEMETRY))

    def test_mixed_payloads_report_every_shape_present(self):
        """A payload that is part text and part binary is itself the signal, and the
        headline takes the worst shape so nothing is understated."""
        described = SolarParser._describe_foreign_blocks(captures.CAPTURE_DEVICE_B_FOREIGN)
        self.assertIn("body_shapes", described)
        self.assertIn("ascii=1", described["body_shapes"])
        self.assertIn("binary=11", described["body_shapes"])

    def test_the_known_name_registry_matches_the_decoder(self):
        """KNOWN_BLOCK_NAMES is declared in parallel with the literals inside
        _try_ascii_schema rather than driving them, so nothing but this test stops the
        two drifting -- and a stale registry makes the diagnostic above lie about how
        many blocks were recognised."""
        source = inspect.getsource(SolarParser._try_ascii_schema)
        literals = set(re.findall(r'parsed\.get\("([^"]{4})"', source))
        literals |= set(re.findall(r'"([^"]{4})" in parsed', source))
        self.assertEqual(
            literals,
            set(parser_module.KNOWN_BLOCK_NAMES),
            "KNOWN_BLOCK_NAMES and the block names _try_ascii_schema decodes have drifted",
        )


class TestBatteryStatusMatchesReportedPower(_ParserTestCase):
    """battery_status used to come from the inverter's own ammeter while the power
    sensors came from the BMS. On a live installation the two disagreed: the status
    read "Idle" in the same publish that reported 344 W flowing into the battery.

    It is now derived from the calculated power, so the contradiction is impossible
    rather than merely unlikely.
    """

    def _resolve(self, state, count=2):
        with mock.patch("src.siseli_bridge.parsers.INVERTER_COUNT", count):
            SolarParser._apply_energy_dashboard_calculations(state, now_ts=1.0)
            SolarParser._derive_battery_status(state)
        return state

    def test_the_live_case_that_reported_idle_while_charging(self):
        """Values taken verbatim from a running installation."""
        state = self._resolve({
            "bat_v": 53.7,
            "bat_charge_current": 0.0,      # the inverter's ammeter
            "dischg_current": 0.0,
            "bms_charging_current_a": 6.4,  # the BMS
            "bms_discharge_current_a": 0.0,
        })
        self.assertEqual(state["c_battery_charge_power_w"], 344)
        self.assertEqual(state["battery_status"], "Charge")

    def test_status_and_power_can_never_contradict(self):
        for label, inputs, expected in (
            ("idle", {"bat_v": 53.7, "bat_charge_current": 0.0, "dischg_current": 0.0}, "Idle"),
            ("charging", {"bat_v": 53.7, "bat_charge_current": 5.0, "dischg_current": 0.0}, "Charge"),
            ("discharging", {"bat_v": 53.7, "bat_charge_current": 0.0, "dischg_current": 10.0}, "Discharge"),
        ):
            with self.subTest(case=label):
                state = self._resolve(dict(inputs))
                status = state["battery_status"]
                charge = state["c_battery_charge_power_w"]
                discharge = state["c_battery_discharge_power_w"]
                self.assertEqual(status, expected)
                if status == "Charge":
                    self.assertGreater(charge, 0)
                elif status == "Discharge":
                    self.assertGreater(discharge, 0)
                else:
                    self.assertEqual((charge, discharge), (0, 0))

    def test_no_battery_data_means_no_status(self):
        state = self._resolve({"mains_wdrr_value": 100})
        self.assertNotIn("battery_status", state)

    def test_one_source_reading_zero_is_reported(self):
        """A ratio test cannot express this, and it is the most informative
        disagreement there is -- it is what produced the Idle-while-charging case."""
        state = {"bat_v": 53.7, "bat_charge_current": 0.0, "bms_charging_current_a": 6.4}
        with mock.patch("src.siseli_bridge.parsers.log_kv") as logged:
            SolarParser._apply_energy_dashboard_calculations(state, now_ts=1.0)
        tags = [call.args[0] for call in logged.call_args_list if call.args]
        self.assertIn("[ENERGY SOURCE DISAGREEMENT]", tags)

    def test_agreeing_sources_stay_quiet(self):
        state = {"bat_v": 53.7, "bat_charge_current": 3.0, "bms_charging_current_a": 6.0}
        with mock.patch("src.siseli_bridge.parsers.log_kv") as logged:
            with mock.patch("src.siseli_bridge.parsers.INVERTER_COUNT", 2):
                SolarParser._apply_energy_dashboard_calculations(state, now_ts=1.0)
        tags = [call.args[0] for call in logged.call_args_list if call.args]
        self.assertNotIn("[ENERGY SOURCE DISAGREEMENT]", tags)


class TestGridDirectionConflictIsReported(_ParserTestCase):
    """WdRR[6]'s sign drives the grid-import counter; WdRR[7]'s flow code drives the
    label. Nothing forced them to agree: +01500 with code 1 labelled the flow
    "Inverter To Mains" while crediting 1500 W of import. Every capture is +00000 with
    code 0, so which token is right is unknown -- and the counter can never go down.
    2.6.23 reports the disagreement and deliberately leaves crediting alone."""

    CONFLICT = "[GRID DIRECTION CONFLICT]"

    def _decode(self, token, code, now=1000.0):
        block = captures.BLOCK_WDRR_NO_GRID_FLOW.replace(
            b"+00000 0 ", token.encode("ascii") + b" " + code.encode("ascii") + b" ", 1
        )
        with mock.patch("src.siseli_bridge.parsers.time.monotonic", return_value=now):
            return SolarParser._try_ascii_schema({"WdRR": block})

    def _conflicts(self, logged):
        return [c for c in logged.call_args_list if c.args and c.args[0] == self.CONFLICT]

    def test_every_sign_and_direction_combination(self):
        cases = [
            (1500, "Mains To Inverter", False),
            (1500, "Inverter To Mains", True),
            (1500, "Idle", True),
            (-1500, "Mains To Inverter", True),
            (-1500, "Inverter To Mains", False),
            (-1500, "Idle", True),
            (0, "Inverter To Mains", False),
            (None, "Idle", False),
            (1500, None, False),
        ]
        for signed, direction, expected in cases:
            with self.subTest(signed=signed, direction=direction):
                self.assertEqual(
                    SolarParser._grid_direction_conflicts(signed, direction), expected
                )

    def test_the_reproduced_contradiction_is_reported_once_with_its_raw_tokens(self):
        with mock.patch("src.siseli_bridge.parsers.log_kv") as logged:
            for i in range(3):
                state = self._decode("+01500", "1", now=1000.0 + 300 * i)
        self.assertEqual(state["mains_current_flow_direction"], "Inverter To Mains")
        conflicts = self._conflicts(logged)
        self.assertEqual(len(conflicts), 1, "one report per process, not per payload")
        self.assertEqual(conflicts[0].kwargs["level"], "warning")
        self.assertEqual(conflicts[0].kwargs["token"], "+01500")
        self.assertEqual(conflicts[0].kwargs["flow_code"], "1")
        self.assertEqual(conflicts[0].kwargs["direction"], "Inverter To Mains")

    def test_a_negative_sign_under_code_zero_is_reported(self):
        """The case the architecture map names: the code wins the label, so -01500
        with code 0 reads "Mains To Inverter" while the counter credits nothing."""
        with mock.patch("src.siseli_bridge.parsers.log_kv") as logged:
            state = self._decode("-01500", "0")
        self.assertEqual(state["mains_current_flow_direction"], "Mains To Inverter")
        self.assertEqual(len(self._conflicts(logged)), 1)

    def test_crediting_is_unchanged(self):
        """Pinned on purpose. Changing which token credits import is a decision for
        evidence, not for this release -- a wrong change is permanent on the counter."""
        factor = max(1.0, float(parser_module.INVERTER_COUNT))
        first = self._decode("+01500", "1", now=1000.0)
        self.assertEqual(first["c_grid_import_power_w"], int(round(1500 * factor)))
        shared_state.LAST_STATE.update(first)
        second = self._decode("+01500", "1", now=1300.0)
        self.assertGreater(second["c_grid_import_energy_kwh"], first.get("c_grid_import_energy_kwh", 0))

    def test_agreeing_tokens_raise_nothing(self):
        with mock.patch("src.siseli_bridge.parsers.log_kv") as logged:
            self._decode("+01500", "0")
            self._decode("-01500", "1")
        self.assertEqual(self._conflicts(logged), [])

    def test_the_off_grid_capture_raises_nothing(self):
        """Every real payload ever captured. A false alarm here would fire on every
        install at every start."""
        with mock.patch("src.siseli_bridge.parsers.log_kv") as logged:
            SolarParser._try_ascii_schema(captures.CAPTURE_TELEMETRY)
        self.assertEqual(self._conflicts(logged), [])


class TestCellListOverflow(_ParserTestCase):
    """v09K carries at most 16 cells on the reference pack; a longer list is counted in
    full but only 16 cell entities exist. The warning used to fire on every payload."""

    def test_seventeen_cells_are_counted_but_only_sixteen_published(self):
        with mock.patch("src.siseli_bridge.parsers.log"):
            state = SolarParser._try_ascii_schema({"v09K": captures.SYNTH_V09K_CELLS_17})
        self.assertEqual(state["bms_cell_count"], 17)
        self.assertEqual(state["cell_16_mv"], 3316)
        self.assertNotIn("cell_17_mv", state)

    def test_the_overflow_is_reported_once_not_per_payload(self):
        with mock.patch("src.siseli_bridge.parsers.log") as logged:
            for _ in range(3):
                SolarParser._try_ascii_schema({"v09K": captures.SYNTH_V09K_CELLS_17})
        overflow = [c for c in logged.call_args_list if c.args and "[CELLS]" in str(c.args[0])]
        self.assertEqual(len(overflow), 1)
        self.assertEqual(overflow[0].kwargs.get("level"), "warning")


if __name__ == "__main__":
    unittest.main()
