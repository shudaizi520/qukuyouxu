"""Guarded synchronization for playlists owned by external imports."""
from __future__ import annotations

import hashlib
import json
import re


def _safety(message):
    from .engine import SafetyError

    return SafetyError(message)


def _digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def _ids(state):
    return [str(row["id"]) for row in state.get("items") or []]


def playlist_fingerprint(state):
    return _digest({
        "title": str(state.get("title") or ""),
        "summary": str(state.get("summary") or ""),
        "ids": _ids(state),
    })


def _safe_identifier(value, name):
    value = str(value or "")
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,96}", value):
        raise ValueError(f"{name}无效")
    return value


def external_marker(installation_id: str, source_id: str) -> str:
    installation_id = _safe_identifier(installation_id, "安装标识")
    source_id = _safe_identifier(source_id, "外部歌单标识")
    return f"[QKYX:external:{installation_id}:{source_id}]"


def _validate_desired(desired_ids):
    if not isinstance(desired_ids, list):
        raise _safety("外部歌单曲目无效")
    desired = [str(value) for value in desired_ids]
    if not desired:
        raise _safety("没有可靠匹配的本地歌曲，不创建或清空 Plex 歌单")
    if len(desired) > 10_000 or len(set(desired)) != len(desired) or any(not value.isdigit() for value in desired):
        raise _safety("外部歌单必须使用不重复的有效 Plex 歌曲标识")
    return desired


def _validate_source(source):
    if not isinstance(source, dict):
        raise ValueError("外部歌单无效")
    source_id = _safe_identifier(source.get("id"), "外部歌单标识")
    title = str(source.get("title") or "").strip()
    if not title or len(title) > 80 or any(ord(char) < 32 for char in title):
        raise ValueError("Plex 歌单名称无效")
    return source_id, title


def _observe(plex, playlist_id, predicate, attempts=4):
    if hasattr(plex, "read_playlist_until"):
        return plex.read_playlist_until(playlist_id, predicate, attempts=attempts, delay=0.25)
    last = None
    for _ in range(attempts):
        last = plex.playlist_state(playlist_id)
        if predicate(last):
            break
    return last


def _owned_state(state, playlist_id, title, marker):
    return (
        str(state.get("id") or "") == str(playlist_id)
        and state.get("title") == title
        and marker in str(state.get("summary") or "")
    )


def _managed_record(state, source, marker, order_attention=False):
    return {
        "id": str(state["id"]),
        "title": str(state["title"]),
        "fingerprint": playlist_fingerprint(state),
        "revision": str(source.get("revision") or ""),
        "marker": marker,
        "order_attention": bool(order_attention),
    }


def _initial_state(plex, source, marker, managed, desired):
    source_id, source_title = _validate_source(source)
    if managed is not None:
        if not isinstance(managed, dict):
            raise _safety("外部歌单托管记录无效")
        playlist_id = str(managed.get("id") or "")
        title = str(managed.get("title") or "")
        if not playlist_id or not title or not managed.get("fingerprint"):
            raise _safety("外部歌单托管记录不完整")
        current = plex.playlist_state(playlist_id)
        if not _owned_state(current, playlist_id, title, marker):
            raise _safety("Plex 歌单名称、标识或管理标记已被修改，停止写入")
        if playlist_fingerprint(current) != managed.get("fingerprint"):
            raise _safety("Plex 歌单内容已被手工修改，停止写入")
        return current, title

    listed = plex.playlists()
    exact = [
        row for row in listed
        if marker in str(row.get("summary") or "")
    ]
    if len(exact) > 1:
        raise _safety("发现多个相同管理标记的 Plex 歌单，请先核对")
    if exact:
        playlist_id = str(exact[0].get("ratingKey") or exact[0].get("id") or "")
        current = plex.playlist_state(playlist_id)
        if not _owned_state(current, playlist_id, source_title, marker):
            raise _safety("已拥有的 Plex 歌单名称或管理标记不一致")
        return current, source_title
    if any(str(row.get("title") or "") == source_title for row in listed):
        raise _safety("Plex 中已有同名歌单，但没有本安装的管理标记，不会接管")
    created = plex.create(
        source_title, desired, marker,
        description="由曲库有序从外部歌单匹配；缺失歌曲补齐后会按原位置加入。",
    )
    playlist_id = str(created.get("id") or "")
    current = _observe(
        plex, playlist_id,
        lambda row: _owned_state(row, playlist_id, source_title, marker) and _ids(row) == desired,
    )
    if not current or not _owned_state(current, playlist_id, source_title, marker) or _ids(current) != desired:
        raise _safety("创建后回读不一致；不会自动重复创建")
    return current, source_title


