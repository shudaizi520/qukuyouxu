import asyncio
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from xml.etree import ElementTree as ET

import requests

from tests.test_v130_external_service import snapshot


class FakeAudioResponse:
    def __init__(self, status=206, headers=None, chunks=None):
        self.status_code = status
        self.headers = headers or {
            "Content-Type": "audio/flac", "Content-Length": "1024",
            "Content-Range": "bytes 0-1023/4096", "Accept-Ranges": "bytes",
            "X-Plex-Token": "must-not-leak",
        }
        self.chunks = list(chunks or [b"a" * 512, b"b" * 512])
        self.closed = False

    def iter_content(self, size):
        if size != 65536:
            raise AssertionError("wrong chunk size")
        yield from self.chunks

    def close(self):
        self.closed = True


class FakePlex:
    def __init__(self, response=None, error=None):
        self.response = response or FakeAudioResponse()
        self.error = error
        self.calls = []
        self.browser_calls = []

    def open_audio_part(self, track_id, range_header=""):
        self.calls.append((track_id, range_header))
        if self.error:
            raise self.error
        return self.response

    def open_browser_audio(self, track_id, range_header="", offset_seconds=0):
        self.browser_calls.append((track_id, range_header, offset_seconds))
        return self.open_audio_part(track_id, range_header)


async def close_response(response):
    if response.background:
        await response.background()


