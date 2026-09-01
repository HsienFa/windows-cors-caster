"""Bounded, fail-open RTCM parser startup readiness tests."""

import socket
import os
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from unittest import mock


def _no_log(*args, **kwargs):
    return None


logger_stub = types.ModuleType("src.logger")
logger_stub.__getattr__ = lambda name: _no_log
for _log_name in (
    "log_debug",
    "log_info",
    "log_warning",
    "log_error",
    "log_critical",
):
    setattr(logger_stub, _log_name, _no_log)

pyproj_stub = types.ModuleType("pyproj")
pyproj_stub.Transformer = mock.Mock()

_IMPORT_TEMP = tempfile.TemporaryDirectory()
_IMPORT_ROOT = Path(_IMPORT_TEMP.name)
_CONFIG_PATH = _IMPORT_ROOT / "rtcm-readiness-test.ini"
_CONFIG_PATH.write_text(
    "[network]\n"
    "host = 127.0.0.1\n"
    "[ntrip]\n"
    "port = 2101\n"
    "[web]\n"
    "port = 5757\n"
    "[database]\n"
    f"path = {(_IMPORT_ROOT / 'test.db').as_posix()}\n"
    "[logging]\n"
    f"log_dir = {(_IMPORT_ROOT / 'logs').as_posix()}\n"
    "level = CRITICAL\n",
    encoding="utf-8",
)

_PREVIOUS_CONFIG = os.environ.get("NTRIP_CONFIG_FILE")
os.environ["NTRIP_CONFIG_FILE"] = str(_CONFIG_PATH)
try:
    sys.modules["src.logger"] = logger_stub
    sys.modules["pyproj"] = pyproj_stub
    from src import rtcm2
    from src.rtcm2_manager import RTCM2ParserManager
finally:
    if _PREVIOUS_CONFIG is None:
        os.environ.pop("NTRIP_CONFIG_FILE", None)
    else:
        os.environ["NTRIP_CONFIG_FILE"] = _PREVIOUS_CONFIG


class PollingReader:
    """Reader double that lets the parser observe stop without consuming data."""

    def __init__(self, stream):
        self.stream = stream

    def __next__(self):
        time.sleep(0.005)
        raise socket.timeout()


