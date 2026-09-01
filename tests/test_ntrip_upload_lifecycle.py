"""NTRIP SOURCE upload socket lifecycle regression tests."""

import hashlib
import os
import inspect
import socket
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    import psutil  # noqa: F401
except ModuleNotFoundError:
    psutil_stub = types.ModuleType("psutil")
    psutil_stub.net_connections = lambda kind="tcp": []
    sys.modules["psutil"] = psutil_stub


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
    "log_system_event",
    "log_mount_operation",
):
    setattr(logger_stub, _log_name, _no_log)
sys.modules["src.logger"] = logger_stub

_IMPORT_TEMP = tempfile.TemporaryDirectory()
_IMPORT_ROOT = Path(_IMPORT_TEMP.name)
_CONFIG_PATH = _IMPORT_ROOT / "ntrip-lifecycle-test.ini"
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
    "level = CRITICAL\n"
    "[tcp]\n"
    "keepalive_enabled = false\n"
    "socket_timeout = 2\n",
    encoding="utf-8",
)

_PREVIOUS_CONFIG = os.environ.get("NTRIP_CONFIG_FILE")
os.environ["NTRIP_CONFIG_FILE"] = str(_CONFIG_PATH)
try:
    from pyrtcm import RTCMReader, calc_crc24q, crc2bytes, len2bytes
    from src import connection, ntrip
    from src.network_utils import ConnectionInspectionResult
finally:
    if _PREVIOUS_CONFIG is None:
        os.environ.pop("NTRIP_CONFIG_FILE", None)
    else:
        os.environ["NTRIP_CONFIG_FILE"] = _PREVIOUS_CONFIG


