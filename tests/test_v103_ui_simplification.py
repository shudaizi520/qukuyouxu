import unittest
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src" / "helper" / "static"


class _Markup(HTMLParser):
    VOID = {"meta", "link", "input", "br", "img"}

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
            "ancestors": tuple(self.stack),
        }
        self.nodes.append(node)
        if tag not in self.VOID:
            self.stack.append(node)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index]["tag"] == tag:
                del self.stack[index:]
                return

    def by_id(self, value):
        return next(node for node in self.nodes if node["id"] == value)


class UISimplificationV103Tests(unittest.TestCase):
    def parse(self, name):
        parser = _Markup()
        parser.feed((STATIC / name).read_text(encoding="utf-8"))
        return parser

    def test_daily_keeps_manual_actions_without_a_duplicate_schedule_switch(self):
        page = self.parse("daily.html")
        html = (STATIC / "daily.html").read_text(encoding="utf-8")

        playlist = page.by_id("dailyPlaylist")
        for control in ("generate", "fullRefresh", "publish"):
            self.assertIn(playlist, page.by_id(control)["ancestors"])
        self.assertNotIn('id="dailyToggle"', html)
        self.assertFalse(any("daily-overview" in node["classes"] for node in page.nodes))
        self.assertTrue(page.by_id("dailyMessage")["hidden"])

    def test_library_groups_run_controls_with_progress_and_removes_workflow_copy(self):
        page = self.parse("home.html")
        html = (STATIC / "home.html").read_text(encoding="utf-8")

        task = page.by_id("task")
        for control in ("analyzeLibrary", "pause", "progressArea"):
            self.assertIn(task, page.by_id(control)["ancestors"])
        managed = next(node for node in page.nodes if "managed-playlists-card" in node["classes"])
        self.assertIn(managed, page.by_id("incrementalAction")["ancestors"])
        self.assertNotIn('id="autoToggle"', html)
        self.assertNotIn("workflow-path", html)
        self.assertNotIn("<th>说明</th>", html)
        self.assertTrue(page.by_id("taskMessage")["hidden"])
        self.assertTrue(page.by_id("progressDetail")["hidden"])
        self.assertTrue(page.by_id("nextStep")["hidden"])

    def test_plex_account_page_has_one_connection_module(self):
        page = self.parse("settings.html")

        current = page.by_id("currentUser")
        self.assertIn(current, page.by_id("officialSection")["ancestors"])
        self.assertIn(current, page.by_id("connectPlex")["ancestors"])
        self.assertTrue(page.by_id("plexSavedSummary")["hidden"])
        self.assertTrue(page.by_id("plexConnectionTools")["hidden"])
        self.assertIn(page.by_id("people"), page.by_id("batchDailyTools")["ancestors"])

    def test_learning_controls_live_with_profile_rows_without_repeated_explanation_rows(self):
        page = self.parse("settings.html")
        html = (STATIC / "settings.html").read_text(encoding="utf-8")

        people = page.by_id("people")
        self.assertIn(people, page.by_id("webhookTools")["ancestors"])
        self.assertNotIn('id="behaviorUser"', html)
        self.assertNotIn('id="behaviorSave"', html)
        self.assertNotIn("儿歌播放", html)
        self.assertNotIn("webhook-guide", html)
        self.assertNotIn("设置状态", html)
        self.assertNotIn("还差 1 步", html)
        self.assertNotIn("请选择要学习的 Plex 用户", html)

    def test_automatic_tasks_use_the_single_global_endpoint(self):
        script = (STATIC / "settings.js").read_text(encoding="utf-8")

        self.assertIn("/api/automation", script)
        self.assertNotIn("/api/workflow/schedule", script)

    def test_library_review_shows_the_reason_when_every_playlist_is_blocked(self):
        script = (STATIC / "home.js").read_text(encoding="utf-8")

        self.assertIn("function blockedReviewReasons", script)
        self.assertIn("blockedReasons.length", script)
        self.assertIn("reviewNeedsMessage", script)


if __name__ == "__main__":
    unittest.main()
