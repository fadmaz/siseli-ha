"""Coverage for core.py, which had none.

core.py was previously untestable: importing it installed signal handlers and read
/data/state.json at module scope. Both now live in functions called from __main__,
so the module imports cleanly and its packet path can be driven directly.

core.py does `from .config import *`, so every constant is a module attribute on
core itself -- patch there, never on config.
"""

import json
import logging
import os
import tempfile
import threading
import unittest
from unittest import mock

from scapy.all import ARP, AsyncSniffer  # type: ignore
from scapy.error import Scapy_Exception  # type: ignore

from src.siseli_bridge import core
from src.siseli_bridge import mqtt as mqtt_mod
from src.siseli_bridge import parsers as parser_module
from src.siseli_bridge import state as shared_state
from tests.captures import CAPTURE_TELEMETRY
from tests.helpers import FakeMqttClient, envelope, inverter_packet, isolated_state, publish_packet, tcp_segments

INV_IP = "192.168.1.139"
RTR_IP = "192.168.1.1"
CLOUD_IP = "8.212.18.157"
INV_MAC = "aa:bb:cc:dd:ee:01"
RTR_MAC = "aa:bb:cc:dd:ee:02"

NET = dict(
    INVERTER_IP=INV_IP,
    ROUTER_IP=RTR_IP,
    TARGET_HOST=CLOUD_IP,
    TARGET_PORT=1883,
    AUTO_INTERCEPT=True,
    SNIFF_IFACE=None,
    LOG_VERBOSE=False,
    LOG_MQTT_TOPICS=False,
    LOG_MQTT_PAYLOAD_PREVIEW=False,
    LOG_UNPARSED_PUBLISH=False,
)


class _CoreTestCase(unittest.TestCase):
    """Saves and restores every core module global the packet path mutates."""

    def setUp(self):
        self._saved = (
            core.INV_MAC,
            core.RTR_MAC,
            shared_state.RUNNING,
            core.LAST_PACKET_TS,
            set(core.KNOWN_INVERTER_MACS),
            set(core.KNOWN_ROUTER_MACS),
            core.ADAPTIVE_TIMEOUT_LOGGED,
        )
        core.INV_MAC, core.RTR_MAC = INV_MAC, RTR_MAC
        shared_state.RUNNING = True
        core.KNOWN_INVERTER_MACS.clear()
        core.KNOWN_ROUTER_MACS.clear()

        self.sent = []
        p = mock.patch(
            "src.siseli_bridge.core.send_layer2",
            side_effect=lambda frame, iface=None: self.sent.append(frame),
        )
        p.start()
        self.addCleanup(p.stop)

        consts = mock.patch.multiple(core, **NET)
        consts.start()
        self.addCleanup(consts.stop)

        ctx = isolated_state()
        ctx.__enter__()
        self.addCleanup(lambda: ctx.__exit__(None, None, None))

        self.addCleanup(self._restore)

    def _restore(self):
        (
            core.INV_MAC,
            core.RTR_MAC,
            shared_state.RUNNING,
            core.LAST_PACKET_TS,
            known_inv,
            known_rtr,
            core.ADAPTIVE_TIMEOUT_LOGGED,
        ) = self._saved
        core.KNOWN_INVERTER_MACS.clear()
        core.KNOWN_INVERTER_MACS.update(known_inv)
        core.KNOWN_ROUTER_MACS.clear()
        core.KNOWN_ROUTER_MACS.update(known_rtr)


class TestNormMac(unittest.TestCase):
    def test_dashes_and_case_are_normalised(self):
        self.assertEqual(core.norm_mac("AA-BB-CC-DD-EE-01"), "aa:bb:cc:dd:ee:01")

    def test_whitespace_is_stripped(self):
        self.assertEqual(core.norm_mac("  aa:bb:cc:dd:ee:01 "), "aa:bb:cc:dd:ee:01")

    def test_empty_and_none_return_none(self):
        self.assertIsNone(core.norm_mac(""))
        self.assertIsNone(core.norm_mac(None))


class TestLoadCachedState(unittest.TestCase):
    def test_restores_a_saved_dict(self):
        with isolated_state(), tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "state.json")
            with open(path, "w") as f:
                json.dump({"bat_v": 53.4}, f)
            shared_state.LAST_STATE.clear()
            core.load_cached_state(path)
            self.assertEqual(shared_state.LAST_STATE["bat_v"], 53.4)

    def test_keys_this_build_no_longer_defines_are_dropped(self):
        """The undecodable purge only covers keys listed there, and that list must
        name registered sensors -- so a key deleted from SENSORS outright had no purge
        path at all. It was restored, merged into LAST_STATE and republished in the
        retained group payload forever, because publish_grouped_state iterates the
        payload rather than the registry."""
        with isolated_state(), tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "state.json")
            with open(path, "w") as f:
                json.dump({
                    "bat_v": 53.6,
                    "c_bms_remaining_capacity_ah": None,   # deleted in 2.5.17
                    "dbg_wdrr_raw": "231.9 49.9 280 170 65 40 +00000 0 11000 11+00000",
                }, f)
            shared_state.LAST_STATE.clear()
            with mock.patch("src.siseli_bridge.core.log"):
                core.load_cached_state(path)

            self.assertEqual(shared_state.LAST_STATE["bat_v"], 53.6)
            self.assertNotIn("c_bms_remaining_capacity_ah", shared_state.LAST_STATE)
            self.assertNotIn("dbg_wdrr_raw", shared_state.LAST_STATE)

    def test_a_registered_key_is_kept_even_with_no_value(self):
        """util_chg has no writer but is registered, so it is the list's business and
        not this filter's."""
        with isolated_state(), tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "state.json")
            with open(path, "w") as f:
                json.dump({"util_chg": None, "bat_v": 53.6}, f)
            shared_state.LAST_STATE.clear()
            with mock.patch("src.siseli_bridge.core.log"):
                core.load_cached_state(path)
            self.assertIn("util_chg", shared_state.LAST_STATE)

    def test_missing_file_is_not_an_error(self):
        with isolated_state(), tempfile.TemporaryDirectory() as d:
            shared_state.LAST_STATE.clear()
            core.load_cached_state(os.path.join(d, "absent.json"))
            self.assertEqual(shared_state.LAST_STATE, {})

    def test_truncated_file_is_swallowed_and_logged(self):
        """CURRENT BEHAVIOUR: a torn write silently yields an empty state, which
        zeroes the cumulative energy counters."""
        with isolated_state(), tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "state.json")
            with open(path, "w") as f:
                f.write('{"bat_v": 53.4, "c_batt')
            shared_state.LAST_STATE.clear()
            with mock.patch("src.siseli_bridge.core.log") as logged:
                core.load_cached_state(path)
            self.assertEqual(shared_state.LAST_STATE, {})
            self.assertTrue(logged.called)


