"""Shared contracts for converging application-owned Plex playlists."""
from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import time
from typing import Mapping


SUCCESS_STATUSES = frozenset({"unchanged", "updated", "created"})


class ReconcileConflict(RuntimeError):
    """The remote state cannot be safely attributed to this installation."""


@dataclass(frozen=True)
class ManagedPlaylistTarget:
    category_id: str
    title: str
    marker: str
    member_ids: tuple[str, ...]
    machine: str
    scope: str
    description: str = ""


@dataclass(frozen=True)
class ManagedPlaylistResult:
    status: str
    playlist: Mapping[str, object]

    def __post_init__(self):
        if self.status not in SUCCESS_STATUSES:
            raise ValueError("托管歌单结果状态无效")


def validate_target(target: ManagedPlaylistTarget) -> ManagedPlaylistTarget:
    """Validate and normalize a safe, non-empty desired playlist state."""
    title = str(target.title or "").strip()
    category_id = str(target.category_id or "").strip()
    marker = str(target.marker or "").strip()
    machine = str(target.machine or "").strip()
    scope = str(target.scope or "").strip()
    member_ids = tuple(str(value or "").strip() for value in target.member_ids)
    if not category_id:
        raise ValueError("托管歌单分类不能为空")
    if not title:
        raise ValueError("托管歌单名称不能为空")
    if not marker:
        raise ValueError("托管歌单管理标记不能为空")
    if not machine:
        raise ValueError("Plex 服务器身份不能为空")
    if not scope:
        raise ValueError("托管歌单范围不能为空")
    if not member_ids:
        raise ValueError("托管歌单成员不能为空")
    if len(set(member_ids)) != len(member_ids):
        raise ValueError("托管歌单成员不能重复")
    if any(not value.isdigit() for value in member_ids):
        raise ValueError("托管歌单曲目 ID 必须是数字")
    return replace(
        target,
        category_id=category_id,
        title=title,
        marker=marker,
        member_ids=member_ids,
        machine=machine,
        scope=scope,
    )


def _playlist_id(row: Mapping[str, object]) -> str:
    return str(row.get("id") or row.get("ratingKey") or "")


def _member_ids(state: Mapping[str, object]) -> list[str]:
    return [str(row.get("id") or "") for row in state.get("items", [])]


def _expected_summary(target: ManagedPlaylistTarget) -> str:
    return "\n".join(filter(None, (
        target.marker,
        _scope_marker(target),
        target.description,
    )))


def _scope_marker(target: ManagedPlaylistTarget) -> str:
    """Return a non-secret, stable discriminator for one server/scope pair."""
    value = f"{target.machine}\0{target.scope}".encode("utf-8")
    return "[QKYX-SCOPE:" + hashlib.sha256(value).hexdigest() + "]"


def _is_target_candidate(row: Mapping[str, object], target: ManagedPlaylistTarget) -> bool:
    lines = str(row.get("summary") or "").splitlines()
    return target.marker in lines and _scope_marker(target) in lines


def _assert_scope_compatible(state: Mapping[str, object], target: ManagedPlaylistTarget) -> None:
    lines = str(state.get("summary") or "").splitlines()
    scope_lines = [line for line in lines if line.startswith("[QKYX-SCOPE:")]
    if scope_lines and _scope_marker(target) not in scope_lines:
        raise ReconcileConflict("Plex 歌单已由其他档案或曲库管理")


def _matches(state: Mapping[str, object], target: ManagedPlaylistTarget) -> bool:
    actual = _member_ids(state)
    return (
        str(state.get("title") or "") == target.title
        and str(state.get("summary") or "") == _expected_summary(target)
        and len(actual) == len(target.member_ids)
        and set(actual) == set(target.member_ids)
    )


def _read_verified(plex, playlist_id: str, target: ManagedPlaylistTarget):
    predicate = lambda row: _matches(row, target)
    state = None
    for attempt in range(8):
        state = plex.playlist_state(playlist_id)
        if predicate(state):
            return state
        if attempt < 7:
            time.sleep(0.25 * (attempt + 1))
    if not state or not predicate(state):
        from .clients import PlexError

        raise PlexError("Plex 托管歌单写入后回读不一致")


def _owned_candidates(plex, target: ManagedPlaylistTarget) -> list[Mapping[str, object]]:
    if hasattr(plex, "owned_playlists"):
        candidates = list(plex.owned_playlists(target.marker))
    else:
        candidates = [
            row for row in plex.playlists()
            if target.marker in str(row.get("summary") or "").splitlines()
        ]
    candidates = [row for row in candidates if _is_target_candidate(row, target)]
    if len(candidates) > 1:
        raise ReconcileConflict("发现多个带有相同管理标记的 Plex 歌单")
    return candidates


