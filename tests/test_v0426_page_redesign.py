import unittest
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src/helper/static"


class _PageStructure(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack = []
        self.nodes = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        node = {
            "tag": tag,
            "id": values.get("id", ""),
            "classes": set(values.get("class", "").split()),
            "hidden": "hidden" in values,
            "open": "open" in values,
            "ancestors": tuple(self.stack),
        }
        self.nodes.append(node)
        if tag not in {"meta", "link", "input", "br", "img"}:
            self.stack.append(node)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index]["tag"] == tag:
                del self.stack[index:]
                break

    def by_id(self, value):
        return next(node for node in self.nodes if node["id"] == value)


class PageRedesignV0426Tests(unittest.TestCase):
    def parse(self, name):
        parser = _PageStructure()
        parser.feed((STATIC / name).read_text(encoding="utf-8"))
        return parser

    def test_daily_removes_repeated_explanation_area(self):
        page = self.parse("daily.html")
        html = (STATIC / "daily.html").read_text(encoding="utf-8")

        self.assertNotIn('id="dailyDetails"', html)
        self.assertNotIn('id="behaviorText"', html)
        self.assertTrue(page.by_id("dailyMessage")["hidden"])
        self.assertIn("daily-main-card", page.by_id("dailyTitle")["ancestors"][-2]["classes"])
        self.assertFalse(any(node["tag"] == "footer" for node in page.nodes))

    def test_daily_first_use_shows_one_clear_generate_action(self):
        page = self.parse("daily.html")
        html = (STATIC / "daily.html").read_text(encoding="utf-8")
        script = (STATIC / "daily.js").read_text(encoding="utf-8")

        self.assertFalse(page.by_id("generate")["hidden"])
        self.assertTrue(page.by_id("fullRefresh")["hidden"])
        self.assertTrue(page.by_id("publish")["hidden"])
        self.assertIn('id="generate" class="primary wide">生成今日歌单</button>', html)
        self.assertIn('id="fullRefresh" class="secondary" hidden>换一批</button>', html)
        self.assertNotIn('id="recheckDaily"', html)
        self.assertNotIn('id="publishHint"', html)
        self.assertNotIn("reviewDailyOwnership", script)
        self.assertNotIn("发布检查", script)

    def test_status_leads_with_metrics_and_collapses_diagnostics_and_history(self):
        page = self.parse("status.html")

        diagnostics = page.by_id("statusDetails")
        history = page.by_id("eventHistory")
        self.assertEqual("details", diagnostics["tag"])
        self.assertEqual("details", history["tag"])
        self.assertFalse(diagnostics["open"])
        self.assertFalse(history["open"])
        for value in ("plexDetail", "qqDetail", "jobDetail", "dailyDiagnostics"):
            self.assertIn(diagnostics, page.by_id(value)["ancestors"])
        self.assertIn(history, page.by_id("events")["ancestors"])
        self.assertEqual(3, sum("health-card" in node["classes"] for node in page.nodes))
        main_nodes = [node for node in page.nodes if any(ancestor["tag"] == "main" for ancestor in node["ancestors"])]
        self.assertFalse(any("eyebrow" in node["classes"] for node in main_nodes))


if __name__ == "__main__":
    unittest.main()