class TestHandleInverterTcpPacket(_CoreTestCase):
    def test_a_full_publish_reaches_the_parser(self):
        wire = publish_packet("dtu/x/pub", envelope(CAPTURE_TELEMETRY))
        pkt = inverter_packet(wire, src=INV_IP, dst=CLOUD_IP, src_mac=INV_MAC)
        with mock.patch(
            "src.siseli_bridge.core.SolarParser.parse_payload", return_value=True
        ) as parsed:
            core.handle_inverter_tcp_packet(pkt)
        parsed.assert_called_once()

    def test_a_publish_split_across_segments_is_reassembled(self):
        wire = publish_packet("dtu/x/pub", b"x" * 4096)  # forces a multi-byte varint
        with mock.patch(
            "src.siseli_bridge.core.SolarParser.parse_payload", return_value=True
        ) as parsed:
            for seq, chunk in tcp_segments(wire, mss=500):
                core.handle_inverter_tcp_packet(
                    inverter_packet(chunk, src=INV_IP, dst=CLOUD_IP, seq=seq, src_mac=INV_MAC)
                )
        parsed.assert_called_once()

    def test_payload_free_segment_is_ignored(self):
        pkt = inverter_packet(b"", src=INV_IP, dst=CLOUD_IP, src_mac=INV_MAC)
        with mock.patch("src.siseli_bridge.core.SolarParser.parse_payload") as parsed:
            core.handle_inverter_tcp_packet(pkt)
        parsed.assert_not_called()

    def test_non_publish_control_packet_does_not_reach_the_parser(self):
        pkt = inverter_packet(b"\xc0\x00", src=INV_IP, dst=CLOUD_IP, src_mac=INV_MAC)
        with mock.patch("src.siseli_bridge.core.SolarParser.parse_payload") as parsed:
            core.handle_inverter_tcp_packet(pkt)
        parsed.assert_not_called()


class TestPacketCallback(_CoreTestCase):
    def _cloud_packet(self, **kw):
        kw.setdefault("src_mac", INV_MAC)
        return inverter_packet(publish_packet("dtu/x/pub", b"{}"), src=INV_IP, dst=CLOUD_IP, **kw)

    def test_inverter_to_cloud_is_parsed_and_forwarded_to_the_router(self):
        with mock.patch(
            "src.siseli_bridge.core.SolarParser.parse_payload", return_value=True
        ) as parsed:
            core.packet_callback(self._cloud_packet())
        parsed.assert_called_once()
        self.assertEqual(len(self.sent), 1)

    def test_traffic_to_another_destination_is_neither_parsed_nor_forwarded(self):
        pkt = inverter_packet(publish_packet(), src=INV_IP, dst="1.2.3.4", src_mac=INV_MAC)
        with mock.patch("src.siseli_bridge.core.SolarParser.parse_payload") as parsed:
            core.packet_callback(pkt)
        parsed.assert_not_called()
        self.assertEqual(self.sent, [], "non-broker inverter traffic is currently dropped")

    def test_a_frame_claiming_the_inverter_ip_from_another_mac_is_rejected(self):
        with mock.patch("src.siseli_bridge.core.SolarParser.parse_payload") as parsed:
            core.packet_callback(self._cloud_packet(src_mac="de:ad:be:ef:00:01"))
        parsed.assert_not_called()

    def test_router_to_inverter_is_forwarded(self):
        pkt = inverter_packet(b"", src=CLOUD_IP, dst=INV_IP, src_mac=RTR_MAC)
        core.packet_callback(pkt)
        self.assertEqual(len(self.sent), 1)

    def test_nothing_is_forwarded_when_auto_intercept_is_off(self):
        with mock.patch.multiple(core, AUTO_INTERCEPT=False), mock.patch(
            "src.siseli_bridge.core.SolarParser.parse_payload", return_value=True
        ):
            core.packet_callback(self._cloud_packet())
        self.assertEqual(self.sent, [])

    def test_last_packet_timestamp_advances(self):
        core.LAST_PACKET_TS = 0.0
        core.packet_callback(inverter_packet(b"", src=CLOUD_IP, dst=INV_IP, src_mac=RTR_MAC))
        self.assertGreater(core.LAST_PACKET_TS, 0.0)

    def test_known_macs_record_the_senders_seen(self):
        with mock.patch("src.siseli_bridge.core.SolarParser.parse_payload", return_value=True):
            core.packet_callback(self._cloud_packet())
        core.packet_callback(inverter_packet(b"", src=CLOUD_IP, dst=INV_IP, src_mac=RTR_MAC))
        self.assertIn(INV_MAC, core.KNOWN_INVERTER_MACS)
        self.assertIn(RTR_MAC, core.KNOWN_ROUTER_MACS)

    def test_macs_are_learned_from_the_first_frame_when_unset(self):
        core.INV_MAC = None
        core.RTR_MAC = None
        core.packet_callback(inverter_packet(b"", src=CLOUD_IP, dst=INV_IP, src_mac=RTR_MAC))
        self.assertEqual(core.RTR_MAC, RTR_MAC)

    def test_a_rejected_frame_does_not_pollute_the_known_macs(self):
        """Recording happened before the identity guard, so every rejected frame still
        landed in the set the health line reports."""
        with mock.patch("src.siseli_bridge.core.SolarParser.parse_payload") as parsed:
            core.packet_callback(self._cloud_packet(src_mac="de:ad:be:ef:00:01"))
        parsed.assert_not_called()
        self.assertNotIn("de:ad:be:ef:00:01", core.KNOWN_INVERTER_MACS)

    def test_our_own_re_emitted_frames_are_ignored(self):
        """Forwarded frames carry our MAC but the inverter's IP. They used to be
        indistinguishable from real inverter traffic, which is why the live health
        line listed the bridge's own MAC as both an inverter and a router address."""
        with mock.patch("src.siseli_bridge.core.resolve_own_mac", return_value="0a:0b:0c:0d:0e:0f"), \
             mock.patch("src.siseli_bridge.core.SolarParser.parse_payload") as parsed:
            core.packet_callback(self._cloud_packet(src_mac="0a:0b:0c:0d:0e:0f"))
        parsed.assert_not_called()
        self.assertEqual(core.KNOWN_INVERTER_MACS, set())
        self.assertEqual(self.sent, [])

    def test_a_forwarding_failure_does_not_propagate(self):
        with mock.patch(
            "src.siseli_bridge.core.send_layer2", side_effect=OSError("no route")
        ), mock.patch("src.siseli_bridge.core.SolarParser.parse_payload", return_value=True):
            core.packet_callback(self._cloud_packet())  # must not raise

    def test_a_parser_exception_does_not_kill_the_capture_thread(self):
        with mock.patch(
            "src.siseli_bridge.core.SolarParser.parse_payload", side_effect=ValueError("boom")
        ):
            core.packet_callback(self._cloud_packet())  # must not raise
        self.assertEqual(len(self.sent), 1, "forwarding still happens after a parse error")


