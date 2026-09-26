import copy
import tempfile
import unittest
from pathlib import Path

from tests.test_v130_external_playlist_sync import FakePlex


QQ_URL = "https://y.qq.com/n/ryqq/playlist/123"


def local(track_id, title, artist, duration=180, album=""):
    return {
        "id": str(track_id), "title": title, "artist": artist, "album": album,
        "duration": duration, "available": True, "guid": f"local://{track_id}", "paths": [],
    }


def source_track(key, title, artists, position, duration_ms=180000):
    return {
        "source_track_key": key, "position": position, "source_track_id": key,
        "title": title, "artists": artists, "album": "", "duration_ms": duration_ms,
        "version_flags": [], "version_label": "", "source_url": "",
    }


def snapshot(tracks=None, revision="source-r1"):
    return {
        "provider": "qq", "external_id": "123", "url": QQ_URL,
        "title": "百万收藏", "revision": revision,
        "tracks": tracks or [
            source_track("a", "已有一", ["歌手甲"], 0),
            source_track("b", "缺失歌", ["歌手乙"], 1),
            source_track("c", "已有三", ["歌手丙"], 2),
            source_track("d", "同名歌", ["歌手丁"], 3),
        ],
    }


class FakeProviders:
    def __init__(self, results):
        self.results = list(results)
        self.calls = 0

    def fetch(self, recognized):
        self.calls += 1
        if not self.results:
            raise AssertionError("unexpected provider fetch")
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return copy.deepcopy(result)


class CatalogPlex(FakePlex):
    def __init__(self, catalog):
        super().__init__()
        self.catalog = list(catalog)

    def tracks(self, section):
        if str(section) != "11":
            raise ValueError("wrong section")
        return copy.deepcopy(self.catalog)

    def playlist_ids(self, playlist_id):
        return [row["id"] for row in self.states[str(playlist_id)]["items"]]


