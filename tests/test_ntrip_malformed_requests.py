"""NTRIP malformed initial request and disconnect handling regressions."""

import os
import socket
import sys
import tempfile
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
_CONFIG_PATH = _IMPORT_ROOT / "ntrip-malformed-request-test.ini"
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
    from src import ntrip
finally:
    if _PREVIOUS_CONFIG is None:
        os.environ.pop("NTRIP_CONFIG_FILE", None)
    else:
        os.environ["NTRIP_CONFIG_FILE"] = _PREVIOUS_CONFIG


class FakeSocket:
    def __init__(
        self,
        request_data=b"",
        recv_error=None,
        send_error=None,
        sendall_error=None,
    ):
        self.request_data = request_data
        self.recv_error = recv_error
        self.send_error = send_error
        self.sendall_error = sendall_error
        self.writes = []
        self.closed = False

    def settimeout(self, value):
        return None

    def setsockopt(self, *args):
        return None

    def recv(self, size):
        if self.recv_error is not None:
            raise self.recv_error
        return self.request_data

    def send(self, data):
        if self.send_error is not None:
            raise self.send_error
        self.writes.append(data)
        return len(data)

    def sendall(self, data):
        if self.sendall_error is not None:
            raise self.sendall_error
        self.writes.append(data)

    def shutdown(self, how):
        return None

    def close(self):
        self.closed = True


