"""NTRIP download handshake and RTCM forwarding lifecycle regressions."""

import base64
import contextlib
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
    def __init__(self, fail_handshake=False, request_data=b"", recv_items=None):
        self.fail_handshake = fail_handshake
        self.request_data = request_data
        self.recv_items = list(recv_items or [])
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

    def recv(self, size):
        if self.request_data:
            data = self.request_data
            self.request_data = b""
            return data
        if self.recv_items:
            item = self.recv_items.pop(0)
            if isinstance(item, BaseException):
                raise item
            return item
        return b""

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


class BlockingRoverSocket(ControlledSocket):
    def __init__(self):
        super().__init__()
        self.recv_started = threading.Event()
        self.recv_released = threading.Event()

    def recv(self, size):
        self.recv_started.set()
        self.recv_released.wait(2.0)
        raise OSError(10038, "simulated socket shutdown")

    def shutdown(self, how):
        super().shutdown(how)
        self.recv_released.set()

    def close(self):
        super().close()
        self.recv_released.set()


def nmea_sentence(payload):
    checksum = 0
    for character in payload.encode("ascii"):
        checksum ^= character
    return f"${payload}*{checksum:02X}\r\n".encode("ascii")


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

    @staticmethod
    def _basic_header():
        credential = ":".join(("rover-user", "test-credential"))
        encoded = base64.b64encode(credential.encode("utf-8")).decode("ascii")
        return f"Basic {encoded}", encoded

    def _request_handler(self, request_text, authentication_valid=True):
        client_socket = ControlledSocket(request_data=request_text.encode("ascii"))
        client_socket.release_handshake.set()
        database = mock.Mock()
        database.check_mount_exists_in_db.return_value = True
        database.verify_download_user.return_value = (
            authentication_valid,
            "verified" if authentication_valid else "invalid credentials",
        )
        handler = ntrip.NTRIPHandler(
            client_socket,
            ("127.0.0.1", 33002),
            database,
        )
        handler._keep_connection_alive = mock.Mock()
        return handler, client_socket, database

    def _patch_lifecycle(
        self,
        add_side_effect=None,
        activate_side_effect=None,
        update_gga_side_effect=None,
    ):
        add_client = self.data_forwarder.add_client
        if add_side_effect is not None:
            add_client = add_side_effect
        activate = self.data_forwarder.activate_client
        if activate_side_effect is not None:
            activate = activate_side_effect
        update_gga = self.manager.update_rover_gga
        if update_gga_side_effect is not None:
            update_gga = update_gga_side_effect
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
            mock.patch.object(
                ntrip.connection,
                "update_rover_gga",
                side_effect=update_gga,
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

    def test_connection_ids_are_unique_and_rover_freshness_expires(self):
        first_id = self.manager.add_user_connection(
            "test-rover",
            "TEST",
            "127.0.0.1",
        )
        second_id = self.manager.add_user_connection(
            "test-rover",
            "TEST",
            "127.0.0.1",
        )

        self.assertNotEqual(first_id, second_id)
        self.assertEqual(len(first_id), 32)
        self.assertEqual(len(second_id), 32)
        int(first_id, 16)
        int(second_id, 16)

        updated = self.manager.update_rover_gga(
            "test-rover",
            first_id,
            {
                "latitude": 25.0618933333,
                "longitude": 121.6457533333,
                "gga_fix_quality": 4,
                "satellites": 20,
                "hdop": 0.6,
                "altitude": 50.2,
                "has_valid_position": True,
            },
            received_at=100.0,
        )

        self.assertTrue(updated)
        fresh = self.manager.get_rover_status(now=130.0)
        stale = self.manager.get_rover_status(now=130.001)
        first_fresh = next(item for item in fresh if item["connection_id"] == first_id)
        first_stale = next(item for item in stale if item["connection_id"] == first_id)
        second_status = next(item for item in fresh if item["connection_id"] == second_id)
        self.assertEqual(first_fresh["last_gga_time"], "1970-01-01T00:01:40Z")
        self.assertEqual(first_fresh["gga_age_seconds"], 30.0)
        self.assertTrue(first_fresh["position_fresh"])
        self.assertFalse(first_stale["position_fresh"])
        self.assertIsNone(second_status["gga_age_seconds"])
        self.assertFalse(second_status["has_valid_position"])
        self.assertFalse(second_status["position_fresh"])
        self.assertNotIn("client_socket", first_fresh)

    def test_ntrip_1_initial_gga_is_processed_after_activation(self):
        auth_header, _ = self._basic_header()
        gga = nmea_sentence(
            "GPGGA,123519,2503.7136,N,12138.7452,E,4,20,0.6,50.2,M,0.0,M,,"
        )
        request = (
            "GET /base HTTP/1.0\r\n"
            "Host: caster.example.invalid:2101\r\n"
            "User-Agent: NTRIP rover/1.0\r\n"
            f"Authorization: {auth_header}\r\n"
            "\r\n"
        ).encode("ascii") + gga
        handler, client_socket, database = self._request_handler(
            request.decode("ascii")
        )

        def update_after_activation(username, connection_id, gga_data, received_at=None):
            self.assertTrue(client_socket.writes[0].startswith(b"ICY 200 OK\r\n"))
            self.assertTrue(self.data_forwarder.clients["base"][0]["active"])
            return self.manager.update_rover_gga(
                username,
                connection_id,
                gga_data,
                received_at,
            )

        patches = self._patch_lifecycle(
            update_gga_side_effect=update_after_activation,
        )
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

        with mock.patch.object(ntrip.connection, "get_user_connection_count", return_value=0):
            handler.handle_request()

        self.assertEqual(handler.protocol_type, "ntrip1_0_http")
        database.verify_download_user.assert_called_once()
        status = self.manager.get_rover_status()[0]
        self.assertEqual(status["gga_fix_quality"], 4)
        self.assertTrue(status["has_valid_position"])
        handler._cleanup_download_connection()

    def test_ntrip_2_gga_header_is_processed_without_logging_position(self):
        auth_header, _ = self._basic_header()
        gga = nmea_sentence(
            "GNGGA,123519,2503.7136,N,12138.7452,E,5,18,0.8,50.2,M,0.0,M,,"
        ).decode("ascii").strip()
        request = (
            "GET /base HTTP/1.1\r\n"
            "Host: caster.example.invalid:2101\r\n"
            "User-Agent: NTRIP rover/2.0\r\n"
            "Ntrip-Version: Ntrip/2.0\r\n"
            f"Ntrip-GGA: {gga}\r\n"
            f"Authorization: {auth_header}\r\n"
            "\r\n"
        )
        handler, _, _ = self._request_handler(request)
        logged_messages = []
        patches = self._patch_lifecycle()
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

        with (
            mock.patch.object(ntrip.connection, "get_user_connection_count", return_value=0),
            mock.patch.object(
                ntrip,
                "log_debug",
                side_effect=lambda message, *args, **kwargs: logged_messages.append(str(message)),
            ),
        ):
            handler.handle_request()

        self.assertEqual(handler.protocol_type, "ntrip2_0")
        status = self.manager.get_rover_status()[0]
        self.assertEqual(status["gga_fix_quality"], 5)
        self.assertNotIn(gga, "\n".join(logged_messages))
        self.assertNotIn("2503.7136", "\n".join(logged_messages))
        handler._cleanup_download_connection()

    def test_streaming_gga_updates_state_and_no_gga_timeout_is_non_fatal(self):
        gga = nmea_sentence(
            "GPGGA,123519,2503.7136,N,12138.7452,E,4,20,0.6,50.2,M,0.0,M,,"
        )
        for recv_items, expected_updates in (
            ([gga, b""], 1),
            ([socket.timeout(), b""], 0),
        ):
            with self.subTest(expected_updates=expected_updates):
                client_socket = ControlledSocket(recv_items=recv_items)
                client_socket.release_handshake.set()
                handler = self._make_handler(client_socket)
                update_calls = []

                def record_update(username, connection_id, gga_data, received_at=None):
                    update_calls.append(gga_data)
                    return self.manager.update_rover_gga(
                        username,
                        connection_id,
                        gga_data,
                        received_at,
                    )

                patches = self._patch_lifecycle(update_gga_side_effect=record_update)
                with contextlib.ExitStack() as stack:
                    for patcher in patches:
                        stack.enter_context(patcher)
                    handler.handle_download("/TEST", {})

                self.assertEqual(len(update_calls), expected_updates)
                self.assertTrue(client_socket.writes[0].startswith(b"ICY 200 OK\r\n"))

    def test_gga_parser_failure_does_not_prevent_rtcm_send(self):
        client_socket = ControlledSocket()
        client_socket.release_handshake.set()
        handler = self._make_handler(client_socket)
        valid = nmea_sentence(
            "GPGGA,123519,2503.7136,N,12138.7452,E,4,20,0.6,50.2,M,0.0,M,,"
        )
        invalid_checksum = valid[:-4] + (
            b"00" if valid[-4:-2] != b"00" else b"FF"
        ) + b"\r\n"
        rtcm = b"\xd3\x00\x03\x01\x02\x03"

        def receive_invalid_gga_then_broadcast():
            self.assertEqual(handler._consume_rover_gga(invalid_checksum), 0)
            self.data_forwarder.create_mount_buffer("TEST")
            self.data_forwarder.mount_buffers["TEST"].append(rtcm)
            self.data_forwarder._broadcast_data()
            handler._cleanup_download_connection()

        handler._keep_connection_alive = receive_invalid_gga_then_broadcast
        patches = self._patch_lifecycle()
        with contextlib.ExitStack() as stack:
            for patcher in patches:
                stack.enter_context(patcher)
            handler.handle_download("/TEST", {})

        self.assertTrue(client_socket.writes[0].startswith(b"ICY 200 OK\r\n"))
        self.assertEqual(client_socket.writes[1], rtcm)

    def test_socket_shutdown_releases_blocked_rover_recv(self):
        client_socket = BlockingRoverSocket()
        client_socket.release_handshake.set()
        handler = self._make_handler(client_socket)
        patches = self._patch_lifecycle()
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

        worker = threading.Thread(
            target=handler.handle_download,
            args=("/TEST", {}),
        )
        worker.start()
        self.assertTrue(client_socket.recv_started.wait(1.0))

        client_socket.shutdown(socket.SHUT_RDWR)
        client_socket.close()
        worker.join(2.0)

        self.assertFalse(worker.is_alive())
        self.assertNotIn("TEST", self.data_forwarder.clients)
        self.assertNotIn("test-rover", self.manager.online_users)

    def test_landstar_http_11_without_host_authenticates_as_ntrip_1(self):
        auth_header, encoded = self._basic_header()
        request = (
            "GET /base HTTP/1.1\r\n"
            "User-Agent: NTRIP CHC LandStar/8.3.0.20260616\r\n"
            f"Authorization: {auth_header}\r\n"
            "\r\n"
        )
        handler, client_socket, database = self._request_handler(request)
        logged_messages = []

        patches = self._patch_lifecycle()
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

        with (
            mock.patch.object(ntrip.connection, "get_user_connection_count", return_value=0),
            mock.patch.object(
                ntrip,
                "log_debug",
                side_effect=lambda message, *args, **kwargs: logged_messages.append(str(message)),
            ),
            mock.patch.object(
                ntrip,
                "log_info",
                side_effect=lambda message, *args, **kwargs: logged_messages.append(str(message)),
            ),
        ):
            handler.handle_request()

        self.assertEqual(handler.ntrip_version, "1.0")
        self.assertEqual(handler.protocol_type, "ntrip1_0_http")
        database.verify_download_user.assert_called_once()
        verification_args = database.verify_download_user.call_args.args
        self.assertEqual(verification_args[0], "base")
        self.assertEqual(verification_args[1], "rover-user")
        self.assertEqual(len(self.data_forwarder.get_client_info("base")), 1)

        rtcm = b"\xd3\x00\x03\x04\x05\x06"
        self.data_forwarder.create_mount_buffer("base")
        self.data_forwarder.mount_buffers["base"].append(rtcm)
        self.data_forwarder._broadcast_data()
        self.assertTrue(client_socket.writes[0].startswith(b"ICY 200 OK\r\n"))
        self.assertEqual(client_socket.writes[1], rtcm)

        combined_logs = "\n".join(logged_messages)
        self.assertNotIn(encoded, combined_logs)
        self.assertNotIn("Authorization: Basic", combined_logs)
        self.assertIn("已收到認證標頭", combined_logs)
        handler._cleanup_download_connection()

    def test_landstar_invalid_basic_authentication_is_rejected(self):
        auth_header, _ = self._basic_header()
        request = (
            "GET /base HTTP/1.1\r\n"
            "User-Agent: NTRIP CHC LandStar/8.3.0.20260616\r\n"
            f"Authorization: {auth_header}\r\n"
            "\r\n"
        )
        handler, client_socket, database = self._request_handler(
            request,
            authentication_valid=False,
        )
        handler.send_auth_challenge = mock.Mock()

        with mock.patch.object(ntrip.connection, "get_user_connection_count", return_value=0):
            handler.handle_request()

        self.assertEqual(handler.protocol_type, "ntrip1_0_http")
        database.verify_download_user.assert_called_once()
        handler.send_auth_challenge.assert_called_once()
        handler._keep_connection_alive.assert_not_called()
        self.assertEqual(self.data_forwarder.get_client_info("base"), [])
        client_socket.close()

    def test_landstar_application_version_2_0_is_not_a_protocol_declaration(self):
        handler = ntrip.NTRIPHandler(
            ControlledSocket(),
            ("127.0.0.1", 33005),
            mock.Mock(),
        )
        headers = {"user-agent": "NTRIP CHC LandStar/8.2.0"}

        handler._determine_ntrip_version(
            headers,
            "GET /base HTTP/1.1",
            "GET",
            "/base",
        )

        self.assertEqual(handler.protocol_type, "ntrip1_0_http")
        handler.client_socket.close()

    def test_browser_http_11_without_host_is_still_rejected(self):
        request = "GET /admin HTTP/1.1\r\nUser-Agent: Mozilla/5.0\r\n\r\n"
        handler, client_socket, _ = self._request_handler(request)
        handler.send_error_response = mock.Mock()
        handler.handle_download = mock.Mock()
        handler.handle_http_get = mock.Mock()

        handler.handle_request()

        self.assertEqual(handler.protocol_type, "http")
        handler.send_error_response.assert_called_once_with(
            400,
            "Bad Request: Missing Host header",
        )
        handler.handle_download.assert_not_called()
        handler.handle_http_get.assert_not_called()
        client_socket.close()

    def test_other_or_unknown_rover_without_host_is_not_legacy_compatible(self):
        for user_agent in ("Leica rover", "Trimble receiver", "UnknownRover/1.0"):
            with self.subTest(user_agent=user_agent):
                request = (
                    "GET /base HTTP/1.1\r\n"
                    f"User-Agent: {user_agent}\r\n"
                    "\r\n"
                )
                handler, client_socket, _ = self._request_handler(request)
                handler.send_error_response = mock.Mock()
                handler.handle_download = mock.Mock()
                handler.handle_http_get = mock.Mock()

                handler.handle_request()

                self.assertNotEqual(handler.protocol_type, "ntrip1_0_http")
                handler.send_error_response.assert_called_once_with(
                    400,
                    "Bad Request: Missing Host header",
                )
                handler.handle_download.assert_not_called()
                handler.handle_http_get.assert_not_called()
                client_socket.close()

    def test_explicit_ntrip_2_without_host_is_still_rejected(self):
        request = (
            "GET /base HTTP/1.1\r\n"
            "User-Agent: NTRIP CHC LandStar/8.3.0.20260616\r\n"
            "Ntrip-Version: Ntrip/2.0\r\n"
            "\r\n"
        )
        handler, client_socket, _ = self._request_handler(request)
        handler.send_error_response = mock.Mock()
        handler.handle_download = mock.Mock()

        handler.handle_request()

        self.assertEqual(handler.protocol_type, "ntrip2_0")
        handler.send_error_response.assert_called_once_with(
            400,
            "Bad Request: Missing Host header",
        )
        handler.handle_download.assert_not_called()
        client_socket.close()

    def test_ntrip_2_user_agent_without_host_is_still_rejected(self):
        request = (
            "GET /base HTTP/1.1\r\n"
            "User-Agent: NTRIP/2.0 rover\r\n"
            "\r\n"
        )
        handler, client_socket, _ = self._request_handler(request)
        handler.send_error_response = mock.Mock()
        handler.handle_download = mock.Mock()

        handler.handle_request()

        self.assertEqual(handler.protocol_type, "ntrip2_0")
        handler.send_error_response.assert_called_once_with(
            400,
            "Bad Request: Missing Host header",
        )
        handler.handle_download.assert_not_called()
        client_socket.close()

    def test_explicit_ntrip_2_with_host_reaches_download_handler(self):
        request = (
            "GET /base HTTP/1.1\r\n"
            "Host: caster.example.invalid:2101\r\n"
            "User-Agent: NTRIP rover/2.0\r\n"
            "Ntrip-Version: Ntrip/2.0\r\n"
            "\r\n"
        )
        handler, client_socket, _ = self._request_handler(request)
        handler.handle_download = mock.Mock()

        handler.handle_request()

        self.assertEqual(handler.protocol_type, "ntrip2_0")
        handler.handle_download.assert_called_once()
        self.assertEqual(handler.handle_download.call_args.args[0], "/base")
        client_socket.close()

    def test_post_and_source_protocol_rules_are_unchanged(self):
        handler = ntrip.NTRIPHandler(
            ControlledSocket(),
            ("127.0.0.1", 33003),
            mock.Mock(),
        )
        post_headers = {"user-agent": "NTRIP CHC LandStar/8.3.0.20260616"}
        handler._determine_ntrip_version(
            post_headers,
            "POST /base HTTP/1.1",
            "POST",
            "/base",
        )
        self.assertEqual(handler.protocol_type, "ntrip2_0")
        self.assertEqual(
            handler._is_valid_request("POST", "/base", post_headers),
            (False, "Missing Host header"),
        )

        handler._determine_ntrip_version(
            {},
            "SOURCE placeholder /base",
            "SOURCE",
            "/base",
        )
        self.assertEqual(handler.protocol_type, "ntrip1_0")
        self.assertEqual(
            handler._is_valid_request("SOURCE", "/base", {}),
            (True, "Valid request"),
        )
        handler.client_socket.close()

    def test_illegal_paths_cannot_use_legacy_download_compatibility(self):
        headers = {"user-agent": "NTRIP CHC LandStar/8.3.0.20260616"}
        for raw_path in ("/../admin", "//base", "/base/child", "/%2e%2e/admin", "base"):
            with self.subTest(path=raw_path):
                handler = ntrip.NTRIPHandler(
                    ControlledSocket(),
                    ("127.0.0.1", 33004),
                    mock.Mock(),
                )
                method, parsed_path, _ = handler._parse_request_line(
                    f"GET {raw_path} HTTP/1.1"
                )
                handler._determine_ntrip_version(
                    headers,
                    f"GET {raw_path} HTTP/1.1",
                    method,
                    parsed_path,
                )
                self.assertNotEqual(handler.protocol_type, "ntrip1_0_http")
                self.assertEqual(
                    handler._is_valid_request(method, parsed_path, headers),
                    (False, "Missing Host header"),
                )
                handler.client_socket.close()


if __name__ == "__main__":
    unittest.main()
