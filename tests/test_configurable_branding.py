"""Configurable web-branding regressions."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import tempfile
import unittest
from pathlib import Path

from flask import Flask, render_template_string


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_MODULE_PATH = PROJECT_ROOT / "src" / "config.py"
TEMPLATE_DIR = PROJECT_ROOT / "templates"
RUNTIME_BRAND_FILES = (
    PROJECT_ROOT / "config.ini.example",
    PROJECT_ROOT / "src" / "config.py",
    PROJECT_ROOT / "src" / "web.py",
) + tuple(sorted(TEMPLATE_DIR.glob("*.html")))

TEST_BRAND_CONTEXT = {
    "app_name": "Example Ntrip Caster",
    "app_version": "2.2.0",
    "app_author": "Example Operator",
    "app_contact": "ops@example.invalid",
    "app_website": "https://example.invalid/",
    "current_year": 2026,
    "map_provider": "osm",
    "google_maps_enabled": False,
    "map_default_latitude": 23.7,
    "map_default_longitude": 121.0,
    "map_default_zoom": 7,
    "google_maps_script_url": None,
}


class ConfigurableBrandingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = Flask(__name__)
        cls.app.add_url_rule("/", endpoint="index", view_func=lambda: "")
        cls.app.add_url_rule("/login", endpoint="login", view_func=lambda: "")
        cls.app.add_url_rule("/terms", endpoint="terms_of_use", view_func=lambda: "")
        cls.app.add_url_rule("/privacy", endpoint="privacy_policy", view_func=lambda: "")

    def _load_config(self, app_settings):
        with tempfile.TemporaryDirectory() as temporary_directory:
            config_path = Path(temporary_directory) / "branding.ini"
            config_path.write_text("[app]\n" + app_settings, encoding="utf-8")
            previous = os.environ.get("NTRIP_CONFIG_FILE")
            os.environ["NTRIP_CONFIG_FILE"] = str(config_path)
            try:
                spec = importlib.util.spec_from_file_location(
                    "branding_test_config", CONFIG_MODULE_PATH
                )
                module = importlib.util.module_from_spec(spec)
                with contextlib.redirect_stdout(io.StringIO()):
                    spec.loader.exec_module(module)
                return module
            finally:
                if previous is None:
                    os.environ.pop("NTRIP_CONFIG_FILE", None)
                else:
                    os.environ["NTRIP_CONFIG_FILE"] = previous

    def _render(self, template_name, **overrides):
        context = dict(TEST_BRAND_CONTEXT)
        context.update(overrides)
        template = (TEMPLATE_DIR / template_name).read_text(encoding="utf-8")
        with self.app.test_request_context("/"):
            return render_template_string(template, **context)

    def test_configured_brand_renders_in_login_spa_and_policy_pages(self):
        for template_name in ("login.html", "spa.html", "terms.html", "privacy.html"):
            with self.subTest(template=template_name):
                rendered = self._render(template_name)
                self.assertIn("Example Ntrip Caster", rendered)

        for template_name in ("spa.html", "terms.html", "privacy.html"):
            with self.subTest(template=template_name):
                rendered = self._render(template_name)
                self.assertIn("Example Operator", rendered)
                self.assertIn(
                    'href="mailto:ops@example.invalid"', rendered
                )
                self.assertIn('href="https://example.invalid/"', rendered)

    def test_brand_values_are_loaded_and_website_scheme_is_restricted(self):
        module = self._load_config(
            "name = Example Ntrip Caster\n"
            "author = Example Operator\n"
            "contact = ops@example.invalid\n"
            "website = https://example.invalid/\n"
        )
        self.assertEqual(module.APP_NAME, TEST_BRAND_CONTEXT["app_name"])
        self.assertEqual(module.APP_AUTHOR, TEST_BRAND_CONTEXT["app_author"])
        self.assertEqual(module.APP_CONTACT, TEST_BRAND_CONTEXT["app_contact"])
        self.assertEqual(module.APP_WEBSITE, TEST_BRAND_CONTEXT["app_website"])

        for unsafe_url in (
            "javascript:alert(1)",
            "data:text/html,unsafe",
            "https://user:password@example.com/",
            "//example.com/",
        ):
            with self.subTest(url=unsafe_url):
                self.assertEqual(module.normalize_app_website(unsafe_url), "")

    def test_jinja_escapes_brand_values_and_templates_do_not_use_safe(self):
        rendered = self._render(
            "spa.html",
            app_name='<script>alert("name")</script>',
            app_author='<img src=x onerror="alert(1)">',
            app_contact='person@example.com" onclick="alert(1)',
            app_website='https://example.com/?q=" onclick="alert(1)',
        )
        self.assertNotIn("<script>alert", rendered)
        self.assertNotIn("<img src=x", rendered)
        self.assertNotIn('onclick="alert(1)', rendered)
        self.assertIn("&lt;script&gt;", rendered)
        self.assertIn("&#34;", rendered)

        for template_path in TEMPLATE_DIR.glob("*.html"):
            self.assertNotIn("|safe", template_path.read_text(encoding="utf-8"))

    def test_blank_contact_and_website_do_not_create_links(self):
        for template_name in ("spa.html", "terms.html", "privacy.html"):
            with self.subTest(template=template_name):
                rendered = self._render(
                    template_name,
                    app_contact="",
                    app_website="",
                )
                self.assertNotIn("mailto:", rendered)
                self.assertNotIn('id="app-contact"', rendered)
                self.assertNotIn('id="app-website"', rendered)

    def test_runtime_web_sources_do_not_hardcode_legacy_brand(self):
        legacy_values = (
            "2RTK Ntrip Caster",
            "2RTK NTRIP Caster",
            "i@jia.by",
            "2RTK.COM",
            "https://2rtk.com",
        )
        for source_path in RUNTIME_BRAND_FILES:
            source = source_path.read_text(encoding="utf-8")
            for legacy_value in legacy_values:
                with self.subTest(path=source_path.name, value=legacy_value):
                    self.assertNotIn(legacy_value, source)

    def test_brand_context_is_centrally_added_to_every_template(self):
        web_source = (PROJECT_ROOT / "src" / "web.py").read_text(encoding="utf-8")
        for context_name, config_name in (
            ("app_name", "APP_NAME"),
            ("app_version", "APP_VERSION"),
            ("app_author", "APP_AUTHOR"),
            ("app_contact", "APP_CONTACT"),
            ("app_website", "APP_WEBSITE"),
        ):
            self.assertIn(f"'{context_name}': config.{config_name}", web_source)
        self.assertIn("template_context.update", web_source)

    def test_license_and_third_party_attribution_files_remain_present(self):
        required_files = (
            PROJECT_ROOT / "LICENSE",
            PROJECT_ROOT / "THIRD-PARTY-NOTICES.md",
            PROJECT_ROOT / "static" / "vendor" / "openlayers" / "8.2.0" / "LICENSE.md",
            PROJECT_ROOT / "static" / "vendor" / "socket.io-client" / "4.0.1" / "LICENSE",
        )
        for required_file in required_files:
            with self.subTest(path=required_file):
                self.assertTrue(required_file.is_file())


if __name__ == "__main__":
    unittest.main()
