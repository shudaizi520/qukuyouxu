import unittest
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


class ReleaseVersionTests(unittest.TestCase):
    def test_application_and_static_pages_use_release_1_4_6(self):
        from helper import __version__

        self.assertEqual("1.4.6", __version__)
        pages = []
        for name in ("playlists.html", "daily.html", "home.html", "mixes.html", "external.html", "settings.html", "status.html"):
            with self.subTest(name=name):
                html = (ROOT / "src/helper/static" / name).read_text(encoding="utf-8")
                pages.append(html)
                self.assertIn("?v=1.4.6", html)
                self.assertIn(">v1.4.6<", html)
        self.assertNotIn("1.4.5", "".join(pages))

    def test_release_notes_describe_external_playlist_workflow_and_limits(self):
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("## 1.4.2 - 2026-09-20", changelog)
        self.assertIn("## 1.4.3 - 2026-09-20", changelog)
        self.assertIn("## 1.4.4 - 2026-09-20", changelog)
        self.assertIn("## 1.4.5 - 2026-09-21", changelog)
        self.assertIn("## 1.4.6 - 2026-09-21", changelog)
        for phrase in ("Plex / Plexamp", "智能歌单", "曲库整理", "我的最爱", "爱心"):
            self.assertIn(phrase, changelog)
        for phrase in ("FLAC", "进度", "静音", "搜索全库添加", "网页播放器", "播放学习"):
            self.assertIn(phrase, changelog)
        self.assertIn("全库搜索", changelog)
        self.assertIn("自动重试", changelog)
        self.assertIn("20,000", changelog)
        self.assertIn("Plex Sonic", changelog)
        self.assertIn("可靠匹配", changelog)
        self.assertIn("不是直接写入", changelog)
        self.assertIn("分析曲库", readme)
        self.assertIn("自动任务", readme)
        self.assertIn("北京时间", readme)
        self.assertIn("外部歌单", readme)
        self.assertIn("不会下载外部音频", readme)
        self.assertIn("保留上一次成功结果", readme)

    def test_accepts_exact_v_prefixed_application_version(self):
        from tools.check_release_version import check_release_version

        self.assertEqual("1.4.6", check_release_version("v1.4.6", "1.4.6"))

    def test_rejects_mismatched_or_malformed_tags(self):
        from tools.check_release_version import check_release_version

        for tag in ("v0.9.9", "1.0.0", "v1.0", "latest"):
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                check_release_version(tag, "1.0.0")


if __name__ == "__main__":
    unittest.main()
