"""Voltronic PI30 query replies, as spoken by the Falcon VMIII-4000 of issue #32.

This is the second protocol family the add-on has met, and it is decoded on a different
basis from the first. Device A's blocks are read **by position with no schema**, from one
device, with no checksum. PI30 blocks are framed, CRC-checked query replies whose field
order is published in three documents, and this device's every frame was captured beside
vendor-portal screenshots taken in the same minute.

**What that does not buy.** The name-to-query map holds for *one* device. Nothing shows
whether another PI30 dongle reuses these four-character names, so the map is explicit and a
name this module does not know is a diagnostic, never a guess. See
`captures/2026-09-02_device-c-voltronic-pi30.md` for the evidence and
`docs/PI30_DESIGN.md` for the design.

**The rule that governs this file is the one that governs `parsers.py`:** publish a value
only when this payload carries evidence for it. Here that means:

- Every frame's CRC is verified before a single token is read. A frame that fails is not
  decoded, and its name does not count toward recognising the protocol.
- A field whose token will not coerce is **omitted**. Never a zero, never a default, never
  the previous payload's value.
- Fields excluded on purpose are listed in `docs/PI30_DESIGN.md` §5 with the reason, and
  the exact published key set is pinned in `tests/test_pi30_fields.py`. Adding a key here
  fails that test, which is the point: the exclusions are evidence-based, not oversights.

Nothing in this module logs, publishes or touches shared state. It turns bytes into values
and says what it recognised; `parsers.py` decides what to do about it.
"""

import binascii
import re
from collections import namedtuple
from typing import Callable, Dict, List, Optional, Sequence, Tuple

# ----------------------------------------------------------------------- frames

#: Every reply opens with it, and the CRC is computed from it inclusive.
_FRAME_START = b"("

#: mpp-solar increments a CRC byte that would collide with one of these, because a raw
#: `(` would look like a second frame start and CR/LF/NUL would end the frame early. No
#: CRC byte in either capture of this device hit the case, so whether this firmware does
#: it is untested (`docs/PI30_DESIGN.md` §12) -- both forms are therefore accepted.
_BUMPABLE = (0x28, 0x0D, 0x0A, 0x00)

_CRC_LEN = 2


def _bumped(crc: bytes) -> bytes:
    return bytes((value + 1) if value in _BUMPABLE else value for value in crc)


def strip_frame(body: bytes) -> Optional[bytes]:
    """The frame with **exactly one** trailing CR removed.

    Exactly one, never `rstrip`: a CRC byte is frequently 0x0D or 0x0A, and stripping
    greedily would eat it and fail every frame whose checksum happens to end that way.
    `parsers.SolarParser._parse_ascii_text` does strip greedily, which is correct there
    -- Device A frames carry no checksum -- and is why it cannot be reused here.
    """
    if not body or not body.startswith(_FRAME_START):
        return None
    frame = body[:-1] if body.endswith(b"\r") else body
    if len(frame) < len(_FRAME_START) + _CRC_LEN:
        return None
    return frame


def verify_frame(body: bytes) -> Optional[bytes]:
    """The payload between `(` and the CRC, or None when the frame does not verify.

    CRC16-XMODEM: polynomial 0x1021, initial value 0x0000, stored big-endian, computed
    over the frame from the leading `(` up to but not including the two CRC bytes. All 22
    distinct frames of the 2026-09-02 capture verify this way.
    """
    frame = strip_frame(body)
    if frame is None:
        return None
    payload, crc = frame[:-_CRC_LEN], frame[-_CRC_LEN:]
    want = binascii.crc_hqx(payload, 0).to_bytes(_CRC_LEN, "big")
    if crc == want or crc == _bumped(want):
        return payload[len(_FRAME_START):]
    return None


def tokenise(payload: bytes) -> List[str]:
    """The payload's space-separated tokens.

    Split on a single space rather than on runs of whitespace. Every field here is read by
    position, so a doubled space must change the token count -- and fail the reply's shape
    check -- rather than silently shift every field after it by one.
    """
    return payload.decode("ascii", "replace").split(" ")


# ------------------------------------------------------------------ the name map

