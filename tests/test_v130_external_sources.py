import json
import socket
import tempfile
import unittest
from pathlib import Path


FIXTURES = Path(__file__).parent / "fixtures" / "external"


class FakeResponse:
    def __init__(self, status=200, body=b"{}", headers=None, chunks=None):
        self.status_code = status
        self.headers = dict(headers or {})
        self._body = body
        self._chunks = chunks
        self.closed = False

    def iter_content(self, chunk_size=65536):
        if self._chunks is not None:
            yield from self._chunks
            return
        for offset in range(0, len(self._body), chunk_size):
            yield self._body[offset:offset + chunk_size]

    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.trust_env = True

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if not self.responses:
            raise AssertionError("unexpected request")
        return self.responses.pop(0)


def public_resolver(host, port, *args, **kwargs):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]


class ExternalSourcesV130Tests(unittest.TestCase):
    def test_platform_track_key_is_stable_when_playlist_position_changes(self):
        from helper.external_sources import make_track

        first = make_track(0, "同一首歌", ["歌手"], source_id="12345")
        moved = make_track(37, "同一首歌", ["歌手"], source_id="12345")

        self.assertEqual(first["source_track_key"], moved["source_track_key"])

    def fixture(self, name):
        return (FIXTURES / name).read_bytes()

    def test_qq_and_netease_links_are_canonicalized(self):
        from helper.external_sources import recognize_source

        qq = recognize_source("https://y.qq.com/n/ryqq/playlist/123?ADTAG=x")
        qq_v2 = recognize_source("https://y.qq.com/n/ryqq_v2/playlist/987?ADTAG=h5_share_playlist")
        qq_share_target = recognize_source(
            "https://i.y.qq.com/n2/m/share/details/taoge.html?ADTAG=pc_v17&id=7817632948"
        )
        netease = recognize_source("https://music.163.com/#/playlist?id=456")

        self.assertEqual({"provider": "qq", "external_id": "123", "url": "https://y.qq.com/n/ryqq/playlist/123"}, qq)
        self.assertEqual({"provider": "qq", "external_id": "987", "url": "https://y.qq.com/n/ryqq/playlist/987"}, qq_v2)
        self.assertEqual(
            {"provider": "qq", "external_id": "7817632948", "url": "https://y.qq.com/n/ryqq/playlist/7817632948"},
            qq_share_target,
        )
        self.assertEqual({"provider": "netease", "external_id": "456", "url": "https://music.163.com/playlist?id=456"}, netease)

    def test_qq_share_short_link_is_resolved_only_through_official_public_hosts(self):
        from helper.external_sources import SafeSourceHttp, resolve_source_reference

        session = FakeSession([
            FakeResponse(302, headers={
                "Location": "https://i.y.qq.com/n2/m/share/details/taoge.html?id=7817632948"
            }),
            FakeResponse(302, headers={
                "Location": "https://i2.y.qq.com/n3/other/pages/details/playlist.html?id=7817632948"
            }),
            FakeResponse(302, headers={
                "Location": "https://y.qq.com/n/ryqq_v2/playlist/7817632948?ADTAG=h5_share_playlist"
            }),
            FakeResponse(200, body=b"share page"),
        ])
        http = SafeSourceHttp(session=session, resolver=public_resolver)

        result = resolve_source_reference(
            "https://c6.y.qq.com/base/fcgi-bin/u?__=IdNgNPHZ9fiW", http
        )

        self.assertEqual(
            {"provider": "qq", "external_id": "7817632948", "url": "https://y.qq.com/n/ryqq/playlist/7817632948"},
            result,
        )
        self.assertEqual(4, len(session.calls))
        self.assertTrue(all(call[1]["allow_redirects"] is False for call in session.calls))

        unsafe = SafeSourceHttp(
            session=FakeSession([FakeResponse(302, headers={"Location": "https://evil.example/playlist/1"})]),
            resolver=public_resolver,
        )
        with self.assertRaisesRegex(ValueError, "支持"):
            resolve_source_reference(
                "https://c6.y.qq.com/base/fcgi-bin/u?__=IdNgNPHZ9fiW", unsafe
            )

    def test_private_credentialed_and_unknown_urls_are_rejected(self):
        from helper.external_sources import recognize_source

        for value in (
            "http://127.0.0.1/x", "https://user:pass@y.qq.com/n/ryqq/playlist/1",
            "file:///etc/passwd", "https://evil.example/playlist/1",
            "https://music.163.com/artist?id=1",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                recognize_source(value)

    def test_m3u_csv_and_text_keep_order_and_version_text(self):
        from helper.external_sources import parse_uploaded_playlist

        m3u = parse_uploaded_playlist("sample.m3u8", self.fixture("sample.m3u8"))
        csv_result = parse_uploaded_playlist("sample.csv", self.fixture("sample.csv"))
        text = parse_uploaded_playlist("list.txt", "歌名 - 歌手\n歌曲甲 - 歌手甲\n歌曲乙 (伴奏) - 歌手乙\n".encode())

        self.assertEqual([0, 1], [row["position"] for row in m3u["tracks"]])
        self.assertEqual("现场版", m3u["tracks"][1]["version_label"])
        self.assertEqual("", m3u["tracks"][0]["source_url"])
        self.assertEqual("歌手甲", csv_result["tracks"][0]["artists"][0])
        self.assertEqual(180_000, csv_result["tracks"][0]["duration_ms"])
        self.assertEqual("伴奏", text["tracks"][1]["version_label"])

    def test_text_without_an_unambiguous_header_is_rejected(self):
        from helper.external_sources import ExternalSourceError, parse_uploaded_playlist

        with self.assertRaisesRegex(ExternalSourceError, "第一行"):
            parse_uploaded_playlist("list.txt", "歌手甲 - 歌曲甲\n".encode())

    def test_file_limits_encoding_row_limits_and_formula_cells_are_rejected(self):
        from helper.external_sources import ExternalSourceError, parse_uploaded_playlist

        cases = (
            ("huge.txt", b"x" * (2 * 1024 * 1024 + 1), "过大"),
            ("bad.txt", b"\xff\xfe\xfa", "UTF-8"),
            ("formula.csv", "歌名,歌手\n=CMD(),歌手甲\n".encode(), "公式"),
            ("many.txt", ("歌名 - 歌手\n" + "\n".join(f"歌{i} - 人{i}" for i in range(10001))).encode(), "10000"),
        )
        for filename, content, message in cases:
            with self.subTest(filename=filename), self.assertRaisesRegex(ExternalSourceError, message):
                parse_uploaded_playlist(filename, content)

    def test_safe_http_rejects_private_dns_and_credentials_before_request(self):
        from helper.external_sources import ExternalSourceError, SafeSourceHttp

        private = lambda host, port, *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.50.99", port))
        ]
        session = FakeSession([])
        http = SafeSourceHttp(session=session, resolver=private)

        with self.assertRaisesRegex(ExternalSourceError, "公网"):
            http.get_json("https://music.163.com/api/playlist/detail?id=1", allowed_hosts={"music.163.com"})
        with self.assertRaises(ExternalSourceError):
            SafeSourceHttp(session=session, resolver=public_resolver).get_json(
                "https://u:p@music.163.com/x", allowed_hosts={"music.163.com"}
            )
        self.assertEqual([], session.calls)

    def test_safe_http_validates_every_redirect_and_disables_automatic_redirects(self):
        from helper.external_sources import ExternalSourceError, SafeSourceHttp

        session = FakeSession([
            FakeResponse(302, headers={"Location": "https://api.music.163.com/next"}),
            FakeResponse(302, headers={"Location": "https://127.0.0.1/private"}),
        ])
        http = SafeSourceHttp(session=session, resolver=public_resolver)
        with self.assertRaises(ExternalSourceError):
            http.get_json(
                "https://music.163.com/start",
                allowed_hosts={"music.163.com", "api.music.163.com"},
            )
        self.assertTrue(all(call[1]["allow_redirects"] is False for call in session.calls))

    def test_safe_http_limits_redirects_and_body_size_and_decodes_json(self):
        from helper.external_sources import ExternalSourceError, SafeSourceHttp

        redirects = [FakeResponse(302, headers={"Location": f"https://music.163.com/{i}"}) for i in range(6)]
        with self.assertRaisesRegex(ExternalSourceError, "重定向"):
            SafeSourceHttp(session=FakeSession(redirects), resolver=public_resolver).get_json(
                "https://music.163.com/start", allowed_hosts={"music.163.com"}
            )

        oversized = FakeResponse(200, headers={"Content-Length": "4194305"})
        with self.assertRaisesRegex(ExternalSourceError, "过大"):
            SafeSourceHttp(session=FakeSession([oversized]), resolver=public_resolver).get_json(
                "https://music.163.com/x", allowed_hosts={"music.163.com"}
            )

        streamed = FakeResponse(200, chunks=[b"{\"ok\":", b"true}"])
        result = SafeSourceHttp(session=FakeSession([streamed]), resolver=public_resolver).get_json(
            "https://music.163.com/x", allowed_hosts={"music.163.com"}, max_bytes=32
        )
        self.assertEqual({"ok": True}, result)


if __name__ == "__main__":
    unittest.main()
