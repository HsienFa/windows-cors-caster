"""Authenticated Rover API and monitor privacy regressions."""

from __future__ import annotations

import os
import secrets
import sys
import tempfile
import threading
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
        self.web_manager.db_manager.verify_admin.return_value = True
        response = self.client.post('/api/login', json={
            'username': 'test-admin', 'password': secrets.token_urlsafe(24),
        })
        self.assertEqual(response.status_code, 200)

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

        self._login()
        admin_socket = self.web_manager.socketio.test_client(
            self.web_manager.app, flask_test_client=self.client,
        )
        self.addCleanup(admin_socket.disconnect)
        manager = self.web_manager.socketio.server.manager
        sid = manager.sid_from_eio_sid(admin_socket.eio_sid, '/')
        with mock.patch.object(self.web_manager.socketio, "emit") as emit:
            self.web_manager.push_log_message("sensitive administrative message")
        self.assertEqual(emit.call_args.kwargs["to"], sid)
        self.assertEqual(emit.call_count, 1)

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


class SocketIOParserLifecycleTests(unittest.TestCase):
    """Web parser ownership belongs to the server, not a browser socket."""

    def setUp(self):
        self.web_manager = web.WebManager(
            db_manager=mock.Mock(),
            data_forwarder=mock.Mock(),
            start_time=0,
        )
        self.web_manager.app.secret_key = secrets.token_urlsafe(32)
        self.web_manager.app.config.update(TESTING=True)

        self.current_mount_patcher = mock.patch.object(
            web.rtcm_manager,
            "get_current_web_mount",
            return_value="TEST",
        )
        self.current_mount = self.current_mount_patcher.start()
        self.addCleanup(self.current_mount_patcher.stop)

        self.stop_parser_patcher = mock.patch.object(
            web.rtcm_manager,
            "stop_realtime_parsing",
        )
        self.stop_parser = self.stop_parser_patcher.start()
        self.addCleanup(self.stop_parser_patcher.stop)

    @staticmethod
    def _disconnect_if_connected(socket_client):
        if socket_client.is_connected():
            socket_client.disconnect()

    def _make_socket_client(self, authenticated=False):
        flask_client = self.web_manager.app.test_client()
        if authenticated:
            self.web_manager.db_manager.verify_admin.return_value = True
            response = flask_client.post('/api/login', json={
                'username': 'test-admin', 'password': secrets.token_urlsafe(24),
            })
            self.assertEqual(response.status_code, 200)

        socket_client = self.web_manager.socketio.test_client(
            self.web_manager.app,
            flask_test_client=flask_client,
        )
        self.assertTrue(socket_client.is_connected())
        self.addCleanup(self._disconnect_if_connected, socket_client)
        return flask_client, socket_client

    def _assert_socket_rooms(self, socket_client, authenticated):
        manager = self.web_manager.socketio.server.manager
        sid = manager.sid_from_eio_sid(socket_client.eio_sid, "/")
        self.assertIsNotNone(sid)
        rooms = manager.get_rooms(sid, "/")
        self.assertIn("data_push", rooms)
        self.assertEqual("admin_data" in rooms, authenticated)
        return sid

    def test_socketio_public_and_admin_room_delivery_remains_separate(self):
        _, anonymous = self._make_socket_client()
        _, administrator = self._make_socket_client(authenticated=True)
        self._assert_socket_rooms(anonymous, authenticated=False)
        self._assert_socket_rooms(administrator, authenticated=True)
        for client in (anonymous, administrator):
            self.assertEqual(
                [event["name"] for event in client.get_received()], ["status"],
            )

        self.web_manager.socketio.emit(
            "synthetic_summary", {"count": 1}, to="data_push",
        )
        self.web_manager.push_log_message("synthetic administration event")
        public_events = anonymous.get_received()
        admin_events = administrator.get_received()
        self.assertEqual([event["name"] for event in public_events], ["synthetic_summary"])
        self.assertEqual(public_events[0]["args"], [{"count": 1}])
        self.assertEqual(
            [event["name"] for event in admin_events],
            ["synthetic_summary", "log_message"],
        )
        self.assertEqual(
            admin_events[1]["args"][0]["message"], "synthetic administration event",
        )

    def test_http_login_logout_and_socket_reconnect_preserve_session_rooms(self):
        flask_client, socket_client = self._make_socket_client()
        self._assert_socket_rooms(socket_client, authenticated=False)
        socket_client.disconnect()

        password = secrets.token_hex(12)
        self.web_manager.db_manager.verify_admin.return_value = True
        response = flask_client.post(
            "/login?redirect=monitor",
            data={"username": "syntheticadmin", "password": password},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.location.endswith("/?page=monitor"))
        self.web_manager.db_manager.verify_admin.assert_called_once_with(
            "syntheticadmin", password,
        )
        with flask_client.session_transaction() as session:
            self.assertTrue(session["admin_logged_in"])
            self.assertEqual(session["admin_username"], "syntheticadmin")

        socket_client.connect()
        self.assertTrue(socket_client.is_connected())
        admin_sid = self._assert_socket_rooms(socket_client, authenticated=True)
        socket_client.disconnect()
        response = flask_client.get("/logout")
        self.assertEqual(response.status_code, 302)
        with flask_client.session_transaction() as session:
            self.assertNotIn("admin_logged_in", session)

        socket_client.connect()
        self.assertTrue(socket_client.is_connected())
        anonymous_sid = self._assert_socket_rooms(socket_client, authenticated=False)
        self.assertNotEqual(anonymous_sid, admin_sid)
        socket_client.get_received()
        self.web_manager.push_log_message("synthetic post-logout event")
        self.assertEqual(socket_client.get_received(), [])
        self.stop_parser.assert_not_called()

    def test_failed_http_login_keeps_socket_connection_anonymous(self):
        flask_client = self.web_manager.app.test_client()
        self.web_manager.db_manager.verify_admin.return_value = False
        response = flask_client.post(
            "/login",
            data={"username": "syntheticadmin", "password": secrets.token_hex(12)},
        )
        self.assertEqual(response.status_code, 200)
        with flask_client.session_transaction() as session:
            self.assertFalse(session.get("admin_logged_in", False))
        socket_client = self.web_manager.socketio.test_client(
            self.web_manager.app, flask_test_client=flask_client,
        )
        self.addCleanup(self._disconnect_if_connected, socket_client)
        self.assertTrue(socket_client.is_connected())
        self._assert_socket_rooms(socket_client, authenticated=False)

    def test_socketio_data_event_callbacks_and_replies_survive_reconnect(self):
        _, socket_client = self._make_socket_client(authenticated=True)
        parsed = {"message_count": 2}
        statistics = {"bytes_received": 8}
        with (
            mock.patch.object(web.rtcm_manager, "get_parsed_mount_data", return_value=parsed),
            mock.patch.object(web.rtcm_manager, "get_mount_statistics", return_value=statistics),
        ):
            for reconnect in (False, True):
                with self.subTest(reconnect=reconnect):
                    if reconnect:
                        socket_client.disconnect()
                        socket_client.connect()
                        self.assertTrue(socket_client.is_connected())
                    socket_client.get_received()
                    acknowledgement = socket_client.emit(
                        "request_mount_data", {"mount": "SYNTHETIC"}, callback=True,
                    )
                    self.assertEqual(acknowledgement, [])
                    self.assertEqual(socket_client.get_received(), [{
                        "name": "mount_data",
                        "args": [{"mount": "SYNTHETIC", "data": parsed, "statistics": statistics}],
                        "namespace": "/",
                    }])
        self.stop_parser.assert_not_called()

    def test_anonymous_disconnect_does_not_stop_active_web_parser(self):
        _, socket_client = self._make_socket_client()

        socket_client.disconnect()

        self.stop_parser.assert_not_called()

    def test_authenticated_disconnect_does_not_stop_active_web_parser(self):
        _, socket_client = self._make_socket_client(authenticated=True)

        socket_client.disconnect()

        self.stop_parser.assert_not_called()

    def test_anonymous_disconnect_does_not_interrupt_authenticated_client(self):
        _, anonymous_socket = self._make_socket_client()
        _, authenticated_socket = self._make_socket_client(authenticated=True)

        anonymous_socket.disconnect()

        self.assertTrue(authenticated_socket.is_connected())
        self.stop_parser.assert_not_called()

    def test_one_of_two_authenticated_disconnects_does_not_stop_parser(self):
        _, first_socket = self._make_socket_client(authenticated=True)
        _, second_socket = self._make_socket_client(authenticated=True)

        first_socket.disconnect()

        self.assertTrue(second_socket.is_connected())
        self.stop_parser.assert_not_called()

    def test_refresh_old_connection_disconnect_does_not_stop_current_parser(self):
        _, old_socket = self._make_socket_client()
        _, replacement_socket = self._make_socket_client()

        old_socket.disconnect()

        self.assertTrue(replacement_socket.is_connected())
        self.stop_parser.assert_not_called()

    def test_repeated_delayed_disconnects_do_not_stop_new_parser(self):
        _, first_old_socket = self._make_socket_client()
        _, second_old_socket = self._make_socket_client(authenticated=True)
        _, current_socket = self._make_socket_client(authenticated=True)

        first_old_socket.disconnect()
        second_old_socket.disconnect()

        self.assertTrue(current_socket.is_connected())
        self.stop_parser.assert_not_called()

    def test_authenticated_stop_api_still_stops_global_parser(self):
        flask_client, _ = self._make_socket_client(authenticated=True)

        response = flask_client.post("/api/mount/rtcm-parse/stop")

        self.assertEqual(response.status_code, 200)
        self.stop_parser.assert_called_once_with()

    def test_authenticated_start_api_behavior_is_preserved(self):
        flask_client, _ = self._make_socket_client(authenticated=True)

        with mock.patch.object(
            web.rtcm_manager,
            "start_realtime_parsing",
            return_value=True,
        ) as start_parser:
            response = flask_client.post("/api/mount/TEST/rtcm-parse/start")

        self.assertEqual(response.status_code, 200)
        start_parser.assert_called_once()
        self.assertEqual(start_parser.call_args.kwargs["mount_name"], "TEST")
        self.assertTrue(callable(start_parser.call_args.kwargs["push_callback"]))

    def test_web_manager_shutdown_still_stops_listener_and_push_thread(self):
        with (
            mock.patch.object(self.web_manager, "stop_web_server") as stop_server,
            mock.patch.object(self.web_manager, "stop_rtcm_parsing") as stop_push,
        ):
            self.web_manager.stop()

        stop_server.assert_called_once_with()
        stop_push.assert_called_once_with()

    def test_disconnect_does_not_touch_ntrip_forwarding_services(self):
        _, socket_client = self._make_socket_client()

        with (
            mock.patch.object(web.forwarder, "stop_forwarder") as stop_forwarder,
            mock.patch.object(web.forwarder, "remove_mount_buffer") as remove_buffer,
            mock.patch.object(web.forwarder, "force_disconnect_user") as disconnect_user,
            mock.patch.object(web.forwarder, "force_disconnect_mount") as disconnect_mount,
        ):
            socket_client.disconnect()

        self.stop_parser.assert_not_called()
        stop_forwarder.assert_not_called()
        remove_buffer.assert_not_called()
        disconnect_user.assert_not_called()
        disconnect_mount.assert_not_called()


