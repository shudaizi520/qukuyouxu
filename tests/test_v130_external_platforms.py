import copy
import json
import unittest
from pathlib import Path


FIXTURES = Path(__file__).parent / "fixtures" / "external"
QQ = {"provider": "qq", "external_id": "123", "url": "https://y.qq.com/n/ryqq/playlist/123"}
QQ_TOP = {"provider": "qq", "external_id": "26", "url": "https://y.qq.com/n/ryqq/toplist/26"}
NETEASE = {"provider": "netease", "external_id": "456", "url": "https://music.163.com/playlist?id=456"}


class FakeQQ:
    def __init__(self, playlist=None, toplist=None, error=None):
        self.playlist_result = playlist
        self.toplist_result = toplist
        self.error = error

    def playlist(self, playlist_id):
        if self.error:
            raise self.error
        return copy.deepcopy(self.playlist_result)

    def external_toplist(self, top_id):
        if self.error:
            raise self.error
        return copy.deepcopy(self.toplist_result)


class FakeHttp:
    def __init__(self, responses=None, error=None):
        self.responses = list(responses or [])
        self.error = error
        self.calls = []

    def get_json(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.error:
            raise self.error
        if not self.responses:
            raise AssertionError("unexpected HTTP call")
        return copy.deepcopy(self.responses.pop(0))


class ExternalPlatformsV130Tests(unittest.TestCase):
    def fixture(self, name):
        return json.loads((FIXTURES / name).read_text(encoding="utf-8"))

    def test_qq_adapter_preserves_all_artists_and_source_order(self):
        from helper.external_qq import QQPublicPlaylistSource

        result = QQPublicPlaylistSource(FakeQQ(playlist=self.fixture("qq_playlist.json"))).fetch(QQ)

        self.assertEqual("百万收藏", result["title"])
        self.assertEqual(["歌手甲", "歌手乙"], result["tracks"][0]["artists"])
        self.assertEqual([0, 1], [row["position"] for row in result["tracks"]])
        self.assertEqual(181000, result["tracks"][0]["duration_ms"])
        self.assertTrue(result["revision"])

    def test_qq_toplist_uses_the_narrow_public_toplist_boundary(self):
        from helper.external_qq import QQPublicPlaylistSource

        result = QQPublicPlaylistSource(FakeQQ(toplist=self.fixture("qq_toplist.json"))).fetch(QQ_TOP)

        self.assertEqual("热歌榜", result["title"])
        self.assertEqual(["歌手乙", "歌手丙"], result["tracks"][1]["artists"])
        self.assertEqual("https://y.qq.com/n/ryqq/toplist/26", result["url"])

    def test_qq_adapter_rejects_empty_duplicate_or_structurally_bad_results(self):
        from helper.external_qq import QQPublicPlaylistSource
        from helper.external_sources import ExternalSourceError

        good = self.fixture("qq_playlist.json")
        cases = [
            {**good, "tracks": []},
            {**good, "tracks": [good["tracks"][0], good["tracks"][0]]},
            {**good, "tracks": [{**good["tracks"][0], "artist": ""}]},
        ]
        for payload in cases:
            with self.subTest(payload=payload), self.assertRaises(ExternalSourceError):
                QQPublicPlaylistSource(FakeQQ(playlist=payload)).fetch(QQ)

    def test_netease_adapter_preserves_order_and_rejects_truncated_track_ids(self):
        from helper.external_netease import NetEasePublicPlaylistSource
        from helper.external_sources import ExternalSourceError

        payload = self.fixture("netease_playlist.json")
        result = NetEasePublicPlaylistSource(FakeHttp([payload])).fetch(NETEASE)
        self.assertEqual(["歌手甲", "歌手乙"], result["tracks"][0]["artists"])
        self.assertEqual(["1001", "1002"], [row["source_track_id"] for row in result["tracks"]])

        payload["playlist"]["trackIds"].append({"id": 999})
        payload["playlist"]["trackCount"] = 3
        with self.assertRaisesRegex(ExternalSourceError, "不完整"):
            NetEasePublicPlaylistSource(FakeHttp([payload, {"code": 200, "songs": []}])).fetch(NETEASE)

    def test_netease_adapter_expands_missing_details_in_bounded_batches(self):
        from helper.external_netease import NetEasePublicPlaylistSource

        payload = self.fixture("netease_playlist.json")
        missing = payload["playlist"]["tracks"].pop()
        http = FakeHttp([payload, {"code": 200, "songs": [missing]}])

        result = NetEasePublicPlaylistSource(http).fetch(NETEASE)

        self.assertEqual(2, len(result["tracks"]))
        self.assertEqual(2, len(http.calls))
        self.assertIn("1002", http.calls[1][0])

    def test_netease_adapter_rejects_private_error_duplicate_empty_and_changing_totals(self):
        from helper.external_netease import NetEasePublicPlaylistSource
        from helper.external_sources import ExternalSourceError

        good = self.fixture("netease_playlist.json")
        duplicate = copy.deepcopy(good)
        duplicate["playlist"]["trackIds"] = [{"id": 1001}, {"id": 1001}]
        empty = copy.deepcopy(good)
        empty["playlist"].update(trackCount=0, trackIds=[], tracks=[])
        changed = copy.deepcopy(good)
        changed["playlist"]["trackCount"] = 3
        for payload in ({"code": 404}, duplicate, empty, changed):
            with self.subTest(payload=payload), self.assertRaises(ExternalSourceError):
                NetEasePublicPlaylistSource(FakeHttp([payload])).fetch(NETEASE)

    def test_retryable_platform_failure_is_not_downgraded(self):
        from helper.external_netease import NetEasePublicPlaylistSource
        from helper.external_sources import ExternalSourceError

        upstream = ExternalSourceError("限流", retryable=True, kind="upstream")
        with self.assertRaises(ExternalSourceError) as caught:
            NetEasePublicPlaylistSource(FakeHttp(error=upstream)).fetch(NETEASE)
        self.assertTrue(caught.exception.retryable)
        self.assertEqual("upstream", caught.exception.kind)

    def test_provider_registry_routes_only_recognized_platforms(self):
        from helper.external_sources import ExternalProviderRegistry, ExternalSourceError

        qq = FakeQQ(playlist=self.fixture("qq_playlist.json"))
        netease = FakeHttp([self.fixture("netease_playlist.json")])
        registry = ExternalProviderRegistry(qq_source=qq, netease_source=netease)

        self.assertEqual("百万收藏", registry.fetch(QQ)["title"])
        self.assertEqual("网易热门", registry.fetch(NETEASE)["title"])
        with self.assertRaises(ExternalSourceError):
            registry.fetch({"provider": "file", "external_id": "x", "url": ""})

    def test_large_removal_requires_confirmation(self):
        from helper.external_sources import refresh_needs_confirmation

        self.assertTrue(refresh_needs_confirmation(old_count=50, new_count=39))
        self.assertFalse(refresh_needs_confirmation(old_count=50, new_count=41))
        self.assertTrue(refresh_needs_confirmation(old_count=500, new_count=479))
        self.assertTrue(refresh_needs_confirmation(old_count=2, new_count=0))


if __name__ == "__main__":
    unittest.main()
