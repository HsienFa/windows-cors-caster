"""Rover monitor UI, marker reconciliation, and map policy regressions."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
APP_PATH = PROJECT_ROOT / "static" / "app.js"
ROVER_STATE_PATH = PROJECT_ROOT / "static" / "rover-state.js"
SPA_PATH = PROJECT_ROOT / "templates" / "spa.html"
PRIVACY_PATH = PROJECT_ROOT / "templates" / "privacy.html"
PRIVACY_MARKDOWN_PATH = PROJECT_ROOT / "PRIVACY-POLICY.md"


class RoverFrontendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app_source = APP_PATH.read_text(encoding="utf-8")
        cls.state_source = ROVER_STATE_PATH.read_text(encoding="utf-8")
        cls.template_source = SPA_PATH.read_text(encoding="utf-8")
        cls.privacy_source = PRIVACY_PATH.read_text(encoding="utf-8")
        cls.privacy_markdown_source = PRIVACY_MARKDOWN_PATH.read_text(encoding="utf-8")

    def test_rover_state_rules_filter_and_marker_reconciliation(self):
        node = (
            os.environ.get("NODE_BINARY")
            or shutil.which("node")
            or shutil.which("node.exe")
        )
        if node is None:
            self.skipTest("Node.js is required for frontend behavior tests")

        script = f"""
            require({json.dumps(str(ROVER_STATE_PATH))});
            const state = globalThis.RoverState;
            const base = {{
                latitude: 25,
                longitude: 121,
                has_valid_position: true,
                position_fresh: true,
                last_gga_time: '2026-01-01T00:00:00Z'
            }};
            const statuses = [4, 5, 1, 2, 9].map(quality =>
                state.getPositionStatus({{...base, gga_fix_quality: quality}}).label
            );
            statuses.push(state.getPositionStatus({{
                ...base, gga_fix_quality: 4, position_fresh: false
            }}).label);
            statuses.push(state.getPositionStatus({{
                ...base, gga_fix_quality: 0, has_valid_position: false,
                position_fresh: false
            }}).label);
            statuses.push(state.getPositionStatus({{connection_id: 'missing'}}).label);

            const markers = new Map();
            const events = [];
            const callbacks = {{
                create: rover => {{ events.push(`create:${{rover.connection_id}}`); return rover; }},
                update: (marker, rover) => events.push(`update:${{rover.connection_id}}`),
                remove: (marker, id) => events.push(`remove:${{id}}`)
            }};
            state.reconcileMarkers([
                {{...base, connection_id: 'a', username: 'Alpha', gga_fix_quality: 4}},
                {{connection_id: 'no-gga', username: 'No GGA', has_valid_position: false}},
                {{...base, connection_id: 'quality-zero', gga_fix_quality: 0}}
            ], markers, callbacks);
            state.reconcileMarkers([
                {{...base, connection_id: 'a', username: 'Alpha', longitude: 121.1, gga_fix_quality: 4}},
                {{...base, connection_id: 'c', username: 'Charlie', gga_fix_quality: 5}}
            ], markers, callbacks);
            state.reconcileMarkers([
                {{connection_id: 'no-gga', username: 'No GGA', has_valid_position: false}}
            ], markers, callbacks);

            console.log(JSON.stringify({{
                statuses,
                events,
                markerCount: markers.size,
                filtered: state.filterByUsername([
                    {{username: 'Alpha'}}, {{username: 'Charlie'}}
                ], 'alp').map(item => item.username),
                summary: state.summarize([
                    {{...base, gga_fix_quality: 4}},
                    {{...base, gga_fix_quality: 5}},
                    {{...base, gga_fix_quality: 2}},
                    {{...base, gga_fix_quality: 4, position_fresh: false}},
                    {{has_valid_position: false, position_fresh: false}}
                ])
            }}));
        """
        completed = subprocess.run(
            [node, "-e", script],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
        )
        result = json.loads(completed.stdout)

        self.assertEqual(
            result["statuses"],
            [
                "RTK 固定", "RTK 浮點", "單點", "DGPS", "其他定位 (9)",
                "位置逾時", "無定位", "無位置資料",
            ],
        )
        self.assertEqual(
            result["events"],
            ["create:a", "update:a", "create:c", "remove:a", "remove:c"],
        )
        self.assertEqual(result["markerCount"], 0)
        self.assertEqual(result["filtered"], ["Alpha"])
        self.assertEqual(
            result["summary"],
            {"online": 5, "valid": 3, "fixed": 1, "float": 1,
             "other": 1, "noPosition": 2},
        )

    def test_monitor_is_protected_and_polling_stops_on_leave_or_unauthorized(self):
        self.assertIn(
            "const requireLoginPages = ['users', 'mounts', 'monitor', 'settings']",
            self.app_source,
        )
        self.assertIn("fetch('/api/rovers'", self.app_source)
        self.assertIn("ROVER_POLL_INTERVAL_MS = 3000", self.app_source)
        self.assertIn("stopRoverPolling();\n        destroyCurrentMap();", self.app_source)
        self.assertIn("if (response.status === 401)", self.app_source)
        self.assertIn("roverPollingAbortController.abort()", self.app_source)
        public_user_handler = self.app_source.split(
            "socket.on('online_users_update'", 1
        )[1].split("socket.on('online_mounts_update'", 1)[0]
        self.assertIn("data.online_user_count", public_user_handler)
        self.assertNotIn("data.users", public_user_handler)

    def test_rover_table_and_popups_use_safe_dom_for_third_party_text(self):
        table_source = self.app_source.split(
            "function renderRoverTable", 1
        )[1].split("function renderRoverStatus", 1)[0]
        popup_source = self.app_source.split(
            "function appendSafePopupRow", 1
        )[1].split("function createOpenStreetMapRoverFeature", 1)[0]
        for source in (table_source, popup_source):
            self.assertIn("textContent", source)
            self.assertNotIn("innerHTML", source)
            self.assertNotIn("insertAdjacentHTML", source)

    def test_openlayers_and_google_use_independent_connection_id_marker_maps(self):
        self.assertIn("let osmRoverLayer = null", self.app_source)
        self.assertIn("const osmRoverFeatures = new Map()", self.app_source)
        self.assertIn("const googleRoverMarkers = new Map()", self.app_source)
        self.assertIn("roverConnectionId: String(rover.connection_id)", self.app_source)
        self.assertIn("RoverState.reconcileMarkers(rovers, osmRoverFeatures", self.app_source)
        self.assertIn("RoverState.reconcileMarkers(rovers, googleRoverMarkers", self.app_source)
        self.assertIn("googleRoverMarkers.forEach(marker => marker.setMap(null))", self.app_source)

        rover_sync_source = self.app_source.split(
            "function syncOpenStreetMapRovers", 1
        )[1].split("function googleRoverMarkerAppearance", 1)[0]
        self.assertNotIn("osmMarkerLayer", rover_sync_source)
        self.assertIn("osmRoverLayer.getSource().removeFeature", rover_sync_source)

        station_source = self.app_source.split(
            "function updateOpenStreetMapMarker", 1
        )[1].split("function openGoogleMarkerInfo", 1)[0]
        self.assertIn("osmMarkerLayer.getSource()", station_source)
        self.assertNotIn("osmRoverLayer.getSource().clear", station_source)

    def test_osm_layer_uses_bounded_preload_and_short_transition(self):
        osm_source = self.app_source.split(
            "function createOSMLayer", 1
        )[1].split("function isCurrentMountOnline", 1)[0]
        self.assertIn("new ol.source.OSM", osm_source)
        self.assertIn("transition: 100", osm_source)
        self.assertIn("preload: 1", osm_source)
        self.assertIn("useInterimTilesOnError: true", osm_source)
        self.assertNotIn("url:", osm_source)
        self.assertIn("OpenStreetMap contributors", osm_source)

    def test_existing_station_markers_coverage_and_fallback_remain_intact(self):
        osm_station = self.app_source.split(
            "function updateOpenStreetMapMarker", 1
        )[1].split("function openGoogleMarkerInfo", 1)[0]
        self.assertIn("isStationMarker: true", osm_station)
        self.assertIn("[20000, 'rgba(21, 101, 192, 0.15)']", osm_station)
        self.assertIn("[50000, 'rgba(66, 165, 245, 0.2)']", osm_station)
        self.assertIn("populateMarkerDetails(popupElement, details)", osm_station)

        google_station = self.app_source.split(
            "function updateGoogleMapMarker", 1
        )[1].split("function updateMapLocation", 1)[0]
        self.assertIn("new google.maps.Circle", google_station)
        self.assertIn("radius: 20000", google_station)
        self.assertIn("radius: 50000", google_station)
        self.assertIn("openGoogleMarkerInfo()", google_station)

        self.assertIn("window.googleMapsApiFailed", self.app_source)
        self.assertIn("fallbackToOpenStreetMap(", self.app_source)

    def test_privacy_text_covers_authenticated_google_rover_coordinates(self):
        self.assertIn("登入後的管理頁若使用 Google Maps", self.privacy_source)
        self.assertIn("Rover 座標", self.privacy_source)
        self.assertIn("不把 Rover 位置寫入資料庫或日誌", self.privacy_source)
        self.assertIn("登入後的管理頁若使用 Google Maps", self.privacy_markdown_source)
        self.assertIn("只保存在伺服器記憶體", self.privacy_markdown_source)

    def test_rover_runtime_is_loaded_before_application_script(self):
        rover_script = '<script src="/static/rover-state.js"></script>'
        app_script = '<script src="/static/app.js"></script>'
        self.assertIn(rover_script, self.template_source)
        self.assertLess(
            self.template_source.index(rover_script),
            self.template_source.index(app_script),
        )


if __name__ == "__main__":
    unittest.main()
