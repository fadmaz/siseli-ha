"""Deciding that a payload is Voltronic PI30 -- and, more importantly, that it is not.

A false positive here is worse than a missed detection: PI18 and PI41 share the same
envelope and the same CRC but not the same field order, so decoding one as PI30 would
publish entirely plausible wrong numbers. The thresholds exist for that, and so does the
`cCft` veto.

Detection is exercised **directly**, not by driving `parse_payload`. The PI30 path is
reached only when the Device A decoder returned nothing, and a Device A payload never
does, so a test that drove the parser and asserted "no switch happened" would prove only
that the short-circuit exists while never executing a single threshold. That
short-circuit is pinned separately, in its own test, as a second property.
"""

import binascii
import unittest
from unittest import mock

from src.siseli_bridge import parsers as parser_module
from src.siseli_bridge import pi30
from tests import captures
from tests.helpers import capture_logs, envelope, isolated_state


def _frame(body: bytes) -> bytes:
    return body + binascii.crc_hqx(body, 0).to_bytes(2, "big") + b"\r"


class TestTheShapePredicateTableIsTotal(unittest.TestCase):
    def test_every_mapped_name_has_a_predicate(self):
        """Otherwise "each predicate rejects a wrong shape" can never notice the gaps.

        A mapped name without a predicate would default to accepting any body at all,
        and count toward the threshold on a frame carrying nothing.
        """
        self.assertEqual(set(pi30.PI30_BLOCK_MAP), set(pi30.PI30_SHAPE_PREDICATES))

    def test_the_unattributed_names_are_not_in_the_map(self):
        self.assertFalse(set(pi30.PI30_BLOCK_MAP) & pi30.PI30_UNATTRIBUTED_NAMES)

    def test_the_map_holds_the_twenty_identified_queries(self):
        """The capture identified twenty; three replies are NAKs and one is unresolved."""
        self.assertEqual(len(pi30.PI30_BLOCK_MAP), 20)
        self.assertEqual(len(pi30.PI30_UNATTRIBUTED_NAMES), 4)

    def test_each_captured_body_satisfies_its_own_predicate(self):
        blocks = dict(captures.PI30_FRAGMENT_1)
        blocks.update(captures.PI30_FRAGMENT_2)
        for name, body in sorted(blocks.items()):
            if name not in pi30.PI30_BLOCK_MAP:
                continue
            with self.subTest(block=name):
                tokens = pi30.tokenise(pi30.verify_frame(body))
                self.assertTrue(pi30.PI30_SHAPE_PREDICATES[name](tokens))

    def test_a_nak_body_satisfies_no_mapped_predicate(self):
        """Three of this device's replies are a bare `NAK`, and they verify.

        If any mapped name accepted it, two NAKs would be enough to recognise the
        protocol on a payload carrying no data whatsoever.
        """
        tokens = ["NAK"]
        for name, predicate in sorted(pi30.PI30_SHAPE_PREDICATES.items()):
            with self.subTest(block=name):
                self.assertFalse(predicate(tokens))

    def test_qpigs_is_bounded_at_both_ends(self):
        """An open `>= 21` is also satisfied by QPIRI (25) and both schedules (28)."""
        self.assertTrue(pi30.PI30_SHAPE_PREDICATES["G4WT"](["x"] * 24))
        self.assertFalse(pi30.PI30_SHAPE_PREDICATES["G4WT"](["x"] * 25))
        self.assertFalse(pi30.PI30_SHAPE_PREDICATES["G4WT"](["x"] * 20))

    def test_qbeqi_is_not_satisfied_by_qbms(self):
        """Both answer with exactly ten tokens; field 6 is what separates them."""
        qbms = pi30.tokenise(pi30.verify_frame(captures.BLOCK_PI30_UEFO_QBMS))
        self.assertEqual(len(qbms), 10)
        self.assertFalse(pi30.PI30_SHAPE_PREDICATES["7v9T"](qbms))