class ExternalAudioV130Tests(unittest.TestCase):
    def setUp(self):
        from helper.external_store import ExternalRepository
        from helper.profiles import ProfileRegistry
        from helper.scoped_store import ScopedStore
        from helper.store import Store

        self.temp = tempfile.TemporaryDirectory()
        self.base = Store(Path(self.temp.name))
        registry = ProfileRegistry(self.base)
        registry.create("长辈", "home", profile_id="parent")
        self.store = ScopedStore(self.base, "default", registry=registry)
        self.other = ScopedStore(self.base, "parent", registry=registry)
        settings = self.store.get("settings")
        settings.update(plex_url="http://plex", plex_token="token-token", section="11")
        self.store.set("settings", settings)
        self.store.set("catalog", [
            {"id": "10", "title": "已有一", "artist": "歌手甲", "available": True},
            {"id": "40", "title": "同名歌", "artist": "歌手丁", "available": True},
        ])
        repository = ExternalRepository(self.store)
        self.source = repository.upsert_source("default", snapshot(), 2_000_000_000)
        repository.replace_matches("default", self.source["id"], [
            {"source_track_key": "a", "status": "matched", "plex_track_id": "10", "candidate_ids": [], "reason": "matched", "manual": False},
            {"source_track_key": "b", "status": "missing", "plex_track_id": "", "candidate_ids": [], "reason": "missing", "manual": False},
            {"source_track_key": "d", "status": "review", "plex_track_id": "", "candidate_ids": ["40"], "reason": "ambiguous", "manual": False},
        ], "catalog-r1")

    def tearDown(self):
        self.temp.cleanup()

    def stream(self, plex=None, track="a", range_header="bytes=0-1023", session="session-a", candidate="", offset=0):
        from helper.external_audio import stream_local_audio

        plex = plex or FakePlex()
        response = stream_local_audio(
            self.store, lambda _cfg: plex, self.source["id"], track,
            range_header, session, candidate_id=candidate, offset_seconds=offset,
        )
        return plex, response

    def test_audio_requires_source_and_match_in_current_profile(self):
        from helper.external_audio import stream_local_audio

        with self.assertRaisesRegex(ValueError, "当前歌单"):
            stream_local_audio(self.other, lambda _cfg: FakePlex(), self.source["id"], "a", "", "other")
        with self.assertRaisesRegex(ValueError, "可靠匹配"):
            self.stream(track="b")

    def test_range_and_safe_headers_are_forwarded_without_token(self):
        plex, response = self.stream()
        self.assertEqual(206, response.status_code)
        self.assertEqual("bytes 0-1023/4096", response.headers["Content-Range"])
        self.assertNotIn("X-Plex-Token", response.headers)
        self.assertEqual([("10", "bytes=0-1023")], plex.calls)
        self.assertEqual([("10", "bytes=0-1023", 0)], plex.browser_calls)
        asyncio.run(close_response(response))
        self.assertTrue(plex.response.closed)

    def test_playback_checks_only_the_selected_cached_track(self):
        original_get = self.store.get
        def get_without_catalog(key, default=None):
            if key == "catalog":
                raise AssertionError("播放导入歌单不应解码整个曲库")
            return original_get(key, default)

        with patch.object(self.store, "get", side_effect=get_without_catalog):
            _plex, response = self.stream(session="selected-only")
        asyncio.run(close_response(response))

    def test_review_candidate_must_belong_to_that_row(self):
        plex, response = self.stream(track="d", candidate="40")
        self.assertEqual([("40", "bytes=0-1023")], plex.calls)
        asyncio.run(close_response(response))
        with self.assertRaisesRegex(ValueError, "候选"):
            self.stream(track="d", candidate="10", session="candidate-invalid")

    def test_invalid_multiple_ranges_and_unavailable_catalog_tracks_are_rejected(self):
        for value in ("items=0-1", "bytes=0-1,4-5", "bytes=-", "bytes=abc-10"):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "Range"):
                self.stream(range_header=value, session="bad-" + value)
        catalog = self.store.get("catalog")
        catalog[0]["available"] = False
        self.store.set("catalog", catalog)
        with self.assertRaisesRegex(ValueError, "当前曲库"):
            self.stream(session="unavailable")

    def test_redirect_timeout_and_oversized_declared_audio_are_rejected_and_closed(self):
        from helper.clients import PlexError

        redirect = FakeAudioResponse(status=302)
        with self.assertRaisesRegex(PlexError, "HTTP"):
            self.stream(FakePlex(response=redirect), session="redirect")
        self.assertTrue(redirect.closed)

        oversized = FakeAudioResponse(headers={"Content-Type": "audio/flac", "Content-Length": str(1024 ** 3 + 1)})
        with self.assertRaisesRegex(ValueError, "过大"):
            self.stream(FakePlex(response=oversized), session="oversized")
        self.assertTrue(oversized.closed)

        with self.assertRaisesRegex(PlexError, "连接"):
            self.stream(FakePlex(error=requests.Timeout()), session="timeout")

    def test_three_active_streams_allow_one_replacement_but_remain_bounded(self):
        first_plex, first = self.stream(session="same-session")
        second_plex, second = self.stream(session="same-session")
        third_plex, third = self.stream(session="same-session")
        with self.assertRaisesRegex(ValueError, "试听"):
            self.stream(session="same-session")
        asyncio.run(close_response(first))
        fourth_plex, fourth = self.stream(session="same-session")
        asyncio.run(close_response(second))
        asyncio.run(close_response(third))
        asyncio.run(close_response(fourth))
        self.assertTrue(first_plex.response.closed)
        self.assertTrue(second_plex.response.closed)
        self.assertTrue(third_plex.response.closed)
        self.assertTrue(fourth_plex.response.closed)

    def test_stream_iterator_close_releases_slot_before_background_cleanup(self):
        async def scenario():
            responses = [self.stream(session="disconnect") for _ in range(3)]
            response = responses[0][1]

            async def one_chunk():
                yield b"a" * 512

            async def disconnected(message):
                if message["type"] == "http.response.body":
                    raise asyncio.CancelledError()

            response.body_iterator = one_chunk()
            with self.assertRaises(asyncio.CancelledError):
                await response.stream_response(disconnected)
            self.assertTrue(responses[0][0].response.closed)
            _next_plex, next_response = self.stream(session="disconnect")
            await next_response.background()
            for _plex, response in responses[1:]:
                await response.background()

        asyncio.run(scenario())

    def test_stream_forwards_validated_transcode_offset(self):
        plex, response = self.stream(offset=91.25)
        self.assertEqual([("10", "bytes=0-1023", 91.25)], plex.browser_calls)
        asyncio.run(close_response(response))


