"""Real inverter block data captured from the wire. DATA ONLY -- no imports, no logic.

Naming contract, enforced by review:

  BLOCK_*   Verbatim bytes from a real device. Reconstructed from the ``hex_preview``
            field of a ``[BLOCK RAW]`` debug line, so the framing is exact: a leading
            ``(`` and a trailing ``\\r``, with no closing paren. Golden value
            assertions MAY use these.
  SYNTH_*   Hand-constructed. For structural and edge-case tests ONLY -- never for
            value-parity assertions, because a hand-built block proves nothing about
            what a device actually emits.

To add a capture: ask the reporter for ``LOG_LEVEL: debug`` output, take the
``hex_preview`` from the ``[BLOCK RAW]`` line, decode it, and paste it verbatim under
a name that records the block key and the device state it was taken in. Record the
model and firmware in a comment.
"""

# ---------------------------------------------------------------------------
# Device A -- HPVINV04, firmware 0010.11, 2x parallel, 2x battery (32-cell bank).
# Captured 2026-08-20 while charging in battery mode with PV on string 2.
# This device's payloads arrive as TWO disjoint block sets, which is why the
# energy calculation must gate per payload rather than assume every field is
# present every time.
# ---------------------------------------------------------------------------

# --- payload 1: telemetry (11 blocks) --------------------------------------
BLOCK_2ONL_CHARGING = b"(04 053.4 058 007 00000 420 110007200000 00000000\r"
BLOCK_2L0E_LOADED = b"(228.5 49.9 00868 00766 007 018 11000 008.7 01175\r"
BLOCK_WDRR_NO_GRID_FLOW = b"(232.7 49.9 280 170 65 40 +00000 0 11000 11+00000\r"
BLOCK_YAVB_CHARGING = b"(04 1001100000000000 042.0 057.6 195.0 058 0029.1 0000.0 03041 000000\r"
BLOCK_93VQ_SETTINGS = b"(1 050 010 13310110230 011 1 1 0 1 1 015 035 050 025 056.4 056.4 042.0 020 0 0\r"
BLOCK_DHRK_SETTINGS = b"(1 044.0 020 044.0 046.0 054.0 0 058.4 060 120 030 0000 0000 05 0000 52.0 50000\r"
BLOCK_EO8W_STATUS = b"(00 B010000000000 20211002120B117020000\r"
BLOCK_MPOD_PV1_IDLE = b"(000.0 00.0 00000 00000.0 00000 0 380.0 018 12000\r"
BLOCK_NOEP_PV2_ACTIVE = b"(132.0 10.6 01403 2 132.5 00000000000000000000000\r"
BLOCK_V4W3_TEMPS = b"(058 048 038 059 059 050 050 11 048 051 000000000\r"
BLOCK_COST_ENERGY = b"(260720 10:04 02.939 0315.7 2081.0 000002284.6 000000000000\r"

# --- payload 2: identity + BMS detail (4 blocks, zero overlap with payload 1)
BLOCK_SUCV_MODEL = b"(HPVINV04\r"
BLOCK_HR6Y_FIRMWARE = b"(0010.11 20250630 14\r"
BLOCK_UXJP_BMS_CAPACITY = b"(0173.0 0299.8 2 3327 0009 3320 0032 0000000000000000000000\r"
BLOCK_V09K_CELLS_16 = (
    b"(3321 3321 3323 3322 3323 3322 3323 3321 3328 3326 3323 3323 3322 3325 3324 3321 00000000\r"
)

CAPTURE_TELEMETRY = {
    "2ONL": BLOCK_2ONL_CHARGING,
    "2l0E": BLOCK_2L0E_LOADED,
    "WdRR": BLOCK_WDRR_NO_GRID_FLOW,
    "Yavb": BLOCK_YAVB_CHARGING,
    "93VQ": BLOCK_93VQ_SETTINGS,
    "dHrK": BLOCK_DHRK_SETTINGS,
    "eo8w": BLOCK_EO8W_STATUS,
    "Mpod": BLOCK_MPOD_PV1_IDLE,
    "noeP": BLOCK_NOEP_PV2_ACTIVE,
    "V4W3": BLOCK_V4W3_TEMPS,
    "COST": BLOCK_COST_ENERGY,
}

