"""Regression checks for the compact settings and embedded account flow."""

from html.parser import HTMLParser
from pathlib import Path


STATIC = Path(__file__).resolve().parents[1] / "src/helper/static"


class _SettingsSystem(HTMLParser):
    def __init__(self):
        super().__init__()
        self.inside = False
        self.depth = 0
        self.links = []
        self.ids = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "section" and attrs.get("id") == "settings-system":
            self.inside = True
            self.depth = 1
        elif self.inside and tag == "section":
            self.depth += 1
        if self.inside:
            if tag == "a":
                self.links.append(attrs.get("href"))
            if "id" in attrs:
                self.ids.append(attrs["id"])

    def handle_endtag(self, tag):
        if self.inside and tag == "section":
            self.depth -= 1
            if self.depth == 0:
                self.inside = False


def test_settings_does_not_repeat_navigation_or_header_version():
    page = _SettingsSystem()
    page.feed((STATIC / "settings.html").read_text())
    assert page.links == []
    assert "systemVersion" not in page.ids
    assert "passwordTools" in page.ids


def test_embedded_profile_change_reaches_outer_playlist_workspace():
    auth = (STATIC / "auth.js").read_text()
    playlists = (STATIC / "playlists.js").read_text()
    assert "type:'pch-profile-selected'" in auth
    assert "event.data?.type==='pch-profile-selected'" in playlists
    assert "await loadProfiles()" in playlists


def test_stale_embedded_profile_selection_cannot_override_newer_selection():
    playlists = (STATIC / "playlists.js").read_text()
    assert "let embeddedProfileRequest=0" in playlists
    assert "const sequence=++embeddedProfileRequest" in playlists
    assert "sequence!==embeddedProfileRequest||PCHAuth.profile()!==profileId" in playlists


def test_typing_import_url_clears_stale_selected_file_label():
    script = (STATIC / "external.js").read_text()
    assert "$('selectedFile').hidden=true" in script


def test_import_export_actions_live_beside_the_missing_tab_without_repeating_its_count():
    page = (STATIC / "external.html").read_text()
    tab = page.split('data-match-status="missing"', 1)[1].split("</button>", 1)[0]
    export_row = page.split('id="replenishmentCard"', 1)[1].split('id="reviewToolbar"', 1)[0]
    assert "缺失歌曲" in tab
    assert "下载缺失歌曲" in export_row
    assert "missingCount" not in export_row
    assert "missingSummary" not in export_row


def test_library_task_heading_names_the_stage_not_its_action():
    page = (STATIC / "home.html").read_text()
    script = (STATIC / "home.js").read_text()
    assert 'id="taskTitle">整理任务' in page
    assert "$('taskTitle').textContent='整理任务'" in script
    assert 'id="analyzeLibrary">分析曲库' in page


def test_narrow_search_keeps_like_and_add_actions_available():
    styles = (STATIC / "product.css").read_text()
    assert "@media(max-width:720px){body[data-view=playlists] .playlist-search-result .playlist-search-actions" in styles
    assert "body[data-view=playlists] .playlist-search-result .playlist-search-actions button{display:inline-grid}" in styles
    assert "body[data-view=playlists] .playlist-search-result .playlist-search-title-line .playlist-heart{display:grid}" in styles
