import unittest
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class _Page(HTMLParser):
    def __init__(self):
        super().__init__()
        self.nodes = []
        self.stack = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        node = {
            "tag": tag,
            "id": values.get("id", ""),
            "classes": set(values.get("class", "").split()),
            "hidden": "hidden" in values,
            "ancestors": list(self.stack),
        }
        self.nodes.append(node)
        if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}:
            self.stack.append(node)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index]["tag"] == tag:
                del self.stack[index:]
                return

    def by_id(self, value):
        return next(node for node in self.nodes if node["id"] == value)


class ProfileLibraryUIV108Tests(unittest.TestCase):
    def parse(self, name):
        page = _Page()
        page.feed((ROOT / "src/helper/static" / name).read_text(encoding="utf-8"))
        return page

    def test_connection_keeps_first_library_choice_without_duplicate_user_switcher(self):
        page = self.parse("settings.html")
        current = page.by_id("currentUser")

        self.assertFalse(any(node["id"] == "plexProfile" for node in page.nodes))
        self.assertIn(current, page.by_id("officialSection")["ancestors"])
        self.assertIn("account-selector-row", page.by_id("plexLibraryPanel")["classes"])
        self.assertTrue(page.by_id("savePlexLibrary")["hidden"])

    def test_library_change_requires_explicit_save(self):
        script = (ROOT / "src/helper/static/settings.js").read_text(encoding="utf-8")
        self.assertNotIn("$('officialSection').onchange=()=>action(saveOfficialLibrary)", script)
        self.assertIn("$('savePlexLibrary').onclick=()=>action(saveOfficialLibrary)", script)
        self.assertIn("$('savePlexLibrary').hidden=", script)

    def test_removed_user_add_uses_fresh_import_and_owner_keeps_action_slot(self):
        script = (ROOT / "src/helper/static/settings.js").read_text(encoding="utf-8")
        self.assertNotIn("重新添加", script)
        self.assertNotIn("'/api/plex/profiles/restore'", script)
        self.assertIn("actions.className='settings-actions profile-actions'", script)

    def test_daily_safety_pause_reason_is_visible_in_profile_row(self):
        script = (ROOT / "src/helper/static/settings.js").read_text(encoding="utf-8")
        self.assertIn("row.daily_status?.status==='needs_attention'", script)
        self.assertIn("badge.title=row.daily_status.reason", script)

    def test_add_user_dialog_has_separate_people_and_library_steps(self):
        page = self.parse("settings.html")
        dialog = page.by_id("addUserDialog")

        self.assertIn(dialog, page.by_id("profileRecipientList")["ancestors"])
        self.assertIn(dialog, page.by_id("profileRecipientLibraries")["ancestors"])
        self.assertTrue(page.by_id("profileRecipientLibraries")["hidden"])

    def test_main_page_does_not_add_explanation_panels(self):
        page = self.parse("settings.html")

        self.assertFalse(any("instruction-card" in node["classes"] for node in page.nodes))

    def test_script_uses_library_lifecycle_without_owner_library_shortcut(self):
        script = (ROOT / "src/helper/static/settings.js").read_text(encoding="utf-8")

        for endpoint in (
            "/api/plex/profiles/libraries",
            "/api/plex/profiles/library",
            "/api/plex/recipients/libraries",
        ):
            self.assertIn(endpoint, script)
        self.assertNotIn("library_id:String(owner.library?.id||'')", script)
        self.assertIn("PCHAuth.setProfile", script)

    def test_opening_a_managed_user_stays_on_accounts_without_batch_state(self):
        page = self.parse("settings.html")
        script = (ROOT / "src/helper/static/settings.js").read_text(encoding="utf-8")

        self.assertNotIn("showSettingsPanel('recommend')", script)
        self.assertNotIn("/api/profiles/daily/batch-status", script)
        self.assertNotIn("/api/profiles/daily/batch-schedule", script)
        self.assertNotIn('id="batchDailyAuto"', (ROOT / "src/helper/static/settings.html").read_text(encoding="utf-8"))

    def test_playback_learning_is_a_profile_row_switch_not_a_separate_settings_page(self):
        html = (ROOT / "src/helper/static/settings.html").read_text(encoding="utf-8")
        script = (ROOT / "src/helper/static/settings.js").read_text(encoding="utf-8")
        page = self.parse("settings.html")

        self.assertNotIn('data-settings-target="learning"', html)
        self.assertNotIn('id="settings-learning"', html)
        self.assertNotIn('id="behaviorUser"', html)
        self.assertNotIn('id="behaviorSave"', html)
        self.assertIn(page.by_id("people"), page.by_id("webhookTools")["ancestors"])
        self.assertIn("settings-control-cell", script)
        self.assertIn("/api/plex/profiles/control", script)


if __name__ == "__main__":
    unittest.main()