class PlexAudioPartV130Tests(unittest.TestCase):
    def test_audio_source_prefers_the_selected_safe_media_without_rejecting_alternatives(self):
        from helper.clients import PlexClient

        client = object.__new__(PlexClient)
        client._xml = lambda _path: ET.fromstring(
            '<MediaContainer><Track ratingKey="10">'
            '<Media container="flac"><Part key="/library/parts/first.flac" accessible="1" exists="1" /></Media>'
            '<Media container="flac" selected="1"><Part key="/library/parts/selected.flac" accessible="1" exists="1" /></Media>'
            '</Track></MediaContainer>'
        )

        track_id, media_index, part_index, _media, part = client._audio_source("10")

        self.assertEqual("10", track_id)
        self.assertEqual(1, media_index)
        self.assertEqual(0, part_index)
        self.assertEqual("/library/parts/selected.flac", part.get("key"))

    def test_part_lookup_rejects_ambiguous_or_unsafe_parts_and_disables_redirects(self):
        from helper.clients import PlexClient, PlexError

        class Session:
            def __init__(self):
                self.calls = []

            def request(self, method, url, **kwargs):
                self.calls.append((method, url, kwargs))
                return FakeAudioResponse()

        client = object.__new__(PlexClient)
        client.base = "http://plex"
        client.session = Session()
        client._xml = lambda _path: ET.fromstring(
            '<MediaContainer><Track ratingKey="10"><Media><Part key="/library/parts/1/file.flac" accessible="1" exists="1" /></Media></Track></MediaContainer>'
        )
        response = client.open_audio_part("10", "bytes=0-1023")
        self.assertEqual("http://plex/library/parts/1/file.flac", client.session.calls[0][1])
        self.assertFalse(client.session.calls[0][2]["allow_redirects"])
        self.assertEqual("bytes=0-1023", client.session.calls[0][2]["headers"]["Range"])
        response.close()

        client._xml = lambda _path: ET.fromstring(
            '<MediaContainer><Track ratingKey="10"><Media><Part key="https://attacker.example/a" /></Media></Track></MediaContainer>'
        )
        with self.assertRaisesRegex(PlexError, "音频"):
            client.open_audio_part("10")

    def test_browser_audio_direct_plays_seekable_flac_with_range(self):
        from helper.clients import PlexClient

        class Session:
            def __init__(self):
                self.calls = []

            def request(self, method, url, **kwargs):
                self.calls.append((method, url, kwargs))
                return FakeAudioResponse(status=206, headers={
                    "Content-Type": "audio/flac", "Content-Length": "1024",
                    "Content-Range": "bytes 4096-5119/12000", "Accept-Ranges": "bytes",
                })

        client = object.__new__(PlexClient)
        client.base = "http://plex"
        client.session = Session()
        client._xml = lambda _path: ET.fromstring(
            '<MediaContainer><Track ratingKey="10"><Media container="flac" audioCodec="flac"><Part key="/library/parts/1/file.flac" container="flac" accessible="1" exists="1" /></Media></Track></MediaContainer>'
        )
        response = client.open_browser_audio("10", "bytes=4096-")
        method, url, kwargs = client.session.calls[-1]
        self.assertEqual("GET", method)
        self.assertEqual("http://plex/library/parts/1/file.flac", url)
        self.assertEqual("bytes=4096-", kwargs["headers"]["Range"])
        self.assertIsNone(kwargs.get("params"))
        response.close()

        for value in (-1, float("inf"), 86401, "bad"):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "播放位置"):
                client.open_browser_audio("10", offset_seconds=value)

    def test_browser_audio_direct_plays_seekable_ogg_vorbis_and_opus_with_range(self):
        from helper.clients import PlexClient

        class Session:
            def __init__(self):
                self.calls = []

            def request(self, method, url, **kwargs):
                self.calls.append((method, url, kwargs))
                return FakeAudioResponse(status=206, headers={
                    "Content-Type": "audio/ogg", "Content-Length": "1024",
                    "Content-Range": "bytes 4096-5119/12000", "Accept-Ranges": "bytes",
                })

        for container, codec in (("ogg", "vorbis"), ("ogg", "opus"), ("opus", "opus")):
            with self.subTest(container=container, codec=codec):
                client = object.__new__(PlexClient)
                client.base = "http://plex"
                client.session = Session()
                client._xml = lambda _path, container=container, codec=codec: ET.fromstring(
                    '<MediaContainer><Track ratingKey="10"><Media container="%s" audioCodec="%s">'
                    '<Part key="/library/parts/1/file.%s" container="%s" accessible="1" exists="1" />'
                    '</Media></Track></MediaContainer>' % (container, codec, container, container)
                )

                response = client.open_browser_audio("10", "bytes=4096-")

                _method, url, kwargs = client.session.calls[-1]
                self.assertEqual("http://plex/library/parts/1/file." + container, url)
                self.assertEqual("bytes=4096-", kwargs["headers"]["Range"])
                self.assertIsNone(kwargs.get("params"))
                response.close()

    def test_browser_audio_transcodes_unsupported_codec_in_ogg_container(self):
        from helper.clients import PlexClient

        class Session:
            def __init__(self):
                self.calls = []

            def request(self, method, url, **kwargs):
                self.calls.append((method, url, kwargs))
                return FakeAudioResponse(status=200, headers={"Content-Type": "audio/mpeg"})

        client = object.__new__(PlexClient)
        client.base = "http://plex"
        client.session = Session()
        client._xml = lambda _path: ET.fromstring(
            '<MediaContainer><Track ratingKey="10"><Media container="ogg" audioCodec="flac">'
            '<Part key="/library/parts/1/file.ogg" container="ogg" accessible="1" exists="1" />'
            '</Media></Track></MediaContainer>'
        )

        response = client.open_browser_audio("10", "bytes=4096-")

        self.assertEqual("http://plex/music/:/transcode/universal/start.mp3", client.session.calls[-1][1])
        response.close()

    def test_browser_audio_uses_the_selected_media_and_part_indexes(self):
        from helper.clients import PlexClient

        class Session:
            def __init__(self):
                self.calls = []

            def request(self, method, url, **kwargs):
                self.calls.append((method, url, kwargs))
                return FakeAudioResponse(status=200, headers={"Content-Type": "audio/mpeg"})

        client = object.__new__(PlexClient)
        client.base = "http://plex"
        client.session = Session()
        client._xml = lambda _path: ET.fromstring(
            '<MediaContainer><Track ratingKey="10">'
            '<Media container="wav"><Part key="/unsafe" accessible="0" /></Media>'
            '<Media container="wav" audioCodec="pcm">'
            '<Part key="/library/parts/missing.wav" exists="0" />'
            '<Part key="/library/parts/selected.wav" accessible="1" exists="1" />'
            '</Media></Track></MediaContainer>'
        )

        response = client.open_browser_audio("10")

        params = client.session.calls[-1][2]["params"]
        self.assertEqual("1", params["mediaIndex"])
        self.assertEqual("1", params["partIndex"])
        response.close()

        client.session.calls.clear()
        client._xml = lambda _path: ET.fromstring(
            '<MediaContainer><Track ratingKey="10"><Media container="mp3" audioCodec="mp3"><Part key="/library/parts/2/file.mp3" container="mp3" accessible="1" exists="1" /></Media></Track></MediaContainer>'
        )
        response = client.open_browser_audio("10", "bytes=0-1023")
        self.assertEqual("http://plex/library/parts/2/file.mp3", client.session.calls[-1][1])
        self.assertEqual("bytes=0-1023", client.session.calls[-1][2]["headers"]["Range"])
        response.close()

    def test_browser_audio_seek_uses_transcoder_offset_even_for_direct_play_codec(self):
        from helper.clients import PlexClient

        class Session:
            def __init__(self):
                self.calls = []

            def request(self, method, url, **kwargs):
                self.calls.append((method, url, kwargs))
                return FakeAudioResponse(status=200, headers={"Content-Type": "audio/mpeg"})

        client = object.__new__(PlexClient)
        client.base = "http://plex"
        client.session = Session()
        client._xml = lambda _path: ET.fromstring(
            '<MediaContainer><Track ratingKey="10"><Media container="flac" audioCodec="flac">'
            '<Part key="/library/parts/1/file.flac" container="flac" accessible="1" exists="1" />'
            '</Media></Track></MediaContainer>'
        )
        response = client.open_browser_audio("10", offset_seconds=75.5)
        method, url, kwargs = client.session.calls[-1]
        self.assertEqual("GET", method)
        self.assertEqual("http://plex/music/:/transcode/universal/start.mp3", url)
        self.assertEqual("75.5", kwargs["params"]["offset"])
        response.close()

    def test_import_page_does_not_expose_a_separate_audio_player(self):
        root = Path(__file__).resolve().parents[1] / "src/helper/static"
        page = (root / "external.html").read_text(encoding="utf-8")
        script = (root / "external.js").read_text(encoding="utf-8")
        self.assertNotIn('<audio id="auditionPlayer"', page)
        self.assertNotIn("/audio?", script)
        self.assertNotIn("/:/timeline", script)
        self.assertNotIn("behavior", script)


if __name__ == "__main__":
    unittest.main()
