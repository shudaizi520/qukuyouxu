import copy
import unittest


INSTALL = "install-abc"
SOURCE = {"id": "x-source", "title": "百万收藏", "revision": "rev-1"}


def ids(state):
    return [row["id"] for row in state["items"]]


class FakePlex:
    def __init__(self, states=None, playlists=None, partial_append=False, ignore_moves=False):
        self.states = {str(row["id"]): copy.deepcopy(row) for row in (states or [])}
        self.extra_playlists = copy.deepcopy(playlists or [])
        self.partial_append = partial_append
        self.ignore_moves = ignore_moves
        self.created = []
        self.mutations = []
        self.next_id = 100
        self.next_item = 1000

    def _items(self, track_ids):
        result = []
        for track_id in track_ids:
            self.next_item += 1
            result.append({"id": str(track_id), "item_id": str(self.next_item)})
        return result

    def playlists(self):
        rows = [{"ratingKey": row["id"], "title": row["title"], "summary": row.get("summary", "")} for row in self.states.values()]
        return rows + copy.deepcopy(self.extra_playlists)

    def playlist_state(self, playlist_id):
        state = self.states.get(str(playlist_id))
        if state is None:
            raise ValueError("missing playlist")
        return copy.deepcopy(state)

    def create(self, title, track_ids, marker, description=None):
        self.next_id += 1
        playlist_id = str(self.next_id)
        state = {
            "id": playlist_id, "title": title,
            "summary": marker + "\n" + (description or ""),
            "items": self._items(track_ids),
        }
        self.states[playlist_id] = state
        self.created.append((title, list(track_ids), marker))
        self.mutations.append(("create", playlist_id))
        return self.playlist_state(playlist_id)

    def append(self, playlist_id, track_ids):
        track_ids = list(track_ids)
        if self.partial_append and len(track_ids) > 1:
            track_ids = track_ids[:1]
            self.partial_append = False
        self.states[str(playlist_id)]["items"].extend(self._items(track_ids))
        self.mutations.append(("append", list(track_ids)))

    def remove_items(self, playlist_id, item_ids):
        item_ids = set(map(str, item_ids))
        state = self.states[str(playlist_id)]
        state["items"] = [row for row in state["items"] if row["item_id"] not in item_ids]
        self.mutations.append(("remove", sorted(item_ids)))

    def move_item(self, playlist_id, item_id, after=None):
        self.mutations.append(("move", str(item_id), None if after is None else str(after)))
        if self.ignore_moves:
            return
        items = self.states[str(playlist_id)]["items"]
        moving = next(row for row in items if row["item_id"] == str(item_id))
        items.remove(moving)
        if after is None:
            items.insert(0, moving)
        else:
            index = next(i for i, row in enumerate(items) if row["item_id"] == str(after))
            items.insert(index + 1, moving)

    def delete_playlist(self, playlist_id):
        self.mutations.append(("delete", str(playlist_id)))
        del self.states[str(playlist_id)]


def owned_state(playlist_id="77", source=SOURCE, track_ids=None, title=None):
    from helper.external_playlist_sync import external_marker

    track_ids = track_ids or ["1", "2"]
    return {
        "id": str(playlist_id), "title": title or source["title"],
        "summary": external_marker(INSTALL, source["id"]) + "\n由曲库有序管理",
        "items": [{"id": value, "item_id": f"i-{index}"} for index, value in enumerate(track_ids)],
    }


def managed_for(state, **extra):
    from helper.external_playlist_sync import playlist_fingerprint

    return {"id": state["id"], "title": state["title"], "fingerprint": playlist_fingerprint(state), **extra}


