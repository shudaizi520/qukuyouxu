import sys
import tempfile
import types
import fastapi
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
if "fastapi" not in sys.modules:
    fastapi = types.ModuleType("fastapi")
    fastapi.Request = object
    sys.modules["fastapi"] = fastapi


class SettingsPolicyV0424Tests(unittest.TestCase):
    def test_saved_connection_uses_registry_library_name_instead_of_raw_id(self):
        from helper.plex_state_v0316 import get_public_saved
        from helper.profiles import ProfileRegistry
        from helper.scoped_store import ActiveProfileStore
        from helper.store import Store

        with tempfile.TemporaryDirectory() as root:
            base = Store(Path(root))
            registry = ProfileRegistry(base)
            registry.create(
                "朋友", "shared", profile_id="friend", token="friend-secret",
                account={"id": "7", "username": "friend"},
                server={"name": "家庭服务器", "machine": "m", "url": "http://plex"},
                library={"id": "11", "name": "音乐"},
            )
            registry.select("friend")
            saved = get_public_saved(ActiveProfileStore(base, registry), now=100)

        self.assertEqual({"id": "11", "name": "音乐"}, saved["library"])
        self.assertEqual("friend", saved["account"]["username"])

    def test_recommendation_settings_present_activity_mode_and_relaxed_limits(self):
        page = (ROOT / "src/helper/static/settings.html").read_text(encoding="utf-8")
        script = (ROOT / "src/helper/static/settings.js").read_text(encoding="utf-8")

        self.assertIn("按最近 20 次有效播放", page)
        self.assertNotIn('id="recentDays"', page)
        for value in range(1, 7):
            self.assertIn(f'<option value="{value}">{value} 首</option>', page)
        for value in (10, 20, 30, 40):
            self.assertIn(f'<option value="{value}">{value}%</option>', page)
        self.assertIn("favorite_percent", script)
        self.assertNotIn("favorite_cap:Number", script)


if __name__ == "__main__":
    unittest.main()
