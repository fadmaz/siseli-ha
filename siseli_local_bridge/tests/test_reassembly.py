"""Regression tests for TCP reassembly and MQTT frame alignment.

The reassembler is the best-engineered part of the codebase -- it already handled
retransmits, overlap, gaps, buffer capping and stale-flow eviction. These tests cover
the four ways it could still stop delivering data permanently:

  * a segment the sniffer never saw, which is never retransmitted because the real
    receiver got it and ACKed it;
  * a 32-bit sequence wrap;
  * a reconnect reusing the same socket pair;
  * a mid-stream desync, after which bogus frames consumed the genuine packets.
"""

import time
import unittest
from unittest import mock

from src.siseli_local_bridge import parsers as parser_module
from src.siseli_local_bridge.parsers import (
    FLOW_STATES,
    SEQ_MOD,
    append_stream_data,
    drop_flow,
    extract_mqtt_packets_from_stream,
    get_flow_state,
    reset_flow,
    seq_diff,
    seq_gt,
    seq_lt,
    validate_generic_mqtt_packet,
)
from tests.helpers import control_packet, publish_packet

FLOW = ("192.168.1.139", 51234, "8.212.18.157", 1883)


def _publish(marker=b'{"b":[]}'):
    return publish_packet("device/ABC123/data", marker)


class _FlowTestCase(unittest.TestCase):
    def setUp(self):
        FLOW_STATES.clear()
        self.addCleanup(FLOW_STATES.clear)


class TestSerialArithmetic(unittest.TestCase):
    """RFC 1982 comparison. Plain < / > meant a wrap made every later segment look
    like a duplicate, and the flow never advanced again."""

    def test_ordering_without_a_wrap(self):
        self.assertTrue(seq_lt(100, 200))
        self.assertTrue(seq_gt(200, 100))
        self.assertFalse(seq_lt(200, 100))

    def test_ordering_across_the_wrap(self):
        before = SEQ_MOD - 10
        after = 5  # 15 bytes later, having wrapped
        self.assertTrue(seq_lt(before, after))
        self.assertTrue(seq_gt(after, before))
        self.assertEqual(seq_diff(after, before), 15)

    def test_equal_sequences_are_neither_before_nor_after(self):
        self.assertFalse(seq_lt(42, 42))
        self.assertFalse(seq_gt(42, 42))


class TestSequenceWraparound(_FlowTestCase):
    def test_a_flow_keeps_delivering_across_a_32_bit_wrap(self):
        packet = _publish()
        start = SEQ_MOD - len(packet)  # this packet ends exactly at the wrap

        first = append_stream_data(FLOW, start, packet)
        self.assertEqual(len(first), 1)

        # The next segment legitimately arrives at sequence 0.
        second = append_stream_data(FLOW, 0, packet)
        self.assertEqual(len(second), 1, "a wrap must not wedge the flow")


class TestMissingSegmentRecovery(_FlowTestCase):
    def test_a_dropped_capture_does_not_wedge_the_flow_forever(self):
        """Skip one segment, then feed many more. Previously: nothing was ever
        delivered again and `pending` grew without bound."""
        packet = _publish()
        seq = 1000

        append_stream_data(FLOW, seq, packet)
        seq += len(packet)
        skipped = seq  # this one never reaches the sniffer
        seq += len(packet)

        delivered = 0
        for _ in range(200):
            delivered += len(append_stream_data(FLOW, seq, packet))
            seq += len(packet)

        state = FLOW_STATES[FLOW]
        self.assertLessEqual(len(state.pending), parser_module.MAX_PENDING_SEGMENTS + 1)
        self.assertGreater(delivered, 0, "the flow must resynchronise and resume")
        self.assertNotEqual(state.next_seq, skipped)

    def test_pending_bytes_are_bounded(self):
        packet = _publish()
        seq = 1000
        append_stream_data(FLOW, seq, packet)
        seq += len(packet) * 2  # leave a hole

        for _ in range(300):
            append_stream_data(FLOW, seq, packet)
            seq += len(packet)

        self.assertLessEqual(
            FLOW_STATES[FLOW].pending_bytes, parser_module.MAX_PENDING_BYTES + len(packet)
        )

    def test_a_gap_times_out(self):
        """Every call runs on the same fake clock, including the one that creates the
        flow. Seeding the flow from the real monotonic clock and then patching made the
        result depend on the host's uptime: a developer box days into an uptime saw a
        backward jump and passed, a freshly booted CI runner saw a 9700 s forward jump,
        tripped the stale-flow reset, and cleared the gap under test."""
        packet = _publish()
        gap_seq = 1000 + len(packet) * 2
        clock = {"now": 10_000.0}

        with mock.patch.object(parser_module.time, "monotonic", side_effect=lambda: clock["now"]):
            append_stream_data(FLOW, 1000, packet)

            append_stream_data(FLOW, gap_seq, packet)
            self.assertIsNotNone(FLOW_STATES[FLOW].gap_since)

            clock["now"] = 10_000.0 + parser_module.STREAM_GAP_TIMEOUT_SEC + 1
            append_stream_data(FLOW, gap_seq + len(packet), packet)

        self.assertIsNone(FLOW_STATES[FLOW].gap_since, "the gap must be abandoned")

    def test_continuous_traffic_no_longer_keeps_a_wedged_flow_alive(self):
        """last_seen was refreshed on every call, including gap parking, so the 30 s
        stale reset could never fire on a flow that kept receiving data."""
        packet = _publish()
        append_stream_data(FLOW, 1000, packet)
        state = FLOW_STATES[FLOW]

        state.last_seen = time.monotonic()
        state.last_progress = time.monotonic() - 200

        refreshed = get_flow_state(FLOW)
        self.assertIsNone(refreshed.next_seq, "a flow making no progress must reset")


