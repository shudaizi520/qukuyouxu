import pathlib
import sys
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


class PageVersionV0330Tests(unittest.TestCase):
    def test_server_renders_current_version_into_first_html_frame(self):
        try:
            from helper.page_version import render_versioned_html
        except ModuleNotFoundError:
            render_versioned_html = None
        self.assertTrue(callable(render_versioned_html), "render_versioned_html must exist")
        if not callable(render_versioned_html):
            return
        old = '<header><small id="version">v0.3.25</small></header>'
        rendered = render_versioned_html(old, "0.3.30")
        self.assertEqual('<header><small id="version">v0.3.30</small></header>', rendered)
        self.assertNotIn("v0.3.25", rendered)

    def test_every_served_page_has_one_replaceable_version_slot(self):
        try:
            from helper.page_version import render_versioned_html
        except ModuleNotFoundError:
            render_versioned_html = None
        self.assertTrue(callable(render_versioned_html), "render_versioned_html must exist")
        if not callable(render_versioned_html):
            return
        static = ROOT / "src" / "helper" / "static"
        for name in ("daily.html", "home.html", "status.html", "settings.html", "mixes.html"):
            with self.subTest(page=name):
                rendered = render_versioned_html((static / name).read_text(encoding="utf-8"), "0.3.30")
                self.assertEqual(1, rendered.count('id="version">v0.3.30</small>'))


if __name__ == "__main__":
    unittest.main()
