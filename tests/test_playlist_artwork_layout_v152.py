"""Artwork UI landmarks remain available across the playlist workspace."""
from html.parser import HTMLParser
from pathlib import Path
import re


STATIC = Path(__file__).resolve().parents[1] / "src/helper/static"


class _Landmarks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = set()
        self.attributes = {}
        self.scripts = set()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get("id"):
            self.ids.add(attrs["id"])
            self.attributes[attrs["id"]] = attrs
        if tag == "script" and attrs.get("src"):
            self.scripts.add(attrs["src"].split("?", 1)[0])


def test_playlist_header_has_cover_landmark_and_artwork_module():
    page = _Landmarks()
    page.feed((STATIC / "playlists.html").read_text(encoding="utf-8"))
    script = (STATIC / "playlists.js").read_text(encoding="utf-8")
    assert "playlistHeroArtwork" in page.ids
    assert page.attributes["playlistHeroArtwork"].get("data-variant") == "hero"
    assert "./playlist-artwork.js" in script


def test_playlist_header_has_one_title_without_duplicate_kind_label():
    page = _Landmarks()
    page.feed((STATIC / "playlists.html").read_text(encoding="utf-8"))
    assert "playlistTitle" in page.ids
    assert "playlistSummary" in page.ids
    assert "playlistKind" not in page.ids


def test_artwork_layout_has_a_visible_placeholder_surface():
    css = (STATIC / "product.css").read_text(encoding="utf-8")
    assert ".playlist-cover.is-placeholder" in css
    assert ".playlist-cover-card" in css
    assert ".playlist-cover-sidebar" in css


def test_playlist_cover_and_copy_share_the_same_row_on_desktop():
    css = (STATIC / "product.css").read_text(encoding="utf-8")
    desktop_css = css.split("/* Plex artwork stays secondary to playlist titles and falls back cleanly. */", 1)[0]
    card_rules = re.findall(r"(?<![\w.-])\.playlist-section-card\s*\{([^}]*)\}", desktop_css)
    sidebar_rules = re.findall(r"#customPlaylistList button\s*\{([^}]*)\}", desktop_css)
    assert card_rules and sidebar_rules
    assert "display:grid" in card_rules[-1]
    assert "grid-template-columns:72px minmax(0,1fr)" in card_rules[-1]
    assert "grid-template-columns:40px minmax(0,1fr)" in sidebar_rules[-1]
    assert ".playlist-cover-sidebar{width:38px;height:38px" in css


def test_playlist_cover_and_copy_stay_compact_on_small_screens():
    css = (STATIC / "product.css").read_text(encoding="utf-8")
    after_artwork = css.split(".playlist-cover-card{width:72px;height:72px", 1)[1]
    before_themes = after_artwork.split("/* Appearance palettes: backgrounds and color only. */", 1)[0]
    assert "@media(max-width:700px){.playlist-section-card{grid-template-columns:58px minmax(0,1fr)" in before_themes
    assert ".playlist-cover-card{width:58px;height:58px}" in before_themes