class TestFlowLifecycle(_FlowTestCase):
    def test_reset_flow_seeds_the_new_sequence(self):
        append_stream_data(FLOW, 1000, _publish())
        reset_flow(FLOW, initial_seq=5000)
        self.assertEqual(FLOW_STATES[FLOW].next_seq, 5000)
        self.assertEqual(FLOW_STATES[FLOW].pending, {})

    def test_reset_flow_creates_the_entry_if_absent(self):
        reset_flow(FLOW, initial_seq=7)
        self.assertEqual(FLOW_STATES[FLOW].next_seq, 7)

    def test_drop_flow_forgets_it_entirely(self):
        append_stream_data(FLOW, 1000, _publish())
        drop_flow(FLOW)
        self.assertNotIn(FLOW, FLOW_STATES)

    def test_drop_flow_is_safe_when_unknown(self):
        drop_flow(FLOW)  # must not raise

    def test_a_reconnect_on_the_same_tuple_starts_clean(self):
        packet = _publish()
        append_stream_data(FLOW, 1000, packet)

        # Same 4-tuple, brand new connection with an unrelated ISN.
        reset_flow(FLOW, initial_seq=900_000)
        delivered = append_stream_data(FLOW, 900_000, packet)
        self.assertEqual(len(delivered), 1, "the new connection must not inherit next_seq")


class TestFrameValidation(unittest.TestCase):
    def test_a_real_inline_pingreq_is_accepted(self):
        """Non-negotiable: the live capture shows the inverter sending these between
        telemetry publishes, and the generic branch is what skips them."""
        self.assertTrue(validate_generic_mqtt_packet(b"\xc0\x00"))

    def test_a_pingreq_carrying_a_body_is_rejected(self):
        self.assertFalse(validate_generic_mqtt_packet(b"\xc0\x2a" + b"x" * 42))

    def test_fixed_length_types_must_match_exactly(self):
        self.assertTrue(validate_generic_mqtt_packet(b"\x40\x02\x00\x01"))     # PUBACK
        self.assertFalse(validate_generic_mqtt_packet(b"\x70\x03\x01\x02\x03"))  # PUBCOMP len 3

    def test_reserved_flag_bits_are_enforced(self):
        self.assertTrue(validate_generic_mqtt_packet(b"\x62\x02\x00\x01"))   # PUBREL flags=2
        self.assertFalse(validate_generic_mqtt_packet(b"\x60\x02\x00\x01"))  # flags=0

    def test_a_zero_packet_identifier_is_rejected(self):
        self.assertFalse(validate_generic_mqtt_packet(b"\x40\x02\x00\x00"))

    def test_a_non_minimal_length_encoding_is_rejected(self):
        """The same value in more bytes is not legal MQTT, and accepting it lets a
        large class of random data pass as a header."""
        self.assertFalse(validate_generic_mqtt_packet(b"\xc0\x80\x00"))


class TestResynchronisation(unittest.TestCase):
    def test_a_mid_stream_desync_recovers_every_later_packet(self):
        """Starting part-way into a packet used to yield nothing at all: bogus frames
        consumed the genuine publishes queued behind them."""
        packet = _publish()
        for offset in (1, 6, 13, 25):
            with self.subTest(offset=offset):
                stream = bytearray((packet * 5)[offset:])
                recovered = [p for p in extract_mqtt_packets_from_stream(stream) if b"device/ABC123" in p]
                self.assertEqual(len(recovered), 4)

    def test_real_packets_are_found_after_leading_garbage(self):
        import random

        packet = _publish()
        for size in (64, 4096, 65536):
            with self.subTest(garbage=size):
                random.seed(11)
                junk = bytes(random.getrandbits(8) for _ in range(size))
                stream = bytearray(junk + packet * 5)
                recovered = [p for p in extract_mqtt_packets_from_stream(stream) if b"device/ABC123" in p]
                self.assertGreaterEqual(recovered and len(recovered) or 0, 4)

    def test_an_incomplete_trailing_packet_is_held_not_discarded(self):
        packet = _publish()
        stream = bytearray(packet + packet[:10])
        recovered = extract_mqtt_packets_from_stream(stream)
        self.assertEqual(len(recovered), 1)
        self.assertEqual(len(stream), 10, "the partial packet must wait for more data")

    def test_a_pingreq_between_publishes_is_skipped_without_losing_either(self):
        packet = _publish()
        stream = bytearray(packet + control_packet(12) + packet)
        types = [(p[0] >> 4) & 0x0F for p in extract_mqtt_packets_from_stream(stream)]
        self.assertEqual(types, [3, 12, 3])


if __name__ == "__main__":
    unittest.main()
