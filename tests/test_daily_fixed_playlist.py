import copy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from helper.library_engine import LibraryEngine
from helper.store import Store


class _DailyPlex:
    def __init__(self, existing=True, owned=True):
        self.playlists_by_id = {}
        self.create_calls = 0
        self.summary_updates = []
        if existing:
            self.playlists_by_id["900"] = {
                "id": "900",
                "title": "每日推荐",
                "summary": (
                    "[PCH:11111111111111111111111111111111:daily]\n旧版本每日推荐"
                    if owned else "用户原有歌单"
                ),
                "items": [
                    {"id": "4", "item_id": "4004"},
                    {"id": "5", "item_id": "4005"},
                ],
            }

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
        return [copy.deepcopy(row) for row in self.playlists_by_id.values()]

    def playlist_state(self, playlist_id):
        return copy.deepcopy(self.playlists_by_id[str(playlist_id)])

    def read_playlist_until(self, playlist_id, predicate, attempts=8, delay=0.25):
        state = self.playlist_state(playlist_id)
        if not predicate(state):
            raise AssertionError("playlist state did not satisfy predicate")
        return state

    def create(self, title, ids, marker, description=None):
        self.create_calls += 1
        state = {
            "id": "901",
            "title": title,
            "summary": marker + "\n" + (description or ""),
            "items": [
                {"id": str(track_id), "item_id": f"new-{index}"}
                for index, track_id in enumerate(ids, 1)
            ],
        }
        self.playlists_by_id[state["id"]] = state
        return copy.deepcopy(state)

    def append(self, playlist_id, ids):
        state = self.playlists_by_id[str(playlist_id)]
        start = len(state["items"]) + 1
        for offset, track_id in enumerate(ids):
            state["items"].append({"id": str(track_id), "item_id": f"added-{start + offset}"})

    def remove_items(self, playlist_id, item_ids):
        state = self.playlists_by_id[str(playlist_id)]
        removed = set(map(str, item_ids))
        state["items"] = [row for row in state["items"] if row["item_id"] not in removed]

    def update_playlist_summary(self, playlist_id, summary):
        self.summary_updates.append((str(playlist_id), str(summary)))
        self.playlists_by_id[str(playlist_id)]["summary"] = str(summary)

    def move_item(self, playlist_id, item_id, after=None):
        state = self.playlists_by_id[str(playlist_id)]
        moving = next(row for row in state["items"] if row["item_id"] == str(item_id))
        state["items"].remove(moving)
        if after is None:
            state["items"].insert(0, moving)
            return
        position = next(index for index, row in enumerate(state["items"]) if row["item_id"] == str(after))
        state["items"].insert(position + 1, moving)


class _EventuallyConsistentDailyPlex(_DailyPlex):
    """Plex may return the pre-write playlist once before its new state appears."""

    def __init__(self):
        super().__init__(existing=True)
        self._stale_state = None

    def playlist_state(self, playlist_id):
        if self._stale_state is not None:
            stale, self._stale_state = self._stale_state, None
            return copy.deepcopy(stale)
        return super().playlist_state(playlist_id)

    def append(self, playlist_id, ids):
        before = super().playlist_state(playlist_id)
        super().append(playlist_id, ids)
        self._stale_state = before

    def remove_items(self, playlist_id, item_ids):
        before = super().playlist_state(playlist_id)
        super().remove_items(playlist_id, item_ids)
        self._stale_state = before

    def move_item(self, playlist_id, item_id, after=None):
        before = super().playlist_state(playlist_id)
        super().move_item(playlist_id, item_id, after)
        self._stale_state = before


class _OrderDifferentDailyPlex(_DailyPlex):
    """A valid audio playlist can expose the right members in another order."""

    def __init__(self):
        super().__init__(existing=True)
        self.playlists_by_id["900"]["items"] = [
            {"id": "4", "item_id": "4004"},
            {"id": "3", "item_id": "4003"},
            {"id": "2", "item_id": "4002"},
            {"id": "1", "item_id": "4001"},
        ]
        self.move_calls = 0

    def move_item(self, playlist_id, item_id, after=None):
        self.move_calls += 1
        super().move_item(playlist_id, item_id, after)


