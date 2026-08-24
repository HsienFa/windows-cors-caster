"""Windows 原生相容性单元测试。"""

import configparser
import importlib.util
import os
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Codex 的内建测试 Python 不一定附带项目依赖；测试通过注入资料验证 API 使用方式。
try:
    import psutil  # noqa: F401
except ModuleNotFoundError:
    psutil_stub = types.ModuleType('psutil')
    psutil_stub.net_connections = lambda kind='tcp': []
    psutil_stub.virtual_memory = lambda: SimpleNamespace(percent=0.0)
    psutil_stub.disk_usage = lambda path: SimpleNamespace(percent=0.0)
    sys.modules['psutil'] = psutil_stub

import healthcheck
from src.network_utils import inspect_established_remote_ips


class ConfigPathTests(unittest.TestCase):
    def _load_config_module(self, config_text):
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        config_path = Path(temporary_directory.name) / 'test-config.ini'
        config_path.write_text(config_text, encoding='utf-8')

        module_name = f"windows_compat_config_{id(self)}_{len(self._cleanups)}"
        spec = importlib.util.spec_from_file_location(module_name, PROJECT_ROOT / 'src' / 'config.py')
        module = importlib.util.module_from_spec(spec)

        with mock.patch.dict(os.environ, {'NTRIP_CONFIG_FILE': str(config_path)}, clear=True):
            spec.loader.exec_module(module)
        return module

    def test_relative_database_and_log_paths_use_project_root(self):
        config_module = self._load_config_module(
            "[database]\npath = data/test.db\n"
            "[logging]\nlog_dir = logs/test\n"
        )

        self.assertEqual(config_module.DATABASE_PATH, (PROJECT_ROOT / 'data' / 'test.db').resolve())
        self.assertEqual(config_module.LOG_DIR, (PROJECT_ROOT / 'logs' / 'test').resolve())
        self.assertIsInstance(config_module.DATABASE_PATH, Path)
        self.assertIsInstance(config_module.LOG_DIR, Path)

    def test_config_example_matches_runtime_schema(self):
        example_path = PROJECT_ROOT / 'config.ini.example'
        example_text = example_path.read_text(encoding='utf-8')
        parser = configparser.ConfigParser()
        parser.read_string(example_text)

        expected_sections = {
            'app', 'caster', 'development', 'network', 'ntrip', 'web',
            'map', 'database', 'logging', 'security', 'admin', 'tcp',
            'data_forwarding', 'rtcm', 'websocket', 'performance',
        }
        self.assertEqual(set(parser.sections()), expected_sections)
        self.assertTrue(all(section == section.lower() for section in parser.sections()))
        self.assertEqual(parser.get('database', 'path'), 'data/2rtk.db')
        self.assertEqual(parser.get('logging', 'log_dir'), 'logs')
        self.assertEqual(parser.get('map', 'provider'), 'osm')
        self.assertEqual(parser.get('map', 'google_maps_api_key'), '')
        self.assertEqual(parser.getfloat('map', 'default_latitude'), 23.7)
        self.assertEqual(parser.getfloat('map', 'default_longitude'), 121.0)
        self.assertEqual(parser.getint('map', 'default_zoom'), 7)
        self.assertNotIn('/app/', example_text)

    def test_google_provider_without_key_falls_back_to_osm(self):
        config_module = self._load_config_module(
            '[map]\n'
            'provider = google\n'
            'google_maps_api_key =\n'
            'default_latitude = 23.7\n'
            'default_longitude = 121.0\n'
            'default_zoom = 7\n'
        )

        self.assertEqual(config_module.get_effective_map_provider(), 'osm')
        self.assertIsNone(config_module.get_google_maps_script_url())
        self.assertNotIn('google_maps_api_key', config_module.get_public_map_config())

    def test_google_key_environment_override_only_builds_official_script_url(self):
        config_module = self._load_config_module(
            '[map]\n'
            'provider = google\n'
            'google_maps_api_key = file-test-value\n'
            'default_latitude = 23.7\n'
            'default_longitude = 121.0\n'
            'default_zoom = 7\n'
        )
        environment_key = 'environment-test-value'

        with mock.patch.dict(
            os.environ,
            {'GOOGLE_MAPS_API_KEY': environment_key},
            clear=True,
        ):
            config_module.load_from_env()

        script_url = config_module.get_google_maps_script_url()
        parsed_url = urlsplit(script_url)
        query = parse_qs(parsed_url.query)

        self.assertEqual(parsed_url.scheme, 'https')
        self.assertEqual(parsed_url.netloc, 'maps.googleapis.com')
        self.assertEqual(parsed_url.path, '/maps/api/js')
        self.assertEqual(query['key'], [environment_key])
        self.assertEqual(query['callback'], ['googleMapsApiReady'])
        self.assertEqual(query['loading'], ['async'])
        self.assertNotIn(environment_key, repr(config_module.get_public_map_config()))

    def test_logger_uses_pathlib_for_log_files(self):
        logger_source = (PROJECT_ROOT / 'src' / 'logger.py').read_text(encoding='utf-8')
        self.assertNotIn('os.path.join', logger_source)
        self.assertIn('Path(config.LOG_DIR)', logger_source)
        self.assertIn('mkdir(parents=True, exist_ok=True)', logger_source)