CAPTURE_IDENTITY = {
    "SUCV": BLOCK_SUCV_MODEL,
    "hR6Y": BLOCK_HR6Y_FIRMWARE,
    "uxJp": BLOCK_UXJP_BMS_CAPACITY,
    "v09K": BLOCK_V09K_CELLS_16,
}

# Golden expected state for CAPTURE_TELEMETRY, taken from the add-on's own
# "Published to HA" line for that exact payload. Config-INDEPENDENT values only --
# anything scaled by INVERTER_COUNT lives in EXPECTED_TELEMETRY_SCALED below.
EXPECTED_TELEMETRY = {
    "bat_v": 53.4,
    "second_output_battery_capacity": 50,
    "bat_cap": 58,
    "bat_charge_current": 7.0,
    "dischg_current": 0.0,
    "bus_voltage": 420.0,
    "bms_current_soc": 58,
    "bms_charging_current_a": 29.1,
    "bms_discharge_current_a": 0.0,
    "grid_v": 232.7,
    "grid_hz": 49.9,
    "mains_power_w": 0,
    "mains_apparent_va": 0,
    "out_v": 228.5,
    "out_hz": 49.9,
    "apparent_va": 868,
    "load_w": 766,
    "load_pct": 7,
    "output_dc_comp": 18,
    "inductor_current_a": 8.7,
    "generation_power_w": 1403,
    "pv_v": 0.0,
    "pv_current_a": 0.0,
    "pv_w": 0,
    "pv2_v": 132.0,
    "pv2_current_a": 10.6,
    "pv2_power_w": 1403,
    "pv_today_kwh": 2.939,
    "pv_month_kwh": 315.7,
    "pv_year_kwh": 2081.0,
    "pv_total_kwh": 2284.6,
    "pv_temp": 58.0,
    "pv2_temp": 48.0,
    "inverter_temperature_c": 48.0,
    "transformer_temperature_c": 59.0,
    "max_temperature_c": 59.0,
    "dc_rectification_temperature_c": 51.0,
    "fan_1_speed": 50,
    "fan_2_speed": 50,
}

# Requires INVERTER_COUNT = 2, which is this device's configuration.
EXPECTED_TELEMETRY_SCALED = {
    "c_load_w": 1532,
    "c_generation_power_w": 2806,
    "c_mains_power_w": 0,
}

EXPECTED_IDENTITY = {
    "model_code": "HPVINV04",
    "firmware_version": "0010.11",
    "software_version": "10.11",
    "firmware_build_date": "2025-06-30",
    "firmware_build_slot": "14",
    "bms_remaining_ah": 173.0,
    "bms_nominal_ah": 299.8,
    "bms_cell_count": 16,
    "cell_1_mv": 3321,
    "cell_9_mv": 3328,
    "cell_16_mv": 3321,
    # From uxJp, the BMS's own whole-bank summary. Note min is at position 32 on a
    # bank whose v09K block only carries 16 cells -- cells 17-32 have no entity.
    "bms_max_cell_mv": 3327,
    "bms_max_cell_pos": 9,
    "bms_min_cell_mv": 3320,
    "bms_min_cell_pos": 32,
    "bms_cell_delta_mv": 7,
}

# ---------------------------------------------------------------------------
# Device B -- the reference unit documented in sensor_mapping.md. These are the
# literals the original test suite used; kept so those assertions stay anchored to
# a second, independently captured device.
# ---------------------------------------------------------------------------

BLOCK_HR6Y_FIRMWARE_REF = b"(0010.11 20250630 14)"
BLOCK_2L0E_REF = b"(229.8 49.9 252 129 2 24 11000 006.1 0044)"
BLOCK_93VQ_REF = b"(1 050 010 13310110230 011 1 1 0 1 1 015 035 050 025 056.4 056.4 042.0 020 0 0)"
BLOCK_EO8W_REF = b"(00 B0100000000000 20211002110B117020000)"
BLOCK_YAVB_REF = b"(04 1001100000000000 042.0 057.6 195.0 054 0022.3 0000.0 02921 000000 18.95)"
BLOCK_YAVB_REF_NO_TAIL = b"(04 1001100000000000 042.0 057.6 195.0 054 0022.3 0000.0 02921 000000)"

