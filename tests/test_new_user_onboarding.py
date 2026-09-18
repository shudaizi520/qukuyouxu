import sys
import tempfile
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from helper.store import Store
from helper.theme import DEFAULT_THEME, TOPICS
from helper.workflow_v0317 import build_workflow_status, save_theme_settings


class _FreshEngine:
    job = {}

    def single_status(self):
        return {"state": {}}

    def theme_status(self):
        return {
            "settings": dict(DEFAULT_THEME),
            "topics": list(TOPICS),
            "unavailable": [],
            "skipped_references": [],
        }


class _FreshPlex:
    def __init__(self):
        self.created = {}

    def identity(self):
        return {"machine": "machine-a", "server": "Plex"}

    def tracks(self, _section):
        return [
            {
                "id": str(index),
                "title": f"歌曲 {index}",
                "artist": "歌手",
                "album": "专辑",
                "duration": 180,
                "available": True,
                "guid": f"guid-{index}",
                "paths": [],
            }
            for index in range(1, 6)
        ]

    def playlists(self):
        return list(self.created.values())

    def create(self, title, track_ids, summary):
        playlist_id = f"playlist-{len(self.created) + 1}"
        state = {
            "id": playlist_id,
            "title": title,
            "summary": summary,
            "items": [{"id": str(track_id), "item_id": f"item-{track_id}"} for track_id in track_ids],
        }
        self.created[playlist_id] = state
        return state

    def playlist_state(self, playlist_id):
        return self.created[str(playlist_id)]


class _FreshQQ:
    def prepare_run(self, *_args, **_kwargs):
        return None

    def tags(self):
        return [
            {"id": "100", "name": "网络歌曲"},
            {"id": "200", "name": "睡前"},
        ]

    def fetch(self, source):
        return {
            "title": source["name"],
            "tracks": [
                {"id": f"qq-{index}", "title": f"歌曲 {index}", "artist": "歌手"}
                for index in range(1, 6)
            ],
            "origins": [],
        }


class FreshUserThemeOnboardingTests(unittest.TestCase):
    def test_first_admin_can_open_a_usable_empty_library_workflow(self):
        from helper.web import create_app

        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root))
            app = create_app(store=store, start_scheduler=False)
            route = next(route for route in app.routes if getattr(route, "path", "") == "/api/workflow/status")

            workflow = route.endpoint()["workflow"]
            self.assertTrue(workflow["needs_setup"])
            self.assertEqual([], store.get("sources"))
            self.assertEqual(12, len(workflow["theme"]["topics"]))
            self.assertEqual(12, len(workflow["theme"]["settings"]["selected"]))

    def test_fresh_user_sees_builtin_topics_before_any_source_exists(self):
        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root))

            status = build_workflow_status(
                store,
                _FreshEngine(),
                {"logged_in": True, "phase": "ready"},
            )

            theme = status["workflow"]["theme"]
            self.assertEqual([], store.get("sources"))
            self.assertEqual(
                [row["key"] for row in TOPICS],
                [row["key"] for row in theme["topics"]],
            )
            self.assertEqual(DEFAULT_THEME["selected"], theme["settings"]["selected"])

    def test_fresh_user_can_save_builtin_topics_before_sources_are_provisioned(self):
        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root))

            result = save_theme_settings(store, ["internet", "sleep"], True)

            self.assertEqual("主题选择已保存；下次整理时生效。", result["message"])
            self.assertEqual([], store.get("sources"))
            self.assertEqual(
                {"internet", "sleep"},
                set(store.get("theme_settings")["selected"]),
            )
            self.assertTrue(store.get("theme_settings")["enabled"])

    def test_first_preview_provisions_selected_topics_without_old_cache(self):
        from helper.library_engine import LibraryEngine

        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root))
            settings = store.get("settings")
            settings.update(
                plex_url="http://plex:32400",
                plex_token="fresh-token",
                section="11",
            )
            store.set("settings", settings)
            save_theme_settings(store, ["internet", "sleep"], True)
            engine = LibraryEngine(
                store,
                plex_factory=lambda _settings: _FreshPlex(),
                qq=_FreshQQ(),
            )

            plan = engine.preview(force_sources=True)

            self.assertEqual(2, len(plan["groups"]))
            self.assertEqual(
                {"网络热歌", "睡前舒缓"},
                {group["title"] for group in plan["groups"]},
            )
            self.assertEqual(5, plan["covered"])

    def test_first_confirmation_creates_the_selected_playlists_without_old_cache(self):
        from helper.library_engine import LibraryEngine

        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root))
            settings = store.get("settings")
            settings.update(
                plex_url="http://plex:32400",
                plex_token="fresh-token",
                section="11",
            )
            store.set("settings", settings)
            save_theme_settings(store, ["internet", "sleep"], True)
            plex = _FreshPlex()
            engine = LibraryEngine(store, plex_factory=lambda _settings: plex, qq=_FreshQQ())
            plan = engine.preview(force_sources=True)

            result = engine.apply(plan["id"])

            self.assertEqual(2, result["written"])
            self.assertEqual(0, result["skipped"])
            self.assertEqual({"网络热歌", "睡前舒缓"}, {row["title"] for row in plex.created.values()})
            self.assertEqual(2, len(store.get("managed")))


if __name__ == "__main__":
    unittest.main()
