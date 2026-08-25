"""NTRIP SOURCE upload socket lifecycle regression tests."""

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
    from src import connection, ntrip
    from src.network_utils import ConnectionInspectionResult
finally:
    if _PREVIOUS_CONFIG is None:
        os.environ.pop("NTRIP_CONFIG_FILE", None)
    else:
        os.environ["NTRIP_CONFIG_FILE"] = _PREVIOUS_CONFIG


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
            source.index("manager.start_str_correction(mount)"),
        )

    def test_receive_loop_has_no_delayed_mount_name_only_cleanup(self):
        source = (PROJECT_ROOT / "src" / "ntrip.py").read_text(encoding="utf-8")

        self.assertNotIn("threading.Timer(1.5", source)
        self.assertIn("expected_socket=self.client_socket", source)


if __name__ == "__main__":
    unittest.main()
