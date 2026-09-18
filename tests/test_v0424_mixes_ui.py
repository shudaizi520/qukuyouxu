import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class SmartMixUiV0424Tests(unittest.TestCase):
    def test_page_has_one_master_schedule_three_builtin_rows_and_collapsed_custom(self):
        page = (ROOT / "src/helper/static/mixes.html").read_text(encoding="utf-8")
        script = (ROOT / "src/helper/static/mixes.js").read_text(encoding="utf-8")

        self.assertEqual(1, page.count('id="smartMixAuto"'))
        self.assertEqual(3, page.count('data-auto="weekly"'))
        self.assertEqual(3, page.count('class="mix-row"'))
        self.assertIn('<details class="custom-mix"', page)
        self.assertIn("/api/mixes/auto-schedule", script)
        self.assertNotIn('id="weeklyAuto"', page)
        self.assertIn('<small id="smartMixAutoText">已关闭</small>', page)
        self.assertNotIn('id="smartMixAutoText">先发布任意一个歌单', page)

    def test_builtins_keep_safe_preview_publish_and_remove_actions(self):
        page = (ROOT / "src/helper/static/mixes.html").read_text(encoding="utf-8")

        for action in ("preview-mix", "publish-mix", "remove-mix"):
            self.assertGreaterEqual(page.count(action), 4)
        self.assertNotIn("restore-mix", page)


if __name__ == "__main__":
    unittest.main()
