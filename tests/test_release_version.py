import unittest
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


class ReleaseVersionTests(unittest.TestCase):
    def test_application_and_static_pages_use_release_1_4_2(self):
        from helper import __version__

        self.assertEqual("1.4.2", __version__)
        for name in ("playlists.html", "daily.html", "home.html", "mixes.html", "external.html", "settings.html", "status.html"):
            with self.subTest(name=name):
                html = (ROOT / "src/helper/static" / name).read_text(encoding="utf-8")
                self.assertIn("?v=1.4.2", html)
                self.assertIn(">v1.4.2<", html)

    def test_release_notes_describe_external_playlist_workflow_and_limits(self):
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("## 1.4.2 - 2026-09-20", changelog)
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

        self.assertEqual("1.4.2", check_release_version("v1.4.2", "1.4.2"))

    def test_rejects_mismatched_or_malformed_tags(self):
        from tools.check_release_version import check_release_version

        for tag in ("v0.9.9", "1.0.0", "v1.0", "latest"):
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                check_release_version(tag, "1.0.0")


if __name__ == "__main__":
    unittest.main()
