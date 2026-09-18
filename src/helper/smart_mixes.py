"""Pure, deterministic smart-playlist selectors for local Plex tracks."""
from __future__ import annotations

from collections import Counter
import hashlib
import math
import re
import unicodedata

from .audience import is_childrens_track


DAY = 86400
KINDS = frozenset({"weekly", "time_capsule", "recent_additions", "custom"})
TITLES = {
    "weekly": "每周常听",
    "time_capsule": "时光胶囊",
    "recent_additions": "最近新增",
    "custom": "自定义精选",
}


def _number(value, default=0.0):
    try:
        number = float(value)
        return number if math.isfinite(number) and number >= 0 else default
    except (TypeError, ValueError, OverflowError):
        return default


def _integer(value, name, low, high, default):
    if value is None:
        return default
    if isinstance(value, bool):
        raise ValueError(name + "需要整数")
    try:
        result = int(value)
    except (TypeError, ValueError):
        raise ValueError(name + "需要整数") from None
    if not low <= result <= high:
        raise ValueError(f"{name}必须在{low}到{high}之间")
    return result


def _normal(value):
    value = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return "".join(char for char in value if unicodedata.category(char)[:1] in ("L", "N"))


def _artists(value):
    parts = re.split(r"(?:、|/|&|,|，|\band\b|\bfeat\.?\b|\bft\.?\b)", str(value or ""), flags=re.I)
    return {key for key in (_normal(part) for part in parts) if key}


def _song_key(row):
    return _normal(row.get("title")) + "|" + ",".join(sorted(_artists(row.get("artist"))))


def _tie(seed, value):
    raw = hashlib.sha256((str(seed) + "|" + str(value)).encode()).hexdigest()[:14]
    return int(raw, 16) / float(0xFFFFFFFFFFFFFF)


def _valid(row):
    duration = _number(row.get("duration"))
    return (
        str(row.get("id", "")).isdigit()
        and row.get("available", True)
        and bool(_normal(row.get("title")))
        and bool(_artists(row.get("artist")))
        and not row.get("_metadata_blocked")
        and not is_childrens_track(row)
        and 45 <= duration <= 1800
    )


def _genres(row):
    return {
        _normal(value)
        for value in list(row.get("genres") or []) + list(row.get("styles") or []) + list(row.get("moods") or [])
        if _normal(value)
    }


def _public(row, reasons):
    return {
        "id": str(row["id"]),
        "title": str(row.get("title") or ""),
        "artist": str(row.get("artist") or ""),
        "album": str(row.get("album") or ""),
        "duration": row.get("duration"),
        "year": row.get("year"),
        "reasons": reasons[:3],
    }


def _take(rows, size, artist_cap, reason):
    selected = []
    songs = set()
    artists_used = Counter()
    for row in rows:
        song = _song_key(row)
        artists = _artists(row.get("artist"))
        if not song or song in songs or any(artists_used[key] >= artist_cap for key in artists):
            continue
        selected.append(_public(row, reason(row)))
        songs.add(song)
        artists_used.update(artists)
        if len(selected) >= size:
            break
    return selected


