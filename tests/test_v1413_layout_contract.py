"""Parse rendered page structure for the compact playlist workspace."""

from html.parser import HTMLParser
from pathlib import Path


STATIC = Path(__file__).resolve().parents[1] / "src/helper/static"


class PageElements(HTMLParser):
    def __init__(self):
        super().__init__()
        self.by_id = {}

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if "id" in values:
            self.by_id[values["id"]] = (tag, values)


def page(name):
    parsed = PageElements()
    parsed.feed((STATIC / name).read_text())
    return parsed.by_id


def test_import_has_song_columns_review_selection_and_one_copy_entry():
    elements = page("external.html")
    assert elements["trackColumnHead"][0] == "div"
    assert elements["reviewSelectAll"][0] == "input"
    assert elements["confirmSelected"][0] == "button"
    assert elements["copyMissing"][0] == "button"
    assert "copyShareLink" not in elements
    assert "reloadSources" not in elements


def test_embedded_pages_do_not_repeat_back_navigation_or_settings_title():
    workspace = page("playlists.html")
    settings_html = (STATIC / "settings.html").read_text()
    assert "playlistToolBack" not in workspace
    assert "playlistToolTitle" not in workspace
    assert '<aside class="settings-nav" aria-label="设置分类">\n   <h1>设置</h1>' not in settings_html


def test_smart_and_library_section_headers_use_one_title_only():
    workspace = page("playlists.html")
    assert "playlistSectionTitle" in workspace
    assert "playlistSectionKind" not in workspace
    assert "playlistSectionSummary" not in workspace
    assert "librarySearchBack" not in workspace