def create_or_reconcile_external_playlist(plex, installation_id, source, managed, desired_ids):
    desired = _validate_desired(desired_ids)
    source_id, _ = _validate_source(source)
    marker = external_marker(installation_id, source_id)
    current, title = _initial_state(plex, source, marker, managed, desired)
    playlist_id = str(current["id"])
    actual = _ids(current)
    if len(actual) != len(set(actual)):
        raise _safety("Plex 外部歌单已有重复曲目，请先手工核对")
    if managed and managed.get("order_attention") and set(actual) == set(desired):
        return current, _managed_record(current, source, marker, order_attention=True)

    desired_set = set(desired)
    stale = [row for row in current["items"] if str(row["id"]) not in desired_set]
    for item in stale:
        item_id = str(item.get("item_id") or "")
        if not item_id:
            raise _safety("Plex 歌单条目标识缺失，停止写入")
        plex.remove_items(playlist_id, [item_id])
        current = _observe(plex, playlist_id, lambda row, iid=item_id: all(str(x.get("item_id")) != iid for x in row["items"]))
        if any(str(row.get("item_id")) == item_id for row in current["items"]):
            raise _safety("删除旧曲目后回读不一致，停止后续修改")

    attempts = 0
    while True:
        actual = _ids(current)
        missing = [value for value in desired if value not in set(actual)]
        if not missing:
            break
        before_count = len(actual)
        plex.append(playlist_id, missing)
        current = _observe(
            plex, playlist_id,
            lambda row, old=before_count: len(_ids(row)) > old,
        )
        attempts += 1
        if len(_ids(current)) <= before_count or attempts > len(desired):
            raise _safety("补充匹配歌曲后回读没有进展，停止后续修改")
        if len(_ids(current)) != len(set(_ids(current))):
            raise _safety("补充歌曲后出现重复曲目，停止后续修改")

    if set(_ids(current)) != desired_set or len(_ids(current)) != len(desired):
        raise _safety("Plex 歌单成员与外部歌单匹配结果不一致")

    order_attention = False
    for target_index, target_id in enumerate(desired):
        actual = _ids(current)
        if actual == desired:
            break
        current_index = actual.index(target_id)
        if current_index == target_index:
            continue
        moving = current["items"][current_index]
        after = None if target_index == 0 else current["items"][actual.index(desired[target_index - 1])]["item_id"]
        plex.move_item(playlist_id, moving["item_id"], after=after)
        previous = actual
        current = _observe(plex, playlist_id, lambda row, old=previous: _ids(row) != old)
        if set(_ids(current)) != desired_set or len(_ids(current)) != len(desired):
            raise _safety("调整顺序后成员发生变化，停止后续修改")
        if _ids(current) == previous:
            order_attention = True
            break
    if _ids(current) != desired:
        order_attention = True
    return current, _managed_record(current, source, marker, order_attention=order_attention)


def delete_owned_external_playlist(plex, installation_id, source_id, managed, confirm_title):
    marker = external_marker(installation_id, source_id)
    if not isinstance(managed, dict):
        raise _safety("外部歌单托管记录无效")
    playlist_id = str(managed.get("id") or "")
    title = str(managed.get("title") or "")
    if str(confirm_title or "") != title:
        raise _safety("确认名称不一致，不删除 Plex 歌单")
    current = plex.playlist_state(playlist_id)
    if not _owned_state(current, playlist_id, title, marker):
        raise _safety("Plex 歌单标识、名称或管理标记不一致，不删除")
    if playlist_fingerprint(current) != managed.get("fingerprint"):
        raise _safety("Plex 歌单已被修改，不删除")
    plex.delete_playlist(playlist_id)
    return {"removed": playlist_id}