class HealthCheckTests(unittest.TestCase):
    def test_memory_check_uses_psutil(self):
        checker = healthcheck.HealthChecker(PROJECT_ROOT)
        with mock.patch.object(healthcheck.psutil, 'virtual_memory', return_value=SimpleNamespace(percent=42.5)):
            success, message = checker.check_memory_usage()

        self.assertTrue(success)
        self.assertIn('42.5%', message)

    def test_disk_check_uses_project_path(self):
        checker = healthcheck.HealthChecker(PROJECT_ROOT)
        with mock.patch.object(
            healthcheck.psutil,
            'disk_usage',
            return_value=SimpleNamespace(percent=37.0),
        ) as disk_usage:
            success, message = checker.check_disk_space()

        self.assertTrue(success)
        self.assertIn('37.0%', message)
        disk_usage.assert_called_once_with(str(PROJECT_ROOT.resolve()))

    def test_healthcheck_has_no_linux_fixed_paths(self):
        source = (PROJECT_ROOT / 'healthcheck.py').read_text(encoding='utf-8')
        self.assertNotIn('/proc/meminfo', source)
        self.assertNotIn("disk_usage('/app')", source)


class NetworkInspectionTests(unittest.TestCase):
    def test_collects_established_remote_ips_for_ntrip_port(self):
        connections = [
            SimpleNamespace(status='ESTABLISHED', laddr=('0.0.0.0', 2101), raddr=('192.0.2.10', 50100)),
            SimpleNamespace(status='LISTEN', laddr=('0.0.0.0', 2101), raddr=()),
            SimpleNamespace(status='ESTABLISHED', laddr=('0.0.0.0', 5757), raddr=('192.0.2.20', 50101)),
        ]

        result = inspect_established_remote_ips(2101, lambda kind: connections)

        self.assertTrue(result.success)
        self.assertEqual(result.remote_ips, frozenset({'192.0.2.10'}))
        self.assertIsNone(result.error)

    def test_connection_inspection_failure_is_non_fatal(self):
        def denied_provider(kind):
            raise PermissionError('access denied')

        result = inspect_established_remote_ips(2101, denied_provider)

        self.assertFalse(result.success)
        self.assertEqual(result.remote_ips, frozenset())
        self.assertIn('access denied', result.error)

    def test_netstat_command_is_no_longer_used(self):
        source = (PROJECT_ROOT / 'src' / 'connection.py').read_text(encoding='utf-8')
        self.assertNotIn('subprocess.run', source)
        self.assertNotIn("'netstat'", source)
        self.assertIn('inspect_established_remote_ips', source)


