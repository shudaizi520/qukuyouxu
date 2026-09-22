import re
import unittest
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src/helper/static"


class _Tree(HTMLParser):
    VOID = {"meta", "link", "input", "br", "img"}

    def __init__(self):
        super().__init__()
        self.stack = []
        self.nodes = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        node = {
            "sequence": len(self.nodes),
            "tag": tag,
            "classes": set(values.get("class", "").split()),
            "ancestors": tuple(self.stack),
        }
        self.nodes.append(node)
        if tag not in self.VOID:
            self.stack.append(node)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index]["tag"] == tag:
                del self.stack[index:]
                break


class SmartMixInteractionV0427Tests(unittest.TestCase):
    def page(self):
        parser = _Tree()
        parser.feed((STATIC / "mixes.html").read_text(encoding="utf-8"))
        return parser

    def test_each_builtin_mix_has_one_aligned_head_and_one_shared_drawer(self):
        page = self.page()
        rows = [node for node in page.nodes if "mix-row" in node["classes"]]
        self.assertEqual(3, len(rows))

        for row in rows:
            descendants = [node for node in page.nodes if row in node["ancestors"]]
            heads = [node for node in descendants if "mix-row-head" in node["classes"]]
            drawers = [node for node in descendants if "mix-drawer" in node["classes"]]
            self.assertEqual(1, len(heads))
            self.assertEqual(1, len(drawers))
            for required in ("mix-identity", "mix-state", "mix-actions"):
                node = next(item for item in descendants if required in item["classes"])
                self.assertIn(heads[0], node["ancestors"])
            for required in ("mix-adjust", "mix-preview-panel"):
                node = next(item for item in descendants if required in item["classes"])
                self.assertIn(drawers[0], node["ancestors"])

    def test_viewing_an_existing_preview_does_not_generate_another_preview(self):
        script = (STATIC / "mixes.js").read_text(encoding="utf-8")
        self.assertEqual(1, script.count("'/api/mixes/preview'"))
        self.assertIn("async function generatePreview", script)

        preview_handler = re.search(
            r"querySelector\('\.preview-mix'\)\.onclick=(.*?);box\.querySelector\('\.publish-mix'\)",
            script,
            re.S,
        )
        self.assertIsNotNone(preview_handler)
        self.assertNotIn("/api/mixes/preview", preview_handler.group(1))
        self.assertIn("togglePreview", preview_handler.group(1))

        regenerate_handler = re.search(
            r"querySelector\('\.regenerate-mix'\)\.onclick=(.*?);const closeAdjust",
            script,
            re.S,
        )
        self.assertIsNotNone(regenerate_handler)
        self.assertIn("generatePreview", regenerate_handler.group(1))

    def test_page_requests_the_new_script_and_styles_instead_of_cached_v0424_assets(self):
        page = (STATIC / "mixes.html").read_text(encoding="utf-8")
        self.assertIn('/static/product.css?v=1.4.21', page)
        self.assertIn('/static/mixes.js?v=1.4.21', page)


if __name__ == "__main__":
    unittest.main()