class TestPositiveDetection(unittest.TestCase):
    def test_each_fragment_alone_is_recognised(self):
        """A set arrives as two messages, and either may be the first one seen."""
        for label, blocks in (
            ("fragment 1", captures.PI30_FRAGMENT_1),
            ("fragment 2", captures.PI30_FRAGMENT_2),
        ):
            with self.subTest(fragment=label):
                self.assertEqual(pi30.detect(blocks).verdict, pi30.PI30)

    def test_the_whole_set_is_recognised(self):
        blocks = dict(captures.PI30_FRAGMENT_1)
        blocks.update(captures.PI30_FRAGMENT_2)
        detection = pi30.detect(blocks)
        self.assertEqual(detection.verdict, pi30.PI30)
        self.assertEqual(detection.verified, 24)
        self.assertEqual(len(detection.mapped_names), 20)

    def test_two_mapped_names_are_enough(self):
        blocks = {
            "cT7S": captures.BLOCK_PI30_CT7S_QET,
            "mA9W": captures.BLOCK_PI30_MA9W_QLT,
            "zZ3K": captures.BLOCK_PI30_ZZ3K_MODE_BATTERY,
        }
        self.assertEqual(pi30.detect(blocks).verdict, pi30.PI30)


class TestNegativeDetection(unittest.TestCase):
    def test_device_a_is_never_pi30(self):
        """Its blocks carry no checksum at all, so nothing verifies."""
        for label, blocks in (
            ("telemetry", captures.CAPTURE_TELEMETRY),
            ("identity", captures.CAPTURE_IDENTITY),
        ):
            with self.subTest(payload=label):
                detection = pi30.detect(blocks)
                self.assertEqual(detection.verdict, pi30.NOT_PI30)
                self.assertEqual(detection.verified, 0)

    def test_device_b_is_never_pi30(self):
        """Issue #30: binary Modbus RTU inside the same envelope.

        Its one ASCII frame, an `(ACK9`, verifies -- a single frame is exactly what the
        three-frame threshold is for.
        """
        blocks = {
            "ARV4": captures.DEVB_BLOCK_ARV4,
            "ESQL": captures.DEVB_BLOCK_ESQL,
            "JL4X": captures.DEVB_BLOCK_JL4X,
            "SEO5": captures.DEVB_BLOCK_SEO5,
        }
        self.assertEqual(pi30.detect(blocks).verdict, pi30.NOT_PI30)

    def test_arbitrary_ascii_is_never_pi30(self):
        blocks = {
            "aaaa": b"(hello world\r",
            "bbbb": b"(1 2 3 4 5\r",
            "cccc": b"(not a frame at all\r",
        }
        self.assertEqual(pi30.detect(blocks).verdict, pi30.NOT_PI30)

    def test_three_verified_frames_are_required(self):
        blocks = {
            "cT7S": captures.BLOCK_PI30_CT7S_QET,
            "mA9W": captures.BLOCK_PI30_MA9W_QLT,
        }
        self.assertEqual(pi30.detect(blocks).verdict, pi30.NOT_PI30)

    def test_verified_frames_must_cover_half_the_blocks(self):
        """Three good frames among a crowd of foreign ones is not a PI30 device."""
        blocks = {
            "cT7S": captures.BLOCK_PI30_CT7S_QET,
            "mA9W": captures.BLOCK_PI30_MA9W_QLT,
            "zZ3K": captures.BLOCK_PI30_ZZ3K_MODE_BATTERY,
        }
        blocks.update({f"x{i:03d}": b"(nonsense\r" for i in range(4)})
        self.assertEqual(pi30.detect(blocks).verdict, pi30.NOT_PI30)


