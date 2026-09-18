import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DailyUiV0424Tests(unittest.TestCase):
    def test_daily_page_offers_explicit_full_refresh_separate_from_rolling_update(self):
        page = (ROOT / "src/helper/static/daily.html").read_text(encoding="utf-8")
        script = (ROOT / "src/helper/static/daily.js").read_text(encoding="utf-8")

        self.assertIn('id="fullRefresh"', page)
        self.assertIn(">换一批</button>", page)
        self.assertIn("force_full:true", script)
        self.assertIn("保留没听过的歌", page)


if __name__ == "__main__":
    unittest.main()
