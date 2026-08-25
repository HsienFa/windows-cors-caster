"""NTRIP download handshake and RTCM forwarding lifecycle regressions."""

import os
import socket
import sys
import tempfile
import threading
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
    "log_client_connect",
    "log_client_disconnect",
):
    setattr(logger_stub, _log_name, _no_log)
sys.modules["src.logger"] = logger_stub

_IMPORT_TEMP = tempfile.TemporaryDirectory()
_IMPORT_ROOT = Path(_IMPORT_TEMP.name)
_CONFIG_PATH = _IMPORT_ROOT / "ntrip-download-lifecycle-test.ini"
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
    from src import connection, forwarder, ntrip
finally:
    if _PREVIOUS_CONFIG is None:
        os.environ.pop("NTRIP_CONFIG_FILE", None)
    else:
        os.environ["NTRIP_CONFIG_FILE"] = _PREVIOUS_CONFIG


class ControlledSocket:
    def __init__(self, fail_handshake=False):
        self.fail_handshake = fail_handshake
        self.handshake_started = threading.Event()
        self.release_handshake = threading.Event()
        self.writes = []
        self.closed = False
        self._lock = threading.Lock()

    def settimeout(self, value):
        return None

    def setsockopt(self, *args):
        return None

    def getsockopt(self, *args):
        if self.closed:
            raise OSError("socket closed")
        return 0

    def send(self, data):
        raise AssertionError("download responses and RTCM must use sendall")

    def sendall(self, data):
        is_handshake = data.startswith((b"ICY 200 OK\r\n", b"HTTP/1.1 200 OK\r\n"))
        if is_handshake:
            self.handshake_started.set()
            if self.fail_handshake:
                raise OSError(10038, "simulated closed socket")
            if not self.release_handshake.wait(2.0):
                raise TimeoutError("test did not release handshake")

        with self._lock:
            if self.closed:
                raise OSError(10038, "simulated closed socket")
            self.writes.append(data)

    def shutdown(self, how):
        self.closed = True

    def close(self):
        self.closed = True


class NtripDownloadLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.manager = connection.ConnectionManager()
        self.data_forwarder = forwarder.SimpleDataForwarder()

    def _make_handler(self, client_socket, version="1.0"):
        database = mock.Mock()
        database.check_mount_exists_in_db.return_value = True
        handler = ntrip.NTRIPHandler(client_socket, ("127.0.0.1", 33001), database)
        handler.ntrip_version = version
        handler.protocol_type = "ntrip2_0" if version == "2.0" else "ntrip1_0"
        handler.user_agent = "LandStar"

        def verify_user(mount, auth_header, request_type):
            handler.username = "test-rover"
            return True, "verified"

        handler.verify_user = mock.Mock(side_effect=verify_user)
        return handler

    def _patch_lifecycle(self, add_side_effect=None, activate_side_effect=None):
        add_client = self.data_forwarder.add_client
        if add_side_effect is not None:
            add_client = add_side_effect
        activate = self.data_forwarder.activate_client
        if activate_side_effect is not None:
            activate = activate_side_effect
        return (
            mock.patch.object(
                ntrip.connection,
                "add_user_connection",
                side_effect=self.manager.add_user_connection,
            ),
            mock.patch.object(
                ntrip.connection,
                "remove_user_connection",
                side_effect=self.manager.remove_user_connection,
            ),
            mock.patch.object(
                ntrip.forwarder,
                "add_client",
                side_effect=add_client,
            ),
            mock.patch.object(
                ntrip.forwarder,
                "activate_client",
                side_effect=activate,
            ),
            mock.patch.object(
                ntrip.forwarder,
                "remove_client",
                side_effect=self.data_forwarder.remove_client,
            ),
        )

    def _exercise_ordered_handshake(self, version):
        client_socket = ControlledSocket()
        handler = self._make_handler(client_socket, version)
        keep_entered = threading.Event()
        release_keep = threading.Event()

        def keep_connection_alive():
            keep_entered.set()
            release_keep.wait(2.0)
            handler._cleanup_download_connection()

        handler._keep_connection_alive = keep_connection_alive

        connect_log = mock.Mock()
        disconnect_log = mock.Mock()
        patches = self._patch_lifecycle()
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

        with (
            mock.patch.object(forwarder.logger, "log_client_connect", connect_log),
            mock.patch.object(forwarder.logger, "log_client_disconnect", disconnect_log),
        ):
            worker = threading.Thread(target=handler.handle_download, args=("/TEST", {}))
            worker.start()

            self.assertTrue(client_socket.handshake_started.wait(1.0))
            self.assertEqual(len(self.data_forwarder.clients["TEST"]), 1)
            self.assertFalse(self.data_forwarder.clients["TEST"][0]["active"])
            self.assertEqual(self.data_forwarder.get_client_info("TEST"), [])
            self.assertEqual(self.manager.user_connection_count["test-rover"], 1)

            rtcm = b"\xd3\x00\x03\x01\x02\x03"
            self.data_forwarder.create_mount_buffer("TEST")
            self.data_forwarder.mount_buffers["TEST"].append(rtcm)
            self.data_forwarder._broadcast_data()
            self.assertEqual(client_socket.writes, [])

            client_socket.release_handshake.set()
            self.assertTrue(keep_entered.wait(1.0))
            self.assertEqual(len(self.data_forwarder.get_client_info("TEST")), 1)
            self.data_forwarder._broadcast_data()

            response = client_socket.writes[0]
            expected_prefix = b"HTTP/1.1 200 OK\r\n" if version == "2.0" else b"ICY 200 OK\r\n"
            self.assertTrue(response.startswith(expected_prefix))
            self.assertEqual(client_socket.writes[1], rtcm)
            self.assertEqual(len(client_socket.writes), 2)
            self.assertEqual(connect_log.call_count, 1)

            release_keep.set()
            worker.join(2.0)

        self.assertFalse(worker.is_alive())
        self.assertNotIn("TEST", self.data_forwarder.clients)
        self.assertNotIn("test-rover", self.manager.online_users)
        self.assertTrue(client_socket.closed)
        self.assertEqual(disconnect_log.call_count, 1)

    def test_ntrip_1_response_precedes_rtcm_during_concurrent_broadcast(self):
        self._exercise_ordered_handshake("1.0")

    def test_ntrip_2_response_precedes_rtcm_during_concurrent_broadcast(self):
        self._exercise_ordered_handshake("2.0")

    def test_success_response_failure_rolls_back_every_registration(self):
        client_socket = ControlledSocket(fail_handshake=True)
        handler = self._make_handler(client_socket)
        handler._keep_connection_alive = mock.Mock()

        patches = self._patch_lifecycle()
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

        handler.handle_download("/TEST", {})

        handler._keep_connection_alive.assert_not_called()
        self.assertNotIn("TEST", self.data_forwarder.clients)
        self.assertNotIn("test-rover", self.manager.online_users)
        self.assertTrue(client_socket.closed)

    def test_activation_failure_rolls_back_every_registration(self):
        client_socket = ControlledSocket()
        client_socket.release_handshake.set()
        handler = self._make_handler(client_socket)
        handler._keep_connection_alive = mock.Mock()

        def fail_activation(client_info):
            raise RuntimeError("simulated activation failure")

        patches = self._patch_lifecycle(activate_side_effect=fail_activation)
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

        handler.handle_download("/TEST", {})

        handler._keep_connection_alive.assert_not_called()
        self.assertNotIn("TEST", self.data_forwarder.clients)
        self.assertNotIn("test-rover", self.manager.online_users)
        self.assertTrue(client_socket.closed)

    def test_forwarder_registration_failure_rolls_back_user_connection(self):
        client_socket = ControlledSocket()
        handler = self._make_handler(client_socket)
        handler._keep_connection_alive = mock.Mock()

        def fail_registration(*args, **kwargs):
            raise RuntimeError("simulated forwarder registration failure")

        patches = self._patch_lifecycle(add_side_effect=fail_registration)
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

        handler.handle_download("/TEST", {})

        handler._keep_connection_alive.assert_not_called()
        self.assertNotIn("TEST", self.data_forwarder.clients)
        self.assertNotIn("test-rover", self.manager.online_users)
        self.assertTrue(client_socket.closed)

    def test_ntrip_1_success_response_uses_sendall_and_propagates_failure(self):
        client_socket = ControlledSocket(fail_handshake=True)
        handler = self._make_handler(client_socket)

        with self.assertRaises(OSError):
            handler.send_download_success_response()

    def test_cleanup_is_idempotent_and_does_not_leave_duplicate_users(self):
        client_socket = ControlledSocket()
        client_socket.release_handshake.set()
        handler = self._make_handler(client_socket)
        handler._keep_connection_alive = mock.Mock()

        patches = self._patch_lifecycle()
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

        handler.handle_download("/TEST", {})
        self.assertEqual(len(self.data_forwarder.clients["TEST"]), 1)
        self.assertEqual(self.manager.user_connection_count["test-rover"], 1)

        handler._cleanup_download_connection()
        handler._cleanup_download_connection()

        self.assertNotIn("TEST", self.data_forwarder.clients)
        self.assertNotIn("test-rover", self.manager.online_users)


if __name__ == "__main__":
    unittest.main()
