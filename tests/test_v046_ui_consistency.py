import unittest
from pathlib import Path

from helper import profile_runtime


ROOT = Path(__file__).resolve().parents[1]


class _WorkflowQQ:
    def public_status(self):
        return {
            "logged_in": True,
            "phase": "connected",
            "message": "QQ 音乐已授权",
        }


class _ObsoleteQQ:
    def status(self):
        return {
            "logged_in": False,
            "phase": "unavailable",
            "message": "",
        }


class _Value:
    pass


class UIConsistencyV046Tests(unittest.TestCase):
    def test_status_uses_the_same_qq_authorization_as_library_workflow(self):
        app = _Value()
        app.state = _Value()
        app.state.qq_auth = _WorkflowQQ()
        engine = _Value()
        engine.qq_auth = _ObsoleteQQ()

        resolver = getattr(
            profile_runtime,
            "current_qq_status",
            lambda _app, legacy: legacy.qq_auth.status(),
        )

        self.assertEqual(
            {
                "logged_in": True,
                "phase": "connected",
                "message": "QQ 音乐已授权",
            },
            resolver(app, engine),
        )

    def test_daily_overview_uses_saved_size_instead_of_fixed_count(self):
        script = (ROOT / "src/helper/static/daily.js").read_text(encoding="utf-8")
        page = (ROOT / "src/helper/static/daily.html").read_text(encoding="utf-8")

        self.assertIn("$('targetCount').textContent=n(cfg.size??50)", script)
        self.assertIn("n(plan.items?.length||cfg.size||50)+' 首歌曲已准备好", script)
        self.assertNotIn("$('targetCount').textContent='30'", script)
        self.assertIn('<strong id="targetCount">—</strong>', page)


if __name__ == "__main__":
    unittest.main()
