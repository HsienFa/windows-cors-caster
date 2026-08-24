"""Docker/Linux deployment security and application policy regressions."""

from __future__ import annotations

import argparse
import configparser
import contextlib
import importlib.util
import io
import os
import re
import secrets
import tempfile
import unittest
from pathlib import Path
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_deployment_helper():
    helper_path = PROJECT_ROOT / "scripts" / "deployment_config.py"
    spec = importlib.util.spec_from_file_location("deployment_config_test", helper_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_compose_security_options(path):
    source = path.read_text(encoding="utf-8")
    service_pattern = re.compile(
        r"(?ms)^  (?P<service>[a-z0-9_-]+):\n"
        r"(?P<body>.*?)(?=^  [a-z0-9_-]+:\n|^\S|\Z)"
    )
    option_pattern = re.compile(
        r"(?m)^    security_opt:\n(?P<items>(?:      - [^\n]+\n?)+)"
    )
    result = {}
    for service_match in service_pattern.finditer(source):
        option_match = option_pattern.search(service_match.group("body"))
        if option_match is None:
            continue
        result[service_match.group("service")] = [
            line.strip()[2:].strip()
            for line in option_match.group("items").splitlines()
        ]
    return result


def read_compose_mount_targets(path):
    source = path.read_text(encoding="utf-8")
    service_pattern = re.compile(
        r"(?ms)^  (?P<service>[a-z0-9_-]+):\n"
        r"(?P<body>.*?)(?=^  [a-z0-9_-]+:\n|^\S|\Z)"
    )
    result = {}
    for service_match in service_pattern.finditer(source):
        service_mounts = {"volumes": [], "tmpfs": []}
        for mount_type in service_mounts:
            list_match = re.search(
                rf"(?m)^    {mount_type}:\n(?P<items>(?:      - [^\n]+\n?)+)",
                service_match.group("body"),
            )
            if list_match is None:
                continue
            for line in list_match.group("items").splitlines():
                item = line.strip()[2:].strip()
                parts = item.split(":")
                target = parts[1] if mount_type == "volumes" else parts[0]
                service_mounts[mount_type].append(target)
        if any(service_mounts.values()):
            result[service_match.group("service")] = service_mounts
    return result


class DeploymentConfigHelperTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.helper = load_deployment_helper()

    def test_runtime_config_uses_current_lowercase_schema(self):
        admin_password = secrets.token_urlsafe(24)
        secret_value = self.helper.generate_secret()
        parser = self.helper.build_runtime_config(
            ntrip_host="0.0.0.0",
            web_host="127.0.0.1",
            ntrip_port=2101,
            web_port=5757,
            database_path="data/2rtk.db",
            log_dir="logs",
            admin_username="admin",
            admin_password=admin_password,
            secret_value=secret_value,
        )

        expected_sections = {
            "app", "caster", "development", "network", "ntrip", "web", "map",
            "database", "logging", "security", "admin", "tcp", "data_forwarding",
            "rtcm", "websocket", "performance",
        }
        self.assertEqual(set(parser.sections()), expected_sections)
        self.assertEqual(parser.get("ntrip", "host"), "0.0.0.0")
        self.assertEqual(parser.get("web", "host"), "127.0.0.1")
        self.assertEqual(parser.get("map", "provider"), "osm")
        self.assertTrue(self.helper.is_secure_secret(parser.get("security", "secret_key")))
        self.assertTrue(self.helper.is_secure_password(parser.get("admin", "password")))

    def test_prepare_env_generates_values_without_printing_them(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_root = Path(temporary_directory)
            example_path = temporary_root / ".env.example"
            env_path = temporary_root / ".env"
            example_path.write_text(
                (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            arguments = argparse.Namespace(
                env_file=str(env_path),
                example=str(example_path),
                monitoring=True,
                environment="testing",
                profiles="monitoring",
            )

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.helper.prepare_env(arguments)

            values = self.helper._read_env_values(env_path.read_text(encoding="utf-8"))
            app_password = values["NTRIP_ADMIN_PASSWORD"]
            monitoring_password = values["GRAFANA_ADMIN_PASSWORD"]
            self.assertTrue(self.helper.is_secure_password(app_password))
            self.assertTrue(self.helper.is_secure_password(monitoring_password))
            self.assertNotIn(app_password, output.getvalue())
            self.assertNotIn(monitoring_password, output.getvalue())

    def test_generated_linux_config_is_accepted_by_runtime(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            config_path = Path(temporary_directory) / "runtime.ini"
            admin_password = secrets.token_urlsafe(24)
            arguments = argparse.Namespace(
                output=str(config_path),
                ntrip_host="0.0.0.0",
                web_host="127.0.0.1",
                ntrip_port=2101,
                web_port=5757,
                database_path=str(Path(temporary_directory) / "data" / "runtime.db"),
                log_dir=str(Path(temporary_directory) / "logs"),
                force=False,
            )
            process_environment = {
                "NTRIP_CONFIG_FILE": str(config_path),
                "NTRIP_ADMIN_USERNAME": "admin",
                "NTRIP_ADMIN_PASSWORD": admin_password,
            }
            with mock.patch.dict(os.environ, process_environment, clear=True):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.helper.write_runtime_config(arguments)

                spec = importlib.util.spec_from_file_location(
                    "deployment_generated_runtime_config",
                    PROJECT_ROOT / "src" / "config.py",
                )
                runtime_config = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(runtime_config)
                self.assertTrue(runtime_config.init_config())

            self.assertEqual(runtime_config.NTRIP_HOST, "0.0.0.0")
            self.assertEqual(runtime_config.WEB_HOST, "127.0.0.1")

    def test_known_unsafe_values_are_rejected(self):
        self.assertFalse(self.helper.is_secure_password("admin" + "123"))
        self.assertFalse(self.helper.is_secure_password("change" + "me"))
        self.assertFalse(self.helper.is_secure_secret("replace" + "_with_example"))

    def test_blank_example_and_known_default_credentials_are_rejected(self):
        unsafe_passwords = (
            "",
            "admin",
            "admin" + "123",
            "password",
            "change" + "me",
            "replace_with_private_password",
            "example-password-value",
        )
        unsafe_secrets = (
            "",
            "too-short",
            "replace_with_random_secret_value",
            "example-secret-value-that-is-long-but-unsafe",
        )
        for value in unsafe_passwords:
            with self.subTest(kind="password", value_length=len(value)):
                self.assertFalse(self.helper.is_secure_password(value))
        for value in unsafe_secrets:
            with self.subTest(kind="secret", value_length=len(value)):
                self.assertFalse(self.helper.is_secure_secret(value))


class DockerAndLinuxStaticTests(unittest.TestCase):
    def test_image_never_executes_public_config_example(self):
        dockerfile = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")
        dockerignore = (PROJECT_ROOT / ".dockerignore").read_text(encoding="utf-8")
        entrypoint = (PROJECT_ROOT / "docker-entrypoint.sh").read_text(encoding="utf-8")

        self.assertNotIn("config.ini.example", dockerfile)
        self.assertIn("scripts/deployment_config.py", dockerfile)
        self.assertIn("docker-entrypoint.sh", dockerfile)
        self.assertIn("THIRD-PARTY-NOTICES.md", dockerfile)
        self.assertIn("write-config", entrypoint)
        self.assertIn("--ntrip-host", entrypoint)
        self.assertIn("--web-host", entrypoint)
        self.assertIn("config.ini", dockerignore)
        self.assertIn("config.windows.local.ini", dockerignore)
        self.assertIn("!docker-entrypoint.sh", dockerignore)
        self.assertIn("!LICENSE", dockerignore)
        self.assertIn("!static/vendor/openlayers/8.2.0/LICENSE.md", dockerignore)

    def test_compose_publish_and_container_listeners_are_consistent(self):
        compose = (PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        production = (PROJECT_ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8")

        self.assertIn("${NTRIP_PUBLISH_HOST:-0.0.0.0}:2101:2101", compose)
        self.assertIn("${WEB_PUBLISH_HOST:-127.0.0.1}:5757:5757", compose)
        self.assertIn("${NGINX_PUBLISH_HOST:-127.0.0.1}:80:80", compose)
        self.assertIn("NTRIP_LISTEN_HOST=0.0.0.0", compose)
        self.assertIn("WEB_LISTEN_HOST=0.0.0.0", compose)
        self.assertNotIn("GF_INSTALL_PLUGINS", compose)
        self.assertIn("${NGINX_PUBLISH_HOST:-127.0.0.1}:80:80", production)
        self.assertNotIn('"80:80"', production)
        self.assertNotIn("external: true", production)
        self.assertNotIn("dhparam.pem", production)
        self.assertNotIn("your-domain", production)

    def test_compose_credentials_have_no_public_default(self):
        compose = (PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        production = (PROJECT_ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8")
        env_example = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")
        unsafe_default = "admin" + "123"

        self.assertRegex(env_example, r"(?m)^NTRIP_ADMIN_PASSWORD=$")
        self.assertRegex(env_example, r"(?m)^GRAFANA_ADMIN_PASSWORD=$")
        self.assertNotIn(unsafe_default, compose.lower())
        self.assertNotIn(unsafe_default, production.lower())
        self.assertNotIn(unsafe_default, env_example.lower())
        self.assertNotRegex(
            production,
            r"(?im)^\s*-\s*(?:NTRIP_ADMIN_PASSWORD|GF_SECURITY_ADMIN_PASSWORD)\s*=",
        )
        self.assertIn("normalized", compose)
        self.assertIn('""|admin|admin"123"|password|changeme|letmein', compose)
        self.assertIn("*replace_with_*|*placeholder*|*example*", compose)
        self.assertIn('[ "$${#password}" -lt 16 ]', compose)
        self.assertIn("exec /run.sh", compose)

    def test_compose_security_options_are_unique_after_production_merge(self):
        base_options = read_compose_security_options(PROJECT_ROOT / "docker-compose.yml")
        production_options = read_compose_security_options(
            PROJECT_ROOT / "docker-compose.prod.yml"
        )

        for source_name, options_by_service in (
            ("base", base_options),
            ("production", production_options),
        ):
            for service, options in options_by_service.items():
                with self.subTest(source=source_name, service=service):
                    self.assertEqual(len(options), len(set(options)))

        merged_options = {}
        for service in base_options.keys() | production_options.keys():
            merged_options[service] = (
                base_options.get(service, []) + production_options.get(service, [])
            )
            with self.subTest(source="merged", service=service):
                self.assertEqual(
                    len(merged_options[service]),
                    len(set(merged_options[service])),
                )

        for service, options in base_options.items():
            with self.subTest(source="base-required", service=service):
                self.assertIn("no-new-privileges:true", options)
        self.assertEqual(
            merged_options["ntrip-caster"].count("no-new-privileges:true"),
            1,
        )
        self.assertIn("apparmor:docker-default", merged_options["ntrip-caster"])

    def test_compose_volume_and_tmpfs_targets_do_not_overlap(self):
        base_mounts = read_compose_mount_targets(PROJECT_ROOT / "docker-compose.yml")
        production_mounts = read_compose_mount_targets(
            PROJECT_ROOT / "docker-compose.prod.yml"
        )

        for service in base_mounts.keys() | production_mounts.keys():
            merged_volumes = set(base_mounts.get(service, {}).get("volumes", []))
            merged_volumes.update(
                production_mounts.get(service, {}).get("volumes", [])
            )
            merged_tmpfs = set(base_mounts.get(service, {}).get("tmpfs", []))
            merged_tmpfs.update(production_mounts.get(service, {}).get("tmpfs", []))
            with self.subTest(service=service):
                self.assertEqual(merged_volumes & merged_tmpfs, set())

        nginx_mounts = base_mounts["nginx"]
        self.assertNotIn("/var/cache/nginx", nginx_mounts["volumes"])
        self.assertEqual(nginx_mounts["tmpfs"].count("/var/cache/nginx"), 1)

        compose_sources = "\n".join(
            (PROJECT_ROOT / path).read_text(encoding="utf-8")
            for path in ("docker-compose.yml", "docker-compose.prod.yml")
        )
        self.assertNotIn("nginx-cache", compose_sources)
        self.assertIn("no-new-privileges:true", compose_sources)
        self.assertRegex(
            (PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8"),
            r"(?ms)^  nginx:.*?^    read_only: true$",
        )

    def test_quick_start_prepares_monitoring_credentials_safely(self):
        quick_start = (PROJECT_ROOT / "quick-start.sh").read_text(encoding="utf-8")
        deploy = (PROJECT_ROOT / "docker-deploy.sh").read_text(encoding="utf-8")
        makefile = (PROJECT_ROOT / "Makefile").read_text(encoding="utf-8")

        self.assertIn("deployment_config.py", quick_start)
        self.assertIn("credential_args+=(--monitoring)", quick_start)
        self.assertNotRegex(quick_start, r"(?m)^\s*cp\s+.*\.env\.example\s+.*\.env\s*$")
        self.assertIn("prepare_docker_env true", deploy)
        self.assertIn("create_dirs|create_directories", deploy)
        self.assertIn("scripts/deployment_config.py prepare-env", makefile)
        self.assertNotIn("docker-compose.override.yml", makefile)
        self.assertNotIn("admin" + "/" + "admin", makefile.lower())

    def test_linux_installer_generates_config_and_keeps_web_local(self):
        installer = (PROJECT_ROOT / "install.sh").read_text(encoding="utf-8")

        self.assertIn("scripts/deployment_config.py", installer)
        self.assertIn("--ntrip-host 0.0.0.0", installer)
        self.assertIn("--web-host 127.0.0.1", installer)
        self.assertIn('chmod 0600 "$CONFIG_PATH"', installer)
        self.assertNotIn("cp config.ini.example", installer)
        self.assertNotIn("sed -i", installer)
        self.assertNotIn("listen 80", installer)
        self.assertNotIn("ufw allow 80", installer)

    def test_native_default_remains_loopback(self):
        config_source = (PROJECT_ROOT / "src" / "config.py").read_text(encoding="utf-8")
        example = configparser.ConfigParser()
        example.read(PROJECT_ROOT / "config.ini.example", encoding="utf-8")

        self.assertIn("get_config_value('network', 'host', '127.0.0.1')", config_source)
        self.assertEqual(example.get("network", "host"), "127.0.0.1")
        self.assertEqual(example.get("ntrip", "host"), "127.0.0.1")
        self.assertEqual(example.get("web", "host"), "127.0.0.1")

    def test_deployment_sources_do_not_contain_known_public_default(self):
        unsafe_default = "admin" + "123"
        paths = (
            "Dockerfile", "docker-compose.yml", "docker-compose.prod.yml",
            "docker-entrypoint.sh", "install.sh",
            "quick-start.sh", "docker-deploy.sh", "docker-deploy.ps1", "docker-deploy.bat",
            "build-and-deploy.sh", "Makefile", ".env.example", "config.ini.example",
        )
        failures = [
            path for path in paths
            if unsafe_default in (PROJECT_ROOT / path).read_text(encoding="utf-8").lower()
        ]
        self.assertEqual(failures, [])


class PolicyDocumentTests(unittest.TestCase):
    ADDITIONAL_TERMS = "https://maps.google.com/help/terms_maps/"
    GOOGLE_PRIVACY = "https://policies.google.com/privacy"

    def test_policy_documents_are_drafts_with_required_google_links(self):
        terms = (PROJECT_ROOT / "TERMS-OF-USE.md").read_text(encoding="utf-8")
        privacy = (PROJECT_ROOT / "PRIVACY-POLICY.md").read_text(encoding="utf-8")

        for content in (terms, privacy):
            self.assertIn("未經律師審核", content)
            self.assertIn(self.ADDITIONAL_TERMS, content)
            self.assertIn(self.GOOGLE_PRIVACY, content)
        for required_text in ("公司", "聯絡", "保存期限", "適用地區"):
            self.assertIn(required_text, privacy)

    def test_policy_pages_and_routes_are_available_without_external_assets(self):
        terms_page = (PROJECT_ROOT / "templates" / "terms.html").read_text(encoding="utf-8")
        privacy_page = (PROJECT_ROOT / "templates" / "privacy.html").read_text(encoding="utf-8")
        web_source = (PROJECT_ROOT / "src" / "web.py").read_text(encoding="utf-8")

        for page in (terms_page, privacy_page):
            self.assertIn('lang="zh-Hant-TW"', page)
            self.assertIn(self.ADDITIONAL_TERMS, page)
            self.assertIn(self.GOOGLE_PRIVACY, page)
            self.assertNotRegex(page, r'<(?:script|link)[^>]+https?://')
        self.assertIn("@self.app.route('/terms')", web_source)
        self.assertIn("@self.app.route('/privacy')", web_source)

    def test_login_and_admin_ui_link_to_both_policies(self):
        login = (PROJECT_ROOT / "templates" / "login.html").read_text(encoding="utf-8")
        spa = (PROJECT_ROOT / "templates" / "spa.html").read_text(encoding="utf-8")

        for page in (login, spa):
            self.assertIn("使用條款", page)
            self.assertIn("隱私權政策", page)
            self.assertIn("url_for('terms_of_use')", page)
            self.assertIn("url_for('privacy_policy')", page)

    def test_google_attribution_is_not_hidden_or_modified(self):
        browser_sources = "\n".join(
            (PROJECT_ROOT / path).read_text(encoding="utf-8")
            for path in ("templates/spa.html", "static/app.js")
        )
        self.assertNotRegex(browser_sources, r"(?i)\.gm-(?:style|copyright|logo)[^{]*\{[^}]*display\s*:\s*none")
        notices = (PROJECT_ROOT / "THIRD-PARTY-NOTICES.md").read_text(encoding="utf-8")
        self.assertIn("不得遮蔽", notices)


class DockerBatchLauncherTests(unittest.TestCase):
    def test_batch_file_is_ascii_crlf_without_bom(self):
        content = (PROJECT_ROOT / "docker-deploy.bat").read_bytes()

        self.assertFalse(content.startswith(b"\xef\xbb\xbf"))
        content.decode("ascii")
        self.assertNotIn(b"\n", content.replace(b"\r\n", b""))

    def test_batch_check_is_static_and_does_not_start_services(self):
        source = (PROJECT_ROOT / "docker-deploy.bat").read_text(encoding="ascii")
        check_start = source.index("\n:check\n") + 1
        check_end = source.index("\n:detect_compose\n", check_start)
        check_block = source[check_start:check_end]

        self.assertIn("Docker launcher check OK", check_block)
        self.assertIn("%~dp0", source)
        self.assertNotRegex(check_block, r"(?im)^\s*(?:docker|docker-compose)\s+(?:compose\s+)?up\b")


if __name__ == "__main__":
    unittest.main()
