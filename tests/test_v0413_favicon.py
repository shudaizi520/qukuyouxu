import pathlib
import sys
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from helper.page_version import render_versioned_html


class FaviconV0413Tests(unittest.TestCase):
    def test_every_served_page_gets_one_inline_music_favicon(self):
        static = ROOT / "src" / "helper" / "static"
        for name in (
            "daily.html",
            "home.html",
            "status.html",
            "mixes.html",
            "settings.html",
        ):
            with self.subTest(page=name):
                rendered = render_versioned_html(
                    (static / name).read_text(encoding="utf-8"), "0.4.13"
                )
                self.assertEqual(1, rendered.count('rel="icon"'))
                self.assertIn('type="image/svg+xml"', rendered)
                self.assertIn("data:image/svg+xml", rendered)
                self.assertIn("%230f817e", rendered)
                self.assertIn("%E2%99%AB", rendered)

    def test_rendering_an_existing_favicon_does_not_duplicate_it(self):
        source = (
            '<html><head><link rel="icon" type="image/png" href="/old.png"></head>'
            '<body><small id="version">old</small></body></html>'
        )

        rendered = render_versioned_html(source, "0.4.13")

        self.assertEqual(1, rendered.count('rel="icon"'))
        self.assertIn('href="/old.png"', rendered)

    def test_existing_icon_rel_accepts_legal_spacing_quotes_and_tokens(self):
        variants = (
            '<link rel = "icon" href="/old.png">',
            "<link rel='icon' href='/old.png'>",
            '<link rel=icon href=/old.png>',
            '<link rel="shortcut icon" href="/old.png">',
        )
        for link in variants:
            with self.subTest(link=link):
                source = (
                    f'<html><head>{link}</head>'
                    '<body><small id="version">old</small></body></html>'
                )
                rendered = render_versioned_html(source, "0.4.13")
                self.assertEqual(
                    source.replace(">old</small>", ">v0.4.13</small>"), rendered
                )

    def test_apple_touch_icon_does_not_replace_the_browser_favicon(self):
        source = (
            '<html><head><link rel="apple-touch-icon" href="/touch.png"></head>'
            '<body><small id="version">old</small></body></html>'
        )

        rendered = render_versioned_html(source, "0.4.13")

        self.assertIn('rel="apple-touch-icon"', rendered)
        self.assertEqual(1, rendered.count('rel="icon"'))


if __name__ == "__main__":
    unittest.main()
