import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
STATIC = ROOT / "src" / "helper" / "static"


class LibraryControlsV0330Tests(unittest.TestCase):
    def test_removed_playlists_are_in_a_closed_counted_disclosure(self):
        html = (STATIC / "home.html").read_text(encoding="utf-8")
        script = (STATIC / "theme_home.js").read_text(encoding="utf-8")
        self.assertIn('<details id="retiredDisclosure"', html)
        self.assertNotIn('<details id="retiredDisclosure" open', html)
        self.assertIn('id="retiredCount"', html)
        self.assertIn("disclosure.hidden=!retiredRows.length", script)
        self.assertIn("retiredRows.length+' 个'", script)

    def test_confirm_control_explains_an_empty_selection_instead_of_disabling(self):
        script = (STATIC / "home.js").read_text(encoding="utf-8")
        self.assertNotIn("||n===0", script)
        self.assertIn("if(!ids.length){note('请先勾选至少一个可同步歌单。',true);return;}", script)

    def test_auto_toggle_reverts_and_explains_unmet_prerequisites(self):
        script = (STATIC / "home.js").read_text(encoding="utf-8")
        self.assertIn("$('autoToggle').disabled=running||w.needs_setup;", script)
        self.assertIn("before=!!current.workflow.settings.enabled", script)
        self.assertIn("toggle.checked=before", script)
        self.assertIn("先完成一次歌单同步，再开启自动整理新歌。", script)


if __name__ == "__main__":
    unittest.main()
