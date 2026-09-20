import inspect
import sys
import types
import fastapi
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
if "fastapi" not in sys.modules:
    fastapi = types.ModuleType("fastapi")
    fastapi.Request = object
    sys.modules["fastapi"] = fastapi


class Routes:
    def __init__(self):
        self.handlers = {}

    def get(self, path):
        return lambda handler: self.handlers.setdefault(("GET", path), handler) or handler

    def post(self, path):
        def register(handler):
            self.handlers[("POST", path)] = handler
            return handler
        return register


class HttpAndUiV047Tests(unittest.TestCase):
    def test_post_routes_use_request_injection(self):
        from helper.smart_mix_web import attach_smart_mix_routes

        routes = Routes()
        attach_smart_mix_routes(routes, object(), object(), object(), object(), lambda _r: {}, lambda: None)
        for path in ("/api/mixes/preview", "/api/mixes/publish", "/api/profiles/daily/batch-preview", "/api/profiles/daily/batch-publish"):
            parameter = inspect.signature(routes.handlers[("POST", path)]).parameters["request"]
            self.assertEqual("Request", parameter.annotation)

    def test_mixes_page_and_all_navigation_are_present(self):
        static = ROOT / "src/helper/static"
        page = (static / "mixes.html").read_text(encoding="utf-8")
        script = (static / "mixes.js").read_text(encoding="utf-8")
        self.assertIn('data-kind="weekly"', page)
        self.assertIn('data-kind="time_capsule"', page)
        self.assertIn('data-kind="recent_additions"', page)
        self.assertIn('data-kind="custom"', page)
        self.assertIn('name="year_min" type="number" min="1000" max="3000" placeholder="例如 1980"', page)
        self.assertIn('name="year_max" type="number" min="1000" max="3000" placeholder="例如 2026"', page)
        self.assertNotIn('name="year_min" type="number" min="1000" max="3000" value=', page)
        self.assertNotIn('name="year_max" type="number" min="1000" max="3000" value=', page)
        self.assertNotIn("/api/profiles/daily/batch-preview", script)
        settings_script = (static / "settings.js").read_text(encoding="utf-8")
        self.assertIn("/api/profiles/daily/batch-preview", settings_script)
        home = (static / "playlists.html").read_text(encoding="utf-8")
        self.assertIn('data-tool-url="/mixes"', home)
        for name in ("daily.html", "home.html", "status.html", "settings.html", "mixes.html"):
            self.assertIn('href="/">我的歌单</a>', (static / name).read_text(encoding="utf-8"), name)

    def test_behavior_ui_does_not_claim_learning_before_webhook_connection(self):
        settings = (ROOT / "src/helper/static/settings.html").read_text(encoding="utf-8")
        script = (ROOT / "src/helper/static/settings.js").read_text(encoding="utf-8")
        status_script = (ROOT / "src/helper/static/status.js").read_text(encoding="utf-8")
        styles = (ROOT / "src/helper/static/product.css").read_text(encoding="utf-8")
        self.assertIn('<code id="webhookUrl"', settings)
        self.assertIn('id="copyWebhook"', settings)
        self.assertIn('>复制地址</button>', settings)
        self.assertIn('href="https://app.plex.tv/desktop/#!/settings/webhooks"', settings)
        self.assertIn('>打开 Webhooks</a>', settings)
        self.assertNotIn('id="setupWebhook"', settings)
        self.assertIn('id="webhookHelpToggle"', settings)
        self.assertIn('id="webhookHelp"', settings)
        self.assertNotIn('id="refreshWebhook"', settings)
        self.assertIn("$('copyWebhook').onclick", script)
        self.assertIn("copied=document.execCommand('copy')", script)
        self.assertIn("if(!copied)throw Error", script)
        self.assertNotIn('window.open(PLEX_WEBHOOK_SETTINGS', script)
        self.assertIn('startWebhookPolling', script)
        self.assertIn("connected?'接收正常':lastReceived?'等待验证':'需要设置'", script)
        self.assertIn("verification_needed:'待验证'", status_script)
        self.assertIn("profile-learning-toggle", script)
        self.assertNotIn("not_connected:'学习中'", script)
        self.assertIn("a.primary.link{color:#fff", styles)


if __name__ == "__main__":
    unittest.main()
