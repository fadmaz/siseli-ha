"""Every PI30 value this build publishes, against the vendor portal that paired with it.

The pairing is what makes these assertions worth anything: the 2026-09-02 capture was
taken with four portal screenshots from the same minute, and
`captures/2026-09-02_device-c-voltronic-pi30.md` records value by value what the portal
showed. A test that asserted the decoder agrees with itself would prove nothing.

Two properties are pinned here, and the second matters as much as the first:

1. **Each published key holds the value the portal showed.** A position read one field
   out, or a unit scaled wrongly, fails.
2. **The published key set is exactly this and no more.** Asserted as a whole set, never
   as a list of `assertNotIn`s for names someone invented: an absence assertion on
   `pi30_scc_battery_voltage_v` stays green forever if the field returns as
   `pi30_pv_battery_v`. The exclusions in `docs/PI30_DESIGN.md` §5 are evidence-based
   decisions, and adding a key without revisiting them has to fail.
"""

import unittest

from src.siseli_bridge import pi30
from tests import captures

#: What `decode` may produce from fragment 1, and nothing else. Every exclusion below is
#: a deliberate one; the reason is in `docs/PI30_DESIGN.md` §5 and summarised here.
#:
#: Not present, from QPIGS: field 15 (battery voltage from SCC) reads 00.00 in both
#: captured states with the SCC-charging bit off, so whether it is live is unknown;
#: field 17 b7 and b3, which PI30MAX reserves on Axpert models and which read 0;
#: field 18 (fan-on offset) and 19 (EEPROM version), constants of no use as sensors;
#: field 21 b8, reserved in PI30 2015 and "dustproof, V series only" in PI30MAX, reading
#: 1 in both captures; fields 22-24, reserved features in PI30MAX.
#:
#: Not present, from QFLAG: `d` (solar feed to grid), defined by PI30MAX alone as a
#: reserved feature, and whose portal row may come from QPIGS field 22 instead.
EXPECTED_FRAGMENT_1_KEYS = {
    # QPIGS live status
    "pi30_grid_v", "pi30_grid_hz", "pi30_ac_out_v", "pi30_ac_out_hz",
    "pi30_ac_out_va", "pi30_ac_out_w", "pi30_load_pct", "pi30_bus_v",
    "pi30_bat_v", "pi30_bat_charge_current", "pi30_bat_cap",
    "pi30_heatsink_temp_c", "pi30_pv1_current", "pi30_pv1_v",
    "pi30_bat_discharge_current", "pi30_pv1_charge_w",
    "pi30_status_config_changed", "pi30_status_scc_firmware_updated",
    "pi30_status_load_on", "pi30_status_charging", "pi30_status_scc_charging",
    "pi30_status_ac_charging", "pi30_status_charging_to_float",
    "pi30_status_switch_on",
    # QPIRI ratings and settings
    "pi30_rated_grid_v", "pi30_rated_grid_current", "pi30_rated_ac_out_v",
    "pi30_rated_ac_out_hz", "pi30_rated_ac_out_current", "pi30_rated_ac_out_va",
    "pi30_rated_ac_out_w", "pi30_rated_bat_v", "pi30_bat_recharge_v",
    "pi30_bat_under_v", "pi30_bat_bulk_v", "pi30_bat_float_v", "pi30_battery_type",
    "pi30_max_ac_charge_current", "pi30_max_charge_current", "pi30_input_v_range",
    "pi30_output_source_priority", "pi30_charger_source_priority",
    "pi30_parallel_max_count", "pi30_machine_type", "pi30_topology",
    "pi30_output_mode", "pi30_bat_redischarge_v", "pi30_pv_ok_condition",
    "pi30_pv_power_balance",
    # QMOD, QFLAG, QVFW
    "pi30_mode",
    "pi30_flag_buzzer", "pi30_flag_overload_bypass", "pi30_flag_power_saving",
    "pi30_flag_lcd_timeout", "pi30_flag_overload_restart",
    "pi30_flag_over_temp_restart", "pi30_flag_lcd_backlight",
    "pi30_flag_primary_source_alarm", "pi30_flag_fault_record",
    "pi30_firmware_version",
}

