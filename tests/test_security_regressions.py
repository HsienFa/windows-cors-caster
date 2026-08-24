"""設定、認證日誌與敏感資料的安全回歸測試。"""

import ast
import configparser
import importlib.util
import os
import re
import secrets
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parent.parent
BINARY_SUFFIXES = {'.db', '.gif', '.ico', '.jpeg', '.jpg', '.png', '.webp'}


def prospective_git_files():
    completed = subprocess.run(
        ['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z'],
        cwd=PROJECT_ROOT,
        capture_output=True,
        check=True,
    )
    return tuple(
        PROJECT_ROOT / item.decode('utf-8')
        for item in completed.stdout.split(b'\0')
        if item
    )


def call_name(call):
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


class GitSensitiveDataTests(unittest.TestCase):
    def test_runtime_files_are_not_prospective_git_files(self):
        relative_paths = {
            path.relative_to(PROJECT_ROOT).as_posix()
            for path in prospective_git_files()
        }

        self.assertNotIn('config.ini', relative_paths)
        self.assertNotIn('config.windows.local.ini', relative_paths)
        self.assertFalse(any(path == '.venv' or path.startswith('.venv/') for path in relative_paths))
        self.assertFalse(any(path == 'data' or path.startswith('data/') for path in relative_paths))
        self.assertFalse(any(path == 'logs' or path.startswith('logs/') for path in relative_paths))
        self.assertFalse(any(path.lower().endswith(('.db', '.log')) for path in relative_paths))

    def test_no_known_default_admin_password_is_prospective(self):
        prohibited_password = 'admin' + '123'
        failures = []

        for path in prospective_git_files():
            if path.suffix.lower() in BINARY_SUFFIXES or not path.is_file():
                continue
            source = path.read_text(encoding='utf-8', errors='ignore')
            if prohibited_password.lower() in source.lower():
                failures.append(str(path.relative_to(PROJECT_ROOT)))

        self.assertEqual(failures, [])

    def test_no_google_maps_api_key_signature_is_prospective(self):
        google_key_pattern = re.compile(('AI' + 'za') + r'[0-9A-Za-z_-]{35}')
        failures = []

        for path in prospective_git_files():
            if path.suffix.lower() in BINARY_SUFFIXES or not path.is_file():
                continue
            source = path.read_text(encoding='utf-8', errors='ignore')
            if google_key_pattern.search(source):
                failures.append(str(path.relative_to(PROJECT_ROOT)))

        self.assertEqual(failures, [])

    def test_secret_key_assignments_are_placeholders_or_runtime_values(self):
        assignment_pattern = re.compile(
            r'(?i)(?:[A-Z0-9_]*SECRET_KEY|secret_key)\s*[:=]\s*["\']?([^\s#"\']+)'
        )
        allowed_prefixes = (
            '$',
            '%',
            'config.',
            'get_config_value',
            'none',
            'null',
            'os.environ',
            'replace_with_',
            'secrets.',
            'your-',
        )
        placeholder_markers = ('change', 'example', 'placeholder', 'replace', 'your-')
        failures = []

        for path in prospective_git_files():
            if path.suffix.lower() in BINARY_SUFFIXES or not path.is_file():
                continue

            source = path.read_text(encoding='utf-8', errors='ignore')
            if path.suffix.lower() == '.py':
                tree = ast.parse(source)

                def target_names(target):
                    if isinstance(target, ast.Name):
                        return (target.id,)
                    if isinstance(target, ast.Attribute):
                        return (target.attr,)
                    if isinstance(target, ast.Subscript):
                        key = target.slice
                        if isinstance(key, ast.Constant) and isinstance(key.value, str):
                            return (key.value,)
                    return ()

                secret_literals = []
                for node in ast.walk(tree):
                    candidates = []
                    if isinstance(node, ast.Assign):
                        candidates.extend(
                            (name, node.value)
                            for target in node.targets
                            for name in target_names(target)
                        )
                    elif isinstance(node, ast.AnnAssign):
                        candidates.extend((name, node.value) for name in target_names(node.target))
                    elif isinstance(node, ast.Dict):
                        candidates.extend(
                            (key.value, value)
                            for key, value in zip(node.keys, node.values)
                            if isinstance(key, ast.Constant) and isinstance(key.value, str)
                        )
                    elif isinstance(node, ast.keyword) and node.arg:
                        candidates.append((node.arg, node.value))

                    for name, value_node in candidates:
                        if 'secret_key' not in name.lower():
                            continue
                        if not isinstance(value_node, ast.Constant) or not isinstance(value_node.value, str):
                            continue
                        normalized = value_node.value.strip().lower()
                        if normalized and not any(marker in normalized for marker in placeholder_markers):
                            secret_literals.append(node.lineno)

                failures.extend(
                    f'{path.relative_to(PROJECT_ROOT)}:{line_number}'
                    for line_number in secret_literals
                )
                continue

            for line_number, line in enumerate(
                source.splitlines(),
                1,
            ):
                for match in assignment_pattern.finditer(line):
                    value = match.group(1).strip('"\'').lower()
                    if not value or value.startswith(allowed_prefixes):
                        continue
                    failures.append(f'{path.relative_to(PROJECT_ROOT)}:{line_number}')

        self.assertEqual(failures, [])

        config_source = (PROJECT_ROOT / 'src' / 'config.py').read_text(encoding='utf-8')
        config_tree = ast.parse(config_source)
        secret_assignments = [
            node for node in config_tree.body
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == 'SECRET_KEY' for target in node.targets)
        ]
        self.assertEqual(len(secret_assignments), 1)
        secret_call = secret_assignments[0].value
        self.assertIsInstance(secret_call, ast.Call)
        self.assertEqual(call_name(secret_call), 'get_config_value')
        self.assertGreaterEqual(len(secret_call.args), 3)
        self.assertIsNone(secret_call.args[2].value)


