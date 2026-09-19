import asyncio
import copy
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch


NOW = 1_800_000_010
PUBLISHED = {
    "plan_id": "plan-1",
    "playlist_id": "playlist-1",
    "title": "每日推荐",
    "date": "2027-01-15",
    "published_at": NOW,
    "count": 2,
    "items": [{"id": "1", "title": "歌曲 1", "artist": "歌手"}],
}
ROOT = Path(__file__).resolve().parents[1]


class _Routes:
    def __init__(self):
        self.handlers = {}

    def get(self, path):
        return lambda fn: self.handlers.setdefault(("GET", path), fn) or fn

    def post(self, path):
        return lambda fn: self.handlers.setdefault(("POST", path), fn) or fn


class _Page(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = set()
        self.classes = set()

    def handle_starttag(self, _tag, attrs):
        values = dict(attrs)
        if values.get("id"):
            self.ids.add(values["id"])
        self.classes.update(values.get("class", "").split())


class DailyPublishedViewV108Tests(unittest.TestCase):
    def make_engine(self):
        from helper.library_engine import LibraryEngine
        from helper.store import Store
        from tests.test_daily_fixed_playlist import _DailyPlex

        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        store = Store(Path(self.temp.name))
        settings = dict(store.get("settings"))
        settings.update(plex_url="http://plex", plex_token="owner-token", section="11")
        store.set("settings", settings)
        plex = _DailyPlex(existing=False)
        original_create = plex.create

        def create(*args, **kwargs):
            result = original_create(*args, **kwargs)
            plex.playlists_by_id["playlist-1"] = plex.playlists_by_id.pop(result["id"])
            plex.playlists_by_id["playlist-1"]["id"] = "playlist-1"
            return copy.deepcopy(plex.playlists_by_id["playlist-1"])

        plex.create = create
        return store, LibraryEngine(store, plex_factory=lambda _settings: plex)

    def test_successful_publish_saves_a_durable_public_view(self):
        from tests.test_daily_fixed_playlist import _recommendation

        store, engine = self.make_engine()
        with patch("helper.daily.recommend_rotating", side_effect=_recommendation):
            plan = engine.preview_daily(now=NOW - 10)

        result = engine._publish_daily(plan["id"], NOW)
        view = store.get("daily_published_view")

        self.assertEqual("playlist-1", view["playlist_id"])
        self.assertEqual(["1", "2"], [row["id"] for row in view["items"]])
        self.assertNotIn("signature", view)
        self.assertEqual(2, result["written"])

    def test_invalidating_draft_through_policy_route_does_not_remove_published_view(self):
        from helper.daily_mix_v035 import attach_policy_routes

        store, engine = self.make_engine()
        store.set_many({"daily_plan": {"id": "draft"}, "daily_published_view": PUBLISHED})
        routes = _Routes()
        payload = {
            "size": 40,
            "rediscovery_days": 90,
            "daily_avoid_days": 21,
            "favorite_percent": 20,
            "artist_cap": 2,
            "hour": 6,
        }

        async def body(_request):
            return payload

        attach_policy_routes(routes, store, engine, body, lambda: None)
        asyncio.run(routes.handlers[("POST", "/api/daily/policy")](object()))

        self.assertIsNone(store.get("daily_plan"))
        self.assertEqual(PUBLISHED, store.get("daily_published_view"))

    def test_legacy_managed_playlist_is_reported_as_published_when_plan_is_missing(self):
        from helper.extra_web import extensions_status

        store, _engine = self.make_engine()
        store.set_many({
            "daily_plan": None,
            "daily_managed": {
                "id": "99239", "title": "每日推荐", "published_at": NOW,
            },
            "daily_history": [{"ids": ["1", "2"], "created_at": NOW}],
        })

        status = extensions_status(store)

        self.assertEqual("99239", status["daily_published"]["playlist_id"])
        self.assertEqual(2, status["daily_published"]["count"])
        self.assertEqual([], status["daily_published"]["items"])

    def test_draft_for_one_profile_never_replaces_another_profiles_published_view(self):
        from helper.extra_web import extensions_status
        from helper.profiles import ProfileRegistry
        from helper.scoped_store import ScopedStore
        from helper.store import Store

        with tempfile.TemporaryDirectory() as root:
            base = Store(Path(root))
            registry = ProfileRegistry(base)
            registry.create(name="Friend", kind="shared", profile_id="friend-a", token="friend-token")
            owner = ScopedStore(base, "default")
            friend = ScopedStore(base, "friend-a")
            owner.set_many({"daily_plan": {"id": "owner-draft"}, "daily_published_view": {**PUBLISHED, "items": [{"id": "1", "title": "owner-song"}]}})
            friend.set_many({"daily_plan": {"id": "friend-draft"}, "daily_published_view": {**PUBLISHED, "playlist_id": "friend-list", "items": [{"id": "2", "title": "friend-song"}]}})

            self.assertEqual("owner-song", extensions_status(owner)["daily_published"]["items"][0]["title"])
            self.assertEqual("friend-song", extensions_status(friend)["daily_published"]["items"][0]["title"])

    def test_daily_heading_has_a_compact_profile_label_without_instruction_card(self):
        page = _Page()
        page.feed((ROOT / "src/helper/static/daily.html").read_text(encoding="utf-8"))

        self.assertIn("activeProfileLabel", page.ids)
        self.assertNotIn("instruction-card", page.classes)

    def test_repair_rebuilds_the_published_view_from_the_repaired_plan(self):
        from tests.test_daily_fixed_playlist import _recommendation

        store, engine = self.make_engine()
        with patch("helper.daily.recommend_rotating", side_effect=_recommendation):
            plan = engine.preview_daily(now=NOW - 20)
        plex = engine.plex_factory(store.get("settings"))
        before = plex.create("每日推荐", ["3", "4"], engine.marker("daily"))
        snapshot = {
            "id": "repair-1", "kind": "daily", "category_id": "daily", "title": "每日推荐",
            "created_at": NOW - 10, "status": "uncertain", "before": before,
            "after": None, "add": ["1", "2"], "plan_id": plan["id"],
            "machine": "machine-a", "scope": engine.daily_scope(), "before_daily_record": None,
            "before_published_view": None,
        }
        store.set_many({
            "snapshots": [snapshot], "daily_plan": plan,
            "daily_published_view": {**PUBLISHED, "items": [{"id": "9", "title": "旧显示"}]},
        })

        engine.repair_daily("repair-1", now=NOW)

        view = store.get("daily_published_view")
        self.assertEqual(["1", "2"], [row["id"] for row in view["items"]])
        self.assertEqual(before["id"], view["playlist_id"])

    def test_rollback_restores_the_previous_published_view(self):
        store, engine = self.make_engine()
        plex = engine.plex_factory(store.get("settings"))
        current = plex.create("每日推荐", ["1", "2"], engine.marker("daily"))
        before = {**current, "items": [{"id": "3", "item_id": "old-3"}]}
        previous = {
            **PUBLISHED, "playlist_id": current["id"],
            "items": [{"id": "3", "title": "原歌曲"}], "count": 1,
        }
        snapshot = {
            "id": "restore-1", "kind": "daily", "category_id": "daily", "title": "每日推荐",
            "created_at": NOW - 10, "status": "applied", "before": before, "after": current,
            "add": ["1", "2"], "plan_id": "new-plan", "machine": "machine-a",
            "scope": engine.daily_scope(),
            "before_daily_record": {
                "id": current["id"], "title": "每日推荐", "snapshot_id": "older",
                "machine": "machine-a", "scope": engine.daily_scope(),
            },
            "before_published_view": previous,
        }
        store.set_many({
            "snapshots": [snapshot],
            "daily_managed": {
                "id": current["id"], "title": "每日推荐", "snapshot_id": "restore-1",
                "machine": "machine-a", "scope": engine.daily_scope(),
            },
            "daily_history": [
                {"plan_id": "old-plan", "ids": ["3"]},
                {"plan_id": "new-plan", "ids": ["1", "2"]},
            ],
            "daily_published_view": {**PUBLISHED, "items": [{"id": "1", "title": "新歌曲"}]},
        })

        engine._restore_daily_snapshot(snapshot)

        self.assertEqual(previous, store.get("daily_published_view"))


if __name__ == "__main__":
    unittest.main()
