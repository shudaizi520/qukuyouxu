import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]


class ProfileUIV040Tests(unittest.TestCase):
    def test_settings_keeps_profile_manager_visible(self):
        html = (ROOT / "src/helper/static/settings.html").read_text(encoding="utf-8")
        self.assertIn('id="plexProfile"', html)
        self.assertIn('aria-label="当前用户"', html)
        self.assertIn('id="profileManager"', html)
        start = html.index('id="profileManager"')
        self.assertNotIn(" open", html[start:start + 100])
        self.assertIn('id="profileRecipientList"', html)
        self.assertIn('id="createProfile"', html)

    def test_settings_script_uses_profile_and_recipient_apis(self):
        js = (ROOT / "src/helper/static/settings.js").read_text(encoding="utf-8")
        for endpoint in (
            "/api/plex/profiles",
            "/api/plex/profiles/libraries",
            "/api/plex/profiles/library",
            "/api/plex/profiles/create",
            "/api/plex/recipients/libraries",
            "/api/plex/recipients/home/import",
            "/api/plex/recipients/shared/import",
        ):
            self.assertIn(endpoint, js)
        self.assertNotIn("profile.token", js)
        self.assertNotIn("authToken", js)

    def test_profile_controls_have_accessible_labels(self):
        html = (ROOT / "src/helper/static/settings.html").read_text(encoding="utf-8")
        self.assertIn('aria-live="polite"', html)
        self.assertIn('for="newProfileName"', html)
        self.assertIn('type="button" id="findPeople"', html)

    def test_settings_separates_current_user_people_and_connection(self):
        html = (ROOT / "src/helper/static/settings.html").read_text(encoding="utf-8")

        current = html.index('id="currentUser"')
        people = html.index('id="people"')
        connection = html.index('id="plexConnectionTools"')
        self.assertLess(current, people)
        self.assertLess(connection, people)
        self.assertIn("当前账户", html)
        self.assertIn("每日推荐用户", html)
        self.assertIn("添加用户", html)
        self.assertNotIn('id="findHomeUsers"', html)
        self.assertNotIn('id="findSharedUsers"', html)

    def test_first_run_connection_is_in_accounts_and_has_disconnect_action(self):
        html = (ROOT / "src/helper/static/settings.html").read_text(encoding="utf-8")
        script = (ROOT / "src/helper/static/settings.js").read_text(encoding="utf-8")

        accounts = html.index('id="settings-accounts"')
        system = html.index('id="settings-system"')
        connection = html.index('id="plexConnectionTools"')
        self.assertLess(accounts, connection)
        self.assertLess(connection, system)
        self.assertIn('id="disconnectPlex"', html)
        self.assertIn('id="profileSwitcher"', html)
        self.assertIn("function profileLabel(row)", script)
        self.assertIn("$('profileSwitcher').hidden=!plexProfiles.length", script)
        self.assertIn("/api/plex/disconnect", script)

    def test_first_run_pages_link_directly_to_the_accounts_panel(self):
        for name in ("daily.html", "home.html"):
            html = (ROOT / "src/helper/static" / name).read_text(encoding="utf-8")
            self.assertIn('href="/settings#accounts"', html)

    def test_partial_connection_keeps_library_choice_visible_before_adding_people(self):
        script = (ROOT / "src/helper/static/settings.js").read_text(encoding="utf-8")

        self.assertIn("const libraryReady=!!value.library?.id", script)
        self.assertIn("const showTools=!configured||!libraryReady||state==='auth_invalid'", script)
        self.assertIn("$('plexConnectionTools').open=showTools", script)
        self.assertIn("placeholder.textContent='请选择音乐资料库'", script)
        self.assertIn("select.value=previous", script)
        self.assertIn("note('音乐资料库已保存。')", script)
        guard = script.index("if(!owner?.library?.id)")
        dialog = script.index("$('addUserDialog').showModal()")
        self.assertLess(guard, dialog)

    def test_existing_people_can_be_opened_removed_and_restored(self):
        script = (ROOT / "src/helper/static/settings.js").read_text(encoding="utf-8")

        self.assertIn("row.existing_profile_id", script)
        self.assertIn("remove.textContent='移除'", script)
        self.assertIn("row.archived_profile_id?'重新添加':'添加'", script)
        self.assertIn("/api/plex/profiles/remove", script)
        self.assertIn("/api/plex/profiles/restore", script)
        self.assertIn("/api/plex/recipients?owner_profile_id=", script)


if __name__ == "__main__":
    unittest.main()