class RTCMParserReadinessTests(unittest.TestCase):
    def _start_polling_parser(self, mount="TEST"):
        parser = rtcm2.RTCMParserThread(mount, duration=30)
        parser.start()
        self.assertEqual(parser.wait_for_startup(0.5), rtcm2.PARSER_STARTUP_READY)
        return parser

    def test_ready_is_not_reported_before_subscriber_registration(self):
        register_entered = threading.Event()
        allow_registration = threading.Event()

        def blocked_register(mount, subscriber):
            register_entered.set()
            allow_registration.wait(1.0)

        with (
            mock.patch.object(rtcm2.forwarder, "register_subscriber", side_effect=blocked_register),
            mock.patch.object(rtcm2.forwarder, "unregister_subscriber"),
            mock.patch.object(rtcm2, "RTCMReader", PollingReader),
        ):
            parser = rtcm2.RTCMParserThread("TEST")
            parser.start()
            self.assertTrue(register_entered.wait(0.5))
            self.assertEqual(parser.wait_for_startup(0.02), rtcm2.PARSER_STARTUP_TIMEOUT)
            self.assertEqual(parser.startup_state, rtcm2.PARSER_STARTUP_PENDING)

            allow_registration.set()
            self.assertEqual(parser.wait_for_startup(0.5), rtcm2.PARSER_STARTUP_READY)
            self.assertIsNone(parser.stop())

    def test_ready_is_reported_only_after_rtcm_reader_is_constructed(self):
        reader_constructed = threading.Event()

        class ObservedReader(PollingReader):
            def __init__(self, stream):
                super().__init__(stream)
                reader_constructed.set()

        with (
            mock.patch.object(rtcm2.forwarder, "register_subscriber"),
            mock.patch.object(rtcm2.forwarder, "unregister_subscriber"),
            mock.patch.object(rtcm2, "RTCMReader", ObservedReader),
        ):
            parser = self._start_polling_parser()
            self.assertTrue(reader_constructed.is_set())
            self.assertEqual(parser.startup_state, rtcm2.PARSER_STARTUP_READY)
            self.assertIsNone(parser.stop())

    def test_subscriber_registration_failure_sets_failed_and_closes_pipes(self):
        with (
            mock.patch.object(
                rtcm2.forwarder,
                "register_subscriber",
                side_effect=RuntimeError("synthetic register failure"),
            ),
            mock.patch.object(rtcm2.forwarder, "unregister_subscriber") as unregister,
        ):
            parser = rtcm2.RTCMParserThread("TEST")
            parser.start()
            self.assertEqual(parser.wait_for_startup(0.5), rtcm2.PARSER_STARTUP_FAILED)
            parser.join(0.5)

        self.assertFalse(parser.is_alive())
        unregister.assert_not_called()
        self.assertEqual(parser.pipe_r.fileno(), -1)
        self.assertEqual(parser.pipe_w.fileno(), -1)

    def test_exception_before_registration_still_wakes_waiter_as_failed(self):
        with (
            mock.patch.object(
                rtcm2,
                "log_info",
                side_effect=[RuntimeError("synthetic startup log failure"), None],
            ),
            mock.patch.object(rtcm2.forwarder, "register_subscriber") as register,
            mock.patch.object(rtcm2.forwarder, "unregister_subscriber") as unregister,
        ):
            parser = rtcm2.RTCMParserThread("TEST")
            parser.start()
            self.assertEqual(parser.wait_for_startup(0.5), rtcm2.PARSER_STARTUP_FAILED)
            parser.join(0.5)

        register.assert_not_called()
        unregister.assert_not_called()
        self.assertFalse(parser.is_alive())

    def test_synchronous_thread_start_failure_marks_failed_and_closes_pipes(self):
        created_parsers = []
        real_parser_class = rtcm2.RTCMParserThread

        def capture_parser(*args, **kwargs):
            parser = real_parser_class(*args, **kwargs)
            created_parsers.append(parser)
            parser.start = mock.Mock(side_effect=RuntimeError("synthetic thread start failure"))
            return parser

        with mock.patch.object(rtcm2, "RTCMParserThread", side_effect=capture_parser):
            with self.assertRaisesRegex(RuntimeError, "synthetic thread start failure"):
                rtcm2.start_str_fix_parser("TEST")

        parser = created_parsers[0]
        self.assertEqual(parser.startup_state, rtcm2.PARSER_STARTUP_FAILED)
        self.assertTrue(parser.startup_complete.is_set())
        self.assertEqual(parser.pipe_r.fileno(), -1)
        self.assertEqual(parser.pipe_w.fileno(), -1)

    def test_rtcm_reader_failure_never_registers_subscriber_and_sets_failed(self):
        active_subscribers = set()

        def register(mount, subscriber):
            active_subscribers.add(subscriber)

        def unregister(mount, subscriber):
            active_subscribers.discard(subscriber)

        with (
            mock.patch.object(rtcm2.forwarder, "register_subscriber", side_effect=register) as register_mock,
            mock.patch.object(rtcm2.forwarder, "unregister_subscriber", side_effect=unregister) as unregister_mock,
            mock.patch.object(rtcm2, "RTCMReader", side_effect=RuntimeError("synthetic reader failure")),
        ):
            parser = rtcm2.RTCMParserThread("TEST")
            parser.start()
            self.assertEqual(parser.wait_for_startup(0.5), rtcm2.PARSER_STARTUP_FAILED)
            parser.join(0.5)

        self.assertFalse(parser.is_alive())
        self.assertEqual(active_subscribers, set())
        register_mock.assert_not_called()
        unregister_mock.assert_not_called()

    def test_stop_before_ready_wakes_waiter_and_cleans_subscriber(self):
        reader_entered = threading.Event()
        allow_reader = threading.Event()
        active_subscribers = set()

        def register(mount, subscriber):
            active_subscribers.add(subscriber)

        def unregister(mount, subscriber):
            active_subscribers.discard(subscriber)

        class BlockedReader(PollingReader):
            def __init__(self, stream):
                reader_entered.set()
                allow_reader.wait(1.0)
                super().__init__(stream)

        with (
            mock.patch.object(rtcm2.forwarder, "register_subscriber", side_effect=register),
            mock.patch.object(rtcm2.forwarder, "unregister_subscriber", side_effect=unregister),
            mock.patch.object(rtcm2, "RTCMReader", BlockedReader),
        ):
            parser = rtcm2.RTCMParserThread("TEST")
            parser.start()
            self.assertTrue(reader_entered.wait(0.5))
            stop_result = []
            stopper = threading.Thread(target=lambda: stop_result.append(parser.stop()))
            stopper.start()

            self.assertEqual(parser.wait_for_startup(0.2), rtcm2.PARSER_STARTUP_STOPPED)
            allow_reader.set()
            stopper.join(1.0)

        self.assertEqual(stop_result, [None])
        self.assertEqual(active_subscribers, set())

    def test_timeout_is_bounded_and_does_not_complete_pending_state(self):
        register_entered = threading.Event()
        allow_registration = threading.Event()

        def blocked_register(mount, subscriber):
            register_entered.set()
            allow_registration.wait(1.0)

        with (
            mock.patch.object(rtcm2.forwarder, "register_subscriber", side_effect=blocked_register),
            mock.patch.object(rtcm2.forwarder, "unregister_subscriber"),
            mock.patch.object(rtcm2, "RTCMReader", PollingReader),
        ):
            parser = rtcm2.RTCMParserThread("TEST")
            parser.start()
            self.assertTrue(register_entered.wait(0.5))
            started = time.monotonic()
            state = parser.wait_for_startup(0.05)
            elapsed = time.monotonic() - started

            self.assertEqual(state, rtcm2.PARSER_STARTUP_TIMEOUT)
            self.assertLess(elapsed, 0.2)
            self.assertEqual(parser.startup_state, rtcm2.PARSER_STARTUP_PENDING)

            allow_registration.set()
            self.assertEqual(parser.wait_for_startup(0.5), rtcm2.PARSER_STARTUP_READY)
            self.assertIsNone(parser.stop())

    def test_timeout_then_late_ready_does_not_replay_initial_data(self):
        register_entered = threading.Event()
        allow_registration = threading.Event()
        active_subscribers = set()
        forwarded = []

        def blocked_register(mount, subscriber):
            register_entered.set()
            allow_registration.wait(1.0)
            active_subscribers.add(subscriber)

        def unregister(mount, subscriber):
            active_subscribers.discard(subscriber)

        def forward_once(data):
            forwarded.append(data)
            return len(active_subscribers)

        with (
            mock.patch.object(rtcm2.forwarder, "register_subscriber", side_effect=blocked_register),
            mock.patch.object(rtcm2.forwarder, "unregister_subscriber", side_effect=unregister),
            mock.patch.object(rtcm2, "RTCMReader", PollingReader),
        ):
            parser = rtcm2.RTCMParserThread("TEST")
            parser.start()
            self.assertTrue(register_entered.wait(0.5))
            self.assertEqual(parser.wait_for_startup(0.02), rtcm2.PARSER_STARTUP_TIMEOUT)

            self.assertEqual(forward_once(b"synthetic-initial"), 0)
            allow_registration.set()
            self.assertEqual(parser.wait_for_startup(0.5), rtcm2.PARSER_STARTUP_READY)
            self.assertEqual(forward_once(b"synthetic-subsequent"), 1)
            self.assertIsNone(parser.stop())

        self.assertEqual(
            forwarded,
            [b"synthetic-initial", b"synthetic-subsequent"],
        )

    def test_normal_parser_replacement_cleans_previous_subscriber(self):
        manager = RTCM2ParserManager()
        active_subscribers = set()
        maximum_subscribers = 0
        subscriber_lock = threading.Lock()

        def register(mount, subscriber):
            nonlocal maximum_subscribers
            with subscriber_lock:
                active_subscribers.add(subscriber)
                maximum_subscribers = max(maximum_subscribers, len(active_subscribers))

        def unregister(mount, subscriber):
            with subscriber_lock:
                active_subscribers.discard(subscriber)

        with (
            mock.patch.object(rtcm2.forwarder, "register_subscriber", side_effect=register),
            mock.patch.object(rtcm2.forwarder, "unregister_subscriber", side_effect=unregister),
            mock.patch.object(rtcm2, "RTCMReader", PollingReader),
        ):
            self.assertTrue(manager.start_parser("TEST"))
            self.assertEqual(manager.wait_for_parser_startup("TEST", 0.5), "ready")
            first_parser = manager.parsers["TEST"]

            self.assertTrue(manager.start_parser("TEST"))
            self.assertFalse(first_parser.is_alive())
            self.assertEqual(manager.wait_for_parser_startup("TEST", 0.5), "ready")
            self.assertEqual(maximum_subscribers, 1)
            self.assertIsNone(manager.stop_parser("TEST"))

        self.assertEqual(active_subscribers, set())

    def test_generic_replacement_keeps_main_policy_when_stop_returns_false(self):
        manager = RTCM2ParserManager()
        existing = mock.Mock()
        existing.stop.return_value = False
        manager.parsers["TEST"] = existing
        manager.str_parsers["TEST"] = existing
        replacement = mock.Mock()

        with mock.patch.object(
            rtcm2,
            "start_str_fix_parser",
            return_value=replacement,
        ) as start_parser:
            self.assertTrue(manager.start_parser("TEST"))

        existing.stop.assert_called_once_with()
        start_parser.assert_called_once()
        self.assertIs(manager.parsers["TEST"], replacement)
        self.assertIs(manager.str_parsers["TEST"], replacement)

    def test_web_replacement_keeps_main_policy_and_does_not_wait(self):
        manager = RTCM2ParserManager()
        existing = mock.Mock()
        existing.stop.return_value = False
        replacement = mock.Mock()
        manager.parsers["TEST"] = existing
        manager.web_parsers["TEST"] = existing
        manager.current_web_mount = "TEST"

        with mock.patch.object(
            rtcm2,
            "start_web_parser",
            return_value=replacement,
        ):
            self.assertTrue(manager.start_realtime_parsing("TEST"))

        existing.stop.assert_called_once_with()
        replacement.wait_for_startup.assert_not_called()
        self.assertIs(manager.parsers["TEST"], replacement)
        self.assertIs(manager.web_parsers["TEST"], replacement)

    def test_manager_releases_lock_before_waiting_for_readiness(self):
        manager = RTCM2ParserManager()

        class LockProbeParser:
            def wait_for_startup(self, timeout):
                acquired = manager.lock.acquire(blocking=False)
                if acquired:
                    manager.lock.release()
                return "ready" if acquired else "lock-held"

        manager.parsers["TEST"] = LockProbeParser()
        self.assertEqual(manager.wait_for_parser_startup("TEST", 0.5), "ready")

    def test_start_parser_default_does_not_wait_for_readiness(self):
        manager = RTCM2ParserManager()
        parser = mock.Mock()

        with mock.patch.object(rtcm2, "start_str_fix_parser", return_value=parser):
            self.assertTrue(manager.start_parser("TEST"))

        parser.wait_for_startup.assert_not_called()


if __name__ == "__main__":
    unittest.main()