#: Fragment 2. Not present: QBEQI fields 5 and 7, reserved in PI30MAX §2.20; QBMS, whose
#: fields are zero placeholders while the BMS reports itself disconnected and therefore
#: describe no battery; QGMN, a model-family number where QMN already names the model;
#: QT, the clock, which is logged but is not an entity; QMCHGCR and QMUCHGCR, which are
#: lists of selectable values rather than settings.
EXPECTED_FRAGMENT_2_KEYS = {
    "pi30_eq_enabled", "pi30_eq_time_min", "pi30_eq_period_days",
    "pi30_eq_max_current", "pi30_eq_v", "pi30_eq_over_time_min",
    "pi30_eq_active", "pi30_eq_elapsed_hours",
    "pi30_pv_energy_total_kwh", "pi30_load_energy_total_kwh",
    "pi30_model",
}


class TestPublishedKeySet(unittest.TestCase):
    def test_fragment_1_publishes_exactly_these_keys(self):
        values = pi30.decode(captures.PI30_FRAGMENT_1).values
        self.assertEqual(set(values), EXPECTED_FRAGMENT_1_KEYS)

    def test_fragment_2_publishes_exactly_these_keys(self):
        values = pi30.decode(captures.PI30_FRAGMENT_2).values
        self.assertEqual(set(values), EXPECTED_FRAGMENT_2_KEYS)

    def test_the_field_table_and_the_captured_set_agree(self):
        """Every key the table can produce is produced by the reference capture.

        A key in the table that this device never emits is a key nothing has ever
        verified, and it would reach a user's dashboard reading Unknown forever.
        """
        produced = set(pi30.decode(captures.PI30_FRAGMENT_1).values)
        produced |= set(pi30.decode(captures.PI30_FRAGMENT_2).values)
        self.assertEqual(set(pi30.field_table_keys()), produced)

    def test_the_field_table_has_no_duplicate_keys(self):
        keys = pi30.field_table_keys()
        self.assertEqual(len(keys), len(set(keys)))

    def test_every_key_is_prefixed(self):
        """`pi30_` is what keeps the two registries disjoint and the switch's purge
        able to tell one device's keys from the other's."""
        for key in pi30.field_table_keys():
            with self.subTest(key=key):
                self.assertTrue(key.startswith("pi30_"))


