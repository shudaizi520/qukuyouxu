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
PLEX_NORMALIZED_URI = (
    "library://abc/directory/%2Flibrary%2Fsections%2F11%2Fall%3F"
    "type%3D10%26track%2EuserRating%253E%3D8"
)


class FavoriteSmartClientTests(unittest.TestCase):
    def test_creation_accepts_plex_normalized_rule_for_same_library(self):
        from helper.clients import PlexClient

        client = object.__new__(PlexClient)
        client.machine = "machine123"

        def xml(path, method="GET", params=None):
            if method == "POST":
                return ET.fromstring('<MediaContainer><Playlist ratingKey="51"/></MediaContainer>')
            return ET.fromstring(
                '<MediaContainer><Playlist ratingKey="51" title="我喜欢" '
                'playlistType="audio" smart="1" content="'
                + PLEX_NORMALIZED_URI + '"/></MediaContainer>'
            )

        client._xml = xml
        result = client.create_favorite_smart("11")
        self.assertEqual(PLEX_NORMALIZED_URI, result["content"])

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
                '<MediaContainer><Playlist ratingKey="51" title="我喜欢" '
                'playlistType="audio" smart="1" content="'
                + RULE_URI.replace("&", "&amp;") + '"/></MediaContainer>'
            )

        client._xml = xml
        result = client.create_favorite_smart("11")

        self.assertEqual("51", result["id"])
        self.assertEqual(RULE_URI, result["content"])
        self.assertEqual(
            ("/playlists", "POST", {"title": "我喜欢", "type": "audio", "smart": 1, "uri": RULE_URI}),
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
                '<MediaContainer><Playlist ratingKey="51" title="我喜欢" '
                'playlistType="audio" smart="1" content="'
                + RULE_URI.replace("&", "&amp;") + '"/></MediaContainer>'
            )

        client._xml = xml
        result = client.replace_favorite_smart_rule(
            "51", "11", expected_content=RULE_URI, expected_title="我喜欢",
        )

        self.assertEqual("51", result["id"])
        self.assertEqual(("/playlists/51/items", "PUT", {"uri": RULE_URI}), calls[1])

    def test_rule_replacement_accepts_plex_normalized_post_write_content(self):
        from helper.clients import PlexClient

        client = object.__new__(PlexClient)
        client.machine = "machine123"
        old_uri = RULE_URI.replace("track.userRating%3E=8", "track.userRating=10")
        written = False

        def xml(path, method="GET", params=None):
            nonlocal written
            if method == "PUT":
                written = True
                return ET.fromstring("<MediaContainer/>")
            content = PLEX_NORMALIZED_URI if written else old_uri
            return ET.fromstring(
                '<MediaContainer><Playlist ratingKey="51" title="我喜欢" '
                'playlistType="audio" smart="1" content="'
                + content.replace("&", "&amp;") + '"/></MediaContainer>'
            )

        client._xml = xml
        result = client.replace_favorite_smart_rule(
            "51", "11", expected_content=old_uri, expected_title="我喜欢",
        )
        self.assertEqual(PLEX_NORMALIZED_URI, result["content"])

    def test_replacement_without_verified_prior_identity_does_not_write(self):
        from helper.clients import PlexClient, PlexError

        client = object.__new__(PlexClient)
        client.machine = "machine123"
        client._xml = lambda *_args, **_kwargs: self.fail("unexpected Plex write")
        with self.assertRaisesRegex(PlexError, "必须提供"):
            client.replace_favorite_smart_rule("51", "11")

    def test_migration_rechecks_old_rule_and_title_before_put(self):
        from helper.clients import PlexClient, PlexError

        client = object.__new__(PlexClient)
        client.machine = "machine123"
        calls = []

        def xml(path, method="GET", params=None):
            calls.append((path, method, params))
            return ET.fromstring(
                '<MediaContainer><Playlist ratingKey="51" title="我喜欢" '
                'playlistType="audio" smart="1" content="'
                + RULE_URI.replace("&", "&amp;") + '"/></MediaContainer>'
            )

        client._xml = xml
        with self.assertRaisesRegex(PlexError, "已变化"):
            client.replace_favorite_smart_rule(
                "51", "11", expected_content="old five-star content",
                expected_title="我喜欢",
            )
        self.assertEqual([("/playlists/51", "GET", None)], calls)


if __name__ == "__main__":
    unittest.main()
