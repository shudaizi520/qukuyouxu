import sys
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


RULE_URI = (
    "server://machine123/com.plexapp.plugins.library/"
    "library/sections/11/all?type=10&track.userRating%3E=8"
)


class FavoriteSmartClientTests(unittest.TestCase):
    def test_first_creation_uses_section_scoped_four_star_rule(self):
        from helper.clients import PlexClient

        client = object.__new__(PlexClient)
        client.machine = "machine123"
        calls = []

        def xml(path, method="GET", params=None):
            calls.append((path, method, params))
            if method == "POST":
                return ET.fromstring('<MediaContainer><Playlist ratingKey="51"/></MediaContainer>')
            return ET.fromstring(
                '<MediaContainer><Playlist ratingKey="51" title="我的最爱" '
                'playlistType="audio" smart="1" content="'
                + RULE_URI.replace("&", "&amp;") + '"/></MediaContainer>'
            )

        client._xml = xml
        result = client.create_favorite_smart("11")

        self.assertEqual("51", result["id"])
        self.assertEqual(RULE_URI, result["content"])
        self.assertEqual(
            ("/playlists", "POST", {"title": "我的最爱", "type": "audio", "smart": 1, "uri": RULE_URI}),
            calls[0],
        )

    def test_rule_replacement_keeps_playlist_id(self):
        from helper.clients import PlexClient

        client = object.__new__(PlexClient)
        client.machine = "machine123"
        calls = []

        def xml(path, method="GET", params=None):
            calls.append((path, method, params))
            return ET.fromstring(
                '<MediaContainer><Playlist ratingKey="51" title="我的最爱" '
                'playlistType="audio" smart="1" content="'
                + RULE_URI.replace("&", "&amp;") + '"/></MediaContainer>'
            )

        client._xml = xml
        result = client.replace_favorite_smart_rule("51", "11")

        self.assertEqual("51", result["id"])
        self.assertEqual(("/playlists/51/items", "PUT", {"uri": RULE_URI}), calls[0])

    def test_migration_rechecks_old_rule_and_title_before_put(self):
        from helper.clients import PlexClient, PlexError

        client = object.__new__(PlexClient)
        client.machine = "machine123"
        calls = []

        def xml(path, method="GET", params=None):
            calls.append((path, method, params))
            return ET.fromstring(
                '<MediaContainer><Playlist ratingKey="51" title="我的最爱" '
                'playlistType="audio" smart="1" content="'
                + RULE_URI.replace("&", "&amp;") + '"/></MediaContainer>'
            )

        client._xml = xml
        with self.assertRaisesRegex(PlexError, "已变化"):
            client.replace_favorite_smart_rule(
                "51", "11", expected_content="old five-star content",
                expected_title="我的最爱",
            )
        self.assertEqual([("/playlists/51", "GET", None)], calls)


if __name__ == "__main__":
    unittest.main()
