"""Artwork UI landmarks remain available across the playlist workspace."""
from html.parser import HTMLParser
from pathlib import Path


STATIC = Path(__file__).resolve().parents[1] / "src/helper/static"


class _Landmarks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = set()
        self.scripts = set()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get("id"):
            self.ids.add(attrs["id"])
        if tag == "script" and attrs.get("src"):
            self.scripts.add(attrs["src"].split("?", 1)[0])


def test_playlist_header_has_cover_landmark_and_artwork_module():
    page = _Landmarks()
    page.feed((STATIC / "playlists.html").read_text(encoding="utf-8"))
    script = (STATIC / "playlists.js").read_text(encoding="utf-8")
    assert "playlistHeroArtwork" in page.ids
    assert "./playlist-artwork.js" in script


def test_artwork_layout_has_a_visible_placeholder_surface():
    css = (STATIC / "product.css").read_text(encoding="utf-8")
    assert ".playlist-cover.is-placeholder" in css
    assert ".playlist-cover-card" in css
    assert ".playlist-cover-sidebar" in css
