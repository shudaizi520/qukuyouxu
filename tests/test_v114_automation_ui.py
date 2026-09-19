import unittest
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src" / "helper" / "static"


class _AutomationMarkup(HTMLParser):
    def __init__(self):
        super().__init__()
        self.row_ids = []
        self.hour_count = 0

    def handle_starttag(self, _tag, attrs):
        values = dict(attrs)
        classes = set(values.get("class", "").split())
        if "automation-row" in classes:
            self.row_ids.append(values.get("id"))
        if "automation-hour" in classes:
            self.hour_count += 1


class AutomationUiV114Tests(unittest.TestCase):
    def test_automation_panel_has_exactly_three_compact_rows(self):
        html = (STATIC / "settings.html").read_text(encoding="utf-8")
        page = _AutomationMarkup()
        page.feed(html)

        self.assertEqual(
            ["dailyAutomation", "smartAutomation", "libraryAutomation"],
            page.row_ids,
        )
        self.assertEqual(3, page.hour_count)
        self.assertIn('id="smartIntervalDays"', html)
        self.assertNotIn('id="dailyAutoStatus"', html)
        self.assertNotIn('id="libraryAutoStatus"', html)

    def test_other_pages_have_no_duplicate_automatic_switches(self):
        settings = (STATIC / "settings.html").read_text(encoding="utf-8")
        daily = (STATIC / "daily.html").read_text(encoding="utf-8")
        mixes = (STATIC / "mixes.html").read_text(encoding="utf-8")
        home = (STATIC / "home.html").read_text(encoding="utf-8")

        self.assertNotIn('id="dailyToggle"', daily)
        self.assertNotIn('id="smartMixAuto"', mixes)
        self.assertNotIn('id="autoToggle"', home)
        self.assertNotIn('id="batchDailyAuto"', settings)

    def test_settings_saves_one_global_payload_and_old_controls_are_not_called(self):
        script = (STATIC / "settings.js").read_text(encoding="utf-8")

        self.assertIn("/api/automation", script)
        self.assertNotIn("/api/daily/schedule", script)
        self.assertNotIn("/api/workflow/schedule", script)
        self.assertNotIn("/api/profiles/daily/batch-schedule", script)


if __name__ == "__main__":
    unittest.main()
