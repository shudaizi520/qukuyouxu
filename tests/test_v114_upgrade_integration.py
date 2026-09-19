import tempfile
import unittest
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src" / "helper" / "static"
sys.path.insert(0, str(ROOT / "src"))


class UpgradeIntegrationV114Tests(unittest.TestCase):
    def test_automation_migration_preserves_playlists_cache_and_learning_settings(self):
        from helper.automation import automation_settings
        from helper.profiles import ProfileRegistry
        from helper.scoped_store import ScopedStore
        from helper.store import Store

        with tempfile.TemporaryDirectory() as root:
            base = Store(Path(root))
            registry = ProfileRegistry(base)
            scoped = ScopedStore(base, "default")
            original_managed = {"ktv": {"id": "playlist-1", "title": "KTV金曲"}}
            original_cache = {"song:1": {"title": "歌一", "artist": "歌手"}}
            scoped.set_many({
                "managed": original_managed,
                "cache": original_cache,
                "product_settings": {"behavior_enabled": False},
                "daily_settings": {"enabled": True, "hour": 8},
            })

            class Runtime:
                def engine(self, profile_id):
                    return type("Engine", (), {"store": ScopedStore(base, profile_id)})()

            automation_settings(base, registry, Runtime(), now=1_800_000_000)

            self.assertEqual(original_managed, scoped.get("managed"))
            self.assertEqual(original_cache, scoped.get("cache"))
            self.assertFalse(scoped.get("product_settings")["behavior_enabled"])

    def test_manual_new_song_action_remains_after_automation_migration(self):
        html = (STATIC / "home.html").read_text(encoding="utf-8")
        script = (STATIC / "home.js").read_text(encoding="utf-8")

        self.assertIn('id="incrementalAction"', html)
        self.assertIn("/api/workflow/incremental", script)


if __name__ == "__main__":
    unittest.main()