def _discover_or_create(plex, target: ManagedPlaylistTarget, adopt_existing: bool):
    if hasattr(plex, "owned_playlists"):
        candidates = list(plex.owned_playlists(target.marker))
    else:
        candidates = [row for row in plex.playlists()
                      if target.marker in str(row.get("summary") or "").splitlines()]
    candidates = [row for row in candidates if _is_target_candidate(row, target)]
    if adopt_existing and len(candidates) > 1:
        raise ReconcileConflict("发现多个带有相同管理标记的 Plex 歌单")
    if adopt_existing and candidates:
        playlist_id = _playlist_id(candidates[0])
        if not playlist_id:
            raise ReconcileConflict("托管歌单候选缺少 Plex ID")
        return plex.playlist_state(playlist_id), False
    if any(
        str(row.get("title") or "") == target.title
        and target.marker not in str(row.get("summary") or "").splitlines()
        for row in plex.playlists()
    ):
        raise ReconcileConflict("Plex 中存在同名但没有本应用管理标记的歌单")
    previous_ids = {_playlist_id(row) for row in candidates}
    description = "\n".join(filter(None, (_scope_marker(target), target.description)))
    try:
        return plex.create(
            target.title,
            list(target.member_ids),
            target.marker,
            description=description,
        ), True
    except Exception:
        # A create response can be lost after Plex committed it. Discover the
        # unique marker before allowing the caller's retry policy to run.
        candidates = [row for row in (
            plex.owned_playlists(target.marker) if hasattr(plex, "owned_playlists")
            else [row for row in plex.playlists()
                  if target.marker in str(row.get("summary") or "").splitlines()]
        ) if _playlist_id(row) not in previous_ids and _is_target_candidate(row, target)]
        if not candidates:
            raise
        if len(candidates) > 1:
            raise ReconcileConflict("创建后发现多个新的同标记 Plex 歌单")
        playlist_id = _playlist_id(candidates[0])
        return plex.playlist_state(playlist_id), True


def reconcile_managed_playlist(
    plex,
    target: ManagedPlaylistTarget,
    managed_record: Mapping[str, object] | None,
    *,
    adopt_existing: bool = True,
) -> ManagedPlaylistResult:
    """Converge one ordinary Plex playlist without adopting unowned names."""
    from .clients import PlexNotFound

    target = validate_target(target)
    record = dict(managed_record or {})
    if record and str(record.get("machine") or "") != target.machine:
        raise ReconcileConflict("托管歌单服务器身份与目标范围不一致")
    if record and str(record.get("scope") or "") != target.scope:
        raise ReconcileConflict("托管歌单范围与当前档案或曲库不一致")

    current = None
    created = False
    playlist_id = str(record.get("id") or "")
    if playlist_id:
        try:
            current = plex.playlist_state(playlist_id)
        except PlexNotFound:
            current = None
    if current is None:
        current, created = _discover_or_create(plex, target, adopt_existing)
        playlist_id = _playlist_id(current)
    if not playlist_id:
        raise ReconcileConflict("托管歌单缺少 Plex ID")
    _assert_scope_compatible(current, target)

    if created:
        verified = _read_verified(plex, playlist_id, target)
        return ManagedPlaylistResult(status="created", playlist=verified)

    # Read once more immediately before the first mutation. Manual edits are
    # normal drift, so the latest remote state becomes the diff base.
    current = plex.playlist_state(playlist_id)
    changed = False
    if str(current.get("title") or "") != target.title:
        plex.rename(playlist_id, target.title)
        changed = True
        current = plex.playlist_state(playlist_id)
    expected_summary = _expected_summary(target)
    if str(current.get("summary") or "") != expected_summary:
        plex.update_playlist_summary(playlist_id, expected_summary)
        changed = True
        current = plex.playlist_state(playlist_id)

    desired = set(target.member_ids)
    seen = set()
    remove = []
    for item in current.get("items", []):
        track_id = str(item.get("id") or "")
        item_id = str(item.get("item_id") or "")
        if track_id not in desired or track_id in seen:
            if not item_id:
                raise ReconcileConflict("Plex 歌单条目缺少可删除的项目 ID")
            remove.append(item_id)
        else:
            seen.add(track_id)
    if remove:
        plex.remove_items(playlist_id, remove)
        changed = True
    missing = [track_id for track_id in target.member_ids if track_id not in seen]
    if missing:
        plex.append(playlist_id, missing)
        changed = True

    if not changed and _matches(current, target):
        return ManagedPlaylistResult(status="unchanged", playlist=current)
    verified = _read_verified(plex, playlist_id, target)
    return ManagedPlaylistResult(status="updated", playlist=verified)