class NtripMalformedRequestTests(unittest.TestCase):
    def _make_handler(self, client_socket):
        database = mock.Mock()
        handler = ntrip.NTRIPHandler(
            client_socket,
            ("127.0.0.1", 33010),
            database,
        )
        return handler, database

    @staticmethod
    def _block_request_routing(handler):
        handler.send_error_response = mock.Mock()
        handler.verify_user = mock.Mock()
        handler.handle_download = mock.Mock()
        handler.handle_upload = mock.Mock()
        handler.handle_http_get = mock.Mock()
        handler.handle_options = mock.Mock()
        handler.handle_rtsp_command = mock.Mock()

    def _assert_request_did_not_route(self, handler, database):
        handler.verify_user.assert_not_called()
        handler.handle_download.assert_not_called()
        handler.handle_upload.assert_not_called()
        handler.handle_http_get.assert_not_called()
        handler.handle_options.assert_not_called()
        handler.handle_rtsp_command.assert_not_called()
        database.assert_not_called()

    def test_initial_recv_reset_ends_without_error_response_or_error_log(self):
        client_socket = FakeSocket(
            recv_error=ConnectionResetError(10054, "connection reset")
        )
        handler, database = self._make_handler(client_socket)
        self._block_request_routing(handler)

        with (
            mock.patch.object(ntrip, "log_error") as handler_error_log,
            mock.patch.object(ntrip.logger, "log_error") as logger_error_log,
        ):
            handler.handle_request()

        handler.send_error_response.assert_not_called()
        handler_error_log.assert_not_called()
        logger_error_log.assert_not_called()
        self._assert_request_did_not_route(handler, database)
        self.assertTrue(client_socket.closed)

    def test_empty_request_ends_without_authentication_or_mount_routing(self):
        handler, database = self._make_handler(FakeSocket(request_data=b""))
        self._block_request_routing(handler)

        handler.handle_request()

        handler.send_error_response.assert_not_called()
        self._assert_request_did_not_route(handler, database)

    def test_spaces_only_request_ends_without_index_error(self):
        handler, database = self._make_handler(FakeSocket(request_data=b"   "))
        self._block_request_routing(handler)

        handler.handle_request()

        handler.send_error_response.assert_not_called()
        self._assert_request_did_not_route(handler, database)

    def test_line_endings_only_request_ends_without_index_error(self):
        for request_data in (b"\r\n", b"\n", b"\r\n\r\n"):
            with self.subTest(request_data=request_data):
                handler, database = self._make_handler(
                    FakeSocket(request_data=request_data)
                )
                self._block_request_routing(handler)

                handler.handle_request()

                handler.send_error_response.assert_not_called()
                self._assert_request_did_not_route(handler, database)

    def test_malformed_request_line_uses_safe_error_without_raw_packet_log(self):
        cases = (
            (
                "incomplete-line",
                b"SCANNER_PACKET\r\n",
                "Bad Request: Invalid request line",
            ),
            (
                "unsupported-method",
                b"SCANNER_PACKET / HTTP/1.0\r\n"
                b"Host: caster.example.invalid:2101\r\n"
                b"X-Probe: RAW_PROBE_MARKER\r\n\r\n",
                "Bad Request: Unsupported method",
            ),
        )
        for name, request_data, expected_error in cases:
            with self.subTest(name=name):
                handler, database = self._make_handler(
                    FakeSocket(request_data=request_data)
                )
                self._block_request_routing(handler)
                logged_messages = []

                def record_log(message, *args, **kwargs):
                    logged_messages.append(str(message))

                with (
                    mock.patch.object(ntrip, "log_debug", side_effect=record_log),
                    mock.patch.object(ntrip, "log_info", side_effect=record_log),
                ):
                    handler.handle_request()

                handler.send_error_response.assert_called_once_with(
                    400,
                    expected_error,
                )
                self._assert_request_did_not_route(handler, database)
                combined_logs = "\n".join(logged_messages)
                self.assertNotIn("SCANNER_PACKET", combined_logs)
                self.assertNotIn("RAW_PROBE_MARKER", combined_logs)

    def test_error_response_reset_and_broken_pipe_do_not_log_error(self):
        closed_socket_error = OSError("socket already closed")
        closed_socket_error.winerror = 10038
        cases = (
            (
                "ntrip1-reset",
                "1.0",
                FakeSocket(send_error=ConnectionResetError(10054, "connection reset")),
            ),
            (
                "ntrip2-broken-pipe",
                "2.0",
                FakeSocket(sendall_error=BrokenPipeError(32, "broken pipe")),
            ),
            (
                "ntrip1-closed-socket",
                "1.0",
                FakeSocket(send_error=closed_socket_error),
            ),
        )
        for name, version, client_socket in cases:
            with self.subTest(name=name):
                handler, _ = self._make_handler(client_socket)
                handler.ntrip_version = version
                handler.protocol_type = (
                    "ntrip2_0" if version == "2.0" else "ntrip1_0"
                )

                with mock.patch.object(ntrip.logger, "log_error") as error_log:
                    handler.send_error_response(400, "Bad Request")

                error_log.assert_not_called()

    def test_non_disconnect_program_error_is_still_logged(self):
        handler, _ = self._make_handler(
            FakeSocket(recv_error=RuntimeError("unexpected handler failure"))
        )
        handler.send_error_response = mock.Mock()

        with mock.patch.object(ntrip, "log_error") as error_log:
            handler.handle_request()

        error_log.assert_called_once()
        self.assertTrue(error_log.call_args.kwargs.get("exc_info"))
        handler.send_error_response.assert_called_once_with(
            500,
            "Internal Server Error",
        )

    def test_valid_ntrip_1_and_2_requests_keep_download_routing(self):
        requests = (
            (
                "ntrip1",
                b"GET /base HTTP/1.0\r\nUser-Agent: NTRIP rover/1.0\r\n\r\n",
                "ntrip1_0_http",
            ),
            (
                "ntrip2",
                b"GET /base HTTP/1.1\r\n"
                b"Host: caster.example.invalid:2101\r\n"
                b"User-Agent: NTRIP rover/2.0\r\n"
                b"Ntrip-Version: Ntrip/2.0\r\n\r\n",
                "ntrip2_0",
            ),
        )
        for name, request_data, expected_protocol in requests:
            with self.subTest(name=name):
                handler, _ = self._make_handler(
                    FakeSocket(request_data=request_data)
                )
                handler.handle_download = mock.Mock()
                handler.send_error_response = mock.Mock()

                handler.handle_request()

                self.assertEqual(handler.protocol_type, expected_protocol)
                handler.handle_download.assert_called_once()
                self.assertEqual(handler.handle_download.call_args.args[0], "/base")
                handler.send_error_response.assert_not_called()


if __name__ == "__main__":
    unittest.main()
