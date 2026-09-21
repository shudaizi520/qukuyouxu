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


def test_typing_import_url_clears_stale_selected_file_label():
    script = (STATIC / "external.js").read_text()
    assert "$('selectedFile').hidden=true" in script


def test_import_export_row_does_not_repeat_missing_tab_label_and_count():
    page = (STATIC / "external.html").read_text()
    tab = page.split('data-match-status="missing"', 1)[1].split("</button>", 1)[0]
    export_row = page.split('id="replenishmentCard"', 1)[1].split("</section>", 1)[0]
    assert "缺失歌曲" in tab
    assert "补歌清单" in export_row
    assert "缺失歌曲" not in export_row
    assert "missingSummary" not in export_row


def test_library_task_heading_names_the_stage_not_its_action():
    page = (STATIC / "home.html").read_text()
    script = (STATIC / "home.js").read_text()
    assert 'id="taskTitle">整理任务' in page
    assert "$('taskTitle').textContent='整理任务'" in script
    assert 'id="analyzeLibrary">分析曲库' in page