class TestDeadCaptureThread(_CoreTestCase):
    """The capture thread can end with nothing noticing. The ARP spoofer poisons on
    RUNNING alone and forwarding lives inside packet_callback, so a dead sniffer leaves
    the inverter redirected at a bridge that no longer forwards -- its route to the
    cloud gone, and the first symptom sensors going stale half an hour later,
    indistinguishable from a quiet inverter.

    Nothing local caught this before: the sniffer existed only under __main__, and the
    smoke test ready marker prints whether or not the thread survives.
    """

    class _FakeSniffer:
        def __init__(self, alive, exception=None):
            self.exception = exception
            self.thread = mock.Mock()
            self.thread.is_alive.return_value = alive

        def stop(self):
            # What scapy raises on an already-dead sniffer; shutdown must swallow it.
            raise Scapy_Exception("Not running ! (check .running attr)")

    def setUp(self):
        super().setUp()
        core.CAPTURE_FAILURES = 0
        shared_state.STOP_REQUESTED = False
        self.addCleanup(setattr, core, "CAPTURE_FAILURES", 0)
        self.addCleanup(setattr, shared_state, "STOP_REQUESTED", False)
        patch = mock.patch("src.siseli_bridge.core.time.sleep")
        patch.start()
        self.addCleanup(patch.stop)

    def _with(self, sniffer):
        patch = mock.patch.object(core, "sniffer", sniffer)
        patch.start()
        self.addCleanup(patch.stop)

    # -- liveness ---------------------------------------------------------------

    def test_a_real_unstarted_sniffer_is_not_a_death(self):
        """Against the real dependency rather than a fake: requirements.txt allows
        scapy >=2.5,<2.7, and this pins that .thread stays None until start()."""
        self._with(AsyncSniffer(filter="ip host 127.0.0.1", store=False))
        self.assertFalse(core.capture_thread_is_dead())

    def test_a_created_but_unstarted_thread_is_a_death(self):
        """The one genuinely dangerous window: scapy assigns .thread inside start()
        before calling thread.start(). health_logger sleeps first, which is why it has
        never fired -- the guard must not depend on that timing."""
        holder = self._FakeSniffer(alive=False)
        holder.thread = threading.Thread(target=lambda: None)
        self._with(holder)
        self.assertTrue(core.capture_thread_is_dead())

    def test_no_sniffer_yet_is_not_a_death(self):
        self._with(None)
        self.assertFalse(core.capture_thread_is_dead())

    # -- restart ----------------------------------------------------------------

    def test_a_dead_thread_is_restarted_in_place(self):
        self._with(self._FakeSniffer(alive=False, exception=OSError("socket closed")))
        with mock.patch.object(core, "restart_capture", return_value=True) as restarted:
            acted = core.check_capture_thread()
        self.assertTrue(acted)
        restarted.assert_called_once()
        self.assertFalse(
            shared_state.STOP_REQUESTED, "a recoverable death must not stop the add-on"
        )

    def test_a_healthy_tick_forgives_an_earlier_death(self):
        """Consecutive, so a rare transient does not accumulate toward the limit."""
        core.CAPTURE_FAILURES = 2
        self._with(self._FakeSniffer(alive=True))
        self.assertFalse(core.check_capture_thread())
        self.assertEqual(core.CAPTURE_FAILURES, 0)

    def test_it_gives_up_after_the_limit_and_asks_the_main_thread_to_stop(self):
        self._with(self._FakeSniffer(alive=False))
        with mock.patch.object(core, "restart_capture", return_value=False):
            for _ in range(core.CAPTURE_RESTART_LIMIT):
                core.check_capture_thread()
        self.assertTrue(shared_state.STOP_REQUESTED)

    def test_it_never_calls_shutdown_itself(self):
        """shutdown() spends a second in restore_arp. Run from this daemon thread it is
        killed mid-restore the moment the main loop notices and falls through, which
        truncates the one action that ends the blackhole."""
        self._with(self._FakeSniffer(alive=False))
        with mock.patch.object(core, "restart_capture", return_value=False), mock.patch.object(
            core, "shutdown"
        ) as stopped:
            for _ in range(core.CAPTURE_RESTART_LIMIT):
                core.check_capture_thread()
        stopped.assert_not_called()
        self.assertTrue(shared_state.RUNNING, "RUNNING must stay set so shutdown() still acts")

    def test_a_stop_already_requested_is_not_re_reported(self):
        self._with(self._FakeSniffer(alive=False))
        shared_state.STOP_REQUESTED = True
        self.assertFalse(core.check_capture_thread())

    # -- what it says -----------------------------------------------------------

    def test_the_log_names_the_cause_and_the_impact(self):
        self._with(self._FakeSniffer(alive=False, exception=OSError("socket closed")))
        with mock.patch.object(core, "restart_capture", return_value=True), mock.patch(
            "src.siseli_bridge.core.log"
        ) as logged:
            core.check_capture_thread()
        errors = [c for c in logged.call_args_list if c.kwargs.get("level") == "error"]
        self.assertTrue(errors, "a dead capture thread must be reported at error level")
        said = str(errors[0].args[0])
        self.assertIn("socket closed", said)
        self.assertIn("ARP-poisoned", said)

    def test_a_passive_install_is_not_told_its_inverter_is_cut_off(self):
        """With AUTO_INTERCEPT off the bridge never poisoned anything, so the inverter
        path is untouched. Claiming otherwise sends the reader to their router."""
        self._with(self._FakeSniffer(alive=False))
        with mock.patch.object(core, "AUTO_INTERCEPT", False), mock.patch.object(
            core, "restart_capture", return_value=True
        ), mock.patch("src.siseli_bridge.core.log") as logged:
            core.check_capture_thread()
        said = " ".join(str(c.args[0]) for c in logged.call_args_list if c.args)
        self.assertIn("unaffected", said)
        self.assertNotIn("ARP-poisoned", said)

    def test_the_scapy_warning_is_captured_when_there_is_no_exception(self):
        """The usual death: scapy closes the socket, warns, and returns normally, so
        .exception is None and that warning is the only account of the cause."""
        core.SCAPY_WARNINGS.last = None
        logging.getLogger("scapy.runtime").warning("Sniffing socket closed unexpectedly")
        self.assertEqual(core.SCAPY_WARNINGS.last, "Sniffing socket closed unexpectedly")

        self._with(self._FakeSniffer(alive=False, exception=None))
        with mock.patch.object(core, "restart_capture", return_value=True), mock.patch(
            "src.siseli_bridge.core.log"
        ) as logged:
            core.check_capture_thread()
        said = " ".join(str(c.args[0]) for c in logged.call_args_list if c.args)
        self.assertIn("Sniffing socket closed unexpectedly", said)

    # -- the teardown it asks for -----------------------------------------------

    def test_the_requested_stop_restores_both_peers_in_full(self):
        """What the main thread does once it sees the request. Ten corrective frames,
        each an ARP reply carrying the peers real MACs -- the same assertions
        TestShutdown makes, because a truncated restore is the failure being avoided."""
        self._with(self._FakeSniffer(alive=False))
        with mock.patch.object(core, "publish_availability"), mock.patch.object(core, "client"):
            core.shutdown()
        self.assertEqual(len(self.sent), 10)
        self.assertTrue(all(frame[ARP].op == 2 for frame in self.sent))
        self.assertEqual({frame[ARP].hwsrc for frame in self.sent}, {INV_MAC, RTR_MAC})


