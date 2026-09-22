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
