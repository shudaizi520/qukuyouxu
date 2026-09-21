import sys
import unittest
from contextlib import nullcontext
from pathlib import Path
from urllib.parse import urlencode
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def uri(section, rating_key="track.userRating>", rating=8, extra=None):
    query = {"type": 10, rating_key: rating, **(extra or {})}
    return f"server://machine/com.plexapp.plugins.library/library/sections/{section}/all?{urlencode(query)}"


class Store:
    def __init__(self, section="11"):
        self.values = {"settings": {"section": section}}

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value


class Plex:
    def __init__(self, rows=(), info=None):
        self.machine = "machine"
        self.rows = list(rows)
        self.info = info or {}
        self.calls = []

    def playlists(self):
        return list(self.rows)

    def smart_playlist_info(self, pid):
        self.calls.append(("read", pid))
        return self.info[pid]

    def create_favorite_smart(self, section):
        self.calls.append(("create", section))
        record = {"id": "90", "title": "我喜欢", "smart": True,
                  "section": section, "content": uri(section)}
        self.rows.append({"ratingKey": "90", "title": "我喜欢", "smart": "1", "playlistType": "audio"})
        self.info["90"] = record
        return record

    def replace_favorite_smart_rule(self, pid, section, expected_content=None, expected_title=None):
        self.calls.append(("replace", pid, section))
        self.expected = (expected_content, expected_title)
        self.info[pid] = {**self.info[pid], "content": uri(section)}
        return self.info[pid]


class Engine:
    def __init__(self, plex, section="11"):
        self.store = Store(section)
        self.plex = plex

    def plex_factory(self, _settings):
        return self.plex

    def exclusive(self):
        return nullcontext()