class ExternalServiceV130Tests(unittest.TestCase):
    def setUp(self):
        from helper.external_service import ExternalPlaylistService
        from helper.profiles import ProfileRegistry
        from helper.scoped_store import ScopedStore
        from helper.store import Store

        self.temp = tempfile.TemporaryDirectory()
        self.base = Store(Path(self.temp.name))
        self.registry = ProfileRegistry(self.base)
        self.registry.create("长辈", "home", profile_id="parent")
        self.store = ScopedStore(self.base, "default", registry=self.registry)
        settings = self.store.get("settings")
        settings.update(plex_url="http://plex", plex_token="token-token", section="11")
        self.store.set("settings", settings)
        self.plex = CatalogPlex([
            local("10", "已有一", "歌手甲"),
            local("30", "已有三", "歌手丙"),
            local("40", "同名歌", "歌手丁", album="A"),
            local("41", "同名歌", "歌手丁", album="B"),
        ])
        self.providers = FakeProviders([snapshot()])
        self.now = 2_000_000_000.0
        self.service = ExternalPlaylistService(
            self.store, lambda cfg: self.plex, self.providers, clock=lambda: self.now
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_import_match_publish_and_later_rematch_preserve_source_order(self):
        imported = self.service.import_source(value=QQ_URL)
        self.assertEqual({"matched": 2, "review": 1, "missing": 1, "ignored": 0}, imported["counts"])

        published = self.service.publish(imported["id"], "百万收藏", imported["revision"])
        self.assertEqual(["10", "30"], self.plex.playlist_ids(published["playlist_id"]))
        self.assertEqual(["10", "30"], self.service.repository.get_managed(
            "default", imported["id"]
        )["source_ids"])

        self.plex.catalog.append(local("20", "缺失歌", "歌手乙"))
        result = self.service.rematch_missing()
        self.assertEqual(["10", "20", "30"], self.plex.playlist_ids(published["playlist_id"]))
        self.assertEqual(0, result["missing"])

    def test_legacy_refresh_does_not_treat_a_new_source_track_as_a_manual_exclusion(self):
        imported = self.service.import_source(value=QQ_URL)
        published = self.service.publish(imported["id"], "百万收藏", imported["revision"])
        managed = self.service.repository.get_managed("default", imported["id"])
        managed.pop("source_ids", None)
        self.service.repository.save_managed("default", imported["id"], managed)
        self.service.repository.replace_matches("default", imported["id"], [], "catalog-empty")
        self.plex.states[published["playlist_id"]]["title"] = "Plex 手工新名字"

        self.plex.catalog.append(local("50", "后来新增", "歌手戊"))
        refreshed_tracks = snapshot()["tracks"] + [
            source_track("e", "后来新增", ["歌手戊"], 4),
        ]
        self.providers.results.append(snapshot(refreshed_tracks, revision="source-r2"))

        self.service.refresh(imported["id"], force=True)

        self.assertEqual(
            ["10", "30", "50"],
            self.plex.playlist_ids(published["playlist_id"]),
        )
        revised = self.service.repository.get_managed("default", imported["id"])
        self.assertEqual("Plex 手工新名字", revised["title"])
        self.assertEqual(["10", "30", "50"], revised["source_ids"])

    def test_stale_revision_and_no_reliable_matches_never_create(self):
        from helper.engine import SafetyError

        imported = self.service.import_source(value=QQ_URL)
        with self.assertRaisesRegex(SafetyError, "变化"):
            self.service.publish(imported["id"], "百万收藏", "stale")
        self.assertEqual([], self.plex.created)

        self.plex.catalog = []
        self.service.match(imported["id"])
        with self.assertRaisesRegex(SafetyError, "可靠匹配"):
            self.service.publish(imported["id"], "百万收藏", imported["revision"])
        self.assertEqual([], self.plex.created)

    def test_same_import_is_deduplicated_without_erasing_managed_state(self):
        first = self.service.import_source(value=QQ_URL)
        self.service.publish(first["id"], "百万收藏", first["revision"])
        self.providers.results.append(snapshot())

        second = self.service.import_source(value=QQ_URL)

        self.assertEqual(first["id"], second["id"])
        self.assertEqual(1, len(self.service.repository.list_sources("default")))
        self.assertIsNotNone(self.service.repository.get_managed("default", first["id"]))

    def test_external_management_page_adopts_live_plex_name_and_membership(self):
        from helper.playlist_hub import apply_manual_edits

        imported = self.service.import_source(value=QQ_URL)
        published = self.service.publish(imported["id"], "百万收藏", imported["revision"])
        playlist_id = published["playlist_id"]
        state = self.plex.states[playlist_id]
        state["title"] = "Plex 手工改名"
        state["items"] = [state["items"][0], *self.plex._items(["40"])]

        refreshed = self.service.public_source(imported["id"])

        self.assertEqual("Plex 手工改名", refreshed["managed"]["title"])
        self.assertEqual(["10", "40"], apply_manual_edits(
            self.store, "external", imported["id"], ["10", "30"]
        ))

    def test_background_source_refresh_preserves_direct_plex_edits(self):
        imported = self.service.import_source(value=QQ_URL)
        published = self.service.publish(imported["id"], "百万收藏", imported["revision"])
        playlist_id = published["playlist_id"]
        state = self.plex.states[playlist_id]
        state["title"] = "Plex 手工改名"
        state["items"] = [state["items"][0], *self.plex._items(["40"])]
        self.plex.catalog.append(local("50", "来源新增", "歌手戊"))
        self.providers.results.append(snapshot([
            source_track("a", "已有一", ["歌手甲"], 0),
            source_track("c", "已有三", ["歌手丙"], 1),
            source_track("e", "来源新增", ["歌手戊"], 2),
        ], revision="source-r2"))

        result = self.service.refresh(imported["id"], force=True)

        self.assertEqual("updated", result["status"])
        self.assertEqual("Plex 手工改名", self.plex.states[playlist_id]["title"])
        self.assertEqual(["10", "50", "40"], self.plex.playlist_ids(playlist_id))

    def test_confirmation_and_source_ids_are_profile_isolated(self):
        imported = self.service.import_source(value=QQ_URL)
        confirmed = self.service.confirm(imported["id"], "d", {"status": "matched", "plex_track_id": "40"})
        self.assertEqual("40", next(row for row in confirmed["tracks"] if row["source_track_key"] == "d")["plex_track_id"])

        from helper.external_service import ExternalPlaylistService
        from helper.scoped_store import ScopedStore

        parent = ExternalPlaylistService(
            ScopedStore(self.base, "parent", registry=self.registry), lambda cfg: self.plex,
            FakeProviders([]), clock=lambda: self.now,
        )
        with self.assertRaises(ValueError):
            parent.public_source(imported["id"])

    def test_batch_confirmation_applies_selected_choices_together(self):
        imported = self.service.import_source(value=QQ_URL)
        rows = self.service.repository.list_matches("default", imported["id"])
        for row in rows:
            if row["source_track_key"] == "b":
                row.update(status="review", candidate_ids=["40"], reason="ambiguous")
        self.service.repository.replace_matches("default", imported["id"], rows, rows[0]["catalog_revision"])

        result = self.service.confirm_many(imported["id"], [
            {"track_key": "d", "choice": {"status": "matched", "plex_track_id": "41"}},
            {"track_key": "b", "choice": {"status": "matched", "plex_track_id": "40"}},
        ])

        by_key = {row["source_track_key"]: row for row in result["tracks"]}
        self.assertEqual("41", by_key["d"]["plex_track_id"])
        self.assertEqual("40", by_key["b"]["plex_track_id"])
        self.assertEqual(0, result["counts"]["review"])

    def test_batch_confirmation_rejects_invalid_choice_without_partial_write(self):
        imported = self.service.import_source(value=QQ_URL)
        rows = self.service.repository.list_matches("default", imported["id"])
        for row in rows:
            if row["source_track_key"] == "b":
                row.update(status="review", candidate_ids=["40"], reason="ambiguous")
        self.service.repository.replace_matches("default", imported["id"], rows, rows[0]["catalog_revision"])
        before = self.service.repository.list_matches("default", imported["id"])

        with self.assertRaisesRegex(ValueError, "不存在"):
            self.service.confirm_many(imported["id"], [
                {"track_key": "d", "choice": {"status": "matched", "plex_track_id": "40"}},
                {"track_key": "b", "choice": {"status": "matched", "plex_track_id": "missing-id"}},
            ])

        self.assertEqual(before, self.service.repository.list_matches("default", imported["id"]))

    def test_batch_confirmation_rejects_duplicates_and_non_review_rows(self):
        imported = self.service.import_source(value=QQ_URL)
        choice = {"track_key": "d", "choice": {"status": "matched", "plex_track_id": "40"}}
        with self.assertRaisesRegex(ValueError, "重复"):
            self.service.confirm_many(imported["id"], [choice, choice])
        with self.assertRaisesRegex(ValueError, "待确认"):
            self.service.confirm_many(imported["id"], [
                {"track_key": "a", "choice": {"status": "matched", "plex_track_id": "10"}},
            ])

    def test_batch_confirmation_rematches_stale_catalog_in_one_write(self):
        imported = self.service.import_source(value=QQ_URL)
        previous_revision = self.service.repository.list_matches("default", imported["id"])[0]["catalog_revision"]
        self.plex.catalog.append(local("50", "新入库", "新歌手"))

        result = self.service.confirm_many(imported["id"], [
            {"track_key": "d", "choice": {"status": "matched", "plex_track_id": "40"}},
        ])
        self.assertEqual("40", next(row for row in result["tracks"] if row["source_track_key"] == "d")["plex_track_id"])
        revisions = {row["catalog_revision"] for row in self.service.repository.list_matches("default", imported["id"])}
        self.assertEqual(1, len(revisions))
        self.assertNotIn(previous_revision, revisions)

    def test_batch_confirmation_accepts_a_track_that_becomes_review_after_catalog_rematch(self):
        imported = self.service.import_source(value=QQ_URL)
        self.plex.catalog = [
            row for row in self.plex.catalog if row["id"] != "10"
        ] + [
            local("12", "已有一", "歌手甲", album="A"),
            local("13", "已有一", "歌手甲", album="B"),
        ]

        result = self.service.confirm_many(imported["id"], [
            {"track_key": "a", "choice": {"status": "matched", "plex_track_id": "12"}},
        ])

        confirmed = next(row for row in result["tracks"] if row["source_track_key"] == "a")
        self.assertEqual("matched", confirmed["status"])
        self.assertEqual("12", confirmed["plex_track_id"])
        self.assertTrue(confirmed["manual"])

    def test_batch_confirmation_stale_catalog_invalid_choice_is_atomic(self):
        imported = self.service.import_source(value=QQ_URL)
        before = self.service.repository.list_matches("default", imported["id"])
        self.plex.catalog.append(local("50", "新入库", "新歌手"))

        with self.assertRaisesRegex(ValueError, "不存在"):
            self.service.confirm_many(imported["id"], [
                {"track_key": "d", "choice": {"status": "matched", "plex_track_id": "removed"}},
            ])

        self.assertEqual(before, self.service.repository.list_matches("default", imported["id"]))

    def test_refresh_adds_tracks_and_large_removal_pauses_without_overwriting(self):
        imported = self.service.import_source(value=QQ_URL)
        old_tracks = self.service.repository.list_tracks("default", imported["id"])
        many = [source_track(f"k{i}", f"歌曲{i}", [f"歌手{i}"], i) for i in range(50)]
        accepted = snapshot(many, revision="many")
        self.providers.results.append(accepted)
        self.service.refresh(imported["id"], force=True)
        self.assertEqual(50, len(self.service.repository.list_tracks("default", imported["id"])))

        reduced = snapshot(many[:39], revision="reduced")
        self.providers.results.append(reduced)
        result = self.service.refresh(imported["id"])
        self.assertEqual("confirmation_required", result["status"])
        self.assertEqual(50, len(self.service.repository.list_tracks("default", imported["id"])))
        self.assertTrue(self.service.repository.get_source("default", imported["id"])["needs_confirmation"])
        self.assertNotEqual(old_tracks, self.service.repository.list_tracks("default", imported["id"]))

    def test_failed_refresh_preserves_prior_counts(self):
        from helper.external_sources import ExternalSourceError

        imported = self.service.import_source(value=QQ_URL)
        before = imported["counts"]
        self.providers.results.append(ExternalSourceError("限流", retryable=True, kind="upstream"))
        with self.assertRaises(ExternalSourceError):
            self.service.refresh(imported["id"], force=True)
        after = self.service.public_source(imported["id"])
        self.assertEqual(before, after["counts"])
        self.assertEqual(1, after["failure_count"])

    def test_plex_failure_during_refresh_preserves_snapshot_matches_and_manual_choice(self):
        imported = self.service.import_source(value=QQ_URL)
        confirmed = self.service.confirm(imported["id"], "d", {"status": "matched", "plex_track_id": "40"})
        before = self.service.public_source(imported["id"])
        changed = snapshot([
            *snapshot()["tracks"],
            source_track("e", "新增歌", ["歌手戊"], 4),
        ], revision="source-r2")
        self.providers.results.append(changed)

        def unavailable(_section):
            raise RuntimeError("Plex unavailable")

        self.plex.tracks = unavailable
        with self.assertRaisesRegex(RuntimeError, "unavailable"):
            self.service.refresh(imported["id"], force=True)

        after = self.service.public_source(imported["id"])
        self.assertEqual(before["revision"], after["revision"])
        self.assertEqual(before["tracks"], after["tracks"])
        manual = next(row for row in after["tracks"] if row["source_track_key"] == "d")
        self.assertTrue(manual["manual"])
        self.assertEqual("40", manual["plex_track_id"])
        self.assertEqual(1, after["failure_count"])
        self.assertEqual("40", next(row for row in confirmed["tracks"] if row["source_track_key"] == "d")["plex_track_id"])

    def test_successful_refresh_preserves_manual_choice_for_unchanged_source_track(self):
        imported = self.service.import_source(value=QQ_URL)
        self.service.confirm(imported["id"], "d", {"status": "matched", "plex_track_id": "40"})
        self.providers.results.append(snapshot(revision="source-r2"))

        self.service.refresh(imported["id"], force=True)

        refreshed = self.service.public_source(imported["id"])
        manual = next(row for row in refreshed["tracks"] if row["source_track_key"] == "d")
        self.assertTrue(manual["manual"])
        self.assertEqual("40", manual["plex_track_id"])

    def test_reimporting_changed_source_cannot_bypass_large_removal_confirmation(self):
        many = [source_track(f"k{i}", f"歌曲{i}", [f"歌手{i}"], i) for i in range(50)]
        self.providers.results = [snapshot(many, revision="many")]
        imported = self.service.import_source(value=QQ_URL)
        self.providers.results.append(snapshot(many[:39], revision="reduced"))

        result = self.service.import_source(value=QQ_URL)

        self.assertEqual(imported["revision"], result["revision"])
        self.assertEqual(50, len(result["tracks"]))
        self.assertTrue(result["needs_confirmation"])

    def test_manual_retry_bypasses_backoff_without_confirming_large_removal(self):
        many = [source_track(f"k{i}", f"歌曲{i}", [f"歌手{i}"], i) for i in range(50)]
        self.providers.results = [snapshot(many, revision="many")]
        imported = self.service.import_source(value=QQ_URL)
        self.service.repository.record_failure("default", imported["id"], "暂时失败", self.now)
        self.providers.results.append(snapshot(many[:39], revision="reduced"))

        result = self.service.refresh(imported["id"], bypass_retry=True)

        self.assertEqual("confirmation_required", result["status"])
        self.assertEqual(50, len(self.service.repository.list_tracks("default", imported["id"])))

    def test_uploaded_file_source_cannot_enable_remote_follow_updates(self):
        file_snapshot = {
            **snapshot(), "provider": "txt", "external_id": "upload:abc", "url": "",
        }
        source = self.service.repository.upsert_source("default", file_snapshot, self.now)

        with self.assertRaisesRegex(ValueError, "本地文件"):
            self.service.set_follow_updates(source["id"], True)

        self.assertFalse(self.service.repository.get_source("default", source["id"])["follow_updates"])

    def test_source_without_match_rows_counts_every_track_as_missing(self):
        source = self.service.repository.upsert_source("default", snapshot(), self.now)

        public = self.service.public_source(source["id"])

        self.assertEqual(4, public["counts"]["missing"])
        self.assertEqual(4, len([row for row in public["tracks"] if row["status"] == "missing"]))


if __name__ == "__main__":
    unittest.main()
