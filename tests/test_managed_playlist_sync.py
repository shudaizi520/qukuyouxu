import hashlib

import pytest

from helper.managed_playlist_sync import (
    ManagedPlaylistResult,
    ManagedPlaylistTarget,
    ReconcileConflict,
    reconcile_managed_playlist,
    validate_target,
)


def target(**changes):
    values = {
        "category_id": "smart:weekly",
        "title": "每周常听",
        "marker": "[PCH:installation:smart:weekly]",
        "member_ids": ("11", "22"),
        "machine": "plex-machine",
        "scope": "profile:library",
        "description": "由曲库有序管理",
    }
    values.update(changes)
    return ManagedPlaylistTarget(**values)


def test_target_is_immutable_and_accepts_valid_program_state():
    value = validate_target(target())

    assert value.member_ids == ("11", "22")
    with pytest.raises(AttributeError):
        value.title = "人工改名"


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"member_ids": ()}, "不能为空"),
        ({"member_ids": ("11", "11")}, "不能重复"),
        ({"member_ids": ("song-11",)}, "曲目 ID"),
        ({"category_id": ""}, "分类"),
        ({"machine": ""}, "服务器"),
        ({"scope": ""}, "范围"),
        ({"marker": ""}, "标记"),
    ],
)
def test_target_rejects_unsafe_identity_or_members(changes, message):
    with pytest.raises(ValueError, match=message):
        validate_target(target(**changes))


def test_target_normalizes_outer_title_whitespace_only():
    value = validate_target(target(title="  每周常听  "))

    assert value.title == "每周常听"


def test_result_accepts_only_reconciliation_success_statuses():
    for status in ("unchanged", "updated", "created"):
        result = ManagedPlaylistResult(
            status=status,
            playlist={"id": "9", "title": "每周常听", "items": []},
        )
        assert result.status == status

    with pytest.raises(ValueError, match="结果状态"):
        ManagedPlaylistResult(status="uncertain", playlist={"id": "9"})


def test_reconcile_conflict_is_a_distinct_error_type():
    error = ReconcileConflict("同名歌单没有管理标记")

    assert isinstance(error, RuntimeError)


@pytest.mark.parametrize(
    ("rows", "expected"),
    [
        ([], []),
        ([{"ratingKey": "1", "summary": "owner-marker\ntext"}], ["1"]),
        ([
            {"ratingKey": "1", "summary": "owner-marker\ntext"},
            {"ratingKey": "2", "summary": "text\nowner-marker"},
            {"ratingKey": "3", "summary": "someone-else"},
        ], ["1", "2"]),
    ],
)
def test_plex_owned_playlist_lookup_returns_all_exact_marker_matches(rows, expected):
    from helper.clients import PlexClient

    client = object.__new__(PlexClient)
    client.playlists = lambda: rows

    assert [row["ratingKey"] for row in client.owned_playlists("owner-marker")] == expected


def test_plex_owned_playlist_lookup_rejects_empty_or_overlong_markers():
    from helper.clients import PlexClient

    client = object.__new__(PlexClient)
    client.playlists = lambda: []
    with pytest.raises(ValueError, match="管理标记"):
        client.owned_playlists("")
    with pytest.raises(ValueError, match="管理标记"):
        client.owned_playlists("x" * 513)


