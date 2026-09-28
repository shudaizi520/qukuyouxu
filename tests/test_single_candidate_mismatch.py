import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def detail_response(requested_mid, returned_mid):
    return {
        "code": 0,
        "req": {
            "code": 0,
            "data": {
                "track_info": {
                    "mid": returned_mid,
                    "title": "测试歌曲",
                    "singer": [{"name": "张三"}],
                    "album": {"name": "测试专辑"},
                    "interval": 200,
                },
                "info": {
                    "lan": {"content": [{"value": "国语"}]},
                    "genre": {"content": [{"value": "流行"}]},
                },
            },
        },
    }


class SingleCandidateMismatchTests(unittest.TestCase):
    def test_mismatched_qq_mid_skips_only_that_candidate_and_uses_the_next_one(self):
        """A stale candidate must not stop the whole library scan or enter the cache."""
        from helper.library_engine import LibraryEngine
        from helper.single import parse_detail
        from helper.store import Store

        stale_mid = "00000000000001"
        replacement_mid = "00000000000003"

        class QQClient:
            def search(self, _query):
                return [
                    {"mid": stale_mid, "title": "测试歌曲", "artist": "张三"},
                    {"mid": replacement_mid, "title": "测试歌曲", "artist": "张三"},
                ]

            def detail(self, mid):
                returned_mid = "00000000000002" if mid == stale_mid else mid
                return parse_detail(detail_response(mid, returned_mid), mid)

        track = {
            "id": "1", "title": "测试歌曲", "artist": "张三",
            "album": "测试专辑", "duration": 200, "available": True,
            "guid": "local://1", "paths": [],
        }
        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root))
            result = LibraryEngine(store)._single_lookup(track, [], QQClient())

            self.assertEqual("matched", result["status"])
            self.assertEqual(replacement_mid, result["detail"]["mid"])
            self.assertIsNone(store.get("single_detail:" + stale_mid))


if __name__ == "__main__":
    unittest.main()