#: Block name -> the query its body answers, for this device. Twenty entries: the capture
#: identified twenty, and the remaining four names are in `PI30_UNATTRIBUTED_NAMES`.
PI30_BLOCK_MAP: Dict[str, str] = {
    # fragment 1
    "cCft": "QPI",
    "G5E9": "QSID",
    "ahLb": "QID",
    "o2lC": "QVFW",
    "ag5g": "QVFW3",
    "MrfS": "QPIRI",
    "sJqt": "QFLAG",
    "G4WT": "QPIGS",
    "zZ3K": "QMOD",
    "DB48": "QMCHGCR",
    "wb83": "QMUCHGCR",
    "oTLG": "QOPPT",
    "48rR": "QCHPT",
    # fragment 2
    "lCMp": "QT",
    "7v9T": "QBEQI",
    "EMu5": "QMN",
    "9gbt": "QGMN",
    "cT7S": "QET",
    "mA9W": "QLT",
    "UefO": "QBMS",
}

#: Names this device sends that answer no known query. Three carry a bare `NAK` -- queries
#: this firmware declines -- and `u51Q`'s `1` fits QBOOT but contradicts QPIRI field 22 if
#: it is QOPM, so it is left unresolved.
#:
#: They are listed rather than ignored for one reason: the three NAK bodies are
#: byte-identical and **verify**, so counting them as recognised names would let three
#: content-free frames satisfy the detection threshold on their own.
PI30_UNATTRIBUTED_NAMES = frozenset({"Ezgh", "TWfA", "W7EX", "u51Q"})


def _exact_tokens(count: int) -> Callable[[Sequence[str]], bool]:
    return lambda tokens: len(tokens) == count


def _digit_tokens(minimum: int) -> Callable[[Sequence[str]], bool]:
    return lambda tokens: len(tokens) >= minimum and all(t.isdigit() for t in tokens)


def _single(pattern: str) -> Callable[[Sequence[str]], bool]:
    matcher = re.compile(pattern)
    return lambda tokens: len(tokens) == 1 and bool(matcher.match(tokens[0]))


def _qpigs_shape(tokens: Sequence[str]) -> bool:
    """21 to 24 fields.

    Bounded at both ends. An open `>= 21` is also satisfied by this device's own QPIRI (25
    tokens) and by both hourly schedules (28), so a garbled body under one of those names
    could otherwise be counted as live status. PI30 2014 ends at field 17, PI30 2015 and
    PI30MAX carry 21, and this device sends 24.
    """
    return 21 <= len(tokens) <= 24


def _qbeqi_shape(tokens: Sequence[str]) -> bool:
    """Ten fields, the sixth an equalisation voltage.

    The token count alone is not enough: QBMS also answers with exactly ten. Field 6 reads
    `27.60` here and `29.20` in the 2026-08-31 capture, while QBMS's sixth field is one of
    its `000` placeholders, so the voltage shape separates them.
    """
    return len(tokens) == 10 and bool(re.match(r"^\d{2}\.\d{2}$", tokens[5]))


def _qmn_shape(tokens: Sequence[str]) -> bool:
    """A model name: one token, at least three characters, carrying a letter.

    The letter requirement is what rejects `NAK` -- no -- and the purely numeric bodies
    this device sends under other names, so a firmware that declines QMN does not have the
    refusal registered as its model.
    """
    if len(tokens) != 1:
        return False
    token = tokens[0]
    return 3 <= len(token) <= 32 and token != "NAK" and any(c.isalpha() for c in token)


#: One predicate per mapped name, total by construction: `test_pi30_detection.py` asserts
#: the two key sets are equal, so a name added to the map without a predicate fails rather
#: than defaulting to "anything goes" and counting toward detection on any body at all.
PI30_SHAPE_PREDICATES: Dict[str, Callable[[Sequence[str]], bool]] = {
    "cCft": _single(r"^PI\d{2}$"),
    "G5E9": _single(r"^[0-9A-Za-z]{6,32}$"),
    "ahLb": _single(r"^[0-9A-Za-z]{6,32}$"),
    "o2lC": _single(r"^VERFW:[0-9.]+$"),
    "ag5g": _single(r"^VERFW:[0-9.]+$"),
    "MrfS": _exact_tokens(25),
    "sJqt": _single(r"^(?:[ED][a-z]+){1,2}$"),
    "G4WT": _qpigs_shape,
    "zZ3K": _single(r"^[A-Z]$"),
    "DB48": _digit_tokens(2),
    "wb83": _digit_tokens(2),
    "oTLG": _digit_tokens(2),
    "48rR": _digit_tokens(2),
    "lCMp": _single(r"^\d{14}$"),
    "7v9T": _qbeqi_shape,
    "EMu5": _qmn_shape,
    "9gbt": _single(r"^\d{1,8}$"),
    "cT7S": _single(r"^\d{8}$"),
    "mA9W": _single(r"^\d{8}$"),
    "UefO": _exact_tokens(10),
}

