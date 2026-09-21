"""Normalize assistant-owned and Plex-native playlists for one profile."""
from __future__ import annotations

import math


def _truthy(value):
    return value is True or str(value or "").strip().lower() in {"1", "true", "yes"}


def _count(value):
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _number(value):
    try:
        number = float(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return number if math.isfinite(number) else 0.0


def capabilities(*, source, smart):
    """Return explicit UI permissions; callers never infer them from ``kind``."""
    source = str(source or "")
    smart = bool(smart)
    native = source == "plex"
    managed = source in {"assistant", "external"}
    return {
        "can_play": True,
        "can_rename": native,
        "can_add_tracks": (native and not smart) or managed,
        "can_remove_tracks": (native and not smart) or managed,
        "can_delete": native or managed,
    }


def assistant_playlist_row(row):
    result = dict(row)
    kind = str(result.get("kind") or "")
    source = "external" if kind == "external" else "assistant"
    section = "smart" if kind in {"daily", "smart"} else (
        "library" if kind == "category" else "custom"
    )
    result.update(section=section, source=source, smart=False, stale=False)
    result.update(capabilities(source=source, smart=False))
    if kind == "favorite":
        result.update(
            can_play=True, can_rename=False, can_add_tracks=False,
            can_remove_tracks=False, can_delete=False,
        )
        return result
    if not str(result.get("playlist_id") or ""):
        result["can_play"] = False
        result["can_add_tracks"] = False
        result["can_remove_tracks"] = False
        result["can_delete"] = False
    return result


def native_playlist_row(row):
    playlist_id = str(row.get("ratingKey") or row.get("playlist_id") or "")
    smart = _truthy(row.get("smart"))
    result = {
        "kind": "plex",
        "kind_label": "Plex 歌单",
        "key": playlist_id,
        "playlist_id": playlist_id,
        "title": str(row.get("title") or "未命名歌单"),
        "count": _count(row.get("leafCount", row.get("count"))),
        "updated_at": _number(row.get("updatedAt") or row.get("updated_at")),
        "manage_url": "",
        "status": "已建立",
        "section": "custom",
        "source": "plex",
        "smart": smart,
        "source_section": str(row.get("source_section") or ""),
        "stale": False,
    }
    result.update(capabilities(source="plex", smart=smart))
    return result


def normalize_native_playlist_rows(rows):
    return [
        native_playlist_row(row) for row in (rows or [])
        if isinstance(row, dict)
        and str(row.get("playlistType") or "audio") == "audio"
        and str(row.get("ratingKey") or row.get("playlist_id") or "").isdigit()
    ]


def merge_playlist_rows(assistant_rows, plex_rows):
    assistant_rows = [dict(row) for row in (assistant_rows or [])]
    owned = {
        str(row.get("playlist_id")) for row in assistant_rows
        if str(row.get("playlist_id") or "")
    }
    native = normalize_native_playlist_rows(plex_rows)
    return [*assistant_rows, *(row for row in native if row["playlist_id"] not in owned)]
