import tempfile
import unittest
import sys
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


class _SettingsStructure(HTMLParser):
    def __init__(self):
        super().__init__()
        self.nav_targets = []
        self.panels = []
        self.classes = []
        self.headings = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        classes = set(values.get("class", "").split())
        self.classes.extend(classes)
        if tag == "button" and values.get("data-settings-target"):
            self.nav_targets.append(values["data-settings-target"])
        if "settings-panel" in classes:
            self.panels.append({
                "id": values.get("id", ""),
                "hidden": "hidden" in values,
            })

    def handle_data(self, data):
        text = data.strip()
        if text:
            self.headings.append(text)


class SettingsRedesignV0423Tests(unittest.TestCase):
    def setUp(self):
        from helper.profiles import ProfileRegistry
        from helper.store import Store

        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name))
        self.registry = ProfileRegistry(self.store)
        self.registry.update(
            "default",
            name="Owner",
            token="owner-secret",
            account={"id": "1", "username": "owner"},
            server={"machine": "machine-a", "name": "Main", "url": "https://plex.local:32400"},
            library={"id": "15", "name": "Music"},
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_archiving_friend_stops_tasks_without_losing_playlist_state(self):
        from helper.scoped_store import ScopedStore

        self.registry.create(
            name="Friend",
            kind="shared",
            profile_id="shared-42",
            token="friend-secret",
        )
        scoped = ScopedStore(self.store, "shared-42")
        scoped.set("daily_managed", {"id": "playlist-99", "title": "每日推荐"})
        self.registry.select("shared-42")

        archived = self.registry.archive("shared-42")

        self.assertFalse(archived["enabled"])
        self.assertEqual("default", self.registry.active_id())
        self.assertEqual("playlist-99", scoped.get("daily_managed")["id"])
        self.assertNotIn("shared-42", [row["id"] for row in self.registry.list_public(enabled_only=True)])

        restored = self.registry.restore("shared-42")
        self.assertTrue(restored["enabled"])
        self.assertEqual("playlist-99", scoped.get("daily_managed")["id"])

    def test_people_list_includes_owner_and_distinguishes_archived_friend(self):
        from helper.plex_recipients import PlexRecipientService

        self.registry.create(
            name="Friend",
            kind="shared",
            profile_id="shared-42",
            token="friend-secret",
            account={"id": "42", "username": "friend"},
        )
        self.registry.update("shared-42", enabled=False)
        service = PlexRecipientService(self.store, self.registry)
        service.list_home_users = lambda _owner: [
            {"id": "1", "title": "Owner", "username": "owner"},
            {"id": "2", "title": "Kid", "username": "kid"},
        ]
        service.list_shared_users = lambda _owner: [
            {"id": "42", "title": "Friend", "username": "friend"},
        ]

        result = service.list_people("default")

        self.assertEqual(["1", "2", "42"], [row["id"] for row in result["items"]])
        archived = result["items"][2]
        self.assertEqual("", archived["existing_profile_id"])
        self.assertEqual("shared-42", archived["archived_profile_id"])

    def test_settings_is_a_single_compact_page_without_repeated_account_titles(self):
        parser = _SettingsStructure()
        parser.feed((ROOT / "src/helper/static/settings.html").read_text(encoding="utf-8"))

        self.assertEqual([], parser.nav_targets)
        self.assertFalse(any(panel["hidden"] for panel in parser.panels if panel["id"] in {"settings-accounts", "settings-system"}))
        self.assertNotIn("账户与成员", parser.headings)
        self.assertNotIn("当前账户", parser.headings)
        self.assertIn("Plex 连接", parser.headings)
        self.assertIn("用户管理", parser.headings)
        self.assertNotIn("panel-description", parser.classes)
        self.assertNotIn("people-next-step", parser.classes)

    def test_legacy_hashes_scroll_to_their_setting_instead_of_hiding_content(self):
        script = (ROOT / "src/helper/static/settings.js").read_text(encoding="utf-8")

        self.assertIn("const settingsAnchors={accounts:'currentUser',learning:'people',system:'settings-system'}", script)
        self.assertIn("scrollIntoView({block:'start'})", script)
        self.assertNotIn("panel.hidden=panel.id!==\'settings-\'+name", script)
        boot = script.split("async function boot(){", 1)[1].split("window.addEventListener('pch-auth-ready'", 1)[0]
        self.assertLess(boot.index("await refresh();"), boot.index("showSettingsPanel(panel,false)"))

    def test_settings_has_no_duplicate_daily_save_feedback(self):
        html = (ROOT / "src/helper/static/settings.html").read_text(encoding="utf-8")
        script = (ROOT / "src/helper/static/settings.js").read_text(encoding="utf-8")
        presentation = (ROOT / "src/helper/static/auth.js").read_text(encoding="utf-8")

        self.assertNotIn('id="dailySave"', html)
        self.assertIn('id="dailyPolicySave"', (ROOT / "src/helper/static/mixes.html").read_text(encoding="utf-8"))
        self.assertNotIn("function markSaved", script)
        self.assertNotIn('id="behaviorSave"', html)
        self.assertNotIn("anchor.after(inline)", presentation)


if __name__ == "__main__":
    unittest.main()