class TestShutdown(_CoreTestCase):
    def test_marks_offline_disconnects_and_is_idempotent(self):
        client = FakeMqttClient()
        with mock.patch("src.siseli_bridge.core.client", client), \
             mock.patch("src.siseli_bridge.mqtt.client", client), \
             mock.patch("src.siseli_bridge.core.time.sleep"):
            core.shutdown()
            core.shutdown()

        offline = [p for p in client.published if p.payload == "offline"]
        self.assertEqual(len(offline), 1, "one availability topic covers every entity")
        self.assertTrue(offline[0].retain)
        self.assertTrue(client.disconnected)
        self.assertFalse(shared_state.RUNNING)

    def test_a_broker_failure_during_shutdown_is_swallowed(self):
        client = FakeMqttClient()
        client.raise_on_publish = OSError("broker gone")
        with mock.patch("src.siseli_bridge.core.client", client):
            core.shutdown()  # must not raise
        self.assertFalse(shared_state.RUNNING)

    def test_the_sniffer_is_stopped(self):
        sniffer = mock.Mock()
        client = FakeMqttClient()
        with mock.patch("src.siseli_bridge.core.client", client), mock.patch(
            "src.siseli_bridge.core.sniffer", sniffer
        ):
            core.shutdown()
        sniffer.stop.assert_called_once()

    def test_corrective_arp_is_sent_to_both_peers(self):
        """Without this both caches stay poisoned until they age out, and the inverter
        cannot reach the cloud for that whole window."""
        client = FakeMqttClient()
        with mock.patch("src.siseli_bridge.core.client", client), \
             mock.patch("src.siseli_bridge.mqtt.client", client), \
             mock.patch("src.siseli_bridge.core.time.sleep"):
            core.shutdown()

        self.assertEqual(len(self.sent), 10, "five corrective pairs")
        for frame in self.sent:
            self.assertEqual(int(frame["ARP"].op), 2)
        # hwsrc must name the true peer. The poisoning replies omit it so scapy fills
        # in our own MAC; the corrective ones must not.
        hwsrcs = {frame["ARP"].hwsrc for frame in self.sent}
        self.assertEqual(hwsrcs, {INV_MAC, RTR_MAC})

    def test_no_arp_is_sent_when_interception_is_disabled(self):
        client = FakeMqttClient()
        with mock.patch("src.siseli_bridge.core.client", client), \
             mock.patch("src.siseli_bridge.mqtt.client", client), \
             mock.patch.multiple(core, AUTO_INTERCEPT=False), \
             mock.patch("src.siseli_bridge.core.time.sleep"):
            core.shutdown()
        self.assertEqual(self.sent, [])

    def test_an_arp_failure_does_not_block_the_mqtt_teardown(self):
        client = FakeMqttClient()
        with mock.patch("src.siseli_bridge.core.client", client), \
             mock.patch("src.siseli_bridge.mqtt.client", client), \
             mock.patch("src.siseli_bridge.core.send_layer2", side_effect=OSError("down")), \
             mock.patch("src.siseli_bridge.core.time.sleep"):
            core.shutdown()
        self.assertTrue(client.disconnected)


class TestSignalHandlerInstallation(unittest.TestCase):
    def test_importing_core_does_not_install_handlers(self):
        """Module-level signal.signal() made core.py untestable: it hijacked the
        test runner's SIGINT and raised ValueError off the main thread."""
        import signal

        before = signal.getsignal(signal.SIGINT)
        import importlib

        importlib.reload(core)
        self.assertIs(signal.getsignal(signal.SIGINT), before)

    def test_install_signal_handlers_is_callable(self):
        self.assertTrue(callable(core.install_signal_handlers))