#: The protocol identifier this decoder was built against. `cCft` answering anything else
#: vetoes the switch outright rather than being ignored: a PI18 or PI41 device shares the
#: envelope and the CRC but not the field order, and decoding it as PI30 would publish
#: wrong numbers that look entirely plausible.
PI30_PROTOCOL_ID = "PI30"

#: A payload must carry at least this many verified frames, covering at least half its
#: blocks, before the protocol can be recognised at all.
MIN_VERIFIED_FRAMES = 3

#: ...and at least this many of them must be names this module maps, with a body of the
#: right shape. Two 16-bit CRCs colliding by chance is already remote; three is the
#: threshold a false positive would have to clear.
MIN_MAPPED_NAMES = 2

# ------------------------------------------------------------------- coercion

def _as_float(token: str) -> Optional[float]:
    try:
        return float(token)
    except (TypeError, ValueError):
        return None


def _as_int(token: str) -> Optional[int]:
    try:
        return int(token, 10)
    except (TypeError, ValueError):
        return None


def _as_text(token: str) -> Optional[str]:
    token = token.strip()
    return token or None


def _wh_to_kwh(token: str) -> Optional[float]:
    """Wh to kWh by true division.

    `253800 / 1000` is `253.8`, which is what the portal reads. Integer division gives
    `253.0` and quietly discards the pairing that establishes the Wh unit in the first
    place -- QET's unit is confirmed by that one portal figure, and QLT's comes from
    PI30MAX alone.
    """
    value = _as_int(token)
    return None if value is None else value / 1000


def _enum(mapping: Dict[str, str]) -> Callable[[str], Optional[str]]:
    """Map the codes both published specifications agree on; publish anything else raw.

    A code only one document defines, or one no document defines, is published as the code
    itself. That is honest -- the device said `7` -- where inventing a label from a single
    source would not be.
    """
    def cast(token: str) -> Optional[str]:
        token = token.strip()
        if not token:
            return None
        return mapping.get(token, token)

    return cast


_MODE = _enum({
    "P": "Power on",
    "S": "Standby",
    "L": "Line",
    "B": "Battery",
    "F": "Fault",
})

_BATTERY_TYPE = _enum({"0": "AGM", "1": "Flooded", "2": "User"})
_INPUT_RANGE = _enum({"0": "Appliance", "1": "UPS"})
_OUTPUT_PRIORITY = _enum({
    "0": "Utility first",
    "1": "Solar first",
    "2": "Solar, battery, utility",
})
_CHARGER_PRIORITY = _enum({
    "1": "Solar first",
    "2": "Solar and utility",
    "3": "Solar only",
})
_TOPOLOGY = _enum({"0": "Transformerless", "1": "Transformer"})
_OUTPUT_MODE = _enum({
    "0": "Single machine",
    "1": "Parallel",
    "2": "Phase 1 of 3",
    "3": "Phase 2 of 3",
    "4": "Phase 3 of 3",
})
_PV_OK = _enum({
    "0": "One inverter connected to PV",
    "1": "All inverters connected to PV",
})
_PV_BALANCE = _enum({"0": "Charging current", "1": "Max power"})
_ENABLED = _enum({"0": "disabled", "1": "enabled"})

# --------------------------------------------------------------- the field table

#: (token index, published key, coercion). Positions are 0-based; the documents and the
#: capture note number fields from 1.
Field = namedtuple("Field", "index key cast")

