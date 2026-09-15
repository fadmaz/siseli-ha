import time
import unittest


from src.siseli_local_bridge.parsers import (
    FLOW_STATES,
    TcpFlowState,
    _evict_stale_flows,
    get_flow_state,
    append_stream_data,
)


class TestFlowEviction(unittest.TestCase):
    """Tests that stale TCP flow state is pruned to bound memory usage."""

    def setUp(self):
        FLOW_STATES.clear()

    def tearDown(self):
        FLOW_STATES.clear()

    def _key(self, sport=1234):
        return ("10.0.0.1", sport, "10.0.0.2", 1883)

    def test_stale_flow_removed_by_eviction(self):
        key = self._key()
        state = get_flow_state(key)
        state.last_seen = time.monotonic() - 200  # well past STREAM_STALE_SECONDS (30)

        _evict_stale_flows()

        self.assertNotIn(key, FLOW_STATES)

    def test_active_flow_preserved_by_eviction(self):
        key = self._key()
        get_flow_state(key)  # last_seen = now

        _evict_stale_flows()

        self.assertIn(key, FLOW_STATES)

    def test_selective_eviction_keeps_active_removes_stale(self):
        active_key = self._key(sport=1)
        stale_key = self._key(sport=2)

        get_flow_state(active_key)
        get_flow_state(stale_key).last_seen = time.monotonic() - 200

        _evict_stale_flows()

        self.assertIn(active_key, FLOW_STATES)
        self.assertNotIn(stale_key, FLOW_STATES)

    def test_stale_flow_resets_on_next_access(self):
        key = self._key()
        state = get_flow_state(key)
        state.next_seq = 9999
        # The reset keys off last_progress, not last_seen. Using last_seen meant a
        # flow wedged behind a missing segment kept itself alive indefinitely,
        # because parking a segment counts as activity.
        state.last_progress = time.monotonic() - 200

        fresh = get_flow_state(key)  # should call state.reset()

        self.assertIsNone(fresh.next_seq)

    def test_eviction_counter_triggers_prune(self):
        """Driving get_flow_state enough times should auto-evict without manual call."""
        from src.siseli_local_bridge import parsers

        stale_key = self._key(sport=9999)
        state = TcpFlowState()
        state.last_seen = time.monotonic() - 200
        FLOW_STATES[stale_key] = state

        # Force the counter to trigger on the next get_flow_state call
        parsers._FLOW_EVICT_COUNTER = parsers._FLOW_EVICT_INTERVAL - 1

        get_flow_state(self._key(sport=1))  # This increments counter and triggers eviction

        self.assertNotIn(stale_key, FLOW_STATES)

    def test_stream_data_accumulates_correctly(self):
        """Existing reassembly logic still works after eviction refactor."""
        key = self._key()
        packet = b"\x30\x06\x00\x03a/b\x99"
        packets = append_stream_data(key, 0, packet)
        self.assertEqual(len(packets), 1)
        self.assertEqual(packets[0], packet)


class TestFlowStateBehavior(unittest.TestCase):
    """Tests for TcpFlowState fundamentals."""

    def test_reset_clears_all_state(self):
        state = TcpFlowState()
        state.next_seq = 100
        state.pending[200] = b"data"
        state.stream.extend(b"hello")

        state.reset()

        self.assertIsNone(state.next_seq)
        self.assertEqual(len(state.pending), 0)
        self.assertEqual(len(state.stream), 0)


if __name__ == "__main__":
    unittest.main()