class TestQpigsAgainstThePortal(unittest.TestCase):
    """Battery mode, discharging 6 A at 85 %, on 151 W of PV, grid present but unused."""

    @classmethod
    def setUpClass(cls):
        cls.values = pi30.decode(captures.PI30_FRAGMENT_1).values

    def test_the_paired_scalars(self):
        for key, want in (
            ("pi30_grid_v", 219.4),                 # portal 219.4 V
            ("pi30_grid_hz", 49.7),                 # portal 49.7 Hz
            ("pi30_ac_out_v", 230.0),               # portal 230 V
            ("pi30_ac_out_hz", 49.7),               # portal 49.7 Hz
            ("pi30_ac_out_va", 368),                # portal 368 VA
            ("pi30_ac_out_w", 258),                 # portal 0.258 kW
            ("pi30_load_pct", 9),                   # portal 9 %
            ("pi30_bus_v", 436),                    # portal 436 V
            ("pi30_bat_v", 27.6),                   # portal 27.6 V
            ("pi30_bat_charge_current", 0),         # portal 0 A
            ("pi30_bat_cap", 85),                   # portal 85 %
            ("pi30_pv1_current", 1.2),              # portal 1.2 A
            ("pi30_pv1_v", 184.1),                  # portal 184.1 V
            ("pi30_bat_discharge_current", 6),      # portal 6 A
        ):
            with self.subTest(key=key):
                self.assertEqual(self.values[key], want)

    def test_pv_charging_power_is_the_devices_own_figure(self):
        """151 W, not 221 W.

        184.1 V x 1.2 A is 221 W, and both the wire and the portal say 151. Publish
        what the device states, never a value derived from a different quantity -- the
        rule `tests/test_truthfulness.py` exists to hold for Device A.
        """
        self.assertEqual(self.values["pi30_pv1_charge_w"], 151)

    def test_the_status_bits_read_most_significant_first(self):
        """Field 17 is `00010000` and the portal shows exactly one thing on: the load.

        That single `1` sits at b4, which is what pins the bit order. Read the other
        way round it would land on b3, a bit PI30MAX reserves.
        """
        self.assertIs(self.values["pi30_status_load_on"], True)
        for key in (
            "pi30_status_config_changed",
            "pi30_status_scc_firmware_updated",
            "pi30_status_charging",
            "pi30_status_scc_charging",
            "pi30_status_ac_charging",
        ):
            with self.subTest(key=key):
                self.assertIs(self.values[key], False)

    def test_the_float_bits_split_b10_b9(self):
        """Field 21 is `011`: b10 b9 b8. The portal pairs b9 as "Switch On: yes"."""
        self.assertIs(self.values["pi30_status_switch_on"], True)
        self.assertIs(self.values["pi30_status_charging_to_float"], False)


class TestHeatsinkTemperatureIsGatedOnTheRating(unittest.TestCase):
    """All three specifications call QPIGS field 12 a raw NTC reading on 1-3 kVA models.

    The portal shows 49 °C, but only for this 4000 VA unit -- that is the only evidence
    the field is degrees at all. So the key is published only when the same payload's
    QPIRI proves the machine is bigger than 3 kVA, and the gate can only ever withhold.
    """

    def test_it_is_published_for_the_4000_va_unit(self):
        values = pi30.decode(captures.PI30_FRAGMENT_1).values
        self.assertEqual(values["pi30_heatsink_temp_c"], 49)

    def test_it_is_absent_when_the_rating_is_3000_va(self):
        blocks = dict(captures.PI30_FRAGMENT_1)
        blocks["MrfS"] = captures.SYNTH_PI30_MRFS_3000VA
        values = pi30.decode(blocks).values
        self.assertNotIn("pi30_heatsink_temp_c", values)
        # Everything else still decodes, so this is the gate and not a broken payload.
        self.assertEqual(values["pi30_rated_ac_out_va"], 3000)
        self.assertEqual(values["pi30_grid_v"], 219.4)

    def test_it_is_absent_when_the_payload_carries_no_qpiri(self):
        blocks = dict(captures.PI30_FRAGMENT_1)
        del blocks["MrfS"]
        values = pi30.decode(blocks).values
        self.assertNotIn("pi30_heatsink_temp_c", values)
        self.assertIn("pi30_grid_v", values)