class TestAvailabilityWatchdog(_CoreTestCase):
    """Availability is driven by decoded telemetry age. LAST_PACKET_TS is useless for
    this: it is set for any packet matching the capture filter, bare ACKs included."""

    def setUp(self):
        super().setUp()
        shared_state.AVAILABILITY_ONLINE = True

    def test_fresh_telemetry_keeps_sensors_available(self):
        shared_state.LAST_TELEMETRY_TS = 1000.0
        self.assertTrue(core.telemetry_is_fresh(now=1000.0 + core.TELEMETRY_TIMEOUT_SEC - 1))

    def test_stale_telemetry_marks_sensors_unavailable(self):
        shared_state.LAST_TELEMETRY_TS = 1000.0
        self.assertFalse(core.telemetry_is_fresh(now=1000.0 + core.TELEMETRY_TIMEOUT_SEC + 1))

    def test_the_unavailable_log_names_the_bound_that_actually_fired(self):
        """Before any payload the startup grace governs, not the telemetry timeout.
        Printing the wrong number is the silent-inconsistency shape this repo keeps
        being bitten by."""
        shared_state.LAST_TELEMETRY_TS = 0.0
        shared_state.AVAILABILITY_ONLINE = True
        with mock.patch.object(core, "PROCESS_START_TS", 0.0), mock.patch.object(
            core, "publish_availability"
        ), mock.patch("src.siseli_bridge.core.log") as logged:
            core.availability_watchdog_tick(now=core.STARTUP_GRACE_SEC + 10)
        said = " ".join(str(c.args[0]) for c in logged.call_args_list if c.args)
        self.assertIn(str(core.STARTUP_GRACE_SEC), said)
        self.assertIn("startup", said.lower())

    def test_the_startup_grace_expires_well_inside_the_telemetry_timeout(self):
        """The grace used to be the telemetry timeout itself, so a restart on an install
        whose inverter had gone quiet showed the restored cache as live for 1800 s. It is
        its own, shorter bound now."""
        shared_state.LAST_TELEMETRY_TS = 0.0
        with mock.patch.object(core, "PROCESS_START_TS", 5000.0):
            self.assertTrue(core.telemetry_is_fresh(now=5000.0 + 10))
            self.assertTrue(core.telemetry_is_fresh(now=5000.0 + core.STARTUP_GRACE_SEC - 1))
            self.assertFalse(
                core.telemetry_is_fresh(now=5000.0 + core.STARTUP_GRACE_SEC + 1),
                "cached values are still being asserted live past the grace",
            )
        self.assertLess(
            core.STARTUP_GRACE_SEC,
            core.TELEMETRY_TIMEOUT_SEC,
            "the grace must be shorter than the timeout it used to borrow",
        )

    def test_the_grace_does_not_shadow_the_timeout_once_telemetry_exists(self):
        """Once a payload has landed the normal timeout governs, not the grace."""
        with mock.patch.object(core, "PROCESS_START_TS", 5000.0):
            shared_state.LAST_TELEMETRY_TS = 5000.0
            self.assertTrue(core.telemetry_is_fresh(now=5000.0 + core.STARTUP_GRACE_SEC + 60))

    def test_a_restart_does_not_immediately_blank_every_sensor(self):
        """Without a grace period every restart shows ~200 unavailable entities for
        the whole timeout."""
        shared_state.LAST_TELEMETRY_TS = 0.0
        with mock.patch.object(core, "PROCESS_START_TS", 5000.0):
            self.assertTrue(core.telemetry_is_fresh(now=5010.0))
            self.assertFalse(core.telemetry_is_fresh(now=5000.0 + core.TELEMETRY_TIMEOUT_SEC + 1))

    def test_availability_is_published_only_on_a_transition(self):
        shared_state.LAST_TELEMETRY_TS = 1000.0
        stale = 1000.0 + core.TELEMETRY_TIMEOUT_SEC + 1

        with mock.patch("src.siseli_bridge.core.publish_availability") as pub:
            self.assertIsNone(core.availability_watchdog_tick(now=1000.0))
            pub.assert_not_called()

            self.assertIs(core.availability_watchdog_tick(now=stale), False)
            pub.assert_called_once_with(False)

            pub.reset_mock()
            self.assertIsNone(core.availability_watchdog_tick(now=stale + 1))
            pub.assert_not_called()

            shared_state.LAST_TELEMETRY_TS = stale + 2
            self.assertIs(core.availability_watchdog_tick(now=stale + 2), True)
            pub.assert_called_once_with(True)


class TestAvailabilitySurvivesAReconnect(unittest.TestCase):
    """A reconnect must re-assert the watchdog's verdict, never a literal.

    publish_discovery has to restore availability on connect -- the retained LWT fires
    on an unclean drop, so nothing else would. It published a literal True, so any
    broker restart during a quiet period flipped all ~200 entities back to available,
    showing their last decoded values as live. The watchdog is edge-triggered, so it
    saw no transition and could never take it back; the 600 s heartbeat then kept
    refreshing expire_after, so that backstop never fired either.

    This drives both threads' real entry points in order.
    """

    def setUp(self):
        self.client = FakeMqttClient()
        for target in ("src.siseli_bridge.mqtt.client", "src.siseli_bridge.core.client"):
            patcher = mock.patch(target, self.client)
            patcher.start()
            self.addCleanup(patcher.stop)

        ctx = isolated_state()
        ctx.__enter__()
        self.addCleanup(lambda: ctx.__exit__(None, None, None))

        shared_state.LAST_STATE.clear()
        shared_state.AVAILABILITY_ONLINE = True
        shared_state.RUNNING = True
        self.addCleanup(lambda: setattr(shared_state, "RUNNING", True))
        # Skips the sweep, which would otherwise write the marker file under /data.
        shared_state.DISCOVERY_CLEANED = True
        self.addCleanup(lambda: setattr(shared_state, "DISCOVERY_CLEANED", False))

        core.ADAPTIVE_TIMEOUT_LOGGED = False
        self.addCleanup(lambda: setattr(core, "ADAPTIVE_TIMEOUT_LOGGED", False))

        for target in ("src.siseli_bridge.core.log", "src.siseli_bridge.mqtt.log"):
            patcher = mock.patch(target)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _availability(self):
        return self.client.retained.get(mqtt_mod.AVAILABILITY_TOPIC)

    def test_a_reconnect_does_not_resurrect_a_stale_bridge(self):
        shared_state.LAST_TELEMETRY_TS = 1000.0
        stale = 1000.0 + 4000  # well past the 1800 s default

        self.assertIs(core.availability_watchdog_tick(now=stale), False)
        self.assertEqual(self._availability(), "offline")

        mqtt_mod.on_connect(self.client, None, {}, 0)
        self.assertEqual(
            self._availability(),
            "offline",
            "a reconnect must re-assert the watchdog's verdict, not a literal online",
        )

        self.assertIsNone(
            core.availability_watchdog_tick(now=stale + 10),
            "the watchdog and the broker must still agree after the reconnect",
        )

        shared_state.record_telemetry(stale + 20)
        self.assertIs(core.availability_watchdog_tick(now=stale + 21), True)
        self.assertEqual(self._availability(), "online")

    def test_a_reconnect_while_fresh_still_marks_online(self):
        """The re-assert is load-bearing -- it must not be lost."""
        shared_state.LAST_TELEMETRY_TS = 1000.0
        self.client.retained.pop(mqtt_mod.AVAILABILITY_TOPIC, None)

        mqtt_mod.on_connect(self.client, None, {}, 0)
        self.assertEqual(self._availability(), "online")

    def test_shutdown_is_terminal_for_availability(self):
        """shutdown clears RUNNING, then spends about a second restoring ARP before
        publishing offline. An in-flight tick landing after that would republish
        online onto a client about to disconnect cleanly, which suppresses the LWT."""
        shared_state.LAST_TELEMETRY_TS = 1000.0
        shared_state.AVAILABILITY_ONLINE = False
        shared_state.RUNNING = False

        self.assertIsNone(core.availability_watchdog_tick(now=1010.0))
        self.assertIsNone(self._availability(), "nothing may be published after shutdown")