# ---------------------------------------------------------------------------
# Device B -- Beve Mega 6kW L1PE-ECO. NOT a supported device, and not decodable by
# this add-on. Reported in issue #30 with add-on 2.6.16, every sensor Unknown.
#
# The DTU transport is identical -- same topic, same base64 block envelope -- but
# the inverter's blocks are binary Modbus RTU rather than Device A's ASCII token
# strings, and not one block name overlaps. These exist so the "this is a foreign
# protocol" diagnostic is tested against a real foreign device instead of a
# hand-built stand-in; nothing here is a decode target.
#
# Verbatim from the reporter's `hex_preview` lines, so BLOCK_*, per the naming
# contract above. Frames are addr, function, byte count, data, CRC16 little-endian:
# every one below verifies, except CLNi, which is length-complete but carries the
# vendor function code 0x21 and a byte-swapped CRC.
#
# r8BV and Sgx0's siblings are deliberately absent: their hex_preview was cut off
# by the debug logger itself (r8BV declares 146 data bytes and only 61 survived),
# so they are not verbatim and must not masquerade as BLOCK_* data.
# ---------------------------------------------------------------------------

DEVB_BLOCK_ESQL = bytes.fromhex("050302f9000bd4")
DEVB_BLOCK_FDFM = bytes.fromhex("0503100100030003000000000000000a000100d1b6")
DEVB_BLOCK_JL4X = bytes.fromhex("05030600002a02eeffb78d")
DEVB_BLOCK_PS4Z = bytes.fromhex(
    "05032804007508f701000000001c026400000000007508f701d7031d031000"
    "11000100f401040000000200c8e5"
)
DEVB_BLOCK_SGX0 = bytes.fromhex(
    "05033a010001000100030000007800e6002800cc011c0234021c02a4014402"
    "1e0000001e0066150100030003000000000000000a0001000000730726013435"
)
DEVB_BLOCK_ZMNP = bytes.fromhex("050382e150")
DEVB_BLOCK_AKUG = bytes.fromhex("0503167d0726010500000014013e0158010000000000000000febe")
DEVB_BLOCK_HIG6 = bytes.fromhex("05031070177017e6001a00e001e600f4011a00e8b9")
DEVB_BLOCK_SEO5 = bytes.fromhex("0503026615a3eb")
DEVB_BLOCK_XVQ9 = bytes.fromhex("05030200004984")
DEVB_BLOCK_CLNI = bytes.fromhex("05210a0064000a0014005000288452")

#: The one block in Device A's ASCII framing -- evidence the DTU wrapper is shared
#: and only the inverter payload differs.
DEVB_BLOCK_ARV4 = bytes.fromhex("2841434b39200d")

CAPTURE_DEVICE_B_FOREIGN = {
    "CLNi": DEVB_BLOCK_CLNI,
    "EsQL": DEVB_BLOCK_ESQL,
    "FDFm": DEVB_BLOCK_FDFM,
    "Jl4X": DEVB_BLOCK_JL4X,
    "PS4Z": DEVB_BLOCK_PS4Z,
    "Sgx0": DEVB_BLOCK_SGX0,
    "ZMnp": DEVB_BLOCK_ZMNP,
    "aKuG": DEVB_BLOCK_AKUG,
    "aRv4": DEVB_BLOCK_ARV4,
    "hIg6": DEVB_BLOCK_HIG6,
    "seO5": DEVB_BLOCK_SEO5,
    "xvq9": DEVB_BLOCK_XVQ9,
}


