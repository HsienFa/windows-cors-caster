"""前端離線資源與外連防回歸測試。"""

import hashlib
import unittest
from html.parser import HTMLParser
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SPA_TEMPLATE = PROJECT_ROOT / 'templates' / 'spa.html'
APP_SCRIPT = PROJECT_ROOT / 'static' / 'app.js'
VENDOR_RUNTIME_FILES = (
    PROJECT_ROOT / 'static' / 'vendor' / 'openlayers' / '8.2.0' / 'ol.js',
    PROJECT_ROOT / 'static' / 'vendor' / 'openlayers' / '8.2.0' / 'ol.css',
    PROJECT_ROOT / 'static' / 'vendor' / 'socket.io-client' / '4.0.1' / 'socket.io.min.js',
)

OPENLAYERS_CSS = '/static/vendor/openlayers/8.2.0/ol.css'
OPENLAYERS_JS = '/static/vendor/openlayers/8.2.0/ol.js'
SOCKET_IO_JS = '/static/vendor/socket.io-client/4.0.1/socket.io.min.js'
APP_JS = '/static/app.js'
OPTIONAL_GOOGLE_SCRIPT = '{{ google_maps_script_url }}'

VENDOR_SHA256 = {
    'static/vendor/openlayers/8.2.0/ol.js':
        'ae5e487a52b7fdc7167dce953f3a3968d305053051e380751d8bdb154d9bba6d',
    'static/vendor/openlayers/8.2.0/ol.css':
        'b46a588ec4f9db4f824ea15ab2b78bd9d1dfb17172a785c69e23fa8953db437f',
    'static/vendor/openlayers/8.2.0/LICENSE.md':
        '6c4347b83a8c9feef18d57b18e3b6c44cf901b3c344a4a1fbd837e421555ab8e',
    'static/vendor/socket.io-client/4.0.1/socket.io.min.js':
        'e8da407a321da9d28520d362f6202b458b1f5718240de5d47ab5dbc8911842e7',
    'static/vendor/socket.io-client/4.0.1/LICENSE':
        '62e2032a1e1458b1d92a62f5fc51be48e08b95062295c91a9f3bd3686809d37e',
}


class RuntimeAssetParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.scripts = []
        self.stylesheets = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == 'script' and attributes.get('src'):
            self.scripts.append(attributes['src'])
        if tag == 'link' and 'stylesheet' in attributes.get('rel', '').split():
            self.stylesheets.append(attributes.get('href', ''))


class FrontendOfflinePrivacyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template_source = SPA_TEMPLATE.read_text(encoding='utf-8')
        cls.app_source = APP_SCRIPT.read_text(encoding='utf-8')
        cls.assets = RuntimeAssetParser()
        cls.assets.feed(cls.template_source)

    def test_vendor_assets_are_present_and_unmodified(self):
        for relative_path, expected_hash in VENDOR_SHA256.items():
            with self.subTest(path=relative_path):
                content = (PROJECT_ROOT / relative_path).read_bytes()
                self.assertTrue(content)
                self.assertEqual(hashlib.sha256(content).hexdigest(), expected_hash)

    def test_spa_loads_runtime_libraries_from_local_static_files_once(self):
        self.assertIn(OPENLAYERS_CSS, self.assets.stylesheets)
        self.assertEqual(self.assets.scripts.count(OPENLAYERS_JS), 1)
        self.assertEqual(self.assets.scripts.count(SOCKET_IO_JS), 1)
        self.assertEqual(self.assets.scripts.count(APP_JS), 1)
        self.assertEqual(self.assets.scripts.count(OPTIONAL_GOOGLE_SCRIPT), 1)
        self.assertLess(
            self.assets.scripts.index(SOCKET_IO_JS),
            self.assets.scripts.index(APP_JS),
        )

        for asset_path in self.assets.scripts + self.assets.stylesheets:
            with self.subTest(asset=asset_path):
                self.assertTrue(
                    asset_path.startswith('/static/')
                    or asset_path == OPTIONAL_GOOGLE_SCRIPT
                )

    def test_project_frontend_has_no_disallowed_runtime_source(self):
        project_frontend = self.template_source + '\n' + self.app_source
        forbidden = (
            'cdnjs.cloudflare.com',
            'cdn.jsdelivr.net',
            'cdn.socket.io',
            'autonavi',
            'amap',
            '高德',
        )

        for value in forbidden:
            with self.subTest(value=value):
                self.assertNotIn(value, project_frontend.lower())

        self.assertNotIn("document.createElement('script')", self.app_source)
        self.assertNotIn('document.createElement("script")', self.app_source)
        self.assertNotIn("document.createElement('link')", self.app_source)
        self.assertNotIn('document.createElement("link")', self.app_source)

    def test_all_browser_runtime_files_exclude_banned_service_domains(self):
        runtime_source = '\n'.join(
            path.read_text(encoding='utf-8')
            for path in VENDOR_RUNTIME_FILES
        )
        runtime_source += '\n' + self.template_source + '\n' + self.app_source

        forbidden_domains = (
            'cdnjs.cloudflare.com',
            'cdn.jsdelivr.net',
            'cdn.socket.io',
            'autonavi.com',
            'amap.com',
            'amapcdn.com',
        )
        for domain in forbidden_domains:
            with self.subTest(domain=domain):
                self.assertNotIn(domain, runtime_source.lower())

    def test_openstreetmap_is_default_and_tile_failure_is_non_fatal(self):
        self.assertIn('new ol.source.OSM(', self.app_source)
        self.assertNotIn('new ol.source.XYZ(', self.app_source)
        self.assertIn('https://www.openstreetmap.org/copyright', self.app_source)
        self.assertIn('OpenStreetMap contributors', self.app_source)
        self.assertIn("tileSource.on('tileloaderror'", self.app_source)
        self.assertIn('其他管理功能仍可正常使用', self.app_source)
        self.assertIn('id="map-tile-error"', self.app_source)

    def test_google_maps_uses_official_api_and_all_supported_map_types(self):
        config_source = (PROJECT_ROOT / 'src' / 'config.py').read_text(encoding='utf-8')
        self.assertIn('https://maps.googleapis.com/maps/api/js?', config_source)
        self.assertNotIn('google_maps_api_key', self.template_source)
        self.assertIn('src="{{ google_maps_script_url }}"', self.template_source)
        self.assertIn('new google.maps.Map(', self.app_source)

        for map_type in ('ROADMAP', 'SATELLITE', 'HYBRID', 'TERRAIN'):
            with self.subTest(map_type=map_type):
                self.assertIn(f'google.maps.MapTypeId.{map_type}', self.app_source)

    def test_empty_map_uses_configured_taiwan_center_and_google_failure_falls_back(self):
        self.assertIn('id="map" class="map-display"', self.app_source)
        self.assertIn('id="map-empty-state"', self.app_source)
        self.assertIn('尚未收到基站位置，先顯示台灣預設中心。', self.app_source)
        self.assertIn('mapRuntimeConfig.defaultLatitude', self.app_source)
        self.assertIn('mapRuntimeConfig.defaultLongitude', self.app_source)
        self.assertIn('mapRuntimeConfig.defaultZoom', self.app_source)
        self.assertIn('window.googleMapsApiFailed', self.app_source)
        self.assertIn('window.gm_authFailure', self.app_source)
        self.assertIn('fallbackToOpenStreetMap(', self.app_source)

    def test_map_marker_only_uses_approved_station_fields(self):
        marker_source = self.app_source.split(
            'function createMarkerDetails', 1
        )[1].split('function populateMarkerDetails', 1)[0]
        for field in ('name', 'mountName', 'latitude', 'longitude', 'online'):
            with self.subTest(field=field):
                self.assertIn(field, marker_source)
        for forbidden_field in ('username', 'password', 'authorization', 'secret'):
            with self.subTest(field=forbidden_field):
                self.assertNotIn(forbidden_field, marker_source.lower())


if __name__ == '__main__':
    unittest.main()