class TestTheProtocolVeto(unittest.TestCase):
    def test_a_qpi_answer_that_is_not_pi30_vetoes_the_whole_payload(self):
        """PI18 shares the envelope and the CRC, not the field order.

        Decoding it as PI30 would publish numbers that look completely reasonable.
        """
        blocks = dict(captures.PI30_FRAGMENT_1)
        blocks["cCft"] = _frame(b"(PI18")
        detection = pi30.detect(blocks)
        self.assertEqual(detection.verdict, pi30.NOT_PI30)
        self.assertTrue(detection.vetoed)
        self.assertEqual(pi30.decode(blocks).values, {})

    def test_a_corrupt_qpi_does_not_veto(self):
        """A frame that fails its CRC said nothing, so it cannot contradict anything."""
        blocks = dict(captures.PI30_FRAGMENT_1)
        blocks["cCft"] = b"(PI18\x00\x00\r"
        detection = pi30.detect(blocks)
        self.assertEqual(detection.verdict, pi30.PI30)
        self.assertFalse(detection.vetoed)

    def test_an_absent_qpi_does_not_veto(self):
        """Fragment 2 carries no QPI at all, and must still be recognised."""
        self.assertNotIn("cCft", captures.PI30_FRAGMENT_2)
        self.assertEqual(pi30.detect(captures.PI30_FRAGMENT_2).verdict, pi30.PI30)


class TestUnknownBlockNames(unittest.TestCase):
    def test_only_naks_is_a_diagnostic_not_a_detection(self):
        """Three byte-identical `NAK` frames that all verify.

        Without the unattributed-name list, these alone would clear both thresholds.
        """
        detection = pi30.detect(captures.PI30_ONLY_NAKS)
        self.assertEqual(detection.verdict, pi30.UNKNOWN_NAMES)
        self.assertEqual(detection.verified, 3)
        self.assertEqual(detection.mapped_names, ())

    def test_a_pi30_device_with_different_block_names(self):
        """The name map is proven for one device. Another dongle may label its blocks
        differently, and that gets a diagnostic rather than a guess."""
        blocks = {
            "ZZ01": captures.BLOCK_PI30_G4WT_QPIGS_BATTERY,
            "ZZ02": captures.BLOCK_PI30_MRFS_QPIRI,
            "ZZ03": captures.BLOCK_PI30_CT7S_QET,
        }
        detection = pi30.detect(blocks)
        self.assertEqual(detection.verdict, pi30.UNKNOWN_NAMES)
        self.assertEqual(pi30.decode(blocks).values, {})


class TestTheParserReachesPi30OnlyAsARescue(unittest.TestCase):
    """The second property, pinned separately from the thresholds above."""

    def test_a_device_a_payload_never_reaches_the_pi30_decoder(self):
        with isolated_state(), mock.patch.object(
            parser_module, "log_pi30_diagnostic"
        ) as diagnostic:
            parser_module.SolarParser.parse_payload(
                envelope(captures.CAPTURE_TELEMETRY),
                source_topic="dtu/1/pub/event/dev_prop_post",
            )
        diagnostic.assert_not_called()

    def test_the_device_a_block_names_and_the_pi30_names_are_disjoint(self):
        """What makes the rescue path unreachable for a supported device."""
        self.assertFalse(
            set(parser_module.KNOWN_BLOCK_NAMES)
            & (set(pi30.PI30_BLOCK_MAP) | pi30.PI30_UNATTRIBUTED_NAMES)
        )


