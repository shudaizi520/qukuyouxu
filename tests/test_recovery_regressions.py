import tempfile
import unittest
from pathlib import Path


class _Plex:
    def __init__(self, state):
        self.state = state

    def identity(self):
        return {"machine": "machine-a"}

    def playlist_state(self, _playlist_id):
        if self.state is None:
            from helper.clients import PlexNotFound
            raise PlexNotFound("Plex 中没有这个项目")
        return {**self.state, "items": [dict(row) for row in self.state["items"]]}

    def tracks(self, _section):
        return [{"id": row["id"], "available": True} for row in self.state["items"]]

    def playlists(self):
        return [{"ratingKey": self.state["id"], "title": self.state["title"]}]

    def create(self, title, ids, marker, description=None):
        self.state = {
            "id": "restored-1", "title": title,
            "summary": marker + "\n" + (description or ""),
            "items": [{"id": str(value), "item_id": str(index + 1)} for index, value in enumerate(ids)],
        }
        return self.playlist_state(self.state["id"])

    def delete_playlist(self, _playlist_id):
        self.state = None

    def append(self, _playlist_id, ids):
        next_id = len(self.state["items"]) + 1
        self.state["items"].extend(
            {"id": str(track_id), "item_id": str(next_id + offset)}
            for offset, track_id in enumerate(ids)
        )

    def remove_items(self, _playlist_id, item_ids):
        wanted = set(map(str, item_ids))
        self.state["items"] = [row for row in self.state["items"] if row["item_id"] not in wanted]