class TestNonBrokerTraffic(_CoreTestCase):
    """ARP interception makes the add-on the inverter's gateway for everything, but
    only broker traffic was ever relayed -- DNS and NTP were silently blackholed."""

    def setUp(self):
        super().setUp()
        core.DROPPED_NON_TARGET.clear()
        self.addCleanup(core.DROPPED_NON_TARGET.clear)

    def _dns(self):
        from scapy.all import IP, UDP, Ether

        return Ether(src=INV_MAC, dst="0a:0b:0c:0d:0e:0f") / IP(src=INV_IP, dst=RTR_IP) / UDP(dport=53)

    def test_dropped_traffic_is_counted_for_diagnosis(self):
        with mock.patch("src.siseli_bridge.core.resolve_own_mac", return_value="0a:0b:0c:0d:0e:0f"):
            core.packet_callback(self._dns())
        self.assertEqual(core.DROPPED_NON_TARGET.get("UDP:53"), 1)
        self.assertEqual(self.sent, [], "forwarding is opt-in and off by default")

    def test_opt_in_forwarding_relays_the_packet(self):
        with mock.patch.multiple(core, FORWARD_ALL_INVERTER_TRAFFIC=True), \
             mock.patch("src.siseli_bridge.core.resolve_own_mac", return_value="0a:0b:0c:0d:0e:0f"):
            core.packet_callback(self._dns())
        self.assertEqual(len(self.sent), 1)

    def test_broadcast_traffic_is_never_re_emitted(self):
        """Re-emitting it would duplicate what the real router already received."""
        from scapy.all import IP, UDP, Ether

        frame = Ether(src=INV_MAC, dst="ff:ff:ff:ff:ff:ff") / IP(src=INV_IP, dst="255.255.255.255") / UDP(dport=67)
        with mock.patch.multiple(core, FORWARD_ALL_INVERTER_TRAFFIC=True), \
             mock.patch("src.siseli_bridge.core.resolve_own_mac", return_value="0a:0b:0c:0d:0e:0f"):
            core.packet_callback(frame)
        self.assertEqual(self.sent, [])

    def test_broker_traffic_is_not_counted_as_dropped(self):
        with mock.patch("src.siseli_bridge.core.resolve_own_mac", return_value="0a:0b:0c:0d:0e:0f"), \
             mock.patch("src.siseli_bridge.core.SolarParser.parse_payload", return_value=True):
            core.packet_callback(
                inverter_packet(publish_packet("dtu/x", b"{}"), src=INV_IP, dst=CLOUD_IP, src_mac=INV_MAC)
            )
        self.assertEqual(core.DROPPED_NON_TARGET, {})


class TestStartupPath(unittest.TestCase):
    """The startup banner used to live inline in the __main__ body, where no test
    could reach it. A private helper that `from .config import *` does not export was
    referenced there, and the resulting NameError crash-looped the add-on on every
    start -- with a green test suite and a clean lint run.

    ruff cannot catch it either: core.py carries an F405 exemption because the star
    import is load-bearing, and F405 is exactly the rule that would have flagged it.
    Executing the code is the only check that works."""

    def test_logging_the_startup_configuration_does_not_raise(self):
        with mock.patch("src.siseli_bridge.core.log"):
            core.log_startup_configuration()

    def test_it_reports_the_active_debug_flags(self):
        from src.siseli_bridge import config as cfg

        lines = []
        with mock.patch("src.siseli_bridge.core.log", side_effect=lines.append):
            core.log_startup_configuration()
        flags = [ln for ln in lines if "DEBUG_FLAGS" in ln]
        self.assertEqual(len(flags), 1)
        for name in cfg.ACTIVE_DEBUG_FLAGS:
            self.assertIn(name, flags[0])

    def test_no_private_config_name_is_referenced_across_the_star_import(self):
        """`from module import *` skips every name beginning with an underscore, so a
        reference to one resolves at runtime, not at import."""
        import pathlib
        import re

        src_dir = pathlib.Path(core.__file__).parent
        private = set(
            re.findall(r"^_([A-Za-z]\w*)\s*=", (src_dir / "config.py").read_text(encoding="utf-8"), re.M)
        ) | set(
            re.findall(r"^def _([a-z]\w*)\(", (src_dir / "config.py").read_text(encoding="utf-8"), re.M)
        )

        # An empty set would pass every module trivially.
        self.assertTrue(private, "found no private names in config.py to check")

        for module in ("core.py", "mqtt.py", "parsers.py"):
            text = (src_dir / module).read_text(encoding="utf-8")
            if "from .config import *" not in text:
                continue
            for name in sorted(private):
                pattern = r"(?<![\w.])_" + re.escape(name) + r"\b"
                with self.subTest(module=module, name=name):
                    # The pattern must be able to fire at all. It shipped ending in a
                    # literal backspace byte where \b was meant -- a shell heredoc had
                    # turned the escape into the character -- so it could never match,
                    # and this guard passed from the day it was written without
                    # checking anything.
                    self.assertRegex(f"value = _{name}\n", pattern)
                    self.assertNotRegex(
                        text,
                        pattern,
                        f"_{name} is private to config.py and is not exported by the star import",
                    )


