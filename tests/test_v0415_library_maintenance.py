import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src" / "helper" / "static"


class Markup(HTMLParser):
    def __init__(self):
        super().__init__()
        self.elements = {}

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if values.get("id"):
            self.elements[values["id"]] = (tag, values)


class _Engine:
    def __init__(self, store, calls):
        import threading

        self.store = store
        self.calls = calls
        self.stop = threading.Event()
        self.job = {"running": False}

    def daily_due(self, now=None):
        return False

    def auto(self):
        self.calls.append(("library", self.store.profile_id))
        return {"ok": True}


class LibraryMaintenanceV0415Tests(unittest.TestCase):
    def test_review_button_opens_a_library_panel_instead_of_advanced_diagnostics(self):
        page = Markup()
        page.feed((STATIC / "home.html").read_text(encoding="utf-8"))

        tag, attrs = page.elements["attentionLink"]
        self.assertEqual("button", tag)
        self.assertEqual("metadataReviewPanel", attrs.get("aria-controls"))
        self.assertEqual("false", attrs.get("aria-expanded"))
        self.assertEqual("section", page.elements["metadataReviewPanel"][0])
        self.assertIn("hidden", page.elements["metadataReviewPanel"][1])

    def test_library_review_uses_only_the_real_metadata_review_endpoint(self):
        script = (STATIC / "home.js").read_text(encoding="utf-8")

        self.assertIn("/api/metadata?review_only=true", script)
        self.assertNotIn("/advanced#singleResultsBox", script)
        self.assertIn("openMetadataReview()", script)

    def test_advanced_diagnostics_is_not_a_user_facing_page(self):
        for name in ("daily.html", "mixes.html", "home.html", "status.html", "settings.html"):
            page = (STATIC / name).read_text(encoding="utf-8")
            self.assertNotIn('href="/advanced', page, name)

        from fastapi import FastAPI
        from helper.web_surface import STATIC_ASSETS, attach_web_surface

        app = FastAPI()
        attach_web_surface(app, STATIC, "test")
        routes = {route.path: route for route in app.routes}
        response = routes["/advanced"].endpoint()
        self.assertEqual(307, response.status_code)
        self.assertEqual("/status", response.headers["location"])
        self.assertNotIn("advanced.js", STATIC_ASSETS)

    def test_review_only_metadata_excludes_confirmed_and_advisory_rows(self):
        from helper.metadata import metadata_audit_rows

        tracks = [
            {
                "id": "1",
                "title": "Wrong title",
                "artist": "Singer",
                "album": "",
                "duration": 200,
                "available": True,
                "guid": "",
                "paths": ["/music/Singer - Right title.mp3"],
            },
            {
                "id": "2",
                "title": "Known song",
                "artist": "Singer",
                "album": "",
                "duration": 180,
                "available": True,
                "guid": "",
                "paths": ["/music/Singer - Known song.mp3"],
            },
        ]
        corrections = {
            "2": {
                "title": "Known song",
                "artist": "Singer",
                "album": "",
                "note": "checked",
                "fingerprint": __import__("helper.metadata", fromlist=["identity_fingerprint"]).identity_fingerprint(tracks[1]),
            }
        }

        rows = metadata_audit_rows(tracks, corrections, review_only=True)

        self.assertEqual([], rows)

    def test_complete_plex_identity_is_not_blocked_only_because_filename_spelling_differs(self):
        from helper.metadata import metadata_audit_rows, prepare_catalog

        track = {
            "id": "1",
            "title": "溯 (Reverse)",
            "artist": "CORSAK胡梦周/马吟吟",
            "album": "",
            "duration": 240,
            "available": True,
            "guid": "local://1",
            "paths": ["/music/CORSAK胡梦周 _ 马吟吟 - 溯 (Reverse)feat_ 马吟吟.flac"],
        }

        prepared, audit = prepare_catalog([track], {})

        self.assertFalse(prepared[0]["_metadata_blocked"])
        self.assertEqual("filename_difference", prepared[0]["_metadata_status"])
        self.assertEqual("filename_difference", audit[0]["status"])
        self.assertEqual([], metadata_audit_rows([track], {}, review_only=True))

    def test_missing_plex_identity_remains_blocked_even_with_a_filename_hint(self):
        from helper.metadata import prepare_catalog

        track = {
            "id": "1", "title": "妈妈的话", "artist": "/", "album": "",
            "duration": 220, "available": True, "guid": "local://1",
            "paths": ["/music/弹棉花的小花 - 妈妈的话.flac"],
        }

        prepared, audit = prepare_catalog([track], {})

        self.assertTrue(prepared[0]["_metadata_blocked"])
        self.assertEqual("incomplete", audit[0]["status"])

    def test_next_library_run_is_beijing_midnight(self):
        from helper.profile_runtime import next_beijing_midnight

        self.assertEqual(1789660800, next_beijing_midnight(1789624800))
        self.assertEqual(1789747200, next_beijing_midnight(1789660800))

    def test_library_automation_waits_for_its_next_midnight_and_runs_once(self):
        from helper.automation import PROFILE_STATE_KEY, save_automation_settings
        from helper.profile_runtime import ProfileRuntime
        from helper.profiles import ProfileRegistry
        from helper.scoped_store import ScopedStore
        from helper.store import Store

        with tempfile.TemporaryDirectory() as root:
            base = Store(Path(root))
            registry = ProfileRegistry(base)
            owner = ScopedStore(base, "default")
            settings = owner.get("settings")
            settings.update(plex_token="owner-secret")
            owner.set_many({"settings": settings, "managed": {"theme": {"id": "playlist-1"}}})
            calls = []
            runtime = ProfileRuntime(base, registry, engine_factory=lambda store: _Engine(store, calls))
            saved = save_automation_settings(base, {
                "daily": {"enabled": False, "hour": 6},
                "smart": {"enabled": False, "interval_days": 7, "hour": 3},
                "library": {"enabled": True, "hour": 0},
            })
            owner.set(PROFILE_STATE_KEY, {"revision": saved["revision"], "tasks": {
                "library": {"config": saved["library"], "next_at": 1000, "slot": 1000},
            }})

            self.assertEqual([], runtime.run_due(now=999))
            first = runtime.run_due(now=1000)
            self.assertEqual([("library", "default")], calls)
            self.assertEqual("library", first[0]["kind"])
            self.assertEqual([], runtime.run_due(now=1001))
            self.assertGreater(owner.get(PROFILE_STATE_KEY)["tasks"]["library"]["next_at"], 1001)


if __name__ == "__main__":
    unittest.main()
