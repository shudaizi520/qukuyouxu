import re
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src/helper/static"


class UserCleanupTests(unittest.TestCase):
    def test_new_user_daily_default_is_50_without_overwriting_existing_choice(self):
        from helper.profiles import ProfileRegistry
        from helper.scoped_store import ScopedStore
        from helper.store import Store

        with tempfile.TemporaryDirectory() as folder:
            base = Store(Path(folder))
            registry = ProfileRegistry(base)
            profile = registry.create(name="friend", kind="shared")
            scoped = ScopedStore(base, profile["id"], registry=registry)
            self.assertEqual(50, scoped.get("daily_settings")["size"])
            daily = scoped.get("daily_settings")
            daily["size"] = 30
            scoped.set("daily_settings", daily)
            self.assertEqual(30, scoped.get("daily_settings")["size"])

    def test_daily_rules_have_one_home(self):
        settings = (STATIC / "settings.html").read_text()
        mixes = (STATIC / "mixes.html").read_text()
        script = (STATIC / "settings.js").read_text()
        self.assertNotIn('id="settings-recommend"', settings)
        self.assertNotIn('id="dailyForm"', settings)
        self.assertNotIn("$('dailyForm')", script)
        self.assertIn('id="dailyPolicyForm"', mixes)
        self.assertIn('id="dailyAutomationEnabled"', mixes)

    def test_search_is_left_aligned_and_import_header_has_no_hash(self):
        css = (STATIC / "product.css").read_text()
        rules = re.findall(r"\.playlist-global-search\{([^{}]*)\}", css)
        self.assertTrue(rules)
        self.assertIn("margin:0", next(rule for rule in rules if "width:min(460px" in rule))
        external = (STATIC / "external.html").read_text()
        header = re.search(r'<div id="trackColumnHead"[^>]*>(.*?)</div>', external)
        self.assertIsNotNone(header)
        self.assertNotIn("#", header.group(1))
        self.assertEqual(6, header.group(1).count("<span"))

    def test_plex_history_without_verified_account_is_not_used_for_another_user(self):
        from helper.daily_mix_v035 import PlexHistoryIsolationError, read_plex_history

        class Plex:
            def _xml(self, _path, params):
                self.params = params
                return ET.fromstring(
                    '<MediaContainer totalSize="1"><Track ratingKey="123" '
                    'viewedAt="1999999900" type="track" /></MediaContainer>'
                )

        plex = Plex()
        with self.assertRaises(PlexHistoryIsolationError):
            read_plex_history(plex, "15", 2_000_000_000, account_id="42")
        self.assertEqual("42", plex.params["accountID"])

    def test_old_plex_history_cache_is_revalidated_before_use(self):
        from helper.daily_mix_v035 import read_plex_history_cached

        now = 2_000_000_000
        class Store:
            def get(self, key, default=None):
                if key == "plex_history_cache":
                    return {
                        "scope": "profile:15:42", "updated_at": now,
                        "events": [{"id": "888", "viewed_at": now - 100, "account_id": "42"}],
                    }
                return default

        class Engine:
            store = Store()

            def daily_scope(self):
                return "profile"

        class Plex:
            def _xml(self, _path, params):
                return ET.fromstring(
                    '<MediaContainer totalSize="1"><Track ratingKey="123" '
                    'viewedAt="1999999900" accountID="42" type="track" /></MediaContainer>'
                )

        events, state, mode = read_plex_history_cached(Engine(), Plex(), "15", now, "42")
        self.assertEqual(["123"], [row["id"] for row in events])
        self.assertEqual(2, state["verification_version"])
        self.assertEqual("full", mode)


if __name__ == "__main__":
    unittest.main()
