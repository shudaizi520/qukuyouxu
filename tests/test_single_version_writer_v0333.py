import pathlib
import sys
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


class SingleVersionWriterV0333Tests(unittest.TestCase):
    def test_library_html_contains_the_current_release_before_javascript_runs(self):
        from helper.page_version import render_library_html

        source = '<header><small id="version">v0.3.25</small></header>'
        rendered = render_library_html(source, "0.3.33")

        self.assertEqual(
            '<header><small id="version">v0.3.33</small></header>', rendered
        )

    def test_library_javascript_cannot_overwrite_the_server_version(self):
        script = (ROOT / "src/helper/static/home.js").read_text(encoding="utf-8")

        self.assertNotIn("$('version').textContent", script)


if __name__ == "__main__":
    unittest.main()