class FavoriteEnsureTests(unittest.TestCase):
    def test_unrecorded_same_title_playlist_is_never_taken_over(self):
        from helper.favorite_smart import ensure_profile_favorites
        row = {"ratingKey": "51", "title": "我喜欢", "smart": "1", "playlistType": "audio"}
        info = {"51": {"id": "51", "title": "我喜欢", "smart": True,
                       "section": "11", "content": uri("11")}}
        engine = Engine(Plex([row], info))
        self.assertEqual("needs_review", ensure_profile_favorites(engine)["status"])
        self.assertFalse(any(call[0] in ("create", "replace") for call in engine.plex.calls))

    def test_old_named_playlist_and_v1_pending_do_not_block_new_liked_playlist(self):
        from helper.favorite_smart import ensure_profile_favorites
        old = {"ratingKey": "51", "title": "❤️我的最爱", "smart": "1", "playlistType": "audio"}
        info = {"51": {"id": "51", "title": "❤️我的最爱", "smart": True,
                       "section": "11", "content": uri("11", extra={"group": "guid"})}}
        engine = Engine(Plex([old], info))
        engine.store.set("favorite_smart_v1", {"status": "pending", "section": "11"})
        result = ensure_profile_favorites(engine)
        self.assertEqual("synced", result["status"])
        self.assertEqual("我喜欢", engine.plex.info[result["playlist_id"]]["title"])
        self.assertEqual(info["51"], engine.plex.info["51"])
        self.assertEqual("pending", engine.store.get("favorite_smart_v1")["status"])
        self.assertEqual(result, engine.store.get("favorite_smart_v2"))

    def test_legacy_synced_record_never_authorizes_modifying_old_playlist(self):
        from helper.favorite_smart import ensure_profile_favorites

        old = {"ratingKey": "51", "title": "我的最爱", "smart": "1", "playlistType": "audio"}
        info = {"51": {"id": "51", "title": "我的最爱", "smart": True,
                       "section": "11", "content": uri("11", "track.userRating", 10)}}
        engine = Engine(Plex([old], info))
        engine.store.set("favorite_smart_v1", {
            "status": "synced", "playlist_id": "51", "section": "11",
        })
        self.assertEqual("90", ensure_profile_favorites(engine)["playlist_id"])
        self.assertFalse(any(call[0] == "replace" for call in engine.plex.calls))
        self.assertEqual(info["51"], engine.plex.info["51"])

    def test_plex_library_uri_is_accepted_only_for_pure_rating_rule(self):
        from helper.favorite_smart import _rule_kind
        pure = "library://abc/directory/%2Flibrary%2Fsections%2F11%2Fall%3Ftype%3D10%26track%2EuserRating%253E%3D8"
        complex_rule = "library://abc/directory/%2Flibrary%2Fsections%2F11%2Fall%3Ftype%3D10%26group%3Dguid%26track.userRating%253E%3D8"
        self.assertEqual("current", _rule_kind(pure, "11", "machine"))
        self.assertEqual("unknown", _rule_kind(complex_rule, "11", "machine"))
        self.assertEqual("unknown", _rule_kind(pure, "15", "machine"))

    def test_first_enter_creates_once_and_records_for_this_profile(self):
        from helper.favorite_smart import ensure_profile_favorites
        engine = Engine(Plex())
        first = ensure_profile_favorites(engine)
        second = ensure_profile_favorites(engine)
        self.assertEqual("synced", first["status"])
        self.assertEqual("90", first["playlist_id"])
        self.assertEqual("90", second["playlist_id"])
        self.assertEqual(1, engine.plex.calls.count(("create", "11")))
        self.assertEqual("90", engine.store.get("favorite_smart_v2")["playlist_id"])

    def test_same_plex_user_two_music_libraries_get_independent_records(self):
        from helper.favorite_smart import ensure_profile_favorites
        first, second = Engine(Plex(), "11"), Engine(Plex(), "15")
        ensure_profile_favorites(first)
        ensure_profile_favorites(second)
        self.assertIn(("create", "11"), first.plex.calls)
        self.assertIn(("create", "15"), second.plex.calls)
        self.assertEqual("11", first.store.get("favorite_smart_v2")["section"])
        self.assertEqual("15", second.store.get("favorite_smart_v2")["section"])

    def test_existing_five_star_rule_migrates_in_place(self):
        from helper.favorite_smart import ensure_profile_favorites
        row = {"ratingKey": "51", "title": "💖我喜欢", "smart": "1", "playlistType": "audio"}
        info = {"51": {"id": "51", "title": "💖我喜欢", "smart": True,
                       "section": "11", "content": uri("11", "track.userRating", 10)}}
        engine = Engine(Plex([row], info))
        engine.store.set("favorite_smart_v2", {"status": "synced", "playlist_id": "51", "section": "11"})
        result = ensure_profile_favorites(engine)
        self.assertEqual("51", result["playlist_id"])
        self.assertIn(("replace", "51", "11"), engine.plex.calls)
        self.assertEqual((uri("11", "track.userRating", 10), "💖我喜欢"), engine.plex.expected)
        self.assertEqual(uri("11", "track.userRating", 10),
                         engine.store.get("favorite_smart_previous_rule_v2")["content"])

    def test_strictly_greater_than_nine_is_also_five_star_only(self):
        from helper.favorite_smart import ensure_profile_favorites
        row = {"ratingKey": "51", "title": "我喜欢", "smart": "1", "playlistType": "audio"}
        info = {"51": {"id": "51", "title": "我喜欢", "smart": True,
                       "section": "11", "content": uri("11", "track.userRating>>", 9)}}
        engine = Engine(Plex([row], info))
        engine.store.set("favorite_smart_v2", {"status": "synced", "playlist_id": "51", "section": "11"})
        self.assertEqual("synced", ensure_profile_favorites(engine)["status"])
        self.assertIn(("replace", "51", "11"), engine.plex.calls)

    def test_unrelated_filter_and_same_name_plain_playlist_do_not_mutate(self):
        from helper.favorite_smart import ensure_profile_favorites
        for smart, content in [("0", ""), ("1", uri("11", extra={"year>": 2000}))]:
            with self.subTest(smart=smart):
                row = {"ratingKey": "51", "title": "我喜欢", "smart": smart, "playlistType": "audio"}
                info = {"51": {"id": "51", "title": "我喜欢", "smart": True,
                               "section": "11", "content": content}}
                engine = Engine(Plex([row], info))
                self.assertEqual("needs_review", ensure_profile_favorites(engine)["status"])
                self.assertFalse(any(call[0] in ("create", "replace") for call in engine.plex.calls))

    def test_foreign_server_uri_and_changed_detail_title_are_not_adopted(self):
        from helper.favorite_smart import ensure_profile_favorites
        row = {"ratingKey": "51", "title": "我喜欢", "smart": "1", "playlistType": "audio"}
        for title, content in [("我喜欢", uri("11", "track.userRating", 10).replace("server://machine/", "https://foreign.example/")),
                               ("其他歌单", uri("11", "track.userRating", 10))]:
            with self.subTest(title=title):
                info = {"51": {"id": "51", "title": title, "smart": True,
                               "section": "11", "content": content}}
                engine = Engine(Plex([row], info))
                self.assertEqual("needs_review", ensure_profile_favorites(engine)["status"])
                self.assertFalse(any(call[0] in ("create", "replace") for call in engine.plex.calls))

    def test_two_same_name_candidates_do_not_create_or_replace(self):
        from helper.favorite_smart import ensure_profile_favorites
        rows = [{"ratingKey": pid, "title": "我喜欢", "smart": "1", "playlistType": "audio"}
                for pid in ("51", "52")]
        info = {pid: {"id": pid, "title": "我喜欢", "smart": True,
                      "section": "11", "content": uri("11", "track.userRating", 10)}
                for pid in ("51", "52")}
        engine = Engine(Plex(rows, info))
        self.assertEqual("needs_review", ensure_profile_favorites(engine)["status"])
        self.assertFalse(any(call[0] in ("create", "replace") for call in engine.plex.calls))

    def test_other_library_smart_does_not_block_this_library(self):
        from helper.favorite_smart import ensure_profile_favorites
        row = {"ratingKey": "51", "title": "我喜欢", "smart": "1", "playlistType": "audio"}
        info = {"51": {"id": "51", "title": "我喜欢", "smart": True,
                       "section": "15", "content": uri("15")}}
        engine = Engine(Plex([row], info))
        self.assertEqual("90", ensure_profile_favorites(engine)["playlist_id"])
        self.assertNotIn(("replace", "51", "11"), engine.plex.calls)

    def test_deleted_recorded_playlist_is_recreated_only_after_verified_404(self):
        from helper.clients import PlexNotFound
        from helper.favorite_smart import ensure_profile_favorites

        class DeletedPlex(Plex):
            def smart_playlist_info(self, pid):
                self.calls.append(("read", pid))
                raise PlexNotFound("not found")

        engine = Engine(DeletedPlex())
        engine.store.set("favorite_smart_v2", {"status": "synced", "playlist_id": "51", "section": "11"})
        self.assertEqual("90", ensure_profile_favorites(engine)["playlist_id"])
        self.assertIn(("read", "51"), engine.plex.calls)

    def test_unverified_missing_record_is_not_recreated(self):
        from helper.favorite_smart import ensure_profile_favorites
        engine = Engine(Plex())
        engine.store.set("favorite_smart_v2", {"status": "synced", "playlist_id": "51", "section": "11"})
        self.assertEqual("needs_review", ensure_profile_favorites(engine)["status"])
        self.assertFalse(any(call[0] == "create" for call in engine.plex.calls))

    def test_review_downgrades_stale_synced_badge_without_losing_recorded_id(self):
        from helper.favorite_smart import ensure_profile_favorites
        row = {"ratingKey": "51", "title": "我喜欢", "smart": "1", "playlistType": "audio"}
        info = {"51": {"id": "51", "title": "我喜欢", "smart": True,
                       "section": "11", "content": uri("11", extra={"year>": 2000})}}
        engine = Engine(Plex([row], info))
        engine.store.set("favorite_smart_v2", {"status": "synced", "playlist_id": "51", "section": "11"})
        self.assertEqual("needs_review", ensure_profile_favorites(engine)["status"])
        self.assertEqual("needs_review", engine.store.get("favorite_smart_v2")["status"])
        self.assertEqual("51", engine.store.get("favorite_smart_v2")["playlist_id"])

    def test_uncertain_create_result_never_auto_retries_and_risks_duplicate(self):
        from helper.clients import PlexError
        from helper.favorite_smart import ensure_profile_favorites

        class UncertainPlex(Plex):
            def create_favorite_smart(self, section):
                self.calls.append(("create", section))
                raise PlexError("Plex 写入后连接断开")

        engine = Engine(UncertainPlex())
        with self.assertRaises(PlexError):
            ensure_profile_favorites(engine)
        self.assertEqual("pending", engine.store.get("favorite_smart_v2")["status"])
        self.assertEqual("needs_review", ensure_profile_favorites(engine)["status"])
        self.assertEqual(1, engine.plex.calls.count(("create", "11")))

    def test_definitive_permission_rejection_can_retry_after_permission_is_fixed(self):
        from helper.clients import PlexWriteRejected
        from helper.favorite_smart import ensure_profile_favorites

        class RejectedPlex(Plex):
            def create_favorite_smart(self, section):
                self.calls.append(("create", section))
                raise PlexWriteRejected("Plex 返回 HTTP 403")

        engine = Engine(RejectedPlex())
        with self.assertRaises(PlexWriteRejected):
            ensure_profile_favorites(engine)
        self.assertEqual("needs_review", engine.store.get("favorite_smart_v2")["status"])
        engine.plex = Plex()
        self.assertEqual("synced", ensure_profile_favorites(engine)["status"])

    def test_explicit_ensure_route_uses_current_profile_and_get_is_read_only(self):
        from fastapi import FastAPI
        from helper.playlist_hub import attach_playlist_hub_routes

        engine = Engine(Plex())
        engine.store.profile_id = "default"
        runtime = type("Runtime", (), {"engine": lambda self, _id: engine})()
        profiles = type("Profiles", (), {"get": lambda self, _id: {"id": "default"},
                                         "list_public": lambda self, enabled_only=False: []})()
        app = FastAPI()
        with patch("helper.playlist_hub.playlist_rows", return_value=[]):
            attach_playlist_hub_routes(app, engine.store, runtime, profiles, None, lambda: None)
            route = lambda path: next(row.endpoint for row in app.routes if row.path == path)
            route("/api/playlists")()
            self.assertEqual([], engine.plex.calls)
            result = route("/api/playlists/favorite/ensure")()
        self.assertEqual("synced", result["status"])
        self.assertEqual([("create", "11")], [call for call in engine.plex.calls if call[0] == "create"])

    def test_sidebar_reports_plex_sync_status_without_duplicate_native_row(self):
        from helper.playlist_hub import assistant_playlist_rows
        from helper.playlist_inventory import merge_playlist_rows, assistant_playlist_row
        engine = Engine(Plex())
        with patch("helper.playlist_hub.ExternalRepository.list_sources", return_value=[]):
            before = next(row for row in assistant_playlist_rows(engine.store) if row["kind"] == "favorite")
        self.assertEqual("未同步 Plex", before["status"])
        ensure = __import__("helper.favorite_smart", fromlist=["ensure_profile_favorites"]).ensure_profile_favorites
        ensure(engine)
        with patch("helper.playlist_hub.ExternalRepository.list_sources", return_value=[]):
            after = next(row for row in assistant_playlist_rows(engine.store) if row["kind"] == "favorite")
        self.assertEqual("上次已同步 Plex", after["status"])
        self.assertEqual("90", after["playlist_id"])
        merged = merge_playlist_rows([assistant_playlist_row(after)], engine.plex.rows)
        self.assertEqual(1, len(merged))

    def test_recorded_managed_playlist_stays_out_of_native_actions_during_review(self):
        from helper.playlist_hub import assistant_playlist_rows
        from helper.playlist_inventory import merge_playlist_rows, assistant_playlist_row

        engine = Engine(Plex([{
            "ratingKey": "51", "title": "我喜欢", "smart": "1", "playlistType": "audio",
        }]))
        engine.store.set("favorite_smart_v2", {
            "status": "needs_review", "playlist_id": "51", "section": "11",
        })
        with patch("helper.playlist_hub.ExternalRepository.list_sources", return_value=[]):
            favorite = next(row for row in assistant_playlist_rows(engine.store)
                            if row["kind"] == "favorite")
        rows = merge_playlist_rows([assistant_playlist_row(favorite)], engine.plex.rows)
        self.assertEqual(["favorite"], [row["kind"] for row in rows])

    def test_profile_switch_attempts_plex_favorite_ensure_before_listing(self):
        source = (ROOT / "src/helper/static/playlists.js").read_text(encoding="utf-8")
        switch = source.split("async function switchProfile(", 1)[1].split("async function removeTrack(", 1)[0]
        self.assertIn("/api/playlists/favorite/ensure", switch)
        self.assertLess(switch.index("/api/playlists/favorite/ensure"), switch.index("loadPlaylists(undefined,requestId)"))

    def test_unread_favorite_indicator_is_scoped_to_one_library_profile(self):
        source = (ROOT / "src/helper/static/playlists.js").read_text(encoding="utf-8")
        key = source.split("const unseenFavoriteKey=()=>{", 1)[1].split("};", 1)[0]
        self.assertIn("${profileId}", key)


if __name__ == "__main__":
    unittest.main()
