"""Graceful shutdown regressions for Windows and cross-platform service use."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import signal
import socket
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parent.parent
MAIN_PATH = PROJECT_ROOT / "main.py"


class RecordingThread:
    def __init__(self, events=None, name="thread", remains_alive=False):
        self.events = events
        self.name = name
        self.remains_alive = remains_alive
        self.alive = True
        self.join_timeouts = []

    def is_alive(self):
        return self.alive

    def join(self, timeout=None):
        self.join_timeouts.append(timeout)
        if self.events is not None:
            self.events.append(f"{self.name}.join")
        if not self.remains_alive:
            self.alive = False


class RecordingSocket:
    def __init__(self):
        self.shutdown_calls = []
        self.close_calls = 0

    def shutdown(self, how):
        self.shutdown_calls.append(how)

    def close(self):
        self.close_calls += 1


class RecordingPool:
    def __init__(self, worker_threads):
        self._threads = set(worker_threads)
        self.shutdown_calls = []

    def shutdown(self, wait=True, cancel_futures=False):
        self.shutdown_calls.append((wait, cancel_futures))


class GracefulShutdownTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary_directory = tempfile.TemporaryDirectory()
        temporary_root = Path(cls.temporary_directory.name)
        config_path = temporary_root / "shutdown-test.ini"
        config_path.write_text(
            "[database]\n"
            f"path = {(temporary_root / 'shutdown-test.db').as_posix()}\n"
            "[logging]\n"
            f"log_dir = {(temporary_root / 'logs').as_posix()}\n",
            encoding="utf-8",
        )

        previous_config = os.environ.get("NTRIP_CONFIG_FILE")
        previous_argv = sys.argv[:]
        os.environ["NTRIP_CONFIG_FILE"] = str(config_path)
        sys.argv = [str(MAIN_PATH)]
        try:
            spec = importlib.util.spec_from_file_location(
                "graceful_shutdown_test_main",
                MAIN_PATH,
            )
            cls.main_module = importlib.util.module_from_spec(spec)
            with contextlib.redirect_stdout(io.StringIO()):
                spec.loader.exec_module(cls.main_module)
        finally:
            sys.argv = previous_argv
            if previous_config is None:
                os.environ.pop("NTRIP_CONFIG_FILE", None)
            else:
                os.environ["NTRIP_CONFIG_FILE"] = previous_config

    @classmethod
    def tearDownClass(cls):
        cls.main_module.logger.shutdown_logging()
        cls.temporary_directory.cleanup()

    def setUp(self):
        for method_name in (
            "log_system_event",
            "log_error",
            "log_warning",
        ):
            patcher = mock.patch.object(self.main_module.logger, method_name)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _service_fixture(self, web_failure=False):
        events = []

        class NtripService:
            running = True

            def stop_accepting(self):
                events.append("ntrip.stop_accepting")

            def close_client_connections(self):
                events.append("ntrip.close_clients")

            def stop(self):
                events.append("ntrip.stop_pool")

        class WebService:
            def stop_web_server(self):
                events.append("web.stop_server")
                if web_failure:
                    raise RuntimeError("test web shutdown failure")

            def stop_rtcm_parsing(self):
                events.append("web.stop_push")

        class ConnectionService:
            def close_all_connections(self):
                events.append("connections.close_all")

        manager = self.main_module.ServiceManager()
        manager.running = True
        manager.ntrip_caster = NtripService()
        manager.web_manager = WebService()
        manager.stats_thread = RecordingThread(events, "stats")
        manager.ntrip_thread = RecordingThread(events, "ntrip_thread")
        manager.web_thread = RecordingThread(events, "web_thread")
        return manager, events, ConnectionService()

    def _shutdown_patches(self, events, connection_service):
        return (
            mock.patch.object(
                self.main_module.forwarder,
                "stop_forwarder",
                side_effect=lambda: events.append("forwarder.stop"),
            ),
            mock.patch.object(
                self.main_module.parser_manager,
                "stop_all",
                side_effect=lambda: events.append("parsers.stop_all"),
            ),
            mock.patch.object(
                self.main_module,
                "get_connection_manager",
                return_value=connection_service,
            ),
        )

    def test_sigint_and_finally_cleanup_execute_complete_sequence_once(self):
        manager, events, connection_service = self._service_fixture()
        patches = self._shutdown_patches(events, connection_service)

        with patches[0], patches[1], patches[2]:
            manager._signal_handler(signal.SIGINT, None)
            second_result = manager.stop_all_services()

        self.assertFalse(second_result)
        self.assertTrue(manager.stopped)
        self.assertTrue(manager.shutdown_complete.is_set())
        self.assertEqual(
            events,
            [
                "ntrip.stop_accepting",
                "web.stop_server",
                "stats.join",
                "web.stop_push",
                "parsers.stop_all",
                "forwarder.stop",
                "ntrip.close_clients",
                "connections.close_all",
                "ntrip.stop_pool",
                "ntrip_thread.join",
                "web_thread.join",
            ],
        )

    def test_component_failure_does_not_skip_remaining_cleanup(self):
        manager, events, connection_service = self._service_fixture(web_failure=True)
        patches = self._shutdown_patches(events, connection_service)

        with patches[0], patches[1], patches[2]:
            result = manager.stop_all_services()

        self.assertTrue(result)
        self.assertIn("web.stop_server", events)
        self.assertIn("parsers.stop_all", events)
        self.assertIn("forwarder.stop", events)
        self.assertIn("connections.close_all", events)
        self.assertIn("ntrip.stop_pool", events)
        self.assertTrue(manager.shutdown_complete.is_set())

    def test_thread_join_has_bounded_timeout(self):
        manager = self.main_module.ServiceManager()
        thread = RecordingThread(remains_alive=True)

        manager._join_thread(thread, "test thread")

        self.assertEqual(thread.join_timeouts, [manager.THREAD_JOIN_TIMEOUT])
        self.assertLessEqual(manager.THREAD_JOIN_TIMEOUT, 5.0)
        self.main_module.logger.log_warning.assert_called_once()

    def test_ntrip_stop_closes_listener_clients_and_pool_once(self):
        caster = self.main_module.NTRIPCaster(db_manager=None)
        listener = RecordingSocket()
        client = RecordingSocket()
        handler_thread = RecordingThread(name="handler")
        worker_thread = RecordingThread(name="worker")
        pool = RecordingPool([worker_thread])
        caster.server_socket = listener
        caster.running = True
        caster.connection_handler_thread = handler_thread
        caster.thread_pool = pool
        caster._register_client_socket(client)

        ntrip_module = sys.modules[caster.__class__.__module__]
        with (
            mock.patch.object(ntrip_module, "log_system_event"),
            mock.patch.object(ntrip_module, "log_warning"),
            mock.patch.object(ntrip_module.logger, "log_system_event"),
        ):
            caster.stop()
            caster.stop()

        self.assertFalse(caster.running)
        self.assertIsNone(caster.server_socket)
        self.assertEqual(listener.shutdown_calls, [socket.SHUT_RDWR])
        self.assertEqual(listener.close_calls, 1)
        self.assertEqual(client.shutdown_calls, [socket.SHUT_RDWR])
        self.assertEqual(client.close_calls, 1)
        self.assertEqual(pool.shutdown_calls, [(False, True)])
        self.assertEqual(handler_thread.join_timeouts, [caster.THREAD_JOIN_TIMEOUT])
        self.assertEqual(worker_thread.join_timeouts, [caster.THREAD_JOIN_TIMEOUT])

    def test_connection_manager_closes_mount_and_user_sockets(self):
        connection_module = sys.modules["src.connection"]
        manager = connection_module.ConnectionManager()
        mount_socket = RecordingSocket()
        user_socket = RecordingSocket()
        manager.online_mounts["TEST"] = SimpleNamespace(
            client_socket=mount_socket,
            status="online",
            total_bytes=0,
            total_messages=0,
            data_rate=0.0,
            data_count=0,
            idle_time=0.0,
            uptime=0.0,
        )
        manager.online_users["user"].append(
            {"mount_name": "TEST", "client_socket": user_socket}
        )
        manager.user_connection_count["user"] = 1
        manager.mount_connection_count["TEST"] = 1

        with (
            mock.patch.object(connection_module, "log_info"),
            mock.patch.object(connection_module, "log_debug"),
        ):
            closed_count = manager.close_all_connections()
            repeated_count = manager.close_all_connections()

        self.assertEqual(closed_count, 2)
        self.assertEqual(repeated_count, 0)
        self.assertEqual(mount_socket.shutdown_calls, [socket.SHUT_RDWR])
        self.assertEqual(user_socket.shutdown_calls, [socket.SHUT_RDWR])
        self.assertFalse(manager.online_mounts)
        self.assertFalse(manager.online_users)

    def test_web_server_has_explicit_shutdown_handle(self):
        web_module = sys.modules["src.web"]
        web_manager = web_module.WebManager.__new__(web_module.WebManager)
        web_manager.app = object()
        web_manager._server_lock = threading.Lock()
        web_manager._http_server = None
        web_manager._web_stop_requested = False

        class BlockingServer:
            def __init__(self):
                self.started = threading.Event()
                self.stopped = threading.Event()
                self.shutdown_calls = 0
                self.close_calls = 0

            def serve_forever(self):
                self.started.set()
                self.stopped.wait(1)

            def shutdown(self):
                self.shutdown_calls += 1
                self.stopped.set()

            def server_close(self):
                self.close_calls += 1

        server = BlockingServer()
        with mock.patch.object(
            web_module,
            "make_server",
            return_value=server,
        ) as server_factory:
            run_thread = threading.Thread(
                target=lambda: web_manager.run(
                    host="127.0.0.1",
                    port=5757,
                    debug=False,
                )
            )
            run_thread.start()
            self.assertTrue(server.started.wait(1))
            web_manager.stop_web_server()
            web_manager.stop_web_server()
            run_thread.join(timeout=1)

        self.assertFalse(run_thread.is_alive())
        server_factory.assert_called_once_with(
            "127.0.0.1",
            5757,
            web_manager.app,
            threaded=True,
        )
        self.assertEqual(server.shutdown_calls, 1)
        self.assertEqual(server.close_calls, 1)

    def test_web_manager_initializes_threading_primitives(self):
        web_module = sys.modules["src.web"]

        with (
            mock.patch.object(web_module.connection, "ConnectionManager"),
            mock.patch.object(web_module, "SocketIO"),
            mock.patch.object(web_module.WebManager, "_register_routes"),
            mock.patch.object(web_module.WebManager, "_register_socketio_events"),
            mock.patch.object(web_module.logger, "set_web_instance"),
        ):
            web_manager = web_module.WebManager(
                db_manager=object(),
                data_forwarder=object(),
                start_time=0,
            )

        self.assertIsInstance(
            web_manager._push_stop_event,
            type(threading.Event()),
        )
        self.assertIsInstance(
            web_manager._server_lock,
            type(threading.Lock()),
        )

    def test_windows_launcher_keeps_python_in_foreground_for_ctrl_c(self):
        launcher_path = PROJECT_ROOT / "start-windows.bat"
        launcher_bytes = launcher_path.read_bytes()
        launcher = launcher_bytes.decode("ascii")

        self.assertFalse(launcher_bytes.startswith(b"\xef\xbb\xbf"))
        self.assertNotIn(b"\n", launcher_bytes.replace(b"\r\n", b""))
        self.assertIn(
            '"%PYTHON_EXE%" "%MAIN_PATH%" --config "%CONFIG_PATH%"',
            launcher,
        )
        self.assertNotIn("start /b", launcher.lower())
        self.assertNotIn("taskkill", launcher.lower())

        main_source = MAIN_PATH.read_text(encoding="utf-8")
        web_source = (PROJECT_ROOT / "src" / "web.py").read_text(encoding="utf-8")
        app_source = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("signal.SIGINT", main_source)
        self.assertIn("signal.SIGTERM", main_source)
        self.assertNotIn("os._exit", main_source)
        self.assertNotIn("os._exit", web_source)
        self.assertNotIn("Stop-Process", main_source)
        self.assertIn("安全關閉程式", app_source)
        self.assertNotIn("window.location.reload()", app_source)


if __name__ == "__main__":
    unittest.main()
