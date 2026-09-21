import unittest
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class _SettingsLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.navigation = set()
        self.in_tools = False
        self.links = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "button" and "data-settings-target" in attrs:
            self.navigation.add(attrs["data-settings-target"])
        if tag == "section" and attrs.get("id") == "settings-playlists":
            self.in_tools = True
        if self.in_tools and tag == "a" and "href" in attrs:
            self.links[attrs["href"]] = attrs

    def handle_endtag(self, tag):
        if tag == "section" and self.in_tools:
            self.in_tools = False


class SettingsPlaylistAccessTests(unittest.TestCase):
    def test_settings_exposes_direct_links_to_three_existing_music_tools(self):
        document = _SettingsLinks()
        document.feed((ROOT / "src/helper/static/settings.html").read_text())

        self.assertIn("playlists", document.navigation)
        self.assertEqual({"/daily", "/mixes", "/library"}, set(document.links))


if __name__ == "__main__":
    unittest.main()
