import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]


class ProfileUIV040Tests(unittest.TestCase):
    def test_settings_keeps_profile_manager_visible(self):
        html = (ROOT / "src/helper/static/settings.html").read_text(encoding="utf-8")
        self.assertNotIn('id="plexProfile"', html)
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
        self.assertIn("Plex 连接", html)
        self.assertNotIn("当前账户", html)
        self.assertIn("用户管理", html)
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
        self.assertNotIn('id="profileSwitcher"', html)
        self.assertIn("function profileLabel(row)", script)
        self.assertIn("PCHAuth.profile()", script)
        self.assertIn("/api/plex/disconnect", script)

    def test_first_run_pages_link_directly_to_the_accounts_panel(self):
        for name in ("daily.html", "home.html"):
            html = (ROOT / "src/helper/static" / name).read_text(encoding="utf-8")
            self.assertIn('href="/settings#accounts"', html)

    def test_partial_connection_keeps_library_choice_visible(self):
        script = (ROOT / "src/helper/static/settings.js").read_text(encoding="utf-8")

        self.assertIn("const libraryReady=!!value.library?.id", script)
        self.assertIn("const showTools=!configured||!libraryReady||state==='auth_invalid'", script)
        self.assertIn("$('plexConnectionTools').open=showTools", script)
        self.assertIn("placeholder.textContent='请选择音乐资料库'", script)
        self.assertIn("select.value=previous", script)
        self.assertIn("note('音乐资料库已保存。')", script)
        self.assertIn("$('addUserDialog').showModal()", script)

    def test_existing_people_can_be_removed_and_added_fresh(self):
        script = (ROOT / "src/helper/static/settings.js").read_text(encoding="utf-8")

        self.assertIn("row.existing_profile_id", script)
        self.assertIn("remove.textContent=row.removal?", script)
        self.assertIn("button.textContent='添加'", script)
        self.assertIn("/api/plex/profiles/remove", script)
        self.assertNotIn("/api/plex/profiles/restore", script)
        self.assertIn("responseJson('/api/plex/recipients')", script)
        self.assertIn("data.owner_profile_id", script)
        self.assertNotIn("function ownerProfile()", script)

    def test_managed_user_rows_do_not_duplicate_the_profile_switcher(self):
        script = (ROOT / "src/helper/static/settings.js").read_text(encoding="utf-8")
        rows = script.split("function renderManagedUsers(){", 1)[1].split("function renderRecipients(", 1)[0]

        self.assertNotIn("$('plexProfile').onchange", script)
        self.assertNotIn("open.textContent=row.id===activeProfile?'当前':'打开'", rows)
        self.assertNotIn("open.onclick=", rows)
        self.assertIn("remove.textContent=row.removal?", rows)


if __name__ == "__main__":
    unittest.main()
