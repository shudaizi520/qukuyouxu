"""Shared contracts for converging application-owned Plex playlists."""
from __future__ import annotations

from dataclasses import dataclass, replace
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