def _recommendation(*_args, **_kwargs):
    return {
        "items": [
            {"id": "1", "title": "歌曲 1", "artist": "歌手", "song_key": "song-1"},
            {"id": "2", "title": "歌曲 2", "artist": "歌手", "song_key": "song-2"},
        ],
        "stats": {"positive_seed_count": 0},
        "warnings": [],
    }


def _next_recommendation(*_args, **_kwargs):
    return {
        "items": [
            {"id": "3", "title": "歌曲 3", "artist": "歌手", "song_key": "song-3"},
            {"id": "4", "title": "歌曲 4", "artist": "歌手", "song_key": "song-4"},
        ],
        "stats": {"positive_seed_count": 0},
        "warnings": [],
    }


class DailyFixedPlaylistTests(unittest.TestCase):
    def make_engine(self, plex):
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        store = Store(Path(root.name))
        settings = store.get("settings")
        settings.update(plex_url="http://plex:32400", plex_token="token", section="11")
        store.set("settings", settings)
        return store, LibraryEngine(store, plex_factory=lambda _settings: plex)

    def test_preview_uses_existing_legacy_owned_playlist_without_blocking(self):
        plex = _DailyPlex(existing=True)
        _store, engine = self.make_engine(plex)

        with patch("helper.daily.recommend_rotating", side_effect=_recommendation):
            plan = engine.preview_daily(now=1_800_000_000)

        self.assertEqual([], plan["blocked"])
        self.assertEqual("900", plan["before"]["id"])

    def test_preview_blocks_an_unmarked_same_name_playlist(self):
        plex = _DailyPlex(existing=True, owned=False)
        _store, engine = self.make_engine(plex)

        with patch("helper.daily.recommend_rotating", side_effect=_recommendation):
            plan = engine.preview_daily(now=1_800_000_000)

        self.assertTrue(any("同名" in message for message in plan["blocked"]))
        self.assertIsNone(plan["before"])

    def test_new_profile_uses_own_daily_title_when_another_library_owns_default(self):
        from helper.profiles import ProfileRegistry
        from helper.scoped_store import ScopedStore

        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        base = Store(Path(root.name))
        registry = ProfileRegistry(base)
        registry.create(name="第二曲库", kind="owner", profile_id="other",
                        token="other-token", library={"id": "22"})
        owner = ScopedStore(base, "default", registry=registry)
        owner.set("daily_managed", {"id": "900", "machine": "machine-a"})
        other = ScopedStore(base, "other", registry=registry)
        settings = other.get("settings")
        settings.update(plex_url="http://plex:32400", plex_token="other-token", section="22")
        other.set("settings", settings)
        plex = _DailyPlex(existing=True)
        engine = LibraryEngine(other, plex_factory=lambda _settings: plex)

        with patch("helper.daily.recommend_rotating", side_effect=_recommendation):
            plan = engine.preview_daily(now=1_800_000_000)

        self.assertEqual([], plan["blocked"])
        self.assertIsNone(plan["before"])
        self.assertEqual("每日推荐·曲库22", other.get("daily_playlist_target")["title"])
        result = engine.publish_daily(plan["id"], now=1_800_000_010)
        self.assertEqual("901", result["playlist_id"])
        self.assertEqual("每日推荐·曲库22", plex.playlist_state("901")["title"])
        self.assertEqual("每日推荐", plex.playlist_state("900")["title"])

    def test_previewed_daily_cannot_be_adopted_after_another_library_claims_it(self):
        from helper.engine import SafetyError
        from helper.profiles import ProfileRegistry
        from helper.scoped_store import ScopedStore

        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        base = Store(Path(root.name))
        registry = ProfileRegistry(base)
        registry.create(name="第二曲库", kind="owner", profile_id="other",
                        token="other-token", library={"id": "22"})
        owner = ScopedStore(base, "default", registry=registry)
        other = ScopedStore(base, "other", registry=registry)
        settings = other.get("settings")
        settings.update(plex_url="http://plex:32400", plex_token="other-token", section="22")
        other.set("settings", settings)
        plex = _DailyPlex(existing=True)
        engine = LibraryEngine(other, plex_factory=lambda _settings: plex)

        with patch("helper.daily.recommend_rotating", side_effect=_recommendation):
            plan = engine.preview_daily(now=1_800_000_000)
        self.assertEqual([], plan["blocked"])
        owner.set("daily_managed", {"id": "900", "machine": "machine-a"})

        with self.assertRaisesRegex(SafetyError, "其他曲库"):
            engine.publish_daily(plan["id"], now=1_800_000_010)
        self.assertEqual(0, plex.create_calls)

    def test_publish_replaces_existing_same_name_playlist_in_place(self):
        plex = _DailyPlex(existing=True)
        store, engine = self.make_engine(plex)
        with patch("helper.daily.recommend_rotating", side_effect=_recommendation):
            plan = engine.preview_daily(now=1_800_000_000)

        result = engine.publish_daily(plan["id"], now=1_800_000_010)

        self.assertEqual("900", result["playlist_id"])
        self.assertEqual(0, plex.create_calls)
        self.assertEqual(["1", "2"], [row["id"] for row in plex.playlist_state("900")["items"]])
        self.assertEqual("900", store.get("daily_managed")["id"])
        self.assertIn(engine.marker("daily"), plex.playlist_state("900")["summary"])
        self.assertEqual(1, len(plex.summary_updates))

    def test_successful_manual_publish_clears_recovery_suspension(self):
        plex = _DailyPlex(existing=True)
        store, engine = self.make_engine(plex)
        store.set("daily_auto_suspension", {"reason": "上次恢复后暂停"})
        with patch("helper.daily.recommend_rotating", side_effect=_recommendation):
            plan = engine.preview_daily(now=1_800_000_000)

        engine.publish_daily(plan["id"], now=1_800_000_010)

        self.assertIsNone(store.get("daily_auto_suspension"))

    def test_scheduled_daily_waits_for_manual_preview_without_consuming_the_day(self):
        from helper.daily import day_at

        plex = _DailyPlex(existing=True)
        store, engine = self.make_engine(plex)
        now = 1_800_000_000
        store.set_many({
            "daily_managed": {"id": "900", "scope": engine.daily_scope()},
            "daily_plan": {"id": "manual", "origin": "manual", "applied": False,
                           "date": day_at(now), "created_at": now - 300},
        })

        result = engine.daily_auto(
            schedule={"enabled": True, "hour": 6}, scheduled=True, now=now,
        )

        self.assertEqual("deferred", result["status"])
        self.assertGreater(result["retry_at"], now)

    def test_scheduled_daily_catches_up_even_before_configured_hour(self):
        plex = _DailyPlex(existing=True)
        store, engine = self.make_engine(plex)
        store.set("daily_managed", {"id": "900", "scope": engine.daily_scope()})
        now = 1_800_000_000
        with patch.object(engine, "_preview_daily", return_value={
            "id": "auto", "blocked": [], "rolling": {"unchanged": True},
        }) as preview:
            result = engine.daily_auto(
                schedule={"enabled": True, "hour": 23}, scheduled=True, now=now,
            )

        preview.assert_called_once_with(now, origin="auto")
        self.assertTrue(result["unchanged"])

    def test_recovery_suspension_overrides_global_daily_switch(self):
        plex = _DailyPlex(existing=True)
        store, engine = self.make_engine(plex)
        store.set_many({
            "daily_managed": {"id": "900", "scope": engine.daily_scope()},
            "daily_auto_suspension": {"reason": "发布结果待核对"},
        })

        result = engine.daily_auto(
            schedule={"enabled": True, "hour": 0}, scheduled=True, now=1_800_000_000,
        )

        self.assertEqual("suspended", result["status"])

    def test_first_publish_creates_daily_playlist_when_none_exists(self):
        plex = _DailyPlex(existing=False)
        _store, engine = self.make_engine(plex)
        with patch("helper.daily.recommend_rotating", side_effect=_recommendation):
            plan = engine.preview_daily(now=1_800_000_000)

        result = engine.publish_daily(plan["id"], now=1_800_000_010)

        self.assertEqual("901", result["playlist_id"])
        self.assertEqual(1, plex.create_calls)
        self.assertEqual(["1", "2"], [row["id"] for row in plex.playlist_state("901")["items"]])

    def test_scheduled_daily_creates_first_playlist_for_new_profile(self):
        plex = _DailyPlex(existing=False)
        store, engine = self.make_engine(plex)
        with patch("helper.daily.recommend_rotating", side_effect=_recommendation):
            result = engine.daily_auto(
                schedule={"enabled": True, "hour": 6}, scheduled=True, now=1_800_000_000,
            )

        self.assertEqual("901", result["playlist_id"])
        self.assertEqual("901", store.get("daily_managed")["id"])
        self.assertEqual(1, plex.create_calls)

    def test_later_publish_keeps_updating_the_same_adopted_playlist(self):
        plex = _DailyPlex(existing=True)
        _store, engine = self.make_engine(plex)
        with patch("helper.daily.recommend_rotating", side_effect=_recommendation):
            first = engine.preview_daily(now=1_800_000_000)
        engine.publish_daily(first["id"], now=1_800_000_010)

        with patch("helper.daily.recommend_rotating", side_effect=_next_recommendation):
            second = engine.preview_daily(now=1_800_086_400)
        result = engine.publish_daily(second["id"], now=1_800_086_410)

        self.assertEqual([], second["blocked"])
        self.assertEqual("900", result["playlist_id"])
        self.assertEqual(0, plex.create_calls)
        self.assertEqual(["3", "4"], [row["id"] for row in plex.playlist_state("900")["items"]])

    def test_publish_waits_for_plex_read_after_write_visibility(self):
        plex = _EventuallyConsistentDailyPlex()
        store, engine = self.make_engine(plex)
        with patch("helper.daily.recommend_rotating", side_effect=_recommendation):
            plan = engine.preview_daily(now=1_800_000_000)

        result = engine.publish_daily(plan["id"], now=1_800_000_010)

        self.assertEqual("900", result["playlist_id"])
        self.assertTrue(store.get("daily_plan")["applied"])
        self.assertEqual("applied", store.get("snapshots")[-1]["status"])
        self.assertEqual(["1", "2"], [row["id"] for row in plex.playlist_state("900")["items"]])

    def test_sync_accepts_exact_membership_without_forcing_plex_order(self):
        from helper.playlist_sync import sync_owned_items

        plex = _OrderDifferentDailyPlex()
        before = plex.playlist_state("900")

        result = sync_owned_items(plex, before, ["1", "2", "3", "4"])

        self.assertEqual({"1", "2", "3", "4"}, {row["id"] for row in result["items"]})
        self.assertEqual(0, plex.move_calls)

    def test_publish_accepts_right_members_in_a_different_plex_order(self):
        plex = _DailyPlex(existing=True)
        plex.playlists_by_id["900"]["items"] = [
            {"id": "2", "item_id": "4002"},
            {"id": "1", "item_id": "4001"},
        ]
        store, engine = self.make_engine(plex)
        with patch("helper.daily.recommend_rotating", side_effect=_recommendation):
            plan = engine.preview_daily(now=1_800_000_000)

        result = engine.publish_daily(plan["id"], now=1_800_000_010)

        self.assertEqual("900", result["playlist_id"])
        self.assertTrue(store.get("daily_plan")["applied"])

    def test_old_same_name_warning_does_not_keep_an_existing_preview_blocked(self):
        from helper.extra_web import public_daily

        plex = _DailyPlex(existing=True)
        store, engine = self.make_engine(plex)
        with patch("helper.daily.recommend_rotating", side_effect=_recommendation):
            plan = engine.preview_daily(now=1_800_000_000)
        plan["blocked"] = ["存在同名非本助手托管的“每日推荐”，不接管"]
        store.set("daily_plan", plan)

        self.assertEqual([], public_daily(store)["blocked"])
        result = engine.publish_daily(plan["id"], now=1_800_000_010)

        self.assertEqual("900", result["playlist_id"])
        self.assertEqual(["1", "2"], [row["id"] for row in plex.playlist_state("900")["items"]])


if __name__ == "__main__":
    unittest.main()
