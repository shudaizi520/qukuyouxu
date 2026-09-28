import pytest

from helper.managed_playlist_sync import (
    ManagedPlaylistResult,
    ManagedPlaylistTarget,
    ReconcileConflict,
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