#: QPIGS, live status. Field 12 is gated (see `_qpigs_heatsink`), fields 17 and 21 are bit
#: fields, and 15, 18, 19 and 22-24 are excluded -- `docs/PI30_DESIGN.md` §5 gives the
#: reason for each.
_QPIGS_FIELDS = (
    Field(0, "pi30_grid_v", _as_float),
    Field(1, "pi30_grid_hz", _as_float),
    Field(2, "pi30_ac_out_v", _as_float),
    Field(3, "pi30_ac_out_hz", _as_float),
    Field(4, "pi30_ac_out_va", _as_int),
    Field(5, "pi30_ac_out_w", _as_int),
    Field(6, "pi30_load_pct", _as_int),
    Field(7, "pi30_bus_v", _as_int),
    Field(8, "pi30_bat_v", _as_float),
    Field(9, "pi30_bat_charge_current", _as_int),
    Field(10, "pi30_bat_cap", _as_int),
    Field(12, "pi30_pv1_current", _as_float),
    Field(13, "pi30_pv1_v", _as_float),
    Field(15, "pi30_bat_discharge_current", _as_int),
    # The device's own figure. 184.1 V x 1.2 A would be 221 W, not the 151 W both the
    # wire and the portal report; publish what the device states, never a derived value.
    Field(19, "pi30_pv1_charge_w", _as_int),
)

#: QPIGS field 17, `b7...b0`, most significant first -- which the single `1` at b4 pins,
#: since the portal shows exactly one thing on: the load. b7 and b3 are excluded: PI30MAX
#: reserves them on Axpert models, PI30 defines them, and both read 0.
_QPIGS_STATUS_BITS = (
    (1, "pi30_status_config_changed"),
    (2, "pi30_status_scc_firmware_updated"),
    (3, "pi30_status_load_on"),
    (5, "pi30_status_charging"),
    (6, "pi30_status_scc_charging"),
    (7, "pi30_status_ac_charging"),
)

#: QPIGS field 21, `b10 b9 b8`. b9 is paired to the portal's "Switch On"; b10 is defined
#: identically by PI30 2015 (as b104) and PI30MAX, and read 1 in the 2026-08-31 capture
#: with the battery sitting at its 28.8 V float setting. b8 is excluded: reserved in
#: PI30 2015, "dustproof, V series only" in PI30MAX, and 1 in both captures.
_QPIGS_FLOAT_BITS = (
    (0, "pi30_status_charging_to_float"),
    (1, "pi30_status_switch_on"),
)

#: QPIRI, ratings and settings. All 25 are published as diagnostics: their order is
#: defined identically in PI30 2014/2015 and PI30MAX, and the portal agrees with every
#: one -- though only fields 4, 9, 11, 12, 14, 15, 20 and 23 hold a value unique in the
#: reply, so the other 17 rest on the specifications for their position.
#:
#: These are live settings, not constants: five of them changed between the two captures.
_QPIRI_FIELDS = (
    Field(0, "pi30_rated_grid_v", _as_float),
    Field(1, "pi30_rated_grid_current", _as_float),
    Field(2, "pi30_rated_ac_out_v", _as_float),
    Field(3, "pi30_rated_ac_out_hz", _as_float),
    Field(4, "pi30_rated_ac_out_current", _as_float),
    Field(5, "pi30_rated_ac_out_va", _as_int),
    Field(6, "pi30_rated_ac_out_w", _as_int),
    Field(7, "pi30_rated_bat_v", _as_float),
    Field(8, "pi30_bat_recharge_v", _as_float),
    Field(9, "pi30_bat_under_v", _as_float),
    Field(10, "pi30_bat_bulk_v", _as_float),
    Field(11, "pi30_bat_float_v", _as_float),
    Field(12, "pi30_battery_type", _BATTERY_TYPE),
    Field(13, "pi30_max_ac_charge_current", _as_int),
    Field(14, "pi30_max_charge_current", _as_int),
    Field(15, "pi30_input_v_range", _INPUT_RANGE),
    Field(16, "pi30_output_source_priority", _OUTPUT_PRIORITY),
    Field(17, "pi30_charger_source_priority", _CHARGER_PRIORITY),
    Field(18, "pi30_parallel_max_count", _as_int),
    # Raw: the documents list machine types without agreeing on a full set, and the
    # portal shows this one as the bare number 10.
    Field(19, "pi30_machine_type", _as_text),
    Field(20, "pi30_topology", _TOPOLOGY),
    Field(21, "pi30_output_mode", _OUTPUT_MODE),
    Field(22, "pi30_bat_redischarge_v", _as_float),
    Field(23, "pi30_pv_ok_condition", _PV_OK),
    Field(24, "pi30_pv_power_balance", _PV_BALANCE),
)