class TestQpiriAgainstThePortal(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.values = pi30.decode(captures.PI30_FRAGMENT_1).values

    def test_the_eight_fields_whose_value_is_unique_in_the_reply(self):
        """Only these eight are pinned by the portal by value.

        The other seventeen share their value with another field (1/3, 2/5, 6/7, 8/10,
        13/17/18, 16/19/25, 21/22/24), so the portal agrees with them but cannot tell a
        swap within those groups. Their positions rest on the specifications, which all
        define the order identically.
        """
        for key, want in (
            ("pi30_rated_ac_out_hz", 50.0),        # field 4
            ("pi30_bat_recharge_v", 25.0),         # field 9
            ("pi30_bat_bulk_v", 28.8),             # field 11
            ("pi30_bat_float_v", 27.6),            # field 12
            ("pi30_max_ac_charge_current", 60),    # field 14
            ("pi30_max_charge_current", 30),       # field 15
            ("pi30_machine_type", "10"),           # field 20
            ("pi30_bat_redischarge_v", 27.0),      # field 23
        ):
            with self.subTest(key=key):
                self.assertEqual(self.values[key], want)

    def test_the_enums_the_portal_confirms(self):
        self.assertEqual(self.values["pi30_battery_type"], "User")
        self.assertEqual(self.values["pi30_input_v_range"], "UPS")
        self.assertEqual(
            self.values["pi30_output_source_priority"], "Solar, battery, utility"
        )
        self.assertEqual(self.values["pi30_charger_source_priority"], "Solar and utility")
        self.assertEqual(self.values["pi30_topology"], "Transformerless")
        self.assertEqual(self.values["pi30_output_mode"], "Single machine")
        self.assertEqual(
            self.values["pi30_pv_ok_condition"], "One inverter connected to PV"
        )
        self.assertEqual(self.values["pi30_pv_power_balance"], "Max power")

    def test_max_ac_charging_current_has_three_digits_where_the_spec_shows_two(self):
        """Field 14 reads `060`, and the portal is what settles 14 against 15.

        A decoder that trusted the documented width here would read every field after
        it one position out.
        """
        self.assertEqual(self.values["pi30_max_ac_charge_current"], 60)
        self.assertEqual(self.values["pi30_max_charge_current"], 30)


class TestQflag(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.values = pi30.decode(captures.PI30_FRAGMENT_1).values

    def test_the_reply_splits_on_the_uppercase_separators_only(self):
        """`EakxyzDbdjuv`: `d` (0x64) is itself a flag letter in this reply.

        Splitting on a case-insensitive `d` puts `d`, `j`, `u` and `v` in neither list
        and silences four entities.
        """
        for key in (
            "pi30_flag_buzzer", "pi30_flag_lcd_timeout", "pi30_flag_lcd_backlight",
            "pi30_flag_primary_source_alarm", "pi30_flag_fault_record",
        ):
            with self.subTest(key=key):
                self.assertEqual(self.values[key], "enabled")
        for key in (
            "pi30_flag_overload_bypass", "pi30_flag_power_saving",
            "pi30_flag_overload_restart", "pi30_flag_over_temp_restart",
        ):
            with self.subTest(key=key):
                self.assertEqual(self.values[key], "disabled")

    def test_the_buzzer_flag_is_named_for_its_state_not_its_wording(self):
        """The specification says "silence buzzer or open buzzer", which leaves the
        direction open; mpp-solar reads E as the buzzer sounding. The portal pairs the
        state, never the letter, so this is the weakest of the ten and is recorded as
        such in `docs/PI30_DESIGN.md` §12."""
        self.assertEqual(self.values["pi30_flag_buzzer"], "enabled")


class TestQbeqiAgainstThePortal(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.values = pi30.decode(captures.PI30_FRAGMENT_2).values

    def test_the_three_fields_pinned_by_value(self):
        """`060`, `27.60` and `120` each occur once in the reply.

        Fields 3, 4 and 5 all read `030`, 1 and 9 both read `0`, and 7 and 10 are both
        zero, so the portal agrees with them without being able to tell them apart.
        """
        self.assertEqual(self.values["pi30_eq_time_min"], 60)
        self.assertEqual(self.values["pi30_eq_v"], 27.6)
        self.assertEqual(self.values["pi30_eq_over_time_min"], 120)

    def test_the_spec_ordered_fields(self):
        self.assertEqual(self.values["pi30_eq_enabled"], "disabled")
        self.assertEqual(self.values["pi30_eq_period_days"], 30)
        self.assertEqual(self.values["pi30_eq_max_current"], 30)
        self.assertEqual(self.values["pi30_eq_active"], "disabled")
        self.assertEqual(self.values["pi30_eq_elapsed_hours"], 0)


class TestLifetimeEnergy(unittest.TestCase):
    def test_the_totals_match_the_portal(self):
        """253.8 and 114.1 kWh, from `00253800` and `00114100` Wh.

        This single pairing is what establishes the Wh unit: QET's unit is confirmed by
        the portal's 253.8 kWh, and QLT's comes from PI30MAX alone.
        """
        values = pi30.decode(captures.PI30_FRAGMENT_2).values
        self.assertEqual(values["pi30_pv_energy_total_kwh"], 253.8)
        self.assertEqual(values["pi30_load_energy_total_kwh"], 114.1)

    def test_the_conversion_is_true_division(self):
        """Not integer division, which gives 253.0 and discards the pairing.

        Tested on the function rather than on a frame: every captured sample ends in
        `00`, which the capture note calls a suggestion of 100 Wh resolution rather
        than a fact, so no fixture can tell exact division from a rounded one.
        """
        self.assertEqual(pi30._wh_to_kwh("00253800"), 253.8)
        self.assertEqual(pi30._wh_to_kwh("00253812"), 253.812)
        self.assertEqual(pi30._wh_to_kwh("00000001"), 0.001)


class TestIdentity(unittest.TestCase):
    def test_the_model_and_firmware(self):
        values = dict(pi30.decode(captures.PI30_FRAGMENT_1).values)
        values.update(pi30.decode(captures.PI30_FRAGMENT_2).values)
        self.assertEqual(values["pi30_model"], "VMIII-4000")
        self.assertEqual(values["pi30_firmware_version"], "00060.10")

    def test_the_inverter_clock_is_context_and_never_a_value(self):
        """QT pairs with the portal's "System Time" to the second, which is what makes
        a log line and a screenshot comparable -- but it is not an entity."""
        result = pi30.decode(captures.PI30_FRAGMENT_2)
        self.assertEqual(result.context["inverter_clock"], "2026-09-02 17:05:54")
        self.assertNotIn("pi30_clock", result.values)


class TestDecodeNeverRaises(unittest.TestCase):
    """`parse_payload` wraps its whole body in one `except`, so a raise here would cost
    the payload all 24 of its blocks, not just the bad one."""

    def test_a_truncated_qpigs_loses_only_its_own_missing_fields(self):
        import binascii

        short = b"(219.4 49.7 230.0"
        blocks = dict(captures.PI30_FRAGMENT_1)
        blocks["G4WT"] = short + binascii.crc_hqx(short, 0).to_bytes(2, "big") + b"\r"
        values = pi30.decode(blocks).values
        # The shape check rejects a three-token QPIGS outright, so no partial live
        # status is published -- but QPIRI in the same payload still decodes.
        self.assertNotIn("pi30_grid_v", values)
        self.assertEqual(values["pi30_rated_ac_out_va"], 4000)

    def test_a_non_numeric_token_omits_its_own_key_only(self):
        import binascii

        tokens = pi30.tokenise(pi30.verify_frame(captures.BLOCK_PI30_G4WT_QPIGS_BATTERY))
        tokens[7] = "----"                       # bus voltage, unreadable
        body = b"(" + " ".join(tokens).encode("ascii")
        blocks = dict(captures.PI30_FRAGMENT_1)
        blocks["G4WT"] = body + binascii.crc_hqx(body, 0).to_bytes(2, "big") + b"\r"
        values = pi30.decode(blocks).values
        self.assertNotIn("pi30_bus_v", values)
        self.assertEqual(values["pi30_grid_v"], 219.4)
        self.assertEqual(values["pi30_bat_cap"], 85)

    def test_rubbish_input_decodes_to_nothing(self):
        for blocks in (None, {}, {"G4WT": b""}, {"G4WT": None}):
            with self.subTest(blocks=blocks):
                result = pi30.decode(blocks)
                self.assertEqual(result.values, {})


if __name__ == "__main__":
    unittest.main()