class MemoryPlex:
    def __init__(self, states=()):
        self.states = {str(row["id"]): self._copy(row) for row in states}
        self.calls = []
        self.next_id = 100
        self.read_error = None
        self.lose_create_response = False
        self.second_read_state = None
        self.read_count = 0

    @staticmethod
    def _copy(row):
        return {**row, "items": [dict(item) for item in row.get("items", [])]}

    def playlist_state(self, playlist_id):
        from helper.clients import PlexNotFound

        self.read_count += 1
        if self.read_error:
            raise self.read_error
        if self.second_read_state is not None and self.read_count == 2:
            row = self._copy(self.second_read_state)
            self.states[str(row["id"])] = row
        if str(playlist_id) not in self.states:
            raise PlexNotFound("missing")
        return self._copy(self.states[str(playlist_id)])

    def playlists(self):
        return [
            {"ratingKey": row["id"], "title": row["title"], "summary": row.get("summary", "")}
            for row in self.states.values()
        ]

    def owned_playlists(self, marker):
        return [row for row in self.playlists() if marker in row["summary"].splitlines()]

    def create(self, title, ids, marker, description=None):
        self.calls.append(("create", title, tuple(ids)))
        playlist_id = str(self.next_id)
        self.next_id += 1
        self.states[playlist_id] = {
            "id": playlist_id,
            "title": title,
            "summary": marker + "\n" + str(description or ""),
            "items": [
                {"id": str(track_id), "item_id": str(index + 1)}
                for index, track_id in enumerate(ids)
            ],
        }
        if self.lose_create_response:
            from helper.clients import PlexError
            raise PlexError("connection lost after create")
        return self.playlist_state(playlist_id)

    def rename(self, playlist_id, title):
        self.calls.append(("rename", str(playlist_id), title))
        self.states[str(playlist_id)]["title"] = title

    def update_playlist_summary(self, playlist_id, summary):
        self.calls.append(("summary", str(playlist_id), summary))
        self.states[str(playlist_id)]["summary"] = summary

    def remove_items(self, playlist_id, item_ids):
        self.calls.append(("remove", str(playlist_id), tuple(item_ids)))
        unwanted = {str(value) for value in item_ids}
        row = self.states[str(playlist_id)]
        row["items"] = [item for item in row["items"] if item["item_id"] not in unwanted]

    def append(self, playlist_id, ids):
        self.calls.append(("append", str(playlist_id), tuple(ids)))
        row = self.states[str(playlist_id)]
        start = max([int(item["item_id"]) for item in row["items"]] or [0]) + 1
        row["items"].extend(
            {"id": str(track_id), "item_id": str(start + index)}
            for index, track_id in enumerate(ids)
        )

    def move_item(self, playlist_id, item_id, after=None):
        self.calls.append(("move", str(playlist_id), str(item_id), after))
        items = self.states[str(playlist_id)]["items"]
        moving = next(item for item in items if item["item_id"] == str(item_id))
        items.remove(moving)
        index = -1 if after is None else next(i for i, item in enumerate(items) if item["item_id"] == str(after))
        items.insert(index + 1, moving)


def test_ordered_target_waits_for_membership_after_append(monkeypatch):
    class StaleReadPlex(MemoryPlex):
        stale = None

        def append(self, playlist_id, ids):
            self.stale = self.playlist_state(playlist_id)
            super().append(playlist_id, ids)

        def playlist_state(self, playlist_id):
            if self.stale:
                row, self.stale = self.stale, None
                return row
            return super().playlist_state(playlist_id)

    monkeypatch.setattr("helper.managed_playlist_sync.time.sleep", lambda _: None)
    plex = StaleReadPlex([state(ids=("11", "22"))])
    result = reconcile_managed_playlist(
        plex, target(member_ids=("11", "33", "22"), ordered=True), managed(),
    )
    assert result.status == "updated"
    assert [item["id"] for item in result.playlist["items"]] == ["11", "33", "22"]


def test_new_ordered_playlist_identity_survives_partial_create_then_read_timeout():
    from helper.clients import PlexError

    class PartialCreatePlex(MemoryPlex):
        unreadable = False

        def create(self, *args, **kwargs):
            row = super().create(*args, **kwargs)
            self.unreadable = True
            row["items"] = row["items"][:1]
            return row

        def playlist_state(self, playlist_id):
            if self.unreadable:
                raise PlexError("validation read timeout after creation")
            return super().playlist_state(playlist_id)

    plex = PartialCreatePlex()
    desired = target(ordered=True)
    saved = []

    def checkpoint(row):
        saved.append({"id": row["id"], "title": row["title"], "machine": desired.machine, "scope": desired.scope})

    with pytest.raises(PlexError):
        reconcile_managed_playlist(plex, desired, None, adopt_existing=False, checkpoint=checkpoint)
    assert saved
    with pytest.raises(PlexError):
        reconcile_managed_playlist(plex, desired, saved[-1], adopt_existing=True, checkpoint=checkpoint)
    assert len(plex.states) == 1
    assert len([call for call in plex.calls if call[0] == "create"]) == 1