#: QBEQI, equalisation. Fields 5 and 7 are reserved in PI30MAX §2.20 and not decoded. The
#: portal pins 2, 6 and 8 by value; the other five agree with it but share their value
#: with another field, so their positions rest on the specification. QBEQI appears in
#: neither PI30 document.
_QBEQI_FIELDS = (
    Field(0, "pi30_eq_enabled", _ENABLED),
    Field(1, "pi30_eq_time_min", _as_int),
    Field(2, "pi30_eq_period_days", _as_int),
    Field(3, "pi30_eq_max_current", _as_int),
    Field(5, "pi30_eq_v", _as_float),
    Field(7, "pi30_eq_over_time_min", _as_int),
    Field(8, "pi30_eq_active", _ENABLED),
    Field(9, "pi30_eq_elapsed_hours", _as_int),
)

#: QFLAG. `E` opens the enabled list and `D` the disabled one; the split is on those two
#: uppercase letters **only**, because `d` (0x64) is itself a flag letter in this device's
#: `EakxyzDbdjuv`. All ten states agree with the portal, but the portal confirms states
#: and not letters -- five read "enable" and five "disable", so two letters in the same
#: state could be swapped and nothing would show it. The meanings rest on the documents.
#:
#: `d` (solar feed to grid) is excluded: PI30MAX alone defines it, as a reserved feature,
#: and its portal row may come from QPIGS field 22 instead.
_QFLAG_LETTERS = (
    ("a", "pi30_flag_buzzer"),
    ("b", "pi30_flag_overload_bypass"),
    ("j", "pi30_flag_power_saving"),
    ("k", "pi30_flag_lcd_timeout"),
    ("u", "pi30_flag_overload_restart"),
    ("v", "pi30_flag_over_temp_restart"),
    ("x", "pi30_flag_lcd_backlight"),
    ("y", "pi30_flag_primary_source_alarm"),
    ("z", "pi30_flag_fault_record"),
)

#: Below this rated apparent power, all three documents describe QPIGS field 12 as a raw
#: NTC analogue-to-digital reading rather than a temperature. The portal shows it as 49 °C
#: on this 4000 VA unit, which is the only evidence that it is degrees at all -- so the
#: key is published only when the same payload's QPIRI proves the machine is larger.
HEATSINK_TEMP_MIN_VA = 3000

#: QVFW and QVFW3 answer with their own field label in front of the version.
_VERFW_PREFIX = "VERFW:"


# ------------------------------------------------------------------ per-query decode

def _positional(tokens: Sequence[str], fields: Sequence[Field], out: Dict[str, object]) -> None:
    for field in fields:
        if field.index >= len(tokens):
            continue
        value = field.cast(tokens[field.index])
        if value is not None:
            out[field.key] = value


def _bits(token: str, bits: Sequence[Tuple[int, str]], out: Dict[str, object]) -> None:
    for position, key in bits:
        if position >= len(token):
            continue
        char = token[position]
        if char in ("0", "1"):
            out[key] = char == "1"


def _qpigs(tokens: Sequence[str], rated_va: Optional[int], out: Dict[str, object]) -> None:
    _positional(tokens, _QPIGS_FIELDS, out)
    if len(tokens) > 16:
        _bits(tokens[16], _QPIGS_STATUS_BITS, out)
    if len(tokens) > 20:
        _bits(tokens[20], _QPIGS_FLOAT_BITS, out)
    if rated_va is not None and rated_va > HEATSINK_TEMP_MIN_VA and len(tokens) > 11:
        value = _as_int(tokens[11])
        if value is not None:
            out["pi30_heatsink_temp_c"] = value


def _qflag(token: str, out: Dict[str, object]) -> None:
    enabled: set = set()
    disabled: set = set()
    target = None
    for char in token:
        if char == "E":
            target = enabled
        elif char == "D":
            target = disabled
        elif target is not None:
            target.add(char)
    for letter, key in _QFLAG_LETTERS:
        if letter in enabled:
            out[key] = "enabled"
        elif letter in disabled:
            out[key] = "disabled"