class TestAvailabilityAtRealCadence(unittest.TestCase):
    """The availability timeout shipped at 180 s while the inverter reports every
    300 s, with an observed 600 s gap. It therefore fired before every single payload
    -- every entity flapped to Unavailable and back, permanently, on every install.

    The measured cadence is the fixture: a default that cannot survive it is wrong.
    """

    #: Publish times observed on real hardware, in seconds from an arbitrary zero.
    OBSERVED_GAPS = (300, 300, 300, 301, 300, 600)

    def setUp(self):
        ctx = isolated_state()
        ctx.__enter__()
        self.addCleanup(lambda: ctx.__exit__(None, None, None))

    def _flaps_at(self, timeout, measure_cadence=True):
        """Replay the observed cadence and count availability transitions.

        ``measure_cadence=False`` stamps the timestamp directly instead of going
        through ``record_telemetry``, reproducing the behaviour before the watchdog
        learned to floor its timeout on the intervals it observes.
        """
        transitions = []
        now = 1000.0
        shared_state.AVAILABILITY_ONLINE = True
        core.ADAPTIVE_TIMEOUT_LOGGED = False
        shared_state.TELEMETRY_INTERVALS.clear()
        shared_state.LAST_TELEMETRY_TS = 0.0
        shared_state.record_telemetry(now)
        with mock.patch.multiple(core, TELEMETRY_TIMEOUT_SEC=timeout), mock.patch(
            "src.siseli_bridge.core.publish_availability"
        ), mock.patch("src.siseli_bridge.core.log"):
            for gap in self.OBSERVED_GAPS:
                # Tick every 10 s across the gap, as the watchdog thread does.
                for step in range(10, gap + 1, 10):
                    result = core.availability_watchdog_tick(now + step)
                    if result is not None:
                        transitions.append(result)
                now += gap
                if measure_cadence:
                    shared_state.record_telemetry(now)
                else:
                    shared_state.LAST_TELEMETRY_TS = now
        return transitions

    def test_the_shipped_default_survives_the_observed_cadence(self):
        from src.siseli_bridge import config as cfg

        self.assertEqual(
            self._flaps_at(cfg.TELEMETRY_TIMEOUT_SEC),
            [],
            "the default timeout must not mark sensors unavailable at the cadence a "
            "real inverter actually reports",
        )

    def test_the_old_default_flaps_when_the_cadence_is_not_measured(self):
        """Documents why 180 s was wrong, so it is not chosen again."""
        self.assertTrue(
            self._flaps_at(180, measure_cadence=False),
            "180s is expected to flap at this cadence",
        )

    def test_a_stored_180_stops_flapping_once_the_cadence_is_measured(self):
        """The bug a user actually hit on 2.6.5.

        Supervisor pins an option the first time the configuration page is saved, so
        an install that stored 2.6.1's 180 s keeps it however high later releases set
        the default. The watchdog therefore has to protect itself from its own
        configuration. One transition pair before the first interval is known is
        unavoidable; after that the floor holds.
        """
        transitions = self._flaps_at(180)
        self.assertEqual(
            transitions,
            [False, True],
            "a stored 180s must settle after the first measured interval, not flap "
            "on every payload",
        )

    def test_a_genuine_stall_is_still_detected(self):
        from src.siseli_bridge import config as cfg

        shared_state.AVAILABILITY_ONLINE = True
        shared_state.LAST_TELEMETRY_TS = 1000.0
        with mock.patch.multiple(core, TELEMETRY_TIMEOUT_SEC=cfg.TELEMETRY_TIMEOUT_SEC), \
             mock.patch("src.siseli_bridge.core.publish_availability"), \
             mock.patch("src.siseli_bridge.core.log"):
            stalled = core.availability_watchdog_tick(1000.0 + cfg.TELEMETRY_TIMEOUT_SEC + 1)
        self.assertIs(stalled, False, "a real stall must still be reported")


class TestAdaptiveTelemetryTimeout(_CoreTestCase):
    """The watchdog floors its timeout on the cadence it measures.

    A configured timeout cannot be trusted on its own: Supervisor pins an option's
    value the first time the user saves the configuration page, and a pinned value
    shadows every later change to the shipped default. Raising the default in
    config.yaml fixes fresh installs only.
    """

    def setUp(self):
        super().setUp()
        core.ADAPTIVE_TIMEOUT_LOGGED = False
        shared_state.TELEMETRY_INTERVALS.clear()
        shared_state.LAST_TELEMETRY_TS = 0.0

    def test_no_history_reports_no_observed_interval(self):
        self.assertEqual(core.observed_telemetry_interval(), 0.0)

    def test_record_telemetry_measures_the_gap(self):
        shared_state.record_telemetry(1000.0)
        shared_state.record_telemetry(1300.0)
        shared_state.record_telemetry(1900.0)
        self.assertEqual(list(shared_state.TELEMETRY_INTERVALS), [300.0, 600.0])
        self.assertEqual(core.observed_telemetry_interval(), 600.0)

    def test_the_first_payload_records_no_interval(self):
        shared_state.record_telemetry(1000.0)
        self.assertEqual(list(shared_state.TELEMETRY_INTERVALS), [])

    def test_a_generous_configured_timeout_is_left_alone(self):
        shared_state.record_telemetry(1000.0)
        shared_state.record_telemetry(1300.0)
        with mock.patch.multiple(core, TELEMETRY_TIMEOUT_SEC=1800):
            self.assertEqual(core.effective_telemetry_timeout(), 1800.0)

    def test_a_too_small_configured_timeout_is_floored(self):
        shared_state.record_telemetry(1000.0)
        shared_state.record_telemetry(1600.0)  # a 600 s gap, as observed live
        with mock.patch.multiple(core, TELEMETRY_TIMEOUT_SEC=180),              mock.patch("src.siseli_bridge.core.log"):
            self.assertEqual(core.effective_telemetry_timeout(), 1800.0)

    def test_an_overnight_gap_cannot_produce_an_absurd_timeout(self):
        """Without the ceiling an inverter switched off for eight hours would leave
        the watchdog unable to report a real outage for a day."""
        shared_state.record_telemetry(1000.0)
        shared_state.record_telemetry(1000.0 + 8 * 3600)
        with mock.patch.multiple(core, TELEMETRY_TIMEOUT_SEC=180),              mock.patch("src.siseli_bridge.core.log"):
            self.assertEqual(
                core.effective_telemetry_timeout(),
                float(core.TELEMETRY_TIMEOUT_CEILING_SEC),
            )

    def test_the_adjustment_is_logged_once(self):
        shared_state.record_telemetry(1000.0)
        shared_state.record_telemetry(1600.0)
        with mock.patch.multiple(core, TELEMETRY_TIMEOUT_SEC=180),              mock.patch("src.siseli_bridge.core.log") as logged:
            core.effective_telemetry_timeout()
            core.effective_telemetry_timeout()
        self.assertEqual(logged.call_count, 1)
        message = logged.call_args[0][0]
        self.assertIn("180", message)
        self.assertIn("1800", message)

    def test_a_real_outage_is_still_detected_under_the_floor(self):
        shared_state.record_telemetry(1000.0)
        shared_state.record_telemetry(1600.0)
        with mock.patch.multiple(core, TELEMETRY_TIMEOUT_SEC=180),              mock.patch("src.siseli_bridge.core.log"):
            self.assertTrue(core.telemetry_is_fresh(now=1600.0 + 1799))
            self.assertFalse(core.telemetry_is_fresh(now=1600.0 + 1801))