class ExternalPlaylistSyncV130Tests(unittest.TestCase):
    def test_same_name_without_marker_is_never_adopted(self):
        from helper.engine import SafetyError
        from helper.external_playlist_sync import create_or_reconcile_external_playlist

        plex = FakePlex(playlists=[{"ratingKey": "9", "title": "百万收藏", "summary": ""}])
        with self.assertRaisesRegex(SafetyError, "同名"):
            create_or_reconcile_external_playlist(plex, INSTALL, SOURCE, None, ["1", "2"])
        self.assertEqual([], plex.created)

    def test_retry_after_lost_create_response_finds_exact_owned_marker(self):
        from helper.external_playlist_sync import create_or_reconcile_external_playlist

        state = owned_state()
        plex = FakePlex(states=[state])
        after, managed = create_or_reconcile_external_playlist(plex, INSTALL, SOURCE, None, ["1", "2"])
        self.assertEqual("77", managed["id"])
        self.assertEqual([], plex.created)
        self.assertEqual(["1", "2"], ids(after))

    def test_newly_matched_track_is_moved_to_source_position(self):
        from helper.external_playlist_sync import create_or_reconcile_external_playlist

        state = owned_state(track_ids=["1", "3"])
        plex = FakePlex(states=[state])
        after, managed = create_or_reconcile_external_playlist(
            plex, INSTALL, SOURCE, managed_for(state), ["1", "2", "3"]
        )
        self.assertEqual(["1", "2", "3"], ids(after))
        self.assertFalse(managed["order_attention"])

    def test_empty_duplicate_or_invalid_desired_ids_never_mutate(self):
        from helper.engine import SafetyError
        from helper.external_playlist_sync import create_or_reconcile_external_playlist

        for desired in ([], ["1", "1"], ["abc"]):
            plex = FakePlex()
            with self.subTest(desired=desired), self.assertRaises(SafetyError):
                create_or_reconcile_external_playlist(plex, INSTALL, SOURCE, None, desired)
            self.assertEqual([], plex.mutations)

    def test_changed_title_summary_or_membership_blocks_writes(self):
        from helper.engine import SafetyError
        from helper.external_playlist_sync import create_or_reconcile_external_playlist

        original = owned_state()
        managed = managed_for(original)
        for change in ("title", "summary", "membership"):
            live = copy.deepcopy(original)
            if change == "title":
                live["title"] = "手工改名"
            elif change == "summary":
                live["summary"] += "手工改摘要"
            else:
                live["items"].append({"id": "9", "item_id": "manual"})
            plex = FakePlex(states=[live])
            with self.subTest(change=change), self.assertRaisesRegex(SafetyError, "修改"):
                create_or_reconcile_external_playlist(plex, INSTALL, SOURCE, managed, ["1", "2", "3"])
            self.assertEqual([], plex.mutations)

    def test_partial_append_is_reconciled_without_duplicate_creation(self):
        from helper.external_playlist_sync import create_or_reconcile_external_playlist

        state = owned_state(track_ids=["1"])
        plex = FakePlex(states=[state], partial_append=True)
        after, _ = create_or_reconcile_external_playlist(
            plex, INSTALL, SOURCE, managed_for(state), ["1", "2", "3"]
        )
        self.assertEqual(["1", "2", "3"], ids(after))
        self.assertEqual([], plex.created)
        self.assertEqual(2, len([row for row in plex.mutations if row[0] == "append"]))

    def test_ignored_move_keeps_membership_and_returns_attention(self):
        from helper.external_playlist_sync import create_or_reconcile_external_playlist

        state = owned_state(track_ids=["2", "1"])
        plex = FakePlex(states=[state], ignore_moves=True)
        after, managed = create_or_reconcile_external_playlist(
            plex, INSTALL, SOURCE, managed_for(state), ["1", "2"]
        )
        self.assertEqual({"1", "2"}, set(ids(after)))
        self.assertTrue(managed["order_attention"])

    def test_delete_requires_matching_id_title_marker_and_unchanged_fingerprint(self):
        from helper.engine import SafetyError
        from helper.external_playlist_sync import delete_owned_external_playlist

        original = owned_state()
        managed = managed_for(original)
        for changed, confirm in (
            ({**original, "title": "其它"}, "百万收藏"),
            ({**original, "summary": "no marker"}, "百万收藏"),
            (original, "错误名称"),
        ):
            plex = FakePlex(states=[changed])
            with self.subTest(changed=changed, confirm=confirm), self.assertRaises(SafetyError):
                delete_owned_external_playlist(plex, INSTALL, SOURCE["id"], managed, confirm)
            self.assertEqual([], plex.mutations)

        plex = FakePlex(states=[original])
        result = delete_owned_external_playlist(plex, INSTALL, SOURCE["id"], managed, "百万收藏")
        self.assertEqual({"removed": "77"}, result)
        self.assertNotIn("77", plex.states)


if __name__ == "__main__":
    unittest.main()
