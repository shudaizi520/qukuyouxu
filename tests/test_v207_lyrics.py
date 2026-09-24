import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


TIMED_XML = b'''<MediaContainer><Lyrics timed="1">
<Line startOffset="1200" endOffset="2500"><Span text="\xe7\xac\xac\xe4\xb8\x80\xe5\x8f\xa5"/></Line>
<Line startOffset="2500"><Span text="\xe7\xac\xac\xe4\xba\x8c"/><Span text="\xe5\x8f\xa5"/></Line>
</Lyrics></MediaContainer>'''


class LyricsParserTests(unittest.TestCase):
    def test_timed_plex_lyrics_are_normalized(self):
        from helper.lyrics import parse_plex_lyrics

        self.assertEqual({
            "kind": "timed",
            "lines": [
                {"start_ms": 1200, "end_ms": 2500, "text": "第一句"},
                {"start_ms": 2500, "text": "第二句"},
            ],
        }, parse_plex_lyrics(TIMED_XML))

    def test_plain_empty_and_malformed_lyrics_have_bounded_fallbacks(self):
        from helper.lyrics import parse_plex_lyrics

        plain = b'<MediaContainer><Lyrics><Line><Span text="one"/></Line><Line text="two"/></Lyrics></MediaContainer>'
        self.assertEqual({"kind": "plain", "lines": [{"text": "one"}, {"text": "two"}]},
                         parse_plex_lyrics(plain))
        self.assertEqual({"kind": "none", "lines": []},
                         parse_plex_lyrics(b'<MediaContainer/>'))
        with self.assertRaisesRegex(ValueError, "实体"):
            parse_plex_lyrics(b'<!DOCTYPE x [<!ENTITY y "bad">]><MediaContainer/>')
        with self.assertRaisesRegex(ValueError, "过大"):
            parse_plex_lyrics(b"x" * (1024 * 1024 + 1))

    def test_lines_are_trimmed_capped_and_invalid_timing_is_ignored(self):
        from helper.lyrics import parse_plex_lyrics

        body = ('<MediaContainer><Lyrics timed="1">'
                '<Line startOffset="bad"><Span text="ignored"/></Line>'
                '<Line startOffset="0"><Span text="  kept  "/></Line>'
                '<Line startOffset="1"><Span text="' + ('x' * 2100) + '"/></Line>'
                '</Lyrics></MediaContainer>').encode()
        result = parse_plex_lyrics(body)
        self.assertEqual(2, len(result["lines"]))
        self.assertEqual("kept", result["lines"][0]["text"])
        self.assertEqual(2000, len(result["lines"][1]["text"]))


class PlexLyricsClientTests(unittest.TestCase):
    def test_client_reads_only_the_selected_numeric_lyrics_stream(self):
        from helper.clients import PlexClient

        client = object.__new__(PlexClient)
        paths = []
        client._xml = lambda _path: ET.fromstring('''<MediaContainer>
          <Track ratingKey="7" librarySectionID="11"><Media><Part>
           <Stream id="87" streamType="4" codec="lrc"/>
           <Stream id="88" streamType="4" codec="lrc" selected="1"/>
          </Part></Media></Track></MediaContainer>''')
        client._bounded_xml_bytes = lambda path, _limit: paths.append(path) or TIMED_XML

        result = client.track_lyrics("7")

        self.assertEqual("timed", result["kind"])
        self.assertEqual(["/library/streams/88"], paths)

    def test_client_returns_none_without_a_safe_lyrics_stream(self):
        from helper.clients import PlexClient

        for stream in ('', '<Stream id="unsafe" streamType="4" codec="lrc"/>'):
            client = object.__new__(PlexClient)
            client._xml = lambda _path, stream=stream: ET.fromstring(
                f'<MediaContainer><Track ratingKey="7" librarySectionID="11"><Media><Part>{stream}</Part></Media></Track></MediaContainer>'
            )
            client._bounded_xml_bytes = lambda *_args: self.fail("unsafe stream must not be fetched")
            self.assertEqual({"kind": "none", "lines": []}, client.track_lyrics("7"))


class ScopedLyricsTests(unittest.TestCase):
    def test_selected_library_track_is_allowed(self):
        from helper.lyrics import read_track_lyrics

        plex = SimpleNamespace(
            track_section=lambda _track_id: "11",
            track_lyrics=lambda _track_id: {"kind": "plain", "lines": [{"text": "歌词"}]},
        )
        store = SimpleNamespace(get=lambda key, default=None: {"settings": {"section": "11"}}.get(key, default))
        engine = SimpleNamespace(store=store, plex_factory=lambda _settings: plex)
        self.assertEqual("plain", read_track_lyrics(engine, "7")["kind"])

    def test_other_library_track_is_rejected_before_lyrics_are_read(self):
        from helper.lyrics import read_track_lyrics

        called = []
        plex = SimpleNamespace(
            track_section=lambda _track_id: "22",
            track_lyrics=lambda track_id: called.append(track_id),
        )
        store = SimpleNamespace(get=lambda key, default=None: {"settings": {"section": "11"}}.get(key, default))
        engine = SimpleNamespace(store=store, plex_factory=lambda _settings: plex)
        with self.assertRaisesRegex(ValueError, "当前曲库"):
            read_track_lyrics(engine, "7")
        self.assertEqual([], called)

    def test_playlist_hub_registers_profile_scoped_lyrics_route(self):
        source = (ROOT / "src/helper/playlist_hub.py").read_text(encoding="utf-8")
        self.assertIn('@app.get("/api/playlists/library/tracks/{track_id}/lyrics")', source)
        route = source.split('@app.get("/api/playlists/library/tracks/{track_id}/lyrics")', 1)[1].split(
            '@app.get("/api/playlists/library/tracks/{track_id}/artwork")', 1
        )[0]
        self.assertIn("profiles.fixed_active", route)
        self.assertIn("read_track_lyrics", route)


if __name__ == "__main__":
    unittest.main()