class ConfigurationValidationTests(unittest.TestCase):
    def _load_config_module(self, secret_value, password_value, include_secret=True):
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        temporary_root = Path(temporary_directory.name)
        config_path = temporary_root / 'security-test.ini'

        lines = []
        if include_secret:
            lines.extend(('[security]', f'secret_key = {secret_value}'))
        lines.extend(
            (
                '[admin]',
                'username = admin',
                f'password = {password_value}',
                '[database]',
                f'path = {(temporary_root / "data" / "test.db").as_posix()}',
                '[logging]',
                f'log_dir = {(temporary_root / "logs").as_posix()}',
            )
        )
        config_path.write_text('\n'.join(lines) + '\n', encoding='utf-8')

        module_name = f'security_config_{id(self)}_{len(self._cleanups)}'
        spec = importlib.util.spec_from_file_location(
            module_name,
            PROJECT_ROOT / 'src' / 'config.py',
        )
        module = importlib.util.module_from_spec(spec)
        with mock.patch.dict(os.environ, {'NTRIP_CONFIG_FILE': str(config_path)}, clear=True):
            spec.loader.exec_module(module)
        return module

    def test_safe_secret_and_admin_password_are_accepted(self):
        module = self._load_config_module(
            secrets.token_urlsafe(48),
            secrets.token_urlsafe(24),
        )

        self.assertTrue(module.init_config())
        self.assertEqual(module.FLASK_SECRET_KEY, module.SECRET_KEY)

    def test_unsafe_secret_or_admin_password_refuses_startup(self):
        valid_secret = secrets.token_urlsafe(48)
        valid_password = secrets.token_urlsafe(24)
        known_default_password = 'admin' + '123'
        cases = (
            ('missing secret', None, valid_password, False, 'security.secret_key'),
            ('placeholder secret', 'REPLACE_WITH_RANDOM_SECRET_KEY', valid_password, True, 'security.secret_key'),
            ('short secret', 'too-short', valid_password, True, 'security.secret_key'),
            ('empty password', valid_secret, '', True, 'admin.password'),
            ('known default password', valid_secret, known_default_password, True, 'admin.password'),
            ('placeholder password', valid_secret, 'REPLACE_WITH_STRONG_ADMIN_PASSWORD', True, 'admin.password'),
        )

        for name, secret_value, password_value, include_secret, expected_error in cases:
            with self.subTest(name=name):
                module = self._load_config_module(
                    secret_value,
                    password_value,
                    include_secret=include_secret,
                )
                with self.assertRaisesRegex(ValueError, re.escape(expected_error)):
                    module.init_config()


