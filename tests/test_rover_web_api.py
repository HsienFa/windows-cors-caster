"""Authenticated Rover API and monitor privacy regressions."""

from __future__ import annotations

import os
import secrets
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


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
    "log_web_request",
    "set_web_instance",
):
    setattr(logger_stub, _log_name, _no_log)
sys.modules["src.logger"] = logger_stub

_IMPORT_TEMP = tempfile.TemporaryDirectory()
_IMPORT_ROOT = Path(_IMPORT_TEMP.name)
_CONFIG_PATH = _IMPORT_ROOT / "rover-web-test.ini"
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
    f"log_dir = {(_IMPORT_ROOT / 'logs').as_posix()}\n",
    encoding="utf-8",
)

_PREVIOUS_CONFIG = os.environ.get("NTRIP_CONFIG_FILE")
os.environ["NTRIP_CONFIG_FILE"] = str(_CONFIG_PATH)
try:
    from src import connection, web
finally:
    if _PREVIOUS_CONFIG is None:
        os.environ.pop("NTRIP_CONFIG_FILE", None)
    else:
        os.environ["NTRIP_CONFIG_FILE"] = _PREVIOUS_CONFIG


def tearDownModule():
    _IMPORT_TEMP.cleanup()


class RoverWebApiTests(unittest.TestCase):
    def setUp(self):
        previous_server = web.get_server_instance()
        self.addCleanup(web.set_server_instance, previous_server)
        self.connection_manager = connection.ConnectionManager()
        self.connection_patcher = mock.patch.object(
            web.connection,
            "get_connection_manager",
            return_value=self.connection_manager,
        )
        self.connection_patcher.start()
        self.addCleanup(self.connection_patcher.stop)

        self.web_manager = web.WebManager(
            db_manager=mock.Mock(),
            data_forwarder=mock.Mock(),
            start_time=0,
        )
        self.web_manager.app.secret_key = secrets.token_urlsafe(32)
        self.web_manager.app.config.update(TESTING=True)
        self.client = self.web_manager.app.test_client()

    def _login(self):
        with self.client.session_transaction() as session:
            session["admin_logged_in"] = True
            session["admin_username"] = "test-admin"

    def _add_rover(self, username, quality=None, age_seconds=None):
        connection_id = self.connection_manager.add_user_connection(
            username,
            "BASE",
            f"192.0.2.{len(self.connection_manager.online_users) + 10}",
            "TestReceiver/1.0",
        )
        if quality is not None:
            self.connection_manager.update_rover_gga(
                username,
                connection_id,
                {
                    "latitude": 25.0618933333,
                    "longitude": 121.6457533333,
                    "gga_fix_quality": quality,
                    "satellites": 20,
                    "hdop": 0.6,
                    "altitude": 50.2,
                    "has_valid_position": quality > 0,
                },
                received_at=time.time() - (age_seconds or 0),
            )
        return connection_id

    def _add_base_coordinates(self):
        fields = [
            "STR", "BASE", "Test Base", "RTCM3", "1005", "2", "GPS",
            "TEST", "TWN", "25.0000", "121.5000", "0", "0", "TEST",
            "N", "B", "N", "500", "YES",
        ]
        self.connection_manager.online_mounts["BASE"] = connection.MountInfo(
            mount_name="BASE",
            str_data=";".join(fields),
            final_str_generated=True,
        )

    def test_rover_api_and_monitor_require_login(self):
        response = self.client.get("/api/rovers")
        self.assertEqual(response.status_code, 401)

        response = self.client.get("/?page=monitor")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.location.endswith("/login?redirect=monitor"))

        socket_client = self.web_manager.socketio.test_client(
            self.web_manager.app,
            flask_test_client=self.client,
        )
        self.assertTrue(socket_client.is_connected())
        public_events = socket_client.get_received()
        self.assertTrue(any(event["name"] == "status" for event in public_events))
        serialized_events = str(public_events).lower()
        for forbidden in (
            "username", "ip_address", "user_agent", "latitude", "longitude",
            "gga", "connection_id",
        ):
            self.assertNotIn(forbidden, serialized_events)

        web.set_server_instance(SimpleNamespace(
            get_system_stats=lambda: {
                "cpu": {"percent": 12.5},
                "users": [{
                    "username": "private-rover",
                    "ip_address": "192.0.2.10",
                }],
            }
        ))
        socket_client.emit("request_system_stats")
        system_events = [
            event for event in socket_client.get_received()
            if event["name"] == "system_stats_update"
        ]
        self.assertEqual(len(system_events), 1)
        public_stats = system_events[0]["args"][0]["stats"]
        self.assertEqual(public_stats["user_count"], 1)
        self.assertNotIn("users", public_stats)
        self.assertNotIn("private-rover", str(public_stats))

        public_api_stats = self.client.get("/api/system/stats").get_json()
        self.assertEqual(public_api_stats["user_count"], 1)
        self.assertNotIn("users", public_api_stats)
        socket_client.disconnect()

        self._login()
        self.assertEqual(self.client.get("/?page=monitor").status_code, 200)
        authenticated_socket = self.web_manager.socketio.test_client(
            self.web_manager.app,
            flask_test_client=self.client,
        )
        self.assertTrue(authenticated_socket.is_connected())
        authenticated_socket.disconnect()

    def test_socketio_user_update_is_summary_only_and_has_no_rover_event(self):
        source = (PROJECT_ROOT / "src" / "web.py").read_text(encoding="utf-8")
        push_loop = source.split("def _push_data_loop", 1)[1].split(
            "def push_log_message", 1
        )[0]
        self.assertIn("_public_online_user_summary(online_users)", push_loop)
        self.assertNotIn("'users':", push_loop)
        self.assertNotIn("get_rover_status", push_loop)
        self.assertNotIn("rover_status_update", push_loop)

        summary = web._public_online_user_summary({
            "private-rover": [{
                "ip_address": "192.0.2.10",
                "user_agent": "PrivateReceiver/1.0",
                "latitude": 25.0,
                "longitude": 121.0,
                "connection_id": "private-id",
            }],
            "second-private-rover": [{}, {}],
        })
        self.assertEqual(summary, {
            "online_user_count": 2,
            "connection_count": 3,
        })
        serialized = str(summary).lower()
        self.assertNotIn("private-rover", serialized)
        self.assertNotIn("192.0.2.10", serialized)
        self.assertNotIn("latitude", serialized)

        with mock.patch.object(self.web_manager.socketio, "emit") as emit:
            self.web_manager.push_log_message("sensitive administrative message")
        self.assertEqual(emit.call_args.kwargs["to"], "admin_data")

    def test_authenticated_api_returns_only_whitelisted_multi_rover_status(self):
        self._add_base_coordinates()
        fixed_id = self._add_rover("fixed-rover", quality=4)
        self._add_rover("float-rover", quality=5, age_seconds=31)
        self._add_rover("no-fix-rover", quality=0)
        self._add_rover("no-gga-rover")

        fixed_connection = self.connection_manager.online_users["fixed-rover"][0]
        fixed_connection.update({
            "password": "must-not-leak",
            "Authorization": "must-not-leak",
            "secret": "must-not-leak",
            "raw_gga": "must-not-leak",
        })

        self._login()
        response = self.client.get("/api/rovers")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["total_count"], 4)
        self.assertEqual(payload["freshness_threshold_seconds"], 30.0)

        expected_fields = set(web.ROVER_API_FIELDS) | {
            "base_latitude",
            "base_longitude",
            "distance_to_base_km",
        }
        self.assertTrue(all(set(rover) == expected_fields for rover in payload["rovers"]))
        serialized = response.get_data(as_text=True).lower()
        for forbidden in (
            "client_socket", "password", "authorization", "secret", "raw_gga"
        ):
            self.assertNotIn(forbidden, serialized)

        by_username = {rover["username"]: rover for rover in payload["rovers"]}
        fixed = by_username["fixed-rover"]
        self.assertEqual(fixed["connection_id"], fixed_id)
        self.assertEqual(fixed["gga_fix_quality"], 4)
        self.assertTrue(fixed["position_fresh"])
        self.assertIsNotNone(fixed["connect_datetime"])
        self.assertEqual(fixed["base_latitude"], 25.0)
        self.assertEqual(fixed["base_longitude"], 121.5)
        self.assertGreater(fixed["distance_to_base_km"], 0)

        stale_float = by_username["float-rover"]
        self.assertEqual(stale_float["gga_fix_quality"], 5)
        self.assertFalse(stale_float["position_fresh"])
        self.assertTrue(stale_float["has_valid_position"])

        no_fix = by_username["no-fix-rover"]
        self.assertEqual(no_fix["gga_fix_quality"], 0)
        self.assertFalse(no_fix["has_valid_position"])
        self.assertIsNone(no_fix["distance_to_base_km"])

        no_gga = by_username["no-gga-rover"]
        self.assertIsNone(no_gga["last_gga_time"])
        self.assertFalse(no_gga["has_valid_position"])
        self.assertFalse(no_gga["position_fresh"])


if __name__ == "__main__":
    unittest.main()