#: Every key this module can publish. The registry is checked against it, so a key added
#: above without a sensor -- or a sensor with no key here -- fails a test.
def field_table_keys() -> Tuple[str, ...]:
    keys: List[str] = [field.key for field in _QPIGS_FIELDS]
    keys.append("pi30_heatsink_temp_c")
    keys.extend(key for _, key in _QPIGS_STATUS_BITS)
    keys.extend(key for _, key in _QPIGS_FLOAT_BITS)
    keys.extend(field.key for field in _QPIRI_FIELDS)
    keys.append("pi30_mode")
    keys.extend(key for _, key in _QFLAG_LETTERS)
    keys.extend(field.key for field in _QBEQI_FIELDS)
    keys.extend(("pi30_pv_energy_total_kwh", "pi30_load_energy_total_kwh"))
    keys.extend(("pi30_model", "pi30_firmware_version"))
    return tuple(keys)


# ---------------------------------------------------------------------- detection

Pi30Detection = namedtuple("Pi30Detection", "verdict verified total mapped_names vetoed")

#: `detect` verdicts.
PI30 = "pi30"
UNKNOWN_NAMES = "unknown-names"
NOT_PI30 = "not-pi30"


def verified_payloads(blocks: Dict[str, bytes]) -> Dict[str, bytes]:
    """Every block whose frame verifies, by name. Never raises."""
    out: Dict[str, bytes] = {}
    for name, body in (blocks or {}).items():
        try:
            payload = verify_frame(body)
        except Exception:
            continue
        if payload is not None:
            out[name] = payload
    return out


def _shape_ok(name: str, payload: bytes) -> bool:
    predicate = PI30_SHAPE_PREDICATES.get(name)
    if predicate is None:
        return False
    try:
        return bool(predicate(tokenise(payload)))
    except Exception:
        return False


def detect(blocks: Dict[str, bytes]) -> Pi30Detection:
    """What this payload is, on the evidence it carries. Never raises.

    Three ways out. `not-pi30` when the frames do not verify in enough numbers, or when
    `cCft` names a protocol this decoder was not built for. `unknown-names` when the
    frames verify but too few names are ones this module maps -- a PI30 device whose
    dongle labels its blocks differently, which gets a diagnostic rather than a guess.
    `pi30` otherwise.
    """
    total = len(blocks or {})
    verified = verified_payloads(blocks)

    vetoed = False
    protocol = verified.get("cCft")
    if protocol is not None and tokenise(protocol) != [PI30_PROTOCOL_ID]:
        vetoed = True

    mapped = tuple(sorted(
        name for name, payload in verified.items()
        if name in PI30_BLOCK_MAP and _shape_ok(name, payload)
    ))

    enough_frames = len(verified) >= MIN_VERIFIED_FRAMES and len(verified) * 2 >= total
    if vetoed or not enough_frames:
        verdict = NOT_PI30
    elif len(mapped) >= MIN_MAPPED_NAMES:
        verdict = PI30
    else:
        verdict = UNKNOWN_NAMES

    return Pi30Detection(verdict, len(verified), total, mapped, vetoed)


# ------------------------------------------------------------------------ decode

Pi30Decode = namedtuple("Pi30Decode", "values context signature had_qpigs detection")


def _rated_va(verified: Dict[str, bytes]) -> Optional[int]:
    payload = verified.get("MrfS")
    if payload is None or not _shape_ok("MrfS", payload):
        return None
    return _as_int(tokenise(payload)[5])