class WindowsLauncherTests(unittest.TestCase):
    def _prepare_batch_test_directory(self, missing=None):
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        test_root = Path(temporary_directory.name) / 'Windows Launcher Test'
        scripts_directory = test_root / '.venv' / 'Scripts'
        test_root.mkdir(parents=True)

        launcher_source = PROJECT_ROOT / 'start-windows.bat'
        launcher_path = test_root / 'start-windows.bat'
        launcher_path.write_bytes(launcher_source.read_bytes())

        if missing != 'venv':
            scripts_directory.mkdir(parents=True)
            if missing != 'python':
                (scripts_directory / 'python.exe').touch()
        if missing != 'main':
            (test_root / 'main.py').touch()
        if missing != 'config':
            (test_root / 'config.windows.local.ini').touch()

        return test_root, launcher_path

    def _run_batch_check(self, launcher_path, working_directory):
        command_processor = os.environ.get('COMSPEC', 'cmd.exe')
        return subprocess.run(
            [command_processor, '/d', '/c', 'call', str(launcher_path), '--check'],
            cwd=working_directory,
            capture_output=True,
            text=True,
            encoding='ascii',
            errors='replace',
            check=False,
        )

    def test_launchers_require_64_bit_python_311(self):
        powershell_script = (PROJECT_ROOT / 'start-windows.ps1').read_text(encoding='utf-8')
        batch_script = (PROJECT_ROOT / 'start-windows.bat').read_text(encoding='utf-8')

        for script in (powershell_script, batch_script):
            self.assertIn("sys.version_info[:2] == (3, 11)", script)
            self.assertIn("struct.calcsize('P') * 8 == 64", script)
            self.assertIn('.venv', script)
            self.assertIn('config.windows.local.ini', script)
            self.assertIn('--config', script)

        self.assertNotIn('[string]$ConfigPath', powershell_script)

    def test_batch_launcher_is_ascii_crlf_without_bom(self):
        batch_bytes = (PROJECT_ROOT / 'start-windows.bat').read_bytes()
        batch_bytes.decode('ascii')

        self.assertFalse(batch_bytes.startswith(b'\xef\xbb\xbf'))
        self.assertNotIn(b'\n', batch_bytes.replace(b'\r\n', b''))
        self.assertIn(b'\r\n', batch_bytes)

        attributes = (PROJECT_ROOT / '.gitattributes').read_text(encoding='utf-8')
        self.assertIn('*.bat text eol=crlf', attributes)
        self.assertIn('*.cmd text eol=crlf', attributes)

    def test_batch_launcher_uses_quoted_project_paths_and_check_mode(self):
        batch_script = (PROJECT_ROOT / 'start-windows.bat').read_text(encoding='ascii')

        self.assertIn('set "PROJECT_ROOT=%~dp0"', batch_script)
        self.assertIn('set "PYTHON_EXE=%VENV_DIR%\\Scripts\\python.exe"', batch_script)
        self.assertIn('set "MAIN_PATH=%PROJECT_ROOT%main.py"', batch_script)
        self.assertIn('set "CONFIG_PATH=%PROJECT_ROOT%config.windows.local.ini"', batch_script)
        self.assertIn('if /I "%~1"=="--check"', batch_script)
        self.assertIn('Windows launcher check OK', batch_script)
        self.assertNotIn('chcp', batch_script.lower())

    def test_batch_check_mode_does_not_start_python(self):
        test_root, launcher_path = self._prepare_batch_test_directory()

        completed = self._run_batch_check(launcher_path, test_root)

        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn('Windows launcher check OK', completed.stdout)

    def test_batch_check_mode_reports_missing_required_paths(self):
        expected_errors = {
            'venv': 'Virtual environment directory not found',
            'python': 'Python executable not found',
            'main': 'Application entry point not found',
            'config': 'Local configuration file not found',
        }

        for missing, expected_error in expected_errors.items():
            with self.subTest(missing=missing):
                test_root, launcher_path = self._prepare_batch_test_directory(missing=missing)
                completed = self._run_batch_check(launcher_path, test_root)

                self.assertEqual(completed.returncode, 1)
                self.assertIn(expected_error, completed.stdout)


if __name__ == '__main__':
    unittest.main()