SOURCE_HEADER = (
    b"SOURCE /TEST\r\n"
    b"Source-Agent: synthetic-framing-test\r\n\r\n"
)


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _make_synthetic_rtcm3_frame(station_id=0):
    """Build a coordinate-free synthetic RTCM 1005 frame with valid CRC24Q."""
    payload_bit_length = 152
    payload_value = (1005 << (payload_bit_length - 12)) | (
        station_id << (payload_bit_length - 24)
    )
    payload = payload_value.to_bytes(payload_bit_length // 8, "big")
    message_without_crc = b"\xd3" + len2bytes(payload) + payload
    return message_without_crc + crc2bytes(message_without_crc)


class ScriptedRecvSocket:
    """Minimal socket double that returns deterministic recv chunks in order."""

    def __init__(self, recv_chunks, events):
        self.recv_chunks = list(recv_chunks)
        self.events = events
        self.writes = []
        self.closed = False

    def settimeout(self, value):
        return None

    def setsockopt(self, *args):
        return None

    def recv(self, size):
        if not self.recv_chunks:
            return b""
        item = self.recv_chunks.pop(0)
        if isinstance(item, BaseException):
            raise item
        if len(item) > size:
            self.recv_chunks.insert(0, item[size:])
            return item[:size]
        return item

    def send(self, data):
        self._record_write(data)
        return len(data)

    def sendall(self, data):
        self._record_write(data)

    def _record_write(self, data):
        self.writes.append(data)
        if data.startswith((b"ICY 200 OK", b"HTTP/1.1 200 OK")):
            self.events.append("handshake")

    def shutdown(self, how):
        self.events.append("cleanup")

    def close(self):
        self.closed = True


class NtripUploadLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.manager = connection.ConnectionManager()
        self.manager._generate_initial_str = mock.Mock()
        self.manager.start_str_correction = mock.Mock()

    def test_ntrip_1_source_stays_online_and_receives_rtcm_after_icy_response(self):
        caster_socket, source_socket = socket.socketpair()
        self.addCleanup(source_socket.close)
        received_data = []
        data_received = threading.Event()
        logged_errors = []

        def capture_upload(mount, data):
            received_data.append((mount, data))
            data_received.set()

        handler = ntrip.NTRIPHandler(caster_socket, ("127.0.0.1", 32001), mock.Mock())
        handler.verify_user = mock.Mock(return_value=(True, "verified"))
        handler.user_agent = "BD970"
        handler.ntrip_version = "1.0"
        self.manager.update_mount_data_stats = mock.Mock()

        patches = [
            mock.patch.object(ntrip.connection, "get_connection_manager", return_value=self.manager),
            mock.patch.object(ntrip.forwarder, "upload_data", side_effect=capture_upload),
            mock.patch.object(ntrip.forwarder, "remove_mount_buffer"),
            mock.patch.object(
                ntrip.logger,
                "log_error",
                side_effect=lambda message, *args, **kwargs: logged_errors.append(str(message)),
            ),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

        worker = threading.Thread(target=handler.handle_upload, args=("/TEST", {}))
        worker.start()

        response = source_socket.recv(1024)
        self.assertEqual(response, b"ICY 200 OK\r\n\r\n")
        self.assertTrue(self.manager.is_mount_online("TEST"))

        rtcm_bytes = b"\xd3\x00\x03\x01\x02\x03"
        source_socket.sendall(rtcm_bytes)
        self.assertTrue(data_received.wait(1.0), "RTCM bytes were not received")
        time.sleep(0.05)
        self.assertTrue(self.manager.is_mount_online("TEST"))
        self.assertIn(("TEST", rtcm_bytes), received_data)
        self.assertFalse(any("10038" in message for message in logged_errors))

        source_socket.shutdown(socket.SHUT_WR)
        worker.join(3.0)
        self.assertFalse(worker.is_alive())
        self.assertFalse(self.manager.is_mount_online("TEST"))

    def test_stale_cleanup_cannot_close_reconnected_source_socket(self):
        old_socket, old_peer = socket.socketpair()
        new_socket, new_peer = socket.socketpair()
        self.addCleanup(old_peer.close)
        self.addCleanup(new_peer.close)
        self.addCleanup(new_socket.close)

        success, _ = self.manager.add_mount_connection(
            "TEST", "127.0.0.1", client_socket=old_socket, start_correction=False
        )
        self.assertTrue(success)
        self.assertTrue(
            self.manager.remove_mount_connection("TEST", expected_socket=old_socket)
        )

        success, _ = self.manager.add_mount_connection(
            "TEST", "127.0.0.1", client_socket=new_socket, start_correction=False
        )
        self.assertTrue(success)

        self.assertFalse(
            self.manager.remove_mount_connection("TEST", expected_socket=old_socket)
        )
        self.assertTrue(self.manager.is_mount_online("TEST"))
        self.assertGreaterEqual(new_socket.fileno(), 0)

        self.manager.remove_mount_connection("TEST", expected_socket=new_socket)

    def test_tcp_snapshot_does_not_close_an_open_source_socket(self):
        caster_socket, source_socket = socket.socketpair()
        self.addCleanup(source_socket.close)
        self.addCleanup(caster_socket.close)
        success, _ = self.manager.add_mount_connection(
            "TEST", "127.0.0.1", client_socket=caster_socket, start_correction=False
        )
        self.assertTrue(success)

        empty_snapshot = ConnectionInspectionResult(True, frozenset())
        with mock.patch.object(connection, "inspect_established_remote_ips", return_value=empty_snapshot):
            self.assertTrue(self.manager.cleanup_zombie_connections())

        self.assertTrue(self.manager.is_mount_online("TEST"))
        self.assertGreaterEqual(caster_socket.fileno(), 0)
        self.manager.remove_mount_connection("TEST", expected_socket=caster_socket)

    def test_handler_cleanup_is_synchronous_and_idempotent(self):
        caster_socket, source_socket = socket.socketpair()
        self.addCleanup(source_socket.close)
        handler = ntrip.NTRIPHandler(caster_socket, ("127.0.0.1", 32002), mock.Mock())
        handler.mount = "TEST"
        handler.mount_connection_established = True
        success, _ = self.manager.add_mount_connection(
            "TEST", "127.0.0.1", client_socket=caster_socket, start_correction=False
        )
        self.assertTrue(success)

        with (
            mock.patch.object(ntrip.connection, "get_connection_manager", return_value=self.manager),
            mock.patch.object(ntrip.forwarder, "remove_mount_buffer") as remove_buffer,
        ):
            handler._cleanup()
            handler._cleanup()

        self.assertFalse(self.manager.is_mount_online("TEST"))
        self.assertEqual(caster_socket.fileno(), -1)
        remove_buffer.assert_called_once_with("TEST")

    def test_ntrip_2_upload_success_uses_keep_alive_response(self):
        handler = ntrip.NTRIPHandler.__new__(ntrip.NTRIPHandler)
        handler.ntrip_version = "2.0"
        handler.protocol_type = "ntrip2_0"
        handler.client_socket = mock.Mock()

        handler.send_upload_success_response()

        response = handler.client_socket.sendall.call_args.args[0]
        self.assertTrue(response.startswith(b"HTTP/1.1 200 OK\r\n"))
        self.assertIn(b"Connection: keep-alive\r\n", response)

    def test_str_initialization_starts_only_after_success_response(self):
        source = inspect.getsource(ntrip.NTRIPHandler.handle_upload)

        self.assertIn("start_correction=False", source)
        self.assertLess(
            source.index("self.send_upload_success_response()"),
            source.index("manager.start_str_correction("),
        )
        self.assertIn("wait_for_ready=True", source)
        self.assertIn("NTRIP_PARSER_STARTUP_EVENT_WAIT_TIMEOUT_SECONDS", source)

    def test_non_ntrip_correction_caller_does_not_wait_for_readiness(self):
        self.manager.online_mounts["TEST"] = mock.Mock()
        waiter_thread = mock.Mock()

        with (
            mock.patch.object(connection.rtcm_manager, "start_parser", return_value=True),
            mock.patch.object(connection.rtcm_manager, "wait_for_parser_startup") as wait_for_startup,
            mock.patch.object(connection.threading, "Thread", return_value=waiter_thread),
        ):
            result = connection.ConnectionManager.start_str_correction(
                self.manager,
                "TEST",
            )

        self.assertIsNone(result)
        wait_for_startup.assert_not_called()
        waiter_thread.start.assert_called_once_with()

    def test_ntrip_readiness_wait_preserves_start_correction_none_return(self):
        self.manager.online_mounts["TEST"] = mock.Mock()
        waiter_thread = mock.Mock()

        with (
            mock.patch.object(connection.rtcm_manager, "start_parser", return_value=True),
            mock.patch.object(
                connection.rtcm_manager,
                "wait_for_parser_startup",
                return_value="timeout",
            ) as wait_for_startup,
            mock.patch.object(connection.threading, "Thread", return_value=waiter_thread),
        ):
            result = connection.ConnectionManager.start_str_correction(
                self.manager,
                "TEST",
                wait_for_ready=True,
                startup_event_wait_timeout=0.5,
            )

        self.assertIsNone(result)
        wait_for_startup.assert_called_once_with("TEST", 0.5)
        waiter_thread.start.assert_called_once_with()

    def test_receive_loop_has_no_delayed_mount_name_only_cleanup(self):
        source = (PROJECT_ROOT / "src" / "ntrip.py").read_text(encoding="utf-8")

        self.assertNotIn("threading.Timer(1.5", source)
        self.assertIn("expected_socket=self.client_socket", source)


class NtripSourceTcpFramingTests(unittest.TestCase):
    def setUp(self):
        self.manager = connection.ConnectionManager()
        self.manager._generate_initial_str = mock.Mock()
        self.manager.cleanup_zombie_connections = mock.Mock(return_value=True)
        self.manager.force_refresh_connections = mock.Mock()
        self.manager.update_mount_data_stats = mock.Mock()
        self.manager.start_str_correction = mock.Mock()

    def _run_source_session(self, recv_chunks, correction_result="ready", correction_error=None):
        events = []
        client_socket = ScriptedRecvSocket(recv_chunks, events)
        handler = ntrip.NTRIPHandler(
            client_socket,
            ("127.0.0.1", 32003),
            mock.Mock(),
        )

        def verify_user(*args, **kwargs):
            events.append("authentication")
            return True, "verified"

        def start_correction(mount, **kwargs):
            events.append("correction_started")
            self.last_correction_call = (mount, kwargs)
            if correction_error is not None:
                raise correction_error
            return correction_result

        forwarded_chunks = []

        def capture_upload(mount, data):
            events.append("forwarding")
            forwarded_chunks.append(data)

        handler.verify_user = mock.Mock(side_effect=verify_user)
        self.manager.start_str_correction = mock.Mock(side_effect=start_correction)

        with (
            mock.patch.object(
                ntrip.connection,
                "get_connection_manager",
                return_value=self.manager,
            ),
            mock.patch.object(
                ntrip.forwarder,
                "upload_data",
                side_effect=capture_upload,
            ),
            mock.patch.object(ntrip.forwarder, "remove_mount_buffer"),
        ):
            handler.handle_request()

        handler.verify_user.assert_called_once()
        self.assertEqual(len(client_socket.writes), 1)
        self.assertTrue(client_socket.writes[0].startswith(b"ICY 200 OK\r\n"))
        self.manager.start_str_correction.assert_called_once_with(
            "TEST",
            wait_for_ready=True,
            startup_event_wait_timeout=(
                ntrip.NTRIP_PARSER_STARTUP_EVENT_WAIT_TIMEOUT_SECONDS
            ),
        )
        self.assertLess(events.index("authentication"), events.index("handshake"))
        self.assertLess(events.index("handshake"), events.index("correction_started"))
        if "forwarding" in events:
            self.assertLess(events.index("correction_started"), events.index("forwarding"))
        self.assertLess(events.index("correction_started"), events.index("cleanup"))
        self.assertFalse(self.manager.is_mount_online("TEST"))
        self.assertTrue(client_socket.closed)

        self.last_forwarded_chunks = list(forwarded_chunks)
        return b"".join(forwarded_chunks)

    def _assert_valid_synthetic_frame(self, frame, station_id):
        self.assertEqual(calc_crc24q(frame), 0)
        parsed = RTCMReader.parse(frame)
        self.assertEqual(parsed.identity, "1005")
        self.assertEqual(parsed.DF003, station_id)

    def _assert_forwarded_bytes_unchanged(self, expected, actual, scenario):
        details = (
            f"{scenario}: expected_len={len(expected)}, actual_len={len(actual)}, "
            f"expected_sha256={_sha256(expected)}, actual_sha256={_sha256(actual)}"
        )
        self.assertEqual(len(actual), len(expected), details)
        self.assertEqual(_sha256(actual), _sha256(expected), details)

    def test_source_header_and_complete_rtcm_frame_in_one_recv_preserves_tail(self):
        frame = _make_synthetic_rtcm3_frame(station_id=1)
        self._assert_valid_synthetic_frame(frame, station_id=1)

        forwarded = self._run_source_session([SOURCE_HEADER + frame])

        self._assert_forwarded_bytes_unchanged(
            frame,
            forwarded,
            "complete initial RTCM tail",
        )

    def test_source_header_and_partial_rtcm_frame_in_one_recv_preserves_tail(self):
        frame = _make_synthetic_rtcm3_frame(station_id=2)
        self._assert_valid_synthetic_frame(frame, station_id=2)
        split_at = 8

        forwarded = self._run_source_session(
            [SOURCE_HEADER + frame[:split_at], frame[split_at:]]
        )

        self._assert_forwarded_bytes_unchanged(
            frame,
            forwarded,
            "partial initial RTCM tail",
        )

    def test_fragmented_source_header_never_enters_forwarding(self):
        frame = _make_synthetic_rtcm3_frame(station_id=3)
        self._assert_valid_synthetic_frame(frame, station_id=3)
        split_at = SOURCE_HEADER.index(b"synthetic-framing") + len(b"synthetic")
        header_prefix = SOURCE_HEADER[:split_at]
        header_suffix = SOURCE_HEADER[split_at:]

        forwarded = self._run_source_session(
            [header_prefix, header_suffix + frame]
        )

        self._assert_forwarded_bytes_unchanged(
            frame,
            forwarded,
            "fragmented SOURCE header",
        )

    def test_first_rtcm_frame_fragmented_after_header_is_byte_exact(self):
        frame = _make_synthetic_rtcm3_frame(station_id=4)
        self._assert_valid_synthetic_frame(frame, station_id=4)

        forwarded = self._run_source_session(
            [SOURCE_HEADER, frame[:5], frame[5:13], frame[13:]]
        )

        self._assert_forwarded_bytes_unchanged(
            frame,
            forwarded,
            "fragmented RTCM frame after header",
        )
        self._assert_valid_synthetic_frame(forwarded, station_id=4)

    def test_multiple_rtcm_frames_in_initial_recv_are_byte_exact(self):
        first_frame = _make_synthetic_rtcm3_frame(station_id=5)
        second_frame = _make_synthetic_rtcm3_frame(station_id=6)
        self._assert_valid_synthetic_frame(first_frame, station_id=5)
        self._assert_valid_synthetic_frame(second_frame, station_id=6)
        expected = first_frame + second_frame

        forwarded = self._run_source_session([SOURCE_HEADER + expected])

        self._assert_forwarded_bytes_unchanged(
            expected,
            forwarded,
            "multiple initial RTCM frames",
        )

    def test_readiness_timeout_is_bounded_and_initial_tail_is_forwarded_once(self):
        frame = _make_synthetic_rtcm3_frame(station_id=8)

        forwarded = self._run_source_session(
            [SOURCE_HEADER + frame],
            correction_result="timeout",
        )

        self._assert_forwarded_bytes_unchanged(frame, forwarded, "readiness timeout")
        self.assertEqual(self.last_forwarded_chunks, [frame])
        mount, kwargs = self.last_correction_call
        self.assertEqual(mount, "TEST")
        self.assertTrue(kwargs["wait_for_ready"])
        self.assertEqual(
            kwargs["startup_event_wait_timeout"],
            ntrip.NTRIP_PARSER_STARTUP_EVENT_WAIT_TIMEOUT_SECONDS,
        )
        self.assertLessEqual(kwargs["startup_event_wait_timeout"], 0.5)

    def test_parser_start_failure_states_do_not_block_initial_tail(self):
        frame = _make_synthetic_rtcm3_frame(station_id=9)

        for startup_state in ("failed", "stopped"):
            with self.subTest(startup_state=startup_state):
                forwarded = self._run_source_session(
                    [SOURCE_HEADER + frame],
                    correction_result=startup_state,
                )
                self._assert_forwarded_bytes_unchanged(
                    frame,
                    forwarded,
                    f"parser {startup_state}",
                )
                self.assertEqual(self.last_forwarded_chunks, [frame])

    def test_synchronous_parser_start_exception_is_fail_open(self):
        frame = _make_synthetic_rtcm3_frame(station_id=10)

        forwarded = self._run_source_session(
            [SOURCE_HEADER + frame],
            correction_error=RuntimeError("synthetic parser startup failure"),
        )

        self._assert_forwarded_bytes_unchanged(frame, forwarded, "parser exception")
        self.assertEqual(self.last_forwarded_chunks, [frame])

    def test_fragmented_get_header_routes_only_after_complete_boundary(self):
        events = []
        client_socket = ScriptedRecvSocket(
            [
                b"GET /base HTTP/1.0\r\nUser-Agent: NTRIP synthetic",
                b"/1.0\r\n\r\n",
            ],
            events,
        )
        handler = ntrip.NTRIPHandler(
            client_socket,
            ("127.0.0.1", 32004),
            mock.Mock(),
        )
        handler.handle_download = mock.Mock()
        handler.send_error_response = mock.Mock()

        handler.handle_request()

        handler.handle_download.assert_called_once()
        self.assertEqual(handler.handle_download.call_args.args[0], "/base")
        self.assertEqual(handler.protocol_type, "ntrip1_0_http")
        handler.send_error_response.assert_not_called()
        client_socket.close()

    def test_get_header_and_initial_rover_gga_in_one_recv_preserve_body(self):
        initial_gga = b"$GPGGA,SYNTHETIC-NO-POSITION*00\r\n"
        expected_length = len(initial_gga)
        expected_hash = _sha256(initial_gga)
        client_socket = ScriptedRecvSocket(
            [
                b"GET /base HTTP/1.0\r\n"
                b"User-Agent: NTRIP synthetic/1.0\r\n\r\n"
                + initial_gga
            ],
            [],
        )
        handler = ntrip.NTRIPHandler(
            client_socket,
            ("127.0.0.1", 32005),
            mock.Mock(),
        )
        observed_initial_data = []

        def capture_download(path, headers):
            observed_initial_data.append(handler._initial_rover_data)

        handler.handle_download = mock.Mock(side_effect=capture_download)
        handler.send_error_response = mock.Mock()

        handler.handle_request()

        handler.handle_download.assert_called_once()
        self.assertEqual(len(observed_initial_data), 1)
        self.assertEqual(len(observed_initial_data[0]), expected_length)
        self.assertEqual(_sha256(observed_initial_data[0]), expected_hash)
        handler.send_error_response.assert_not_called()
        client_socket.close()

    def test_unauthenticated_source_tail_never_enters_forwarding(self):
        frame = _make_synthetic_rtcm3_frame(station_id=7)
        self._assert_valid_synthetic_frame(frame, station_id=7)
        events = []
        client_socket = ScriptedRecvSocket([SOURCE_HEADER + frame], events)
        handler = ntrip.NTRIPHandler(
            client_socket,
            ("127.0.0.1", 32006),
            mock.Mock(),
        )
        handler.verify_user = mock.Mock(return_value=(False, "rejected"))
        handler.send_auth_challenge = mock.Mock()
        upload_data = mock.Mock()

        with (
            mock.patch.object(
                ntrip.connection,
                "get_connection_manager",
                return_value=self.manager,
            ),
            mock.patch.object(ntrip.forwarder, "upload_data", upload_data),
            mock.patch.object(ntrip.forwarder, "remove_mount_buffer"),
        ):
            handler.handle_request()

        handler.verify_user.assert_called_once()
        handler.send_auth_challenge.assert_called_once_with("rejected")
        upload_data.assert_not_called()
        self.manager.start_str_correction.assert_not_called()
        self.assertNotIn("handshake", events)
        self.assertFalse(self.manager.is_mount_online("TEST"))
        self.assertTrue(client_socket.closed)


if __name__ == "__main__":
    unittest.main()
