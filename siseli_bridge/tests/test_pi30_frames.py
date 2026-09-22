"""The PI30 frame: what verifies, what does not, and where the CRC hides.

The checksum is the whole reason this protocol can be decoded from one device's capture
at all -- it is the difference between "these bytes look like ASCII" and "this device
said exactly this". So the framing gets its own file, and none of it depends on a field
position or a block name.
"""

import binascii
import pathlib
import re
import unittest

from src.siseli_bridge import pi30
from src.siseli_bridge.parsers import SolarParser
from tests import captures

#: The note the fixtures were transcribed from, four directories up from this file.
APPENDIX = (
    pathlib.Path(__file__).resolve().parents[2]
    / "captures" / "2026-09-02_device-c-voltronic-pi30.md"
).read_text(encoding="utf-8")


def _all_captured_blocks():
    merged = dict(captures.PI30_FRAGMENT_1)
    merged.update(captures.PI30_FRAGMENT_2)
    return merged


class TestCapturedFramesVerify(unittest.TestCase):
    def test_every_captured_frame_verifies(self):
        """All 24 blocks of the 2026-09-02 set, the two withheld serials aside.

        If this fails the CRC implementation believes the wrong frames, and every value
        decoded from them is worth nothing.
        """
        blocks = _all_captured_blocks()
        self.assertEqual(len(blocks), 24)
        for name, body in sorted(blocks.items()):
            with self.subTest(block=name):
                self.assertIsNotNone(
                    pi30.verify_frame(body),
                    f"{name} does not verify; the frame or the CRC is wrong",
                )

    def test_every_fixture_is_byte_identical_to_the_capture_note(self):
        """The fixtures are transcribed from a document; prove the transcription.

        Source-as-data: it parses the appendix of
        `captures/2026-09-02_device-c-voltronic-pi30.md` rather than executing anything,
        so reformatting that table breaks it. That is the trade being made -- writing
        these fixtures dropped two bytes from both hourly schedules, and every frame
        test still passed because the CRC was recomputed over the shortened body by the
        same code being tested. Only the document catches that.
        """
        appendix = APPENDIX.split("## Appendix")[1]
        frames = dict(re.findall(r"^\s{2}(\S{4})\s+([0-9a-f]{6,})\s*$", appendix, re.M))
        self.assertEqual(len(frames), 22, "the appendix table stopped parsing")

        fixtures = _all_captured_blocks()
        for name, hexs in sorted(frames.items()):
            with self.subTest(block=name):
                self.assertIn(name, fixtures)
                self.assertEqual(fixtures[name].hex(), hexs)

        withheld = sorted(set(fixtures) - set(frames))
        self.assertEqual(
            withheld, ["G5E9", "ahLb"],
            "only the two serial-bearing replies may be stand-ins",
        )

    def test_the_crc_matches_the_parser_s_own_implementation(self):
        """Two implementations of one checksum must not drift apart.

        `SolarParser._crc16_xmodem` already exists for the unsupported-protocol
        diagnostic and is pinned by `test_truthfulness.py`. This module uses
        `binascii.crc_hqx` instead, which is the same function in C.
        """
        for name, body in sorted(_all_captured_blocks().items()):
            with self.subTest(block=name):
                frame = pi30.strip_frame(body)
                payload = frame[:-2]
                self.assertEqual(
                    binascii.crc_hqx(payload, 0),
                    SolarParser._crc16_xmodem(payload),
                )


class TestFrameBoundaries(unittest.TestCase):
    """Hand-built structural cases. No value is asserted on any of them."""

    def test_only_one_trailing_cr_is_stripped(self):
        """A greedy rstrip would eat a CRC byte that happens to be CR or LF.

        `parsers._parse_ascii_text` strips greedily and is right to: Device A frames
        carry no checksum. Reusing it here would fail every frame whose checksum ends
        in 0x0D or 0x0A -- about one in 128.
        """
        payload = b"(TEST"
        crc = binascii.crc_hqx(payload, 0).to_bytes(2, "big")
        doubled = payload + crc + b"\r\r"
        self.assertIsNone(
            pi30.verify_frame(doubled),
            "a second trailing CR is not part of the frame and must not be stripped",
        )

    def test_a_crc_byte_of_0x0d_still_verifies(self):
        """The case the greedy strip would silently destroy.

        No captured frame happens to have one, so it is constructed: a body searched
        for until its checksum ends in 0x0D.
        """
        body = None
        for attempt in range(10000):
            candidate = b"(CR%d" % attempt
            if binascii.crc_hqx(candidate, 0).to_bytes(2, "big")[1] == 0x0D:
                body = candidate
                break
        self.assertIsNotNone(body, "no body with a 0x0D low CRC byte was found")
        crc = binascii.crc_hqx(body, 0).to_bytes(2, "big")
        self.assertEqual(crc[1], 0x0D)
        self.assertEqual(pi30.verify_frame(body + crc + b"\r"), body[1:])

    def test_the_bumped_crc_form_is_accepted(self):
        """mpp-solar increments a CRC byte colliding with '(' , CR, LF or NUL.

        Whether this firmware does it is untested -- no CRC byte in either capture hit
        the case -- so both forms are accepted. If it does, and this were strict, every
        frame with a colliding checksum would be discarded as corrupt.
        """
        body = None
        for attempt in range(10000):
            candidate = b"(BUMP%d" % attempt
            if 0x0D in binascii.crc_hqx(candidate, 0).to_bytes(2, "big"):
                body = candidate
                break
        self.assertIsNotNone(body)
        raw = binascii.crc_hqx(body, 0).to_bytes(2, "big")
        bumped = bytes((b + 1) if b in (0x28, 0x0D, 0x0A, 0x00) else b for b in raw)
        self.assertNotEqual(raw, bumped)
        self.assertEqual(pi30.verify_frame(body + bumped + b"\r"), body[1:])

    def test_a_corrupted_body_does_not_verify(self):
        block = bytearray(captures.BLOCK_PI30_G4WT_QPIGS_BATTERY)
        block[5] = block[5] ^ 0x01
        self.assertIsNone(pi30.verify_frame(bytes(block)))

    def test_a_body_that_is_not_a_frame_is_rejected(self):
        for body in (b"", b"no leading paren\r", b"(\r", b"(x"):
            with self.subTest(body=body):
                self.assertIsNone(pi30.verify_frame(body))

    def test_device_a_blocks_do_not_verify(self):
        """They carry no checksum, so their last two bytes are data."""
        for name, body in sorted(captures.CAPTURE_TELEMETRY.items()):
            with self.subTest(block=name):
                self.assertIsNone(pi30.verify_frame(body))


class TestTokenising(unittest.TestCase):
    def test_the_crc_is_stripped_before_tokens_are_read(self):
        """The CRC bytes are frequently printable, so the logged text is not the value.

        G4WT's logged text ends in a stray '~' and MrfS's in 'Z'. A decoder that splits
        the text it sees reads that into the last field.
        """
        tokens = pi30.tokenise(pi30.verify_frame(captures.BLOCK_PI30_MRFS_QPIRI))
        self.assertEqual(len(tokens), 25)
        self.assertEqual(tokens[-1], "1", "the CRC leaked into the last field")

    def test_a_doubled_space_changes_the_token_count(self):
        """Split on one space, not on runs of whitespace.

        Every field is read by position. A doubled space must break the reply's shape
        check rather than shift every field after it by one.
        """
        self.assertEqual(pi30.tokenise(b"1  2"), ["1", "", "2"])


if __name__ == "__main__":
    unittest.main()