class RecoveryRegressionTests(unittest.TestCase):
    def managed_engine(self, root, current_ids=("1", "3")):
        from helper.engine import Engine, fingerprint
        from helper.store import Store

        store = Store(Path(root))
        settings = store.get("settings")
        settings.update(plex_url="http://plex:32400", plex_token="secret", section="15")
        store.set("settings", settings)
        engine = Engine(store)
        marker = engine.marker("base:djmix")
        expected = {
            "id": "p1", "title": "DJ混音", "summary": marker,
            "items": [
                {"id": "1", "item_id": "a"},
                {"id": "2", "item_id": "b"},
                {"id": "3", "item_id": "c"},
            ],
        }
        expected_by_id = {row["id"]: row for row in expected["items"]}
        current = {
            **expected,
            "items": [
                dict(expected_by_id.get(value, {"id": value, "item_id": "external-" + value}))
                for value in current_ids
            ],
        }
        plex = _Plex(current)
        engine.plex_factory = lambda _cfg: plex
        snapshot = {
            "id": "base-write-1", "kind": "base", "category_id": "base:djmix",
            "title": "DJ混音", "status": "applied", "created_at": 1,
            "before": None, "after": expected, "marker": marker,
        }
        engine._save_snapshot(snapshot)
        store.set("managed", {"base:djmix": {
            "id": "p1", "title": "DJ混音", "fingerprint": fingerprint(expected),
            "snapshot_id": snapshot["id"],
        }})
        store.set("catalog", [
            {"id": value, "title": "歌曲" + value, "available": True}
            for value in ("1", "2", "3", "4")
        ])
        return store, engine, plex, expected

    def test_accepting_external_removal_records_a_stable_manual_exclusion(self):
        from helper.daily_mix_v036 import managed_playlist_rows, reconcile_managed_playlist
        from helper.engine import fingerprint

        with tempfile.TemporaryDirectory() as root:
            store, engine, plex, _expected = self.managed_engine(root)
            row = managed_playlist_rows(engine)[0]
            self.assertTrue(row["can_accept_changes"])
            self.assertTrue(row["can_restore_changes"])

            result = reconcile_managed_playlist(engine, "base:djmix", "accept")

            self.assertIn("保留", result["message"])
            self.assertEqual(["2"], store.get("playlist_manual_edits")["category:base:djmix"]["exclude"])
            self.assertEqual(fingerprint(plex.playlist_state("p1")), store.get("managed")["base:djmix"]["fingerprint"])
            self.assertEqual("正常", managed_playlist_rows(engine)[0]["status"])

    def test_marked_playlist_with_manually_changed_members_can_still_be_explicitly_deleted(self):
        from helper.daily_mix_v036 import managed_playlist_rows, remove_managed_playlist

        with tempfile.TemporaryDirectory() as root:
            store, engine, _plex, _expected = self.managed_engine(root, current_ids=("1", "3"))
            row = managed_playlist_rows(engine)[0]
            self.assertTrue(row["safe_to_remove"])
            self.assertIn("手动修改", row["status"])

            result = remove_managed_playlist(engine, "base:djmix", "DJ混音")

            self.assertIn("音乐文件未删除", result["message"])
            self.assertEqual({}, store.get("managed"))
            snapshot = next(item for item in store.get("snapshots") if item["id"] == result["snapshot_id"])
            self.assertEqual(["1", "3"], [item["id"] for item in snapshot["before"]["items"]])

    def test_explicit_delete_still_rejects_missing_marker_protected_title_and_scope_mismatch(self):
        from helper.daily_mix_v036 import remove_managed_playlist
        from helper.engine import SafetyError

        cases = ("marker", "protected", "scope")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as root:
                store, engine, plex, _expected = self.managed_engine(root, current_ids=("1", "3"))
                if case == "marker":
                    plex.state["summary"] = ""
                    message = "标记"
                elif case == "protected":
                    plex.state["title"] = "我喜欢"
                    message = "永久保护"
                else:
                    managed = store.get("managed")
                    managed["base:djmix"]["scope"] = "another-library"
                    store.set("managed", managed)
                    message = "资料库"
                with self.assertRaisesRegex(SafetyError, message):
                    remove_managed_playlist(engine, "base:djmix", plex.state["title"])
                self.assertIn("base:djmix", store.get("managed"))

    def test_failed_plex_delete_keeps_the_local_managed_record(self):
        from helper.daily_mix_v036 import remove_managed_playlist
        from helper.engine import SafetyError

        with tempfile.TemporaryDirectory() as root:
            store, engine, plex, _expected = self.managed_engine(root, current_ids=("1", "3"))
            def fail_delete(_playlist_id):
                raise RuntimeError("Plex refused")
            plex.delete_playlist = fail_delete

            with self.assertRaisesRegex(SafetyError, "需要人工核对"):
                remove_managed_playlist(engine, "base:djmix", "DJ混音")

            self.assertIn("base:djmix", store.get("managed"))
            self.assertEqual("uncertain", store.get("snapshots")[-1]["status"])

    def test_restoring_external_removal_readds_missing_members_and_updates_fingerprint(self):
        from helper.daily_mix_v036 import reconcile_managed_playlist
        from helper.engine import fingerprint

        with tempfile.TemporaryDirectory() as root:
            store, engine, plex, _expected = self.managed_engine(root)

            result = reconcile_managed_playlist(engine, "base:djmix", "restore")

            self.assertIn("恢复", result["message"])
            self.assertEqual({"1", "2", "3"}, {row["id"] for row in plex.state["items"]})
            self.assertEqual(fingerprint(plex.playlist_state("p1")), store.get("managed")["base:djmix"]["fingerprint"])

    def test_reconciliation_rejects_external_additions_and_reordering(self):
        from helper.daily_mix_v036 import reconcile_managed_playlist
        from helper.engine import SafetyError

        for current_ids in (("1", "2", "3", "4"), ("2", "1", "3")):
            with self.subTest(current_ids=current_ids), tempfile.TemporaryDirectory() as root:
                _store, engine, _plex, _expected = self.managed_engine(root, current_ids=current_ids)
                with self.assertRaisesRegex(SafetyError, "只能处理.*删除"):
                    reconcile_managed_playlist(engine, "base:djmix", "accept")

    def test_base_playlist_can_stop_maintenance_without_a_theme_source(self):
        from helper.daily_mix_v036 import disable_managed_playlist, managed_playlist_rows

        with tempfile.TemporaryDirectory() as root:
            store, engine, _plex, _expected = self.managed_engine(root, current_ids=("1", "2", "3"))

            result = disable_managed_playlist(engine, "base:djmix")

            self.assertIn("停止维护", result["message"])
            self.assertEqual(["base:djmix"], store.get("managed_disabled_categories"))
            self.assertFalse(managed_playlist_rows(engine)[0]["enabled"])

    def test_stopped_base_playlist_can_resume_maintenance(self):
        from helper.daily_mix_v036 import (
            disable_managed_playlist, enable_managed_playlist, managed_playlist_rows,
        )

        with tempfile.TemporaryDirectory() as root:
            store, engine, _plex, _expected = self.managed_engine(root, current_ids=("1", "2", "3"))
            disable_managed_playlist(engine, "base:djmix")

            result = enable_managed_playlist(engine, "base:djmix")

            self.assertIn("恢复维护", result["message"])
            self.assertEqual([], store.get("managed_disabled_categories"))
            self.assertTrue(managed_playlist_rows(engine)[0]["enabled"])

    def test_daily_restore_completes_when_plex_preserves_membership_in_its_own_order(self):
        from helper.engine import Engine, fingerprint
        from helper.store import Store

        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root))
            settings = store.get("settings")
            settings.update(plex_url="http://plex:32400", plex_token="secret", section="15")
            store.set("settings", settings)
            marker = f"[PCH:{store.get('installation_id')}:daily]"
            current = {
                "id": "daily-1", "title": "每日推荐", "summary": marker,
                "items": [{"id": "1", "item_id": "a"}, {"id": "2", "item_id": "b"}],
            }
            plex = _Plex(current)
            engine = Engine(store, plex_factory=lambda _cfg: plex)
            before = {**current, "items": list(reversed(current["items"]))}
            snapshot = {
                "id": "daily-order-restore", "kind": "daily", "category_id": "daily",
                "title": "每日推荐", "status": "applied", "created_at": 1,
                "before": before, "after": current, "machine": "machine-a",
                "scope": engine.daily_scope(), "plan_id": "plan-1",
                "before_daily_record": {"id": "daily-1", "title": "每日推荐"},
            }
            engine._save_snapshot(snapshot)
            store.set("daily_managed", {
                "id": "daily-1", "title": "每日推荐", "snapshot_id": snapshot["id"],
                "fingerprint": fingerprint(current), "machine": "machine-a",
                "scope": engine.daily_scope(),
            })

            result = engine.restore(snapshot["id"])

            self.assertIn("已恢复", result["message"])
            saved = next(row for row in store.get("snapshots") if row["id"] == snapshot["id"])
            self.assertEqual("restored", saved["status"])

    def test_removed_category_snapshot_records_library_scope(self):
        from helper.daily_mix_v036 import remove_managed_playlist
        from helper.engine import Engine, fingerprint
        from helper.store import Store

        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root))
            settings = store.get("settings")
            settings.update(plex_url="http://plex:32400", plex_token="secret", section="15")
            store.set("settings", settings)
            state = {"id": "p1", "title": "网络热歌", "summary": "", "items": [{"id": "1", "item_id": "a"}]}
            plex = _Plex(state)
            engine = Engine(store, plex_factory=lambda _cfg: plex)
            state["summary"] = engine.marker("hot")
            store.set("managed", {"hot": {"id": "p1", "title": state["title"], "fingerprint": fingerprint(state)}})
            store.set("sources", [{"id": "hot", "enabled": True, "approved": True}])

            result = remove_managed_playlist(engine, "hot", state["title"])

            snapshot = next(row for row in store.get("snapshots") if row["id"] == result["snapshot_id"])
            self.assertEqual(engine.daily_scope(), snapshot["scope"])
            self.assertEqual(engine.daily_scope(), store.get("retired_managed")["hot"]["scope"])

    def test_removed_category_restore_refuses_a_different_library_and_existing_title(self):
        from helper.daily_mix_v036 import restore_removed_playlist
        from helper.engine import Engine, SafetyError
        from helper.store import Store

        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root))
            settings = store.get("settings")
            settings.update(plex_url="http://plex:32400", plex_token="secret", section="15")
            store.set("settings", settings)
            existing = {"id": "other", "title": "网络热歌", "summary": "", "items": [{"id": "1", "item_id": "a"}]}
            plex = _Plex(existing)
            engine = Engine(store, plex_factory=lambda _cfg: plex)
            snapshot = {
                "id": "removed-1", "kind": "managed_remove", "category_id": "hot",
                "title": "网络热歌", "status": "applied", "created_at": 1,
                "before": {"id": "p1", "title": "网络热歌", "summary": engine.marker("hot"),
                           "items": [{"id": "1", "item_id": "a"}]},
                "marker": engine.marker("hot"), "machine": "machine-a", "scope": "another-library",
            }
            engine._save_snapshot(snapshot)
            store.set("retired_managed", {"hot": {"snapshot_id": snapshot["id"], "scope": "another-library"}})

            with self.assertRaisesRegex(SafetyError, "资料库"):
                restore_removed_playlist(engine, snapshot["id"])

            snapshot["scope"] = engine.daily_scope()
            engine._save_snapshot(snapshot)
            store.set("retired_managed", {"hot": {"snapshot_id": snapshot["id"], "scope": engine.daily_scope()}})
            with self.assertRaisesRegex(SafetyError, "同名"):
                restore_removed_playlist(engine, snapshot["id"])
            self.assertEqual("applied", next(row for row in store.get("snapshots") if row["id"] == snapshot["id"])["status"])

    def test_legacy_removed_category_without_library_identity_fails_closed_with_a_clear_reason(self):
        from helper.daily_mix_v036 import restore_removed_playlist
        from helper.engine import Engine, SafetyError
        from helper.store import Store

        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root))
            engine = Engine(store)
            snapshot = {
                "id": "legacy-removed", "kind": "managed_remove", "category_id": "hot",
                "title": "网络热歌", "status": "applied", "created_at": 1,
                "before": {"id": "p1", "title": "网络热歌", "summary": engine.marker("hot"), "items": []},
                "marker": engine.marker("hot"), "machine": "machine-a",
            }
            engine._save_snapshot(snapshot)
            store.set("retired_managed", {"hot": {"snapshot_id": snapshot["id"], "title": snapshot["title"]}})

            with self.assertRaisesRegex(SafetyError, "旧版本.*资料库身份"):
                restore_removed_playlist(engine, snapshot["id"])


if __name__ == "__main__":
    unittest.main()