def decode(blocks: Dict[str, bytes]) -> Pi30Decode:
    """Every value this payload proves, and the context a person needs to read the log.

    **Never raises, and never lets one bad block cost the others.** Each reply is decoded
    inside its own guard, so a single token that will not coerce omits its own key and a
    single malformed body loses only itself. `parse_payload` wraps its whole body in one
    `except`, and without this a stray byte in one of 24 blocks would discard the payload.

    `values` is what may be published. `context` is for the log only -- the serial number
    and the inverter's clock are deliberately not entities.
    """
    detection = detect(blocks)
    values: Dict[str, object] = {}
    context: Dict[str, object] = {}
    if detection.verdict != PI30:
        return Pi30Decode(values, context, "", False, detection)

    verified = verified_payloads(blocks)
    rated_va = _rated_va(verified)
    signature_parts = ["", "", ""]
    had_qpigs = False

    for name, payload in verified.items():
        query = PI30_BLOCK_MAP.get(name)
        if query is None or not _shape_ok(name, payload):
            continue
        try:
            tokens = tokenise(payload)
            if query == "QPIGS":
                had_qpigs = True
                _qpigs(tokens, rated_va, values)
                signature_parts[1] = tokens[16] if len(tokens) > 16 else ""
                signature_parts[2] = tokens[20] if len(tokens) > 20 else ""
            elif query == "QPIRI":
                _positional(tokens, _QPIRI_FIELDS, values)
            elif query == "QBEQI":
                _positional(tokens, _QBEQI_FIELDS, values)
            elif query == "QMOD":
                signature_parts[0] = tokens[0]
                mode = _MODE(tokens[0])
                if mode is not None:
                    values["pi30_mode"] = mode
            elif query == "QFLAG":
                _qflag(tokens[0], values)
            elif query == "QET":
                energy = _wh_to_kwh(tokens[0])
                if energy is not None:
                    values["pi30_pv_energy_total_kwh"] = energy
            elif query == "QLT":
                energy = _wh_to_kwh(tokens[0])
                if energy is not None:
                    values["pi30_load_energy_total_kwh"] = energy
            elif query == "QMN":
                model = _as_text(tokens[0])
                if model is not None:
                    values["pi30_model"] = model
            elif query == "QVFW":
                # `VERFW:` is the reply's own field label, stripped for the same reason
                # the `(` and the CRC are: it is the protocol talking, not a value.
                version = _as_text(tokens[0][len(_VERFW_PREFIX):]
                                   if tokens[0].startswith(_VERFW_PREFIX) else tokens[0])
                if version is not None:
                    values["pi30_firmware_version"] = version
            elif query == "QT":
                context["inverter_clock"] = _readable_clock(tokens[0])
            elif query == "QPI":
                context["protocol"] = tokens[0]
        except Exception:
            # One reply's problem is that reply's problem. Deliberately silent: the
            # decoded-field list in the log is what shows a value went missing, and a
            # per-block error line on every payload would bury it.
            continue

    # Empty when this payload said nothing about the device's state -- fragment 2 carries
    # settings and totals, so it must not read as "the mode changed to nothing".
    signature = "|".join(signature_parts) if any(signature_parts) else ""
    return Pi30Decode(values, context, signature, had_qpigs, detection)


def _readable_clock(token: str) -> str:
    """`YYYYMMDDhhmmss` as something a person can compare with a screenshot.

    The capture proved this block pairs with the vendor portal's "System Time" to the
    second, which is what makes a log line and a screenshot comparable at all.
    """
    if len(token) != 14 or not token.isdigit():
        return token
    return (
        f"{token[0:4]}-{token[4:6]}-{token[6:8]} "
        f"{token[8:10]}:{token[10:12]}:{token[12:14]}"
    )


# ----------------------------------------------------------------- the diagnostic

#: The order the decoded values are reported in, and the queries they came from. Grouped
#: so the log can be read against the vendor portal page by page rather than as one line
#: of seventy fields.
_REPORT_GROUPS = (
    ("QPIGS", tuple(field.key for field in _QPIGS_FIELDS)
        + ("pi30_heatsink_temp_c",)
        + tuple(key for _, key in _QPIGS_STATUS_BITS)
        + tuple(key for _, key in _QPIGS_FLOAT_BITS)),
    ("QPIRI", tuple(field.key for field in _QPIRI_FIELDS)),
    ("QMOD", ("pi30_mode",)),
    ("QFLAG", tuple(key for _, key in _QFLAG_LETTERS)),
    ("QBEQI", tuple(field.key for field in _QBEQI_FIELDS)),
    ("QET/QLT", ("pi30_pv_energy_total_kwh", "pi30_load_energy_total_kwh")),
    ("QMN/QVFW", ("pi30_model", "pi30_firmware_version")),
)

_KEY_PREFIX = "pi30_"


def describe(result: Pi30Decode) -> List[Tuple[str, Dict[str, object]]]:
    """The decoded values as one group of key/value pairs per query, in a fixed order.

    Returned rather than logged, so this module stays free of the logging configuration
    and a test can assert the content without capturing output.
    """
    groups: List[Tuple[str, Dict[str, object]]] = []
    for query, keys in _REPORT_GROUPS:
        fields = {}
        for key in keys:
            if key in result.values:
                fields[key[len(_KEY_PREFIX):]] = result.values[key]
        if fields:
            groups.append((query, fields))
    return groups