class SensitiveLoggingTests(unittest.TestCase):
    def test_authorization_header_content_is_not_logged(self):
        source = (PROJECT_ROOT / 'src' / 'ntrip.py').read_text(encoding='utf-8')
        tree = ast.parse(source)
        log_calls = {
            'log_debug',
            'log_error',
            'log_info',
            'log_system_event',
            'log_warning',
        }
        failures = []

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or call_name(node) not in log_calls:
                continue
            exposes_header = any(
                isinstance(child, ast.Name) and child.id.lower() == 'auth_header'
                for child in ast.walk(node)
            )
            reads_authorization = any(
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Attribute)
                and child.func.attr == 'get'
                and child.args
                and isinstance(child.args[0], ast.Constant)
                and str(child.args[0].value).lower() == 'authorization'
                for child in ast.walk(node)
            )
            if exposes_header or reads_authorization:
                failures.append(f'src/ntrip.py:{node.lineno}')

        self.assertEqual(failures, [])
        self.assertNotRegex(source, r'auth_header\s*\[\s*:\s*50\s*\]')
        self.assertIn('已收到認證標頭', source)

        sanitizer = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == '_sanitize_request_for_logging'
        )
        sanitizer_source = ast.get_source_segment(source, sanitizer) or ''
        self.assertNotIn('Authorization:', sanitizer_source)
        self.assertNotIn('Basic [REDACTED]', sanitizer_source)
        self.assertNotIn('Digest [REDACTED]', sanitizer_source)
        self.assertIn("sanitized_lines.append('已收到認證標頭')", sanitizer_source)

    def test_database_never_prints_admin_password_value(self):
        source = (PROJECT_ROOT / 'src' / 'database.py').read_text(encoding='utf-8')
        tree = ast.parse(source)
        print_calls = [
            ast.get_source_segment(source, node) or ''
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and call_name(node) == 'print'
        ]

        self.assertTrue(print_calls)
        for call in print_calls:
            self.assertNotIn('admin_password', call)
            self.assertNotIn("DEFAULT_ADMIN['password']", call)

    def test_google_key_is_not_sent_to_logs_json_or_socketio(self):
        prohibited_calls = {
            'emit',
            'jsonify',
            'log_debug',
            'log_error',
            'log_info',
            'log_system_event',
            'log_warning',
            'print',
        }
        sensitive_identifiers = {
            'GOOGLE_MAPS_API_KEY',
            'google_maps_script_url',
            'get_google_maps_script_url',
        }
        failures = []

        for relative_path in ('src/config.py', 'src/web.py'):
            path = PROJECT_ROOT / relative_path
            source = path.read_text(encoding='utf-8')
            tree = ast.parse(source, filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or call_name(node) not in prohibited_calls:
                    continue
                identifiers = {
                    child.id
                    for child in ast.walk(node)
                    if isinstance(child, ast.Name)
                }
                identifiers.update(
                    child.attr
                    for child in ast.walk(node)
                    if isinstance(child, ast.Attribute)
                )
                if identifiers & sensitive_identifiers:
                    failures.append(f'{relative_path}:{node.lineno}')

        self.assertEqual(failures, [])

        app_source = (PROJECT_ROOT / 'static' / 'app.js').read_text(encoding='utf-8')
        self.assertNotIn('GOOGLE_MAPS_API_KEY', app_source)
        self.assertNotIn('google_maps_api_key', app_source)

        web_source = (PROJECT_ROOT / 'src' / 'web.py').read_text(encoding='utf-8')
        self.assertNotIn('GOOGLE_MAPS_API_KEY', web_source)
        self.assertNotIn('google_maps_api_key', web_source)

        template_source = (PROJECT_ROOT / 'templates' / 'spa.html').read_text(encoding='utf-8')
        self.assertEqual(template_source.count('google_maps_script_url'), 2)
        self.assertEqual(
            template_source.count('src="{{ google_maps_script_url }}"'),
            1,
        )

        config_source = (PROJECT_ROOT / 'src' / 'config.py').read_text(encoding='utf-8')
        config_tree = ast.parse(config_source)
        config_dict_function = next(
            node for node in config_tree.body
            if isinstance(node, ast.FunctionDef) and node.name == 'get_config_dict'
        )
        config_dict_source = ast.get_source_segment(config_source, config_dict_function) or ''
        self.assertNotIn('GOOGLE_MAPS_API_KEY', config_dict_source)
        self.assertNotIn('google_maps_api_key', config_dict_source)


class SafeExampleTests(unittest.TestCase):
    def test_config_example_requires_secret_and_password_replacement(self):
        parser = configparser.ConfigParser()
        parser.read(PROJECT_ROOT / 'config.ini.example', encoding='utf-8')

        self.assertEqual(parser.get('network', 'host'), '127.0.0.1')
        self.assertEqual(
            parser.get('security', 'secret_key'),
            'REPLACE_WITH_RANDOM_SECRET_KEY',
        )
        self.assertEqual(
            parser.get('admin', 'password'),
            'REPLACE_WITH_STRONG_ADMIN_PASSWORD',
        )

    def test_ntrip_listener_honors_configured_host(self):
        source = (PROJECT_ROOT / 'src' / 'ntrip.py').read_text(encoding='utf-8')

        self.assertIn('self.server_socket.bind((config.NTRIP_HOST, NTRIP_PORT))', source)
        self.assertNotIn("self.server_socket.bind(('0.0.0.0', NTRIP_PORT))", source)


if __name__ == '__main__':
    unittest.main()