# ---------------------------------------------------------------------------
# Device C -- Falcon VMIII 4200W, firmware V1.44.5_SolarV70. NOT supported.
# Reported in issue #32 against 2.6.17, every sensor Unknown.
#
# A third protocol family, and the reason the diagnostic had to stop treating
# "not ASCII" as one bit: these bodies ARE ASCII, with a two-byte CRC16-XMODEM
# appended, and 2.6.17 called them "binary" -- the word DOCS.md named as the
# strongest signal of a different protocol family.
#
# Every body below verifies as CRC16-XMODEM (poly 0x1021, init 0x0000, big-endian)
# computed over the frame INCLUDING the leading "(" and excluding the trailing CR.
# 22 of 22 non-truncated blocks from the report verified; these are a representative
# subset. `(PI30` is the protocol identifying itself -- Voltronic/Axpert PI30.
#
# Note what the CRC bytes did to the debug output that carried them: the bridge
# logged the firmware as "VERFW:00025.129" and the serial with two stray letters
# appended, because the trailing CRC bytes happen to be printable ASCII. The real
# firmware value is "VERFW:00025.12". captures/2026-09-02_device-c-voltronic-pi30.md
# records the complete re-capture from the same device.
#
# The two telemetry-bearing blocks, G4WT and MrfS, are absent: hex_preview capped
# them at 64 bytes, so they are not verbatim. That cap is removed in 2.6.18.
# ---------------------------------------------------------------------------

DEVC_BLOCK_CCFT_PROTOCOL = bytes.fromhex("28504933309a0b0d")          # (PI30
DEVC_BLOCK_EMU5_MODEL = bytes.fromhex("28564d4949492d34303030de930d")  # (VMIII-4000
DEVC_BLOCK_AG5G_FIRMWARE = bytes.fromhex("2856455246573a30303032352e3132ab390d")
DEVC_BLOCK_O2LC_FIRMWARE2 = bytes.fromhex("2856455246573a30303036302e3130be380d")
#: Hand-built stand-in for the reporter's QID reply, which carried their inverter's
#: serial number. Same length and shape; the CRC is recomputed (binascii.crc_hqx over
#: "(" + body is CRC16-XMODEM) and was chosen to stay printable, as the real one was, so
#: the diagnostic classifies this block exactly as it classified the original.
SYNTH_DEVC_AHLB_SERIAL_STANDIN = bytes.fromhex("283030303030303030303030303038422a0d")
DEVC_BLOCK_EZGH_NAK = bytes.fromhex("284e414b73730d")                  # (NAK
DEVC_BLOCK_ZZ3K_SHORT = bytes.fromhex("284c06070d")                    # (L
DEVC_BLOCK_U51Q_FLAG = bytes.fromhex("2831a93d0d")                     # (1
DEVC_BLOCK_9GBT_SCALAR = bytes.fromhex("28303535ebbe0d")               # (055
DEVC_BLOCK_CT7S_COUNTER = bytes.fromhex("28303032353137303033890d")
DEVC_BLOCK_LCMP_CLOCK = bytes.fromhex("28323032363038333130343435333426c50d")
DEVC_BLOCK_DB48_RAMP = bytes.fromhex(
    "2830313020303230203033302030343020303530203036302030373020303830"
    "203039302031303020313130203132300ccb0d"
)
DEVC_BLOCK_7V9T_SETTINGS = bytes.fromhex(
    "2830203036302030333020303330203033302032392e32302030303020313230"
    "2030203030303056580d"
)
DEVC_BLOCK_UEFO_FLAGS = bytes.fromhex(
    "28312030303020302030203020303030203030302030303020303030302030303030"
    "80860d"
)