class TestHeartbeatIsTimerDriven(unittest.TestCase):
    """The heartbeat republish lived inside parse_payload, so it could only fire when
    a payload arrived -- precisely when it was not needed. With a 600 s gap and a
    shorter expiry window, Home Assistant expired the sensors while the bridge was
    perfectly healthy."""

    def setUp(self):
        ctx = isolated_state()
        ctx.__enter__()
        self.addCleanup(lambda: ctx.__exit__(None, None, None))

    def test_it_becomes_due_without_any_payload_arriving(self):
        parser_module.LAST_PUBLISH_TS = 1000.0
        with mock.patch.multiple(
            parser_module, EXPIRE_AFTER_SEC=1800, UPDATE_INTERVAL_SEC=10
        ):
            self.assertFalse(parser_module.heartbeat_due(1000.0 + 100))
            self.assertTrue(parser_module.heartbeat_due(1000.0 + 601))

    def test_it_fires_well_inside_the_expiry_window(self):
        """Whatever the window, the republish interval has to be a fraction of it."""
        for window in (600, 1800, 3600):
            with self.subTest(expire_after=window):
                parser_module.LAST_PUBLISH_TS = 0.0
                with mock.patch.multiple(
                    parser_module, EXPIRE_AFTER_SEC=window, UPDATE_INTERVAL_SEC=10
                ):
                    interval = max(10, window // 3)
                    self.assertTrue(parser_module.heartbeat_due(interval))
                    self.assertLess(interval, window)

    def test_disabling_expiry_disables_the_heartbeat(self):
        parser_module.LAST_PUBLISH_TS = 0.0
        with mock.patch.multiple(parser_module, EXPIRE_AFTER_SEC=0):
            self.assertFalse(parser_module.heartbeat_due(999999))

    def test_republish_sends_the_retained_state(self):
        shared_state.DISCOVERY_PUBLISHED = True
        shared_state.LAST_STATE.clear()
        shared_state.LAST_STATE.update({"bat_v": 53.7})
        publish = mock.Mock()
        with mock.patch.object(
            parser_module, "_get_mqtt_publish", return_value=(mock.Mock(), publish)
        ):
            self.assertTrue(parser_module.republish_state(now=123.0))
        publish.assert_called_once()
        self.assertEqual(parser_module.LAST_PUBLISH_TS, 123.0)

    def test_nothing_is_republished_before_discovery(self):
        shared_state.DISCOVERY_PUBLISHED = False
        self.assertFalse(parser_module.republish_state(now=1.0))


class TestCachedFabricationsArePurged(unittest.TestCase):
    """Stage B stopped generating the fabricated sensors but never removed the ones
    already written to /data/state.json. They were restored on every start and
    republished, so on a live installation `mode` still read "Battery Mode" three
    releases after the code that invented it was deleted."""

    def test_undecodable_keys_are_dropped_on_load(self):
        from src.siseli_bridge.sensors import UNDECODED_SENSOR_KEYS

        with isolated_state(), tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "state.json")
            with open(path, "w") as f:
                json.dump(
                    {
                        "bat_v": 53.7,
                        "mode": "Battery Mode",
                        "overloaded": "No",
                        "bms_communication_normal": "Yes",
                    },
                    f,
                )
            shared_state.LAST_STATE.clear()
            with mock.patch("src.siseli_bridge.core.log"):
                core.load_cached_state(path)

            self.assertEqual(shared_state.LAST_STATE["bat_v"], 53.7, "real values survive")
            for key in ("mode", "overloaded", "bms_communication_normal"):
                with self.subTest(key=key):
                    self.assertIn(key, UNDECODED_SENSOR_KEYS)
                    self.assertNotIn(key, shared_state.LAST_STATE)

    def test_the_discard_is_reported(self):
        with isolated_state(), tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "state.json")
            with open(path, "w") as f:
                json.dump({"mode": "Battery Mode"}, f)
            shared_state.LAST_STATE.clear()
            lines = []
            with mock.patch("src.siseli_bridge.core.log", side_effect=lambda m, **k: lines.append(m)):
                core.load_cached_state(path)
            self.assertTrue(any("no decode path" in line for line in lines))


class TestStartupPrintsTheForwardingMode(unittest.TestCase):
    """AUTO_INTERCEPT and FORWARD_ALL_INVERTER_TRAFFIC together decide what is relayed,
    and FORWARD_ALL was never printed, so no log -- the reference captures included --
    showed which mode was running. Both are pinned options, so the running value can
    differ from the shipped default."""

    def test_both_forwarding_options_are_printed_on_one_line(self):
        lines = []
        with mock.patch("src.siseli_bridge.core.log", side_effect=lines.append):
            core.log_startup_configuration()
        mode = [ln for ln in lines if "AUTO_INTERCEPT=" in ln]
        self.assertEqual(len(mode), 1)
        self.assertIn(f"FORWARD_ALL_INVERTER_TRAFFIC={core.FORWARD_ALL_INVERTER_TRAFFIC}", mode[0])


if __name__ == "__main__":
    unittest.main()
