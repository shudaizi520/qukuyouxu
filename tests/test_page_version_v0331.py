import pathlib
import sys
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


class PageVersionV0331Tests(unittest.TestCase):
    def test_library_first_frame_does_not_expose_a_stale_version(self):
        try:
            from helper.page_version import render_library_html
        except ImportError:
            render_library_html = None
        self.assertTrue(callable(render_library_html), "library-specific renderer must exist")
        if not callable(render_library_html):
            return
        source = '<header><small id="version">v0.3.25</small></header>'
        html = render_library_html(source, "0.3.31")
        self.assertIn('<small id="version">v0.3.31</small>', html)
        self.assertNotIn('<small id="version">v0.3.25</small>', html)

    def test_served_pages_version_static_assets_with_the_live_release(self):
        from helper.page_version import render_versioned_html

        source = (
            '<link rel="stylesheet" href="/static/product.css?v=0.3.20">'
            '<script defer src="/static/home.js?v=0.3.20"></script>'
            '<small id="version">v0.3.25</small>'
        )
        rendered = render_versioned_html(source, "0.3.31")

        self.assertIn('/static/product.css?v=0.3.31', rendered)
        self.assertIn('/static/home.js?v=0.3.31', rendered)
        self.assertNotIn('?v=0.3.20', rendered)


if __name__ == "__main__":
    unittest.main()