CAPTURE_DEVICE_C_VOLTRONIC = {
    "9gbt": DEVC_BLOCK_9GBT_SCALAR,
    "7v9T": DEVC_BLOCK_7V9T_SETTINGS,
    "DB48": DEVC_BLOCK_DB48_RAMP,
    "EMu5": DEVC_BLOCK_EMU5_MODEL,
    "Ezgh": DEVC_BLOCK_EZGH_NAK,
    "UefO": DEVC_BLOCK_UEFO_FLAGS,
    "ag5g": DEVC_BLOCK_AG5G_FIRMWARE,
    "ahLb": SYNTH_DEVC_AHLB_SERIAL_STANDIN,
    "cCft": DEVC_BLOCK_CCFT_PROTOCOL,
    "cT7S": DEVC_BLOCK_CT7S_COUNTER,
    "lCMp": DEVC_BLOCK_LCMP_CLOCK,
    "o2lC": DEVC_BLOCK_O2LC_FIRMWARE2,
    "u51Q": DEVC_BLOCK_U51Q_FLAG,
    "zZ3K": DEVC_BLOCK_ZZ3K_SHORT,
}


# ---------------------------------------------------------------------------
# Synthetic blocks -- structural tests only. Never assert app parity on these.
# ---------------------------------------------------------------------------

SYNTH_UNKNOWN_BLOCK = b"(1 2 3)"
SYNTH_V09K_CELL_3_COLLAPSED = b"(3321 3321 1900 3322 3323 00000000)"
SYNTH_2L0E_OVERLOADED = b"(228.5 49.9 00868 00766 115 018 11000 008.7 01175\r"
SYNTH_2ONL_IDLE = b"(04 053.4 058 000 00000 420 110007200000 00000000\r"
SYNTH_YAVB_ABSURD_CURRENT = b"(04 1001100000000000 042.0 057.6 195.0 058 9999.0 0000.0 03041 000000\r"

#: Hand-built. The signed power token is far wider than the five digits a real
#: device emits, which is what the plausibility bound exists to reject: this value
#: would otherwise be integrated into a counter that can never come back down.
SYNTH_WDRR_ABSURD_POWER = b"(232.7 49.9 280 170 65 40 +999999999 0 11000 11+00000\r"

# ---------------------------------------------------------------------------
# NOT YET CAPTURED
# The parser recognises 15 block keys and Device A above covers every one of them,
# but only in Device A's *configuration*. Captures still wanted: a 120 V unit (the
# 93VQ config decode no-ops unless the packed word ends "230"), an inverter reporting
# a real BMS fault word (every capture so far reads the all-clear 1001100000000000),
# a single-inverter non-parallel install, and a second output capacity set to a single
# digit (see the dHrK[16] note in parsers.py).
# ---------------------------------------------------------------------------

#: Hand-built. Seventeen in-range cell voltages, one more than the 16 cell
#: entities, to exercise the overflow path no real capture reaches.
SYNTH_V09K_CELLS_17 = b"(3301 3302 3303 3304 3305 3306 3307 3308 3309 3310 3311 3312 3313 3314 3315 3316 3317 00000000)"


# ---------------------------------------------------------------------------
# Device C -- Falcon VMIII-4000, Voltronic PI30, firmware 00060.10.
# The 2026-09-02 `dev_prop_post` set, verbatim from the appendix of
# captures/2026-09-02_device-c-voltronic-pi30.md, captured together with four
# vendor-portal screenshots taken in the same minute. Every frame CRC-verifies
# (CRC16-XMODEM over "(" .. pre-CRC, big-endian), which is what makes these usable
# for value-parity assertions where the earlier DEVC_BLOCK_* set is not: that one
# came from the first report, whose two telemetry blocks hex_preview cut at 64 bytes.
#
# The set arrives as two MQTT messages with the same b.ts -- 14 blocks then 10, the
# same split every time -- so the fixtures are per fragment. Nine of these bodies are
# byte-identical to their DEVC_BLOCK_* counterparts and are still written out in full:
# this is the portal-paired evidence, and it should read as one verbatim record.
# ---------------------------------------------------------------------------

