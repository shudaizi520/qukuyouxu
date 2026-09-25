import unittest
from html.parser import HTMLParser
from pathlib import Path

from ui_css import page_css


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src" / "helper" / "static"


class Markup(HTMLParser):
    def __init__(self):
        super().__init__()
        self.elements = {}

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if values.get("id"):
            self.elements[values["id"]] = (tag, values)


class UIHierarchyV0414Tests(unittest.TestCase):
    def pages(self):
        mixes = (STATIC / "mixes.html").read_text(encoding="utf-8")
        settings = (STATIC / "settings.html").read_text(encoding="utf-8")
        return mixes, settings

    def test_batch_daily_is_removed_from_settings(self):
        mixes, settings = self.pages()
        self.assertNotIn('id="batchPreview"', mixes)
        self.assertNotIn('id="batchPublish"', mixes)
        self.assertNotIn('id="batchPreview"', settings)
        self.assertNotIn('id="batchPublish"', settings)

    def test_settings_uses_closed_disclosures_for_infrequent_work(self):
        _mixes, settings = self.pages()
        page = Markup()
        page.feed(settings)
        for element_id in (
            "profileManager",
            "plexConnectionTools",
            "passwordTools",
        ):
            with self.subTest(element_id=element_id):
                tag, attrs = page.elements[element_id]
                self.assertEqual("details", tag)
                self.assertNotIn("open", attrs)
        tag, _attrs = page.elements["webhookTools"]
        self.assertEqual("div", tag)
        self.assertIn("openPlexWebhooks", page.elements)
        for control_id in (
            "dailyAutomationHour",
            "smartAutomationHour",
            "libraryAutomationHour",
        ):
            self.assertIn(control_id, page.elements)
        _tag, webhook_message = page.elements["webhookMessage"]
        self.assertEqual("polite", webhook_message.get("aria-live"))

    def test_settings_explains_library_organizer_is_optional(self):
        _mixes, settings = self.pages()
        automation_start = settings.index('id="settings-automation"')
        automation_end = settings.index('id="settings-system"', automation_start)
        self.assertNotIn('id="settings-recommend"', settings)
        self.assertIn('id="dailyPolicyForm"', _mixes)
        self.assertIn("新增歌曲整理", settings[automation_start:automation_end])
        script = (STATIC / "settings.js").read_text(encoding="utf-8")
        self.assertIn("播放学习", script)
        self.assertNotIn('data-settings-target="learning"', settings)

    def test_smart_mix_cards_override_adjacent_card_margin_and_space_actions(self):
        css = page_css("mixes")
        self.assertIn(".mix-grid>.card{margin-top:0!important}", css)
        start = css.index(".mix-card .button-row{")
        rule = css[start:css.index("}", start)]
        self.assertIn("display:flex", rule)
        self.assertIn("gap:8px", rule)


if __name__ == "__main__":
    unittest.main()