def scoped_summary(scope="profile:library", description="由曲库有序管理"):
    digest = hashlib.sha256(f"plex-machine\0{scope}".encode()).hexdigest()
    return "\n".join(filter(None, (
        "[PCH:installation:smart:weekly]",
        f"[QKYX-SCOPE:{digest}]",
        description,
    )))


def state(playlist_id="9", title="每周常听", summary=None, ids=("11", "22")):
    return {
        "id": str(playlist_id),
        "title": title,
        "summary": scoped_summary() if summary is None else summary,
        "items": [
            {"id": str(track_id), "item_id": str(index + 1)}
            for index, track_id in enumerate(ids)
        ],
    }


def managed(playlist_id="9"):
    return {"id": str(playlist_id), "machine": "plex-machine", "scope": "profile:library"}


def test_reconcile_removes_manual_additions_and_restores_missing_members():
    plex = MemoryPlex([state(ids=("11", "33"))])

    result = reconcile_managed_playlist(plex, target(), managed())

    assert result.status == "updated"
    assert {item["id"] for item in result.playlist["items"]} == {"11", "22"}
    assert ("remove", "9", ("2",)) in plex.calls
    assert ("append", "9", ("22",)) in plex.calls


def test_reconcile_restores_title_and_program_summary():
    plex = MemoryPlex([state(title="人工名称", summary="人工说明")])

    result = reconcile_managed_playlist(plex, target(), managed())

    assert result.status == "updated"
    assert result.playlist["title"] == "每周常听"
    assert result.playlist["summary"] == scoped_summary()


def test_reconcile_recreates_only_after_confirmed_not_found():
    plex = MemoryPlex()

    result = reconcile_managed_playlist(plex, target(), managed())

    assert result.status == "created"
    assert result.playlist["id"] == "100"
    assert [call[0] for call in plex.calls] == ["create"]


def test_reconcile_propagates_transient_read_error_without_creating():
    from helper.clients import PlexError

    plex = MemoryPlex()
    plex.read_error = PlexError("timeout")
    with pytest.raises(PlexError, match="timeout"):
        reconcile_managed_playlist(plex, target(), managed())
    assert plex.calls == []


def test_reconcile_refuses_same_title_without_ownership_marker():
    plex = MemoryPlex([state(playlist_id="44", summary="个人歌单")])

    with pytest.raises(ReconcileConflict, match="同名"):
        reconcile_managed_playlist(plex, target(), managed("9"))
    assert plex.calls == []


def test_reconcile_refuses_multiple_owned_candidates():
    plex = MemoryPlex([state("44"), state("45")])

    with pytest.raises(ReconcileConflict, match="多个"):
        reconcile_managed_playlist(plex, target(), managed("9"))
    assert plex.calls == []


def test_reconcile_discovers_created_playlist_after_response_is_lost():
    plex = MemoryPlex()
    plex.lose_create_response = True

    result = reconcile_managed_playlist(plex, target(), None)

    assert result.status == "created"
    assert result.playlist["id"] == "100"
    assert [call[0] for call in plex.calls] == ["create"]


def test_reconcile_can_create_a_separate_copy_beside_an_owned_sibling():
    sibling = state(
        "44",
        summary="[PCH:installation:external:other]\n" + scoped_summary().splitlines()[1],
    )
    plex = MemoryPlex([sibling])

    result = reconcile_managed_playlist(plex, target(), None, adopt_existing=False)

    assert result.status == "created"
    assert result.playlist["id"] == "100"
    assert set(plex.states) == {"44", "100"}