# --- fragment 1 of 2 (14 blocks) -------------------------------------------
BLOCK_PI30_CCFT_PROTOCOL = bytes.fromhex("28504933309a0b0d")            # (PI30
BLOCK_PI30_O2LC_FIRMWARE = bytes.fromhex("2856455246573a30303036302e3130be380d")
BLOCK_PI30_AG5G_FIRMWARE3 = bytes.fromhex("2856455246573a30303032352e3132ab390d")
BLOCK_PI30_ZZ3K_MODE_BATTERY = bytes.fromhex("2842e7c90d")              # (B
BLOCK_PI30_EZGH_NAK = bytes.fromhex("284e414b73730d")                   # (NAK
#: QPIGS. Battery mode, discharging 6 A at 85 %, on 151 W of PV, grid present at
#: 219.4 V but unused under SBU priority. Field 17 reads 00010000: the load is on and
#: nothing else. Paired field by field with the portal in the capture note.
BLOCK_PI30_G4WT_QPIGS_BATTERY = bytes.fromhex(
    "283231392e342034392e37203233302e302034392e372030333638203032353820"
    "303039203433362032372e3630203030302030383520303034392030312e322031"
    "38342e312030302e30302030303030362030303031303030302030302030302030"
    "303135312030313120302030312030303534e77e0d"
)
#: QPIRI, all 25 fields. A 4000 VA / 4000 W unit on a 24 V bank -- the rating that
#: makes field 12 of QPIGS a temperature rather than a raw NTC reading.
BLOCK_PI30_MRFS_QPIRI = bytes.fromhex(
    "283233302e302031372e33203233302e302035302e302031372e33203430303020"
    "343030302032342e302032352e302032342e302032382e382032372e3620322030"
    "3630203033302031203220322031203130203020302032372e3020302031915a0d"
)
#: QFLAG. `E` opens the enabled list, `D` the disabled one, and `d` is itself a flag
#: letter -- which is why the split is on the uppercase separators only.
BLOCK_PI30_SJQT_QFLAG = bytes.fromhex("2845616b78797a4462646a757678320d")
BLOCK_PI30_DB48_CHARGE_CURRENTS = bytes.fromhex(
    "2830313020303230203033302030343020303530203036302030373020303830"
    "203039302031303020313130203132300ccb0d"
)
BLOCK_PI30_WB83_UTILITY_CURRENTS = bytes.fromhex(
    "28303032203031302030323020303330203034302030353020303630203037302030"
    "383020303930203130305c460d"
)
#: QOPPT and QCHPT answer with byte-identical hourly arrays in this capture, which is
#: why shape alone cannot tell them apart and the decoder needs the name map.
BLOCK_PI30_OTLG_OUTPUT_SCHEDULE = bytes.fromhex(
    "2832203220322032203220322032203220322032203220322032203220322032"
    "203220322032203220322032203220322032203020302030407f0d"
)
BLOCK_PI30_48RR_CHARGER_SCHEDULE = bytes.fromhex(
    "2832203220322032203220322032203220322032203220322032203220322032"
    "203220322032203220322032203220322032203020302030407f0d"
)
#: Hand-built stand-ins for the two serial-bearing replies, which the capture note
#: withholds. Same shape, owner's digits replaced, CRC recomputed -- so fragment 1 is
#: the full 14-block payload the device actually sends. They carry no published value:
#: QID and QSID are decoded by nothing, so no assertion rests on them.
SYNTH_PI30_AHLB_SERIAL = bytes.fromhex("283030303030303030303030303038422a0d")
SYNTH_PI30_G5E9_SERIAL = bytes.fromhex("28303030303030303030303030303030303030303844070d")