class WebDataAccessTests(unittest.TestCase):
    """In-process clients only; no listeners, parser threads or real data."""

    _make_socket_client = SocketIOParserLifecycleTests._make_socket_client
    _assert_socket_rooms = SocketIOParserLifecycleTests._assert_socket_rooms
    _disconnect_if_connected = staticmethod(SocketIOParserLifecycleTests._disconnect_if_connected)

    def setUp(self):
        SocketIOParserLifecycleTests.setUp(self)
        previous_server = web.get_server_instance()
        self.addCleanup(web.set_server_instance, previous_server)
        self.private = 'PRIVATE_SENTINEL'
        self.mount = {
            'mount_name': 'SYNTHETIC', 'status': 'online', 'user_count': 3,
            'data_count': 7, 'uptime': 5,
            'ip_address': self.private, 'user_agent': self.private,
            'lat': self.private, 'lon': self.private, 'str_data': self.private,
            'device': self.private, 'unknown': {'nested': self.private},
        }
        self.public_mount = {
            'mount_name': 'SYNTHETIC', 'status': 'online', 'user_count': 3,
            'data_count': 7, 'uptime': 5,
        }
        self.stats = {
            'timestamp': 10, 'uptime': 20, 'cpu_percent': 12.5,
            'memory': {'percent': 10, 'used': 20, 'total': 200, 'unknown': self.private},
            'network_bandwidth': {'sent_rate': 4, 'recv_rate': 8, 'unknown': self.private},
            'connections': {'active': 3, 'total': 5, 'rejected': 1, 'max_concurrent': 9,
                            'unknown': self.private},
            'data_transfer': {'total_bytes': 123, 'unknown': self.private},
            'users': [{'username': self.private}], 'mounts': [self.mount],
            'unknown': self.private,
        }
        web.set_server_instance(SimpleNamespace(get_system_stats=lambda: self.stats))
        self.cm = mock.Mock()
        self.cm.get_statistics.return_value = {'mounts': [self.mount]}
        self.cm.get_online_users.return_value = {self.private: [{}, {}]}
        self.cm.get_online_mounts.return_value = {'SYNTHETIC': self.mount}
        self.cm.get_all_str_data.return_value = {'SYNTHETIC': self.private}
        self.cm.generate_mount_list.return_value = [self.private]
        self.cm.get_mount_str_data.return_value = self.private
        self.cm.get_mount_statistics.return_value = {'data_count': 7}
        self.cm.get_mount_connection_count.return_value = 3
        patcher = mock.patch.object(web.connection, 'get_connection_manager', return_value=self.cm)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _push_once(self):
        self.web_manager.push_running = True
        def finish(_timeout):
            self.web_manager.push_running = False
        with mock.patch.object(self.web_manager._push_stop_event, 'wait', side_effect=finish):
            self.web_manager._push_data_loop()

    def _assert_denied(self, client):
        client.get_received()
        with mock.patch.object(web.rtcm_manager, 'get_parsed_mount_data', return_value=self.mount) as read:
            for name, args in (
                ('request_mount_data', {'mount': 'SYNTHETIC'}),
                ('request_recent_data', {'mount_name': 'SYNTHETIC'}),
            ):
                ack = client.emit(name, args, callback=True)
                self.assertEqual(ack['error'], 'unauthorized')
                events = client.get_received()
                self.assertEqual([event['name'] for event in events], ['error'])
                self.assertNotIn(self.private, str(events))
            read.assert_not_called()

    def test_public_http_request_and_periodic_push_share_strict_summary(self):
        http, anon = self._make_socket_client()
        _, admin = self._make_socket_client(authenticated=True)
        expected = {
            'timestamp': 10, 'uptime': 20, 'cpu_percent': 12.5,
            'memory': {'percent': 10, 'used': 20, 'total': 200},
            'network_bandwidth': {'sent_rate': 4, 'recv_rate': 8},
            'connections': {'active': 3, 'total': 5, 'rejected': 1, 'max_concurrent': 9},
            'data_transfer': {'total_bytes': 123}, 'user_count': 1,
            'mounts': [self.public_mount],
        }
        self.assertEqual(http.get('/api/system/stats').get_json(), expected)
        anon.get_received()
        anon.emit('request_system_stats')
        self.assertEqual(anon.get_received()[0]['args'][0]['stats'], expected)
        admin.get_received()
        self._push_once()
        events = anon.get_received()
        self.assertEqual([event['name'] for event in events], [
            'system_stats_update', 'online_users_update', 'online_mounts_update',
        ])
        self.assertEqual(events[0]['args'][0]['stats'], expected)
        self.assertEqual(events[1]['args'][0]['online_user_count'], 1)
        self.assertEqual(events[1]['args'][0]['connection_count'], 2)
        self.assertEqual(events[2]['args'][0]['mounts'], {'SYNTHETIC': self.public_mount})
        self.assertNotIn(self.private, str(events))
        admin_events = admin.get_received()
        self.assertEqual(admin_events[-1]['name'], 'str_data_update')
        self.assertEqual(admin_events[-1]['args'][0]['str_data'], {'SYNTHETIC': self.private})

    def test_nested_objects_cannot_escape_through_allowed_scalar_fields(self):
        http, client = self._make_socket_client()
        self.stats['memory']['used'] = {'ip_address': self.private}
        self.stats['cpu_percent'] = [self.private]
        self.mount['data_count'] = {'location': self.private}
        self.mount['mount_name'] = {'unknown': self.private}
        self.mount['status'] = {'unknown': self.private}
        self.assertNotIn(self.private, str(http.get('/api/system/stats').get_json()))
        client.get_received()
        client.emit('request_system_stats')
        self.assertNotIn(self.private, str(client.get_received()))
        self._push_once()
        self.assertNotIn(self.private, str(client.get_received()))

    def test_detail_http_and_socket_permission_matrix_uses_nonempty_data(self):
        anonymous_http, anonymous = self._make_socket_client()
        http, admin = self._make_socket_client(authenticated=True)
        for path in ('/api/mounts/online', '/api/str-table'):
            self.cm.reset_mock()
            self.assertEqual(anonymous_http.get(path).status_code, 401)
            self.assertEqual(self.cm.mock_calls, [])
            response = http.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertIn(self.private, str(response.get_json()))
        self._assert_denied(anonymous)
        admin.get_received()
        with (
            mock.patch.object(web.rtcm_manager, 'get_parsed_mount_data', return_value=self.mount),
            mock.patch.object(web.rtcm_manager, 'get_mount_statistics', return_value={'bitrate': 8}),
        ):
            for name, args, reply, expected in (
                ('request_mount_data', {'mount': 'SYNTHETIC'}, 'mount_data',
                 {'mount': 'SYNTHETIC', 'data': self.mount, 'statistics': {'bitrate': 8}}),
                ('request_recent_data', {'mount_name': 'SYNTHETIC'}, 'recent_data_response',
                 {'mount_name': 'SYNTHETIC', 'data': self.mount}),
            ):
                self.assertEqual(admin.emit(name, args, callback=True), [])
                self.assertEqual(admin.get_received(), [
                    {'name': reply, 'args': [expected], 'namespace': '/'},
                ])
        self.assertEqual(http.post('/logout').status_code, 200)
        for path in ('/api/mounts/online', '/api/str-table'):
            self.cm.reset_mock()
            self.assertEqual(http.get(path).status_code, 401)
            self.assertEqual(self.cm.mock_calls, [])
        self._assert_denied(admin)

    def test_parser_callback_only_delivers_to_current_admins(self):
        _, anonymous = self._make_socket_client()
        http, admin = self._make_socket_client(authenticated=True)
        with mock.patch.object(web.rtcm_manager, 'start_realtime_parsing', return_value=True) as start:
            self.assertEqual(http.post('/api/mount/SYNTHETIC/rtcm-parse/start').status_code, 200)
        callback = start.call_args.kwargs['push_callback']
        packet = {'mount_name': 'SYNTHETIC', 'data_type': 'geography', 'location': self.private}
        anonymous.get_received()
        admin.get_received()
        callback(packet)
        self.assertEqual(anonymous.get_received(), [])
        self.assertEqual(admin.get_received(), [
            {'name': 'rtcm_realtime_data', 'args': [packet], 'namespace': '/'},
        ])
        http.post('/logout')
        callback(packet)
        self.assertEqual(admin.get_received(), [])
        self.stop_parser.assert_not_called()

    def test_logout_revokes_shared_tabs_old_cookie_and_reconnect_only(self):
        http, first = self._make_socket_client(authenticated=True)
        old_cookie = http.get_cookie('session').value
        second = self.web_manager.socketio.test_client(self.web_manager.app, flask_test_client=http)
        self.addCleanup(self._disconnect_if_connected, second)
        _, independent = self._make_socket_client(authenticated=True)
        for client in (first, second, independent):
            client.get_received()
        self.assertEqual(http.post('/logout').status_code, 200)
        for client in (first, second):
            self.assertTrue(client.is_connected())
            self._assert_socket_rooms(client, authenticated=False)
            self._assert_denied(client)
        self._push_once()
        self.web_manager.push_log_message(self.private)
        self.web_manager._emit_admin('rtcm_realtime_data', {'detail': self.private})
        for client in (first, second):
            events = client.get_received()
            self.assertNotIn(self.private, str(events))
            self.assertEqual([e['name'] for e in events], [
                'system_stats_update', 'online_users_update', 'online_mounts_update',
            ])
        self.assertEqual([e['name'] for e in independent.get_received()][-3:], [
            'str_data_update', 'log_message', 'rtcm_realtime_data',
        ])
        replay = self.web_manager.app.test_client()
        replay.set_cookie('session', old_cookie)
        self.assertEqual(replay.get('/api/mounts/online').status_code, 401)
        denied = self.web_manager.socketio.test_client(self.web_manager.app, flask_test_client=replay)
        self.assertFalse(denied.is_connected())
        first.disconnect()
        first.flask_test_client = replay
        first.connect()
        self.assertFalse(first.is_connected())
        independent.disconnect()
        independent.connect()
        self.assertTrue(independent.is_connected())
        self._assert_socket_rooms(independent, authenticated=True)
        self.stop_parser.assert_not_called()

    def test_disconnect_expiry_and_capacity_bound_registry(self):
        self.web_manager._max_admin_logins = 2
        http, admin = self._make_socket_client(authenticated=True)
        _, other = self._make_socket_client(authenticated=True)
        for _ in range(3):
            admin.disconnect()
            self.assertEqual(len(self.web_manager._socket_logins), 1)
            admin.connect()
            self.assertTrue(admin.is_connected())
            self.assertEqual(len(self.web_manager._socket_logins), 2)
        extra = self.web_manager.app.test_client()
        response = extra.post('/api/login', json={
            'username': 'test-admin', 'password': secrets.token_urlsafe(24),
        })
        self.assertEqual(response.status_code, 503)
        self._assert_socket_rooms(other, authenticated=True)
        with http.session_transaction() as session:
            login_id = session['admin_login_id']
        self.web_manager._admin_logins[login_id]['expires_at'] = time.monotonic() - 1
        self.web_manager._emit_admin('synthetic_admin', {})
        self.assertEqual(len(self.web_manager._admin_logins), 1)
        self.assertEqual(len(self.web_manager._socket_logins), 1)
        self._assert_denied(admin)
        self.assertEqual(http.get('/api/mounts/online').status_code, 401)

    def test_public_html_omits_configured_map_details_but_admin_keeps_them(self):
        anonymous_http, _ = self._make_socket_client()
        http, _ = self._make_socket_client(authenticated=True)
        map_settings = {
            'provider': 'google', 'google_enabled': True,
            'default_latitude': self.private + '_LAT',
            'default_longitude': self.private + '_LON', 'default_zoom': 12,
        }
        with (
            mock.patch.object(web.config, 'get_public_map_config', return_value=map_settings) as settings,
            mock.patch.object(web.config, 'get_google_maps_script_url', return_value=self.private + '_URL'),
        ):
            html = anonymous_http.get('/').get_data(as_text=True)
            self.assertNotIn(self.private, html)
            self.assertNotIn('data-map-default-latitude=', html)
            self.assertNotIn('data-map-default-longitude=', html)
            settings.assert_not_called()
            html = http.get('/?page=monitor').get_data(as_text=True)
            for suffix in ('_LAT', '_LON', '_URL'):
                self.assertIn(self.private + suffix, html)
            http.post('/logout')
            self.assertNotIn(self.private, http.get('/').get_data(as_text=True))

    def test_public_errors_do_not_echo_internal_exception_details(self):
        http, client = self._make_socket_client()
        web.set_server_instance(SimpleNamespace(get_system_stats=mock.Mock(side_effect=RuntimeError(self.private))))
        self.assertEqual(http.get('/api/system/stats').status_code, 500)
        self.assertNotIn(self.private, http.get('/api/system/stats').get_data(as_text=True))
        client.get_received()
        client.emit('request_system_stats')
        self.assertNotIn(self.private, str(client.get_received()))

    def test_logout_during_detail_read_prevents_late_reply(self):
        http, admin = self._make_socket_client(authenticated=True)
        entered, release = threading.Event(), threading.Event()
        results = []
        def read(_mount):
            entered.set()
            if not release.wait(2):
                raise AssertionError('bounded read wait expired')
            return self.mount
        def request_detail():
            try:
                results.append(admin.emit('request_recent_data', {'mount_name': 'SYNTHETIC'}, callback=True))
            except Exception as error:
                results.append(error)
        admin.get_received()
        with mock.patch.object(web.rtcm_manager, 'get_parsed_mount_data', side_effect=read):
            worker = threading.Thread(target=request_detail, daemon=True)
            worker.start()
            try:
                self.assertTrue(entered.wait(2))
                self.assertEqual(http.post('/logout').status_code, 200)
            finally:
                release.set()
                worker.join(2)
            self.assertFalse(worker.is_alive())
        self.assertEqual(results, [{'error': 'unauthorized', 'message': '尚未登入或登入狀態已過期'}])
        self.assertEqual([e['name'] for e in admin.get_received()], ['error'])

    def test_logout_and_push_are_serialized_without_post_logout_enqueue(self):
        http, admin = self._make_socket_client(authenticated=True)
        entered, release, logout_started, logout_done = (threading.Event() for _ in range(4))
        errors = []
        original_emit = self.web_manager.socketio.emit
        def enqueue(*args, **kwargs):
            entered.set()
            if not release.wait(2):
                raise AssertionError('bounded push wait expired')
            return original_emit(*args, **kwargs)
        def logout():
            logout_started.set()
            try:
                response = http.post('/logout')
                if response.status_code != 200:
                    errors.append(response.status_code)
            except Exception as error:
                errors.append(error)
            finally:
                logout_done.set()
        admin.get_received()
        with mock.patch.object(self.web_manager.socketio, 'emit', side_effect=enqueue) as emitted:
            push = threading.Thread(target=self.web_manager._emit_admin, args=('synthetic_admin', {}), daemon=True)
            revoke = threading.Thread(target=logout, daemon=True)
            push.start()
            try:
                self.assertTrue(entered.wait(2))
                revoke.start()
                self.assertTrue(logout_started.wait(2))
                self.assertFalse(logout_done.wait(0.05))
            finally:
                release.set()
                push.join(2)
                if revoke.ident is not None:
                    revoke.join(2)
            self.assertFalse(push.is_alive())
            self.assertFalse(revoke.is_alive())
            self.assertEqual(errors, [])
            self.assertTrue(logout_done.is_set())
            self.web_manager._emit_admin('synthetic_after_logout', {})
            self.assertEqual(emitted.call_count, 1)
        self.assertEqual([e['name'] for e in admin.get_received()], ['synthetic_admin'])
        self.stop_parser.assert_not_called()

    def test_http_detail_and_html_recheck_logout_during_data_read(self):
        for path in ('/api/mounts/online', '/'):
            with self.subTest(path=path):
                http, _ = self._make_socket_client(authenticated=True)
                reader = self.web_manager.app.test_client()
                reader.set_cookie('session', http.get_cookie('session').value)
                entered, release = threading.Event(), threading.Event()
                responses = []
                def read():
                    entered.set()
                    if not release.wait(2):
                        raise AssertionError('bounded HTTP read wait expired')
                    if path == '/':
                        return {
                            'provider': 'osm', 'google_enabled': False,
                            'default_latitude': self.private,
                            'default_longitude': self.private, 'default_zoom': 7,
                        }
                    return {'SYNTHETIC': self.mount}
                def get():
                    try:
                        responses.append(reader.get(path))
                    except Exception as error:
                        responses.append(error)
                target, method = (web.config, 'get_public_map_config') if path == '/' else (
                    self.cm, 'get_online_mounts',
                )
                with mock.patch.object(target, method, side_effect=read):
                    worker = threading.Thread(target=get, daemon=True)
                    worker.start()
                    try:
                        self.assertTrue(entered.wait(2))
                        self.assertEqual(http.post('/logout').status_code, 200)
                    finally:
                        release.set()
                        worker.join(2)
                    self.assertFalse(worker.is_alive())
                self.assertEqual(len(responses), 1)
                self.assertEqual(responses[0].status_code, 200 if path == '/' else 401)
                self.assertNotIn(self.private, responses[0].get_data(as_text=True))


    def test_engineio_timeout_during_emit_preserves_other_recipient(self):
        from engineio.socket import Socket

        _, first = self._make_socket_client(authenticated=True)
        _, second = self._make_socket_client(authenticated=True)
        server = self.web_manager.socketio.server
        first_sid = self._assert_socket_rooms(first, authenticated=True)
        expired = Socket(server.eio, first.eio_sid)
        healthy = Socket(server.eio, second.eio_sid)
        expired.last_ping = time.time() - server.eio.ping_timeout - 1
        server.eio.sockets[first.eio_sid] = expired
        server.eio.sockets[second.eio_sid] = healthy
        self.addCleanup(server.eio.sockets.clear)
        # Exercise actual Engine.IO timeout -> synchronous disconnect callback;
        # Socket.send only enqueues synthetic packets, with no network listener.
        with mock.patch.object(server, '_send_eio_packet', side_effect=server.eio.send_packet):
            self.web_manager._emit_admin('synthetic_timeout_delivery', {'count': 1})
        self.assertNotIn(first_sid, self.web_manager._socket_logins)
        self.assertFalse(server.manager.is_connected(first_sid, '/'))
        self.assertIn('synthetic_timeout_delivery', healthy.queue.get_nowait().data)
        self.stop_parser.assert_not_called()

    def test_failed_recipient_does_not_recurse_logs_or_block_other_admin(self):
        _, first = self._make_socket_client(authenticated=True)
        _, second = self._make_socket_client(authenticated=True)
        failed_sid = self._assert_socket_rooms(first, authenticated=True)
        attempts = []
        original_emit = self.web_manager.socketio.emit
        def send(event, payload, **kwargs):
            if kwargs['to'] == failed_sid:
                attempts.append(event)
                raise RuntimeError('synthetic send failure')
            return original_emit(event, payload, **kwargs)
        def recursive_log(message):
            # Bound the old behavior to two attempts instead of overflowing.
            if len(attempts) < 2:
                self.web_manager.push_log_message(message)
        second.get_received()
        with (
            mock.patch.object(self.web_manager.socketio, 'emit', side_effect=send),
            mock.patch.object(web, 'log_error', side_effect=recursive_log) as recursive,
            mock.patch.object(web.logging, 'getLogger') as diagnostic,
        ):
            self.web_manager.push_log_message('synthetic log')
            self.assertEqual(len(attempts), 1)
            recursive.assert_not_called()
            diagnostic.return_value.error.assert_called_once()
        self.assertEqual([e['name'] for e in second.get_received()], ['log_message'])

    def test_expiry_between_recipients_is_rechecked_before_each_emit(self):
        _, first = self._make_socket_client(authenticated=True)
        _, second = self._make_socket_client(authenticated=True)
        clock = [time.monotonic()]
        for grant in self.web_manager._admin_logins.values():
            grant['expires_at'] = clock[0] + 1
        original_emit = self.web_manager.socketio.emit
        def send(*args, **kwargs):
            result = original_emit(*args, **kwargs)
            clock[0] += 2
            return result
        first.get_received()
        second.get_received()
        with (
            mock.patch.object(web.time, 'monotonic', side_effect=lambda: clock[0]),
            mock.patch.object(self.web_manager.socketio, 'emit', side_effect=send),
        ):
            self.web_manager._emit_admin('synthetic_expiry', {})
        self.assertEqual([e['name'] for e in first.get_received()], ['synthetic_expiry'])
        self.assertEqual(second.get_received(), [])

    def test_cookie_is_nonpermanent_and_server_deadline_is_fixed(self):
        http, admin = self._make_socket_client(authenticated=True)
        with http.session_transaction() as session:
            self.assertFalse(session.permanent)
            login_id = session['admin_login_id']
        self.assertIsNone(http.get_cookie('session').expires)
        self.assertEqual(self.web_manager.app.permanent_session_lifetime.total_seconds(), 31 * 86400)
        deadline = self.web_manager._admin_logins[login_id]['expires_at']
        http.get('/api/mounts/online')
        admin.disconnect()
        admin.connect()
        self.assertTrue(admin.is_connected())
        self.assertEqual(self.web_manager._admin_logins[login_id]['expires_at'], deadline)

    def test_relogin_at_capacity_revokes_only_previous_shared_login(self):
        self.web_manager._max_admin_logins = 2
        http, previous = self._make_socket_client(authenticated=True)
        _, independent = self._make_socket_client(authenticated=True)
        response = http.post('/api/login', json={
            'username': 'test-admin', 'password': secrets.token_urlsafe(24),
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self.web_manager._admin_logins), 2)
        self._assert_denied(previous)
        self._assert_socket_rooms(independent, authenticated=True)
        current = self.web_manager.socketio.test_client(self.web_manager.app, flask_test_client=http)
        self.addCleanup(self._disconnect_if_connected, current)
        self._assert_socket_rooms(current, authenticated=True)


if __name__ == "__main__":
    unittest.main()