def select_smart_mix(kind, tracks, events, options, now, seed):
    if kind not in KINDS:
        raise ValueError("未知智能歌单类型")
    options = dict(options or {})
    size = _integer(options.get("size"), "歌单数量", 10, 100, 30)
    artist_cap = _integer(options.get("artist_cap"), "单歌手上限", 1, 5, 3 if kind == "weekly" else 2)
    valid = [dict(row) for row in tracks or [] if _valid(row)]
    childrens_excluded = sum(
        str(row.get("id", "")).isdigit() and is_childrens_track(row)
        for row in tracks or []
    )

    if kind == "weekly":
        recent_days = _integer(options.get("recent_days"), "近期窗口", 7, 90, 30)
        cutoff = now - recent_days * DAY
        by_id = {str(row["id"]): row for row in valid}
        artist_plays = Counter()
        track_plays = Counter()
        for event in events or []:
            tid = str(event.get("id") or event.get("track_id") or "")
            viewed = _number(event.get("viewed_at") or event.get("at"))
            row = by_id.get(tid)
            if row and viewed >= cutoff:
                track_plays[tid] += 1
                artist_plays.update(_artists(row.get("artist")))
        if not artist_plays:
            for row in valid:
                count = int(_number(row.get("view_count")))
                for artist in _artists(row.get("artist")):
                    artist_plays[artist] += count
        top_artists = {key for key, count in artist_plays.most_common(12) if count > 0}
        candidates = [row for row in valid if _artists(row.get("artist")) & top_artists]
        candidates.sort(key=lambda row: (
            -max((artist_plays[key] for key in _artists(row.get("artist"))), default=0),
            -track_plays[str(row["id"])],
            -_number(row.get("user_rating")),
            -_number(row.get("view_count")),
            -_tie(seed, row["id"]),
        ))
        items = _take(candidates, size, artist_cap, lambda row: ["最近30天常听歌手", "近期播放较多"])

    elif kind == "time_capsule":
        stale_days = _integer(options.get("stale_days"), "久未播放天数", 30, 3650, 180)
        candidates = [
            row for row in valid
            if _number(row.get("view_count")) > 0
            and _number(row.get("last_viewed_at")) > 0
            and now - _number(row.get("last_viewed_at")) >= stale_days * DAY
        ]
        candidates.sort(key=lambda row: (
            _number(row.get("last_viewed_at")),
            -_number(row.get("view_count")),
            -_tie(seed, row["id"]),
        ))
        items = _take(
            candidates, size, artist_cap,
            lambda row: [f"{int((now - _number(row.get('last_viewed_at'))) / DAY)}天未听", "曾经播放过"],
        )

    elif kind == "recent_additions":
        added_days = _integer(options.get("added_days"), "最近入库天数", 1, 3650, 90)
        candidates = [
            row for row in valid
            if _number(row.get("added_at")) > 0 and 0 <= now - _number(row.get("added_at")) <= added_days * DAY
        ]
        candidates.sort(key=lambda row: (-_number(row.get("added_at")), -_number(row.get("user_rating")), -_tie(seed, row["id"])))
        items = _take(candidates, size, artist_cap, lambda row: ["最近加入曲库", "优先较新的专辑歌曲"])

    else:
        year_min = _integer(options.get("year_min"), "起始年份", 1000, 3000, 1000) if options.get("year_min") not in (None, "") else None
        year_max = _integer(options.get("year_max"), "结束年份", 1000, 3000, 3000) if options.get("year_max") not in (None, "") else None
        if year_min is not None and year_max is not None and year_min > year_max:
            raise ValueError("起始年份不能晚于结束年份")
        min_rating = _number(options.get("min_rating"), 0)
        max_play_count = _number(options.get("max_play_count"), float("inf")) if options.get("max_play_count") not in (None, "") else float("inf")
        if not 0 <= min_rating <= 10:
            raise ValueError("最低评分必须在0到10之间")
        raw_genres = options.get("genres") or []
        if isinstance(raw_genres, str):
            raw_genres = re.split(r"[,，、]", raw_genres)
        wanted_genres = {_normal(value) for value in raw_genres if _normal(value)}
        added_days = _integer(options.get("added_days"), "最近入库天数", 1, 3650, 3650) if options.get("added_days") not in (None, "") else None
        stale_days = _integer(options.get("stale_days"), "久未播放天数", 1, 3650, 1) if options.get("stale_days") not in (None, "") else None
        candidates = []
        for row in valid:
            year = int(_number(row.get("year")))
            if year_min is not None and (not year or year < year_min):
                continue
            if year_max is not None and (not year or year > year_max):
                continue
            if _number(row.get("user_rating")) < min_rating or _number(row.get("view_count")) > max_play_count:
                continue
            if wanted_genres and not (_genres(row) & wanted_genres):
                continue
            if added_days is not None and not (0 <= now - _number(row.get("added_at"), -1) <= added_days * DAY):
                continue
            if stale_days is not None:
                last = _number(row.get("last_viewed_at"))
                if not last or now - last < stale_days * DAY:
                    continue
            candidates.append(row)
        sort_by = str(options.get("sort_by") or "random")
        sorters = {
            "random": lambda row: (-_tie(seed, row["id"]),),
            "rating": lambda row: (-_number(row.get("user_rating")), -_tie(seed, row["id"])),
            "play_count": lambda row: (-_number(row.get("view_count")), -_tie(seed, row["id"])),
            "added": lambda row: (-_number(row.get("added_at")), -_tie(seed, row["id"])),
            "last_played": lambda row: (-_number(row.get("last_viewed_at")), -_tie(seed, row["id"])),
        }
        if sort_by not in sorters:
            raise ValueError("自定义排序方式无效")
        candidates.sort(key=sorters[sort_by])
        items = _take(candidates, size, artist_cap, lambda row: ["符合自定义筛选", "已执行去重和歌手上限"])

    return {
        "kind": kind,
        "title": TITLES[kind],
        "items": items,
        "stats": {"library_count": len(tracks or []), "eligible_count": len(valid), "childrens_excluded_count": childrens_excluded, "selected": len(items), "requested": size},
        "warnings": [] if len(items) >= size else [f"符合条件的歌曲只有{len(items)}首，没有用重复歌曲凑数。"],
        "options": options,
    }