# --- fragment 2 of 2 (10 blocks) -------------------------------------------
BLOCK_PI30_LCMP_CLOCK = bytes.fromhex("28323032363039303231373035353416340d")
#: QBEQI. Field 6 read 29.20 in the 2026-08-31 capture and 27.60 here, so it is a
#: live setting; the float voltage moved from 28.8 to 27.6 over the same interval.
BLOCK_PI30_7V9T_QBEQI = bytes.fromhex(
    "2830203036302030333020303330203033302032372e363020303030203132302030"
    "203030303061ee0d"
)
BLOCK_PI30_EMU5_MODEL = bytes.fromhex("28564d4949492d34303030de930d")   # (VMIII-4000
BLOCK_PI30_9GBT_GENERAL_MODEL = bytes.fromhex("28303535ebbe0d")         # (055
#: QET, lifetime PV energy: 00253800 Wh, the portal's 253.8 kWh. That pairing is the
#: only thing establishing the Wh unit, which is why the conversion is true division.
BLOCK_PI30_CT7S_QET = bytes.fromhex("283030323533383030f2d00d")
#: QLT, lifetime load energy: 00114100 Wh, the portal's 114.1.
BLOCK_PI30_MA9W_QLT = bytes.fromhex("2830303131343130307a8a0d")
#: QBMS, reporting itself disconnected. Its ten zero placeholders describe no battery,
#: and it answers with exactly ten tokens -- the same count as QBEQI.
BLOCK_PI30_UEFO_QBMS = bytes.fromhex(
    "2831203030302030203020302030303020303030203030302030303030203030303080860d"
)
BLOCK_PI30_U51Q_UNRESOLVED = bytes.fromhex("2831a93d0d")                # (1
BLOCK_PI30_TWFA_NAK = bytes.fromhex("284e414b73730d")                   # (NAK
BLOCK_PI30_W7EX_NAK = bytes.fromhex("284e414b73730d")                   # (NAK

PI30_FRAGMENT_1 = {
    "48rR": BLOCK_PI30_48RR_CHARGER_SCHEDULE,
    "DB48": BLOCK_PI30_DB48_CHARGE_CURRENTS,
    "Ezgh": BLOCK_PI30_EZGH_NAK,
    "G4WT": BLOCK_PI30_G4WT_QPIGS_BATTERY,
    "G5E9": SYNTH_PI30_G5E9_SERIAL,
    "MrfS": BLOCK_PI30_MRFS_QPIRI,
    "ag5g": BLOCK_PI30_AG5G_FIRMWARE3,
    "ahLb": SYNTH_PI30_AHLB_SERIAL,
    "cCft": BLOCK_PI30_CCFT_PROTOCOL,
    "o2lC": BLOCK_PI30_O2LC_FIRMWARE,
    "oTLG": BLOCK_PI30_OTLG_OUTPUT_SCHEDULE,
    "sJqt": BLOCK_PI30_SJQT_QFLAG,
    "wb83": BLOCK_PI30_WB83_UTILITY_CURRENTS,
    "zZ3K": BLOCK_PI30_ZZ3K_MODE_BATTERY,
}

PI30_FRAGMENT_2 = {
    "7v9T": BLOCK_PI30_7V9T_QBEQI,
    "9gbt": BLOCK_PI30_9GBT_GENERAL_MODEL,
    "EMu5": BLOCK_PI30_EMU5_MODEL,
    "TWfA": BLOCK_PI30_TWFA_NAK,
    "UefO": BLOCK_PI30_UEFO_QBMS,
    "W7EX": BLOCK_PI30_W7EX_NAK,
    "cT7S": BLOCK_PI30_CT7S_QET,
    "lCMp": BLOCK_PI30_LCMP_CLOCK,
    "mA9W": BLOCK_PI30_MA9W_QLT,
    "u51Q": BLOCK_PI30_U51Q_UNRESOLVED,
}

#: The three NAK replies alone. They verify, and they are byte-identical, so without
#: the unattributed-name list three content-free frames would satisfy the detection
#: threshold on their own.
PI30_ONLY_NAKS = {
    "Ezgh": BLOCK_PI30_EZGH_NAK,
    "TWfA": BLOCK_PI30_TWFA_NAK,
    "W7EX": BLOCK_PI30_W7EX_NAK,
}

#: Hand-built: the captured QPIRI with field 6 (rated apparent power) rewritten from
#: 4000 to 3000 and the CRC recomputed, everything else byte-for-byte. Backs the
#: heat-sink gate's ABSENCE case only -- a SYNTH_ block may never assert a value.
SYNTH_PI30_MRFS_3000VA = bytes.fromhex(
    "283233302e302031372e33203233302e302035302e302031372e332033303030"
    "20343030302032342e302032352e302032342e302032382e382032372e362032"
    "20303630203033302031203220322031203130203020302032372e3020302031"
    "359f0d"
)
