"""Unit tests for pure logic (run with `python -m unittest` or pytest).

They need homeassistant, pydantic and tinytuya importable (e.g. inside the HA
container or a dev venv with homeassistant installed). No network is used.
"""

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from custom_components.tuya_wizard import cloud_block, companion, discovery  # noqa: E402
from custom_components.tuya_wizard.dp_mapper import map_dp  # noqa: E402
from custom_components.tuya_wizard.models import TuyaCredentials  # noqa: E402


class DpMapperTests(unittest.TestCase):
    def test_portuguese_name_from_code(self):
        dp = map_dp("134", schema={"code": "motion_switch", "type": "Boolean"})
        self.assertEqual(dp.name, "Detecção de movimento")
        self.assertEqual(dp.platform, "switch")

    def test_unknown_code_falls_back_to_title_case(self):
        dp = map_dp("999", schema={"code": "some_new_thing", "type": "Integer"})
        self.assertEqual(dp.name, "Some New Thing")
        self.assertEqual(dp.platform, "number")

    def test_enum_options(self):
        dp = map_dp("106", schema={"code": "motion_sensitivity", "type": "Enum", "values": {"range": ["0", "1", "2"]}})
        self.assertEqual(dp.select_options, {"0": "0", "1": "1", "2": "2"})


class DiscoveryTests(unittest.TestCase):
    def test_build_dps_skips_raw_and_long_payload_codes(self):
        schema = {
            "101": {"code": "basic_indicator", "type": "Boolean"},
            "115": {"code": "movement_detect_pic", "type": "Raw"},
            "185": {"code": "alarm_message", "type": "String"},
            "212": {"code": "initiative_message", "type": "Raw"},
            "124": {"code": "nightvision_mode", "type": "Enum", "values": {"range": ["auto"]}},
        }
        ids = [d.id for d in discovery._build_dps(schema, {})]
        self.assertEqual(ids, ["101", "124"])

    def test_list_homes_marks_unreadable(self):
        fake = mock.MagicMock()

        def req(path, query=None):
            if path == "/v2.0/cloud/thing/device":
                return {"result": [{"id": "a", "bindSpaceId": "1"}, {"id": "b", "bindSpaceId": "1"}, {"id": "c", "bindSpaceId": "2"}]}
            if path == "/v2.0/cloud/space/1":
                return {"result": {"name": "Casa"}}
            return {"success": False, "msg": "No space permission"}

        fake.cloudrequest.side_effect = req
        with mock.patch.object(discovery, "_cloud_client", return_value=fake):
            homes = discovery.list_homes(TuyaCredentials(access_id="x", access_secret="y"))
        self.assertEqual(homes[0], {"id": "1", "name": "Casa", "devices": 2, "readable": True})
        self.assertEqual(homes[1]["readable"], False)


class CompanionTests(unittest.TestCase):
    def _sync(self):
        with mock.patch.object(companion, "Store"):
            return companion.CompanionSync(mock.MagicMock())

    def test_in_sync_compares_ip_and_flags(self):
        s = self._sync()
        s.devices = {"d1": {"device_id": "d1", "ip": "192.0.2.10", "proxy_enabled": True, "privacy_blocked": False}}
        same = {"cameras": [{"device_id": "d1", "ip": "192.0.2.10", "proxy_enabled": True, "privacy_blocked": False}]}
        self.assertTrue(s._in_sync(same))
        self.assertFalse(s._in_sync({"cameras": []}))
        moved = {"cameras": [{"device_id": "d1", "ip": "192.0.2.11", "proxy_enabled": True, "privacy_blocked": False}]}
        self.assertFalse(s._in_sync(moved))


class LeakCheckTests(unittest.TestCase):
    def test_reports_leak_when_cloud_logged_after_block(self):
        fake = mock.MagicMock()
        fake.cloudrequest.return_value = {"success": True, "result": {"logs": [{"event_time": 2000, "code": "initiative_message"}]}}
        with mock.patch.object(cloud_block.tinytuya, "Cloud", return_value=fake):
            r = cloud_block.leak_check(TuyaCredentials(access_id="x", access_secret="y"), "d1", 1000)
        self.assertTrue(r["leaking"])
        self.assertEqual(r["count"], 1)
        self.assertEqual(r["codes"], ["initiative_message"])

    def test_no_leak(self):
        fake = mock.MagicMock()
        fake.cloudrequest.return_value = {"success": True, "result": {"logs": []}}
        with mock.patch.object(cloud_block.tinytuya, "Cloud", return_value=fake):
            r = cloud_block.leak_check(TuyaCredentials(access_id="x", access_secret="y"), "d1", 1000)
        self.assertFalse(r["leaking"])


if __name__ == "__main__":
    unittest.main()