class TestTheDiagnostic(unittest.TestCase):
    def setUp(self):
        ctx = isolated_state()
        ctx.__enter__()
        self.addCleanup(lambda: ctx.__exit__(None, None, None))
        parser_module.PI30_DECODE_LOGGED = False
        parser_module.PI30_BLOCK_NAMES_UNKNOWN_LOGGED = False
        parser_module.PI30_LAST_SIGNATURE = ""
        parser_module.UNSUPPORTED_PROTOCOL_LOGGED = False

    def _parse(self, blocks):
        with capture_logs() as lines:
            parser_module.SolarParser.parse_payload(
                envelope(blocks), source_topic="dtu/34545375423553743260/pub/event/dev_prop_post"
            )
        return lines

    def test_a_pi30_payload_is_decoded_and_not_called_unsupported(self):
        lines = self._parse(captures.PI30_FRAGMENT_1)
        text = "\n".join(lines)
        self.assertIn("[PI30 DECODE]", text)
        self.assertNotIn("[UNSUPPORTED PROTOCOL]", text)
        self.assertNotIn("[NO VALUES DECODED]", text)

    def test_the_dump_carries_what_the_reporter_is_asked_for(self):
        text = "\n".join(self._parse(captures.PI30_FRAGMENT_1))
        self.assertIn("issues/32", text)
        self.assertIn("grid_v=219.4", text)
        self.assertIn('query="QPIRI"', text)

    def test_the_inverter_clock_is_printed_so_a_screenshot_can_be_paired(self):
        text = "\n".join(self._parse(captures.PI30_FRAGMENT_2))
        self.assertIn("2026-09-02 17:05:54", text)

    def test_it_is_logged_at_warning(self):
        """It replaces a warning-level line.

        A user whose inverter is unsupported has no reason to have raised their log
        level, and DOCS.md offers `warning` for quiet logs. At info they would see
        nothing at all -- which is how issue #30 reached "all sensors Unknown" with
        nothing in the log naming the cause.
        """
        from src.siseli_bridge import loggers

        with mock.patch.object(loggers, "CURRENT_LOG_LEVEL", "warning"):
            text = "\n".join(self._parse(captures.PI30_FRAGMENT_1))
        self.assertIn("[PI30 DECODE]", text)

    def test_the_dump_is_one_shot_while_the_device_state_holds(self):
        self.assertIn("[PI30 DECODE]", "\n".join(self._parse(captures.PI30_FRAGMENT_1)))
        self.assertNotIn("[PI30 DECODE]", "\n".join(self._parse(captures.PI30_FRAGMENT_1)))

    def test_it_re_arms_when_the_device_changes_state(self):
        """The evidence still missing is a capture taken in a state this device has
        never been observed in (`docs/PI30_DESIGN.md` §12). A strictly one-shot line
        prints once per restart, which is almost impossible to catch in the right
        state; re-arming on a mode or status-bit change makes it catchable and still
        silent in steady operation.
        """
        self._parse(captures.PI30_FRAGMENT_1)
        blocks = dict(captures.PI30_FRAGMENT_1)
        blocks["zZ3K"] = _frame(b"(L")            # battery mode -> line mode
        self.assertIn("[PI30 DECODE]", "\n".join(self._parse(blocks)))

    def test_fragment_2_does_not_re_arm_the_dump(self):
        """It carries settings and totals and says nothing about the device's state,
        so it must not read as "the mode changed to nothing"."""
        self._parse(captures.PI30_FRAGMENT_1)
        self.assertNotIn("[PI30 DECODE]", "\n".join(self._parse(captures.PI30_FRAGMENT_2)))

    def test_unknown_names_get_their_own_one_shot(self):
        """Sharing `UNSUPPORTED_PROTOCOL_LOGGED` would let whichever diagnosis fired
        first silence the other for the life of the process, and one install can have
        both kinds of device on the same broker."""
        text = "\n".join(self._parse(captures.PI30_ONLY_NAKS))
        self.assertIn("[PI30 BLOCK NAMES UNKNOWN]", text)
        self.assertNotIn("[UNSUPPORTED PROTOCOL]", text)
        self.assertFalse(parser_module.UNSUPPORTED_PROTOCOL_LOGGED)

        # A genuinely foreign device on the same install is still diagnosed.
        foreign = "\n".join(self._parse(captures.CAPTURE_DEVICE_B_FOREIGN))
        self.assertIn("[UNSUPPORTED PROTOCOL]", foreign)

    def test_an_unsupported_device_is_still_called_unsupported(self):
        text = "\n".join(self._parse(captures.CAPTURE_DEVICE_B_FOREIGN))
        self.assertIn("[UNSUPPORTED PROTOCOL]", text)
        self.assertNotIn("[PI30", text)

    def test_nothing_is_published_by_the_diagnostic(self):
        """2.6.25 decodes and reports. It creates no entity and writes no state."""
        from src.siseli_bridge import state as shared_state

        before = dict(shared_state.LAST_STATE)
        self._parse(captures.PI30_FRAGMENT_1)
        self.assertEqual(dict(shared_state.LAST_STATE), before)

    def test_it_returns_false_so_the_payload_counts_as_undecoded(self):
        with capture_logs():
            handled = parser_module.SolarParser.parse_payload(
                envelope(captures.PI30_FRAGMENT_1), source_topic="dtu/1/pub/event/dev_prop_post"
            )
        self.assertFalse(handled)


if __name__ == "__main__":
    unittest.main()