@pytest.mark.parametrize(
    "summary",
    [
        "[PCH:installation:smart:weekly]",
        "[PCH:installation:external:other]\n[QKYX-SCOPE:]",
        "[PCH:installation:external:other]\n[QKYX-SCOPE:abc123]",
        "[PCH:installation:external:other]\n[QKYX-SCOPE:" + "g" * 64 + "]",
        "[PCH:installation:]\n" + scoped_summary().splitlines()[1],
        "[PCH:installation:x]junk]\n" + scoped_summary().splitlines()[1],
        "[PCH:other-installation:external:other]\n" + scoped_summary().splitlines()[1],
    ],
)
def test_reconcile_refuses_same_title_with_incomplete_or_foreign_managed_marker(summary):
    plex = MemoryPlex([state("44", summary=summary)])

    with pytest.raises(ReconcileConflict, match="同名"):
        reconcile_managed_playlist(plex, target(), None, adopt_existing=False)

    assert plex.calls == []


def test_missing_record_never_adopts_same_marker_from_another_scope():
    sibling = state("44", summary=scoped_summary("other-profile:other-library"))
    plex = MemoryPlex([sibling])

    result = reconcile_managed_playlist(plex, target(), managed("9"))

    assert result.status == "created"
    assert result.playlist["id"] == "100"
    assert plex.states["44"] == sibling


def test_known_record_never_overwrites_a_playlist_marked_for_another_scope():
    sibling = state("44", summary=scoped_summary("other-profile:other-library"))
    plex = MemoryPlex([sibling])

    with pytest.raises(ReconcileConflict, match="其他档案或曲库"):
        reconcile_managed_playlist(plex, target(), managed("44"))

    assert plex.calls == []
    assert plex.states["44"] == sibling


def test_lost_create_response_discovers_only_the_new_copy_beside_a_sibling():
    plex = MemoryPlex([state("44")])
    plex.lose_create_response = True

    result = reconcile_managed_playlist(plex, target(), None, adopt_existing=False)

    assert result.status == "created"
    assert result.playlist["id"] == "100"
    assert [call[0] for call in plex.calls] == ["create"]


def test_reconcile_uses_latest_state_when_members_change_before_write():
    plex = MemoryPlex([state(ids=("11", "22"))])
    plex.second_read_state = state(ids=("11", "22", "33"))

    result = reconcile_managed_playlist(plex, target(), managed())

    assert result.status == "updated"
    assert {item["id"] for item in result.playlist["items"]} == {"11", "22"}
    assert ("remove", "9", ("3",)) in plex.calls


def test_reconcile_reports_only_members_added_after_latest_pre_write_read():
    plex = MemoryPlex([state(ids=("11",))])
    # Another actor adds 22 after the first lookup but before reconciliation.
    plex.second_read_state = state(ids=("11", "22"))

    result = reconcile_managed_playlist(
        plex,
        target(member_ids=("11", "22", "33")),
        managed(),
    )

    assert result.added_member_ids == ("33",)
    assert ("append", "9", ("33",)) in plex.calls


def test_reconcile_removes_duplicate_current_occurrences():
    plex = MemoryPlex([state(ids=("11", "11", "22"))])

    result = reconcile_managed_playlist(plex, target(), managed())

    assert result.status == "updated"
    assert [item["id"] for item in result.playlist["items"]].count("11") == 1


def test_reconcile_repeated_exact_target_is_unchanged_without_mutations():
    plex = MemoryPlex([state()])

    first = reconcile_managed_playlist(plex, target(), managed())
    second = reconcile_managed_playlist(plex, target(), managed())

    assert first.status == second.status == "unchanged"
    assert plex.calls == []


def test_reconcile_rejects_managed_record_from_another_scope():
    plex = MemoryPlex([state()])

    with pytest.raises(ReconcileConflict, match="范围"):
        reconcile_managed_playlist(
            plex,
            target(),
            {"id": "9", "machine": "plex-machine", "scope": "someone-else"},
        )
    assert plex.calls == []
