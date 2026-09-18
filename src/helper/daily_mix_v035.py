"""Plex-history Daily Mix policy for v0.3.5.

This module is deliberately independent from publication.  It only reads Plex
signals and returns a preview plan compatible with the existing daily lifecycle.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import inspect
import math
import re
import time
import unicodedata

from fastapi import Request

from .recommend import diagnostic_display


POLICY_VERSION = "daily-mix-v0.4.24"
DAY = 86400
POLICY = {
    "size": 30,
    "recent_seed_limit": 20,
    "recent_max_days": 180,
    "recent_days": 180,
    "rediscovery_days": 90,
    "daily_avoid_days": 21,
    "artist_cap": 2,
    "artist_cap_relaxed": 3,
    "album_cap": 1,
    "favorite_cap": 4,
    "favorite_percent": 20,
    "seed_limit": 20,
    "similar_per_seed": 30,
    "targets": {
        "稳定喜好": 12,
        "久未重听": 9,
        "曲库探索": 6,
        "近期口味": 3,
    },
    "weights": {
        "base": 1.0,
        "similar_hit": 5.0,
        "similar_extra_hit": 2.5,
        "metadata_similarity": 2.0,
        "favorite": 6.0,
        "high_rating": 4.0,
        "assistant_like": 5.0,
        "old_play": 2.0,
        "stale_year": 2.5,
        "low_play_explore": 2.0,
        "avoid_artist_penalty": 20.0,
    },
}


def _scaled_targets(size):
    """Scale the original 30-track bucket mix to the requested list size."""
    size = max(10, min(100, int(size)))
    total = sum(POLICY["targets"].values())
    raw = {key: size * value / total for key, value in POLICY["targets"].items()}
    targets = {key: int(value) for key, value in raw.items()}
    missing = size - sum(targets.values())
    order = sorted(raw, key=lambda key: (-(raw[key] - targets[key]), list(raw).index(key)))
    for key in order[:missing]:
        targets[key] += 1
    return targets


class PlexHistoryIsolationError(RuntimeError):
    """Plex history could not be tied to exactly one intended account."""


def _number(value, default=0.0):
    try:
        number = float(value)
        return number if math.isfinite(number) and number >= 0 else default
    except (TypeError, ValueError, OverflowError):
        return default


def _normal(value):
    value = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return "".join(char for char in value if unicodedata.category(char)[:1] in ("L", "N"))


def _artists(value):
    parts = re.split(r"(?:、|/|&|,|，|\band\b|\bfeat\.?\b|\bft\.?\b)", str(value or ""), flags=re.I)
    return {key for key in (_normal(part) for part in parts) if key}


def _song_key(track):
    return _normal(track.get("title")) + "|" + ",".join(sorted(_artists(track.get("artist"))))


def _jitter(seed, value):
    raw = hashlib.sha256((str(seed) + "|" + str(value)).encode("utf-8")).hexdigest()[:14]
    return int(raw, 16) / float(0xFFFFFFFFFFFFFF)


def _valid(track):
    duration = _number(track.get("duration"))
    return (
        str(track.get("id", "")).isdigit()
        and track.get("available", True)
        and bool(_normal(track.get("title")))
        and bool(_artists(track.get("artist")))
        and not track.get("_metadata_blocked")
        and 45 <= duration <= 1800
    )


def recent_positive_play_times(tracks, play_events, now, limit=20, max_days=180):
    """Return the latest distinct positive plays, bounded by activity and age."""
    cutoff = now - max_days * DAY
    allowed = {str(row.get("id")) for row in tracks if str(row.get("id", "")).isdigit()}
    latest = {}
    if play_events is not None:
        for event in play_events:
            kind = str(event.get("kind") or "").casefold()
            if "skip" in kind or _number(event.get("value"), 0) < 0:
                continue
            tid = str(event.get("id") or event.get("track_id") or "")
            viewed = _number(event.get("viewed_at") or event.get("at"))
            if tid in allowed and viewed >= cutoff:
                latest[tid] = max(latest.get(tid, 0), viewed)
    else:
        for row in tracks:
            viewed = _number(row.get("last_viewed_at"))
            if viewed >= cutoff:
                latest[str(row.get("id", ""))] = viewed
    ordered = sorted(latest.items(), key=lambda item: (-item[1], item[0]))[:max(1, int(limit))]
    return dict(ordered)


def _recent_play_times(tracks, play_events, now, recent_days=None):
    return recent_positive_play_times(
        tracks, play_events, now,
        limit=POLICY["recent_seed_limit"], max_days=POLICY["recent_max_days"],
    )


def _recent_seed_ids(tracks, play_events, now, recent_days, limit):
    latest = recent_positive_play_times(tracks, play_events, now, limit=limit)
    return [key for key, _ in sorted(latest.items(), key=lambda item: (-item[1], item[0]))[:limit]]


def _history_keys(rows, now, window_days):
    ids = set();songs = set()
    for row in rows or []:
        age = now - _number(row.get("created_at"), -1)
        if 0 <= age < window_days * DAY:
            ids.update(str(value) for value in row.get("ids", []) if str(value).isdigit())
            songs.update(str(value) for value in row.get("song_keys", []) if str(value))
    return ids, songs


def _metadata_similarity(candidate, seeds, features):
    strong = 0.0
    coarse = 0.0
    candidate_artists = _artists(candidate.get("artist"))
    candidate_genres = {_normal(x) for x in candidate.get("genres", []) if _normal(x)}
    candidate_album = _normal(candidate.get("album"))
    candidate_features = set(features.get(str(candidate.get("id")), []))
    for seed in seeds:
        if candidate_artists & _artists(seed.get("artist")):
            strong += 1.5
        if candidate_album and candidate_album == _normal(seed.get("album")):
            strong += 0.5
        if candidate_genres & {_normal(x) for x in seed.get("genres", []) if _normal(x)}:
            coarse += 0.35
        if candidate_features & set(features.get(str(seed.get("id")), [])):
            strong += 0.5
    # A shared coarse genre such as "Pop/流行" is too broad to make a track a
    # recent-taste candidate by itself.  It only breaks ties after a stronger
    # artist, album or curated-theme relationship exists.
    return strong + min(0.5, coarse) if strong else 0.0


def select_daily_mix(
    tracks,
    features,
    feedback,
    settings,
    history,
    seed_ids,
    now,
    seed,
    play_events=None,
    similar_ids=None,
    preview_history=None,
    legacy_preference_ids=None,
    behavior=None,
    preserve_ids=None,
):
    """Return a deterministic preview.  No Plex write is performed here."""
    cfg = dict(POLICY)
    cfg.update({key: settings[key] for key in (
        "size", "rediscovery_days", "daily_avoid_days", "artist_cap", "favorite_cap", "favorite_percent"
    ) if key in settings})
    cfg["size"] = max(10, min(100, int(cfg["size"])))
    cfg["rediscovery_days"] = max(30, min(3650, int(cfg["rediscovery_days"])))
    # This release makes the approved repeat guard a product invariant.  Old
    # profiles may still contain 7-day values, but they cannot weaken it.
    cfg["daily_avoid_days"] = POLICY["daily_avoid_days"]
    cfg["artist_cap"] = max(1, min(6, int(cfg["artist_cap"])))
    if "favorite_percent" in settings:
        cfg["favorite_percent"] = int(settings["favorite_percent"])
        cfg["favorite_cap"] = max(0, int(cfg["size"] * cfg["favorite_percent"] / 100))
    else:
        cfg["favorite_cap"] = max(0, int(cfg["favorite_cap"]))
    weights = POLICY["weights"]

    valid = [row for row in tracks if _valid(row)]
    by_id = {str(row["id"]): row for row in valid}
    feedback = feedback or {}
    track_feedback = feedback.get("tracks", {}) or {}
    artist_feedback = feedback.get("artists", {}) or {}
    hard_avoid_ids = {str(key) for key, value in track_feedback.items() if value.get("value") == "avoid"}
    hard_avoid_songs = {_song_key(by_id[key]) for key in hard_avoid_ids if key in by_id}
    avoided_artists = {
        artist
        for label, value in artist_feedback.items()
        if value.get("value") == "avoid"
        for artist in _artists(label)
    }
    recent_play_times = recent_positive_play_times(valid, play_events, now)
    recent_ids = set(recent_play_times)
    recent_songs = {_song_key(by_id[key]) for key in recent_ids if key in by_id}
    recent_seed_ids = [
        key for key, _ in sorted(recent_play_times.items(), key=lambda item: (-item[1], item[0]))
        if key in by_id
    ][: POLICY["seed_limit"]]
    seed_rows = [by_id[key] for key in recent_seed_ids]
    favorite_ids = {str(value) for value in seed_ids or []}
    legacy_preference_ids = {str(value) for value in legacy_preference_ids or []}
    behavior = behavior or {}
    stable_seed_rows = [
        row for row in valid
        if (
            str(row["id"]) in favorite_ids
            or _number(row.get("user_rating")) >= 8
            or track_feedback.get(str(row["id"]), {}).get("value") == "like"
            or float((behavior.get(str(row["id"]), {}) or {}).get("long_term_score") or 0) > 0.5
        )
        and str(row["id"]) not in hard_avoid_ids
        and _song_key(row) not in hard_avoid_songs
        and not (_artists(row.get("artist")) & avoided_artists)
    ]
    similar_ids = similar_ids or {}
    similar_hits = Counter()
    similar_sources = defaultdict(list)
    for source, values in similar_ids.items():
        for value in values:
            tid = str(value)
            if tid in by_id and tid != str(source):
                similar_hits[tid] += 1
                similar_sources[tid].append(str(source))

    preserve_ids = [str(value) for value in (preserve_ids or [])]
    preserve_set = set(preserve_ids)
    candidates = []
    excluded_feedback = excluded_recent = excluded_fatigue = 0
    dedupe_seen = set()
    for row in valid:
        tid = str(row["id"])
        song = _song_key(row)
        if tid in dedupe_seen:
            continue
        dedupe_seen.add(tid)
        if tid in hard_avoid_ids or song in hard_avoid_songs:
            excluded_feedback += 1
            continue
        learned = behavior.get(tid, {}) or {}
        if _number(learned.get("cooldown_until")) > now:
            excluded_fatigue += 1
            continue
        if (tid in recent_ids or song in recent_songs) and tid not in preserve_set:
            excluded_recent += 1
            continue
        artists = _artists(row.get("artist"))
        if artists & avoided_artists:
            excluded_feedback += 1
            continue
        rating = _number(row.get("user_rating"))
        count = _number(row.get("view_count"))
        last = _number(row.get("last_viewed_at"))
        days_since = (now - last) / DAY if last else None
        liked = track_feedback.get(tid, {}).get("value") == "like"
        favorite = tid in favorite_ids or rating >= 8 or liked
        stale = bool(count > 0 and last and days_since >= cfg["rediscovery_days"])
        metadata_sim = _metadata_similarity(row, seed_rows, features) if seed_rows else 0.0
        stable_sim = _metadata_similarity(row, stable_seed_rows, features) if stable_seed_rows else 0.0
        hits = similar_hits.get(tid, 0)
        stable = favorite or float(learned.get("long_term_score") or 0) > 0.5 or stable_sim > 0
        if stale:
            preferred = "久未重听"
        elif stable:
            preferred = "稳定喜好"
        elif hits or metadata_sim:
            preferred = "近期口味"
        else:
            preferred = "曲库探索"
        score = weights["base"] + _jitter(seed + "|rank", tid)
        reasons = []
        if hits:
            score += weights["similar_hit"] + max(0, hits - 1) * weights["similar_extra_hit"]
            reasons.append("多个近期歌曲共同相关" if hits > 1 else "近期口味相似")
        elif metadata_sim:
            score += min(6.0, metadata_sim * weights["metadata_similarity"])
            reasons.append("近期偏好歌手/分类")
        if stale:
            score += weights["old_play"] + min(4.0, (days_since / 365.0) * weights["stale_year"])
            reasons.append(f"{int(days_since)}天以上未听")
            if count >= 3:
                score += min(2.0, math.log1p(count))
                reasons.append("以前经常听")
        if favorite:
            score += weights["favorite"]
            if liked:
                score += weights["assistant_like"]
            if rating >= 8:
                score += weights["high_rating"]
            reasons.append("长期喜欢或收藏")
        learned_long=float(learned.get("long_term_score") or 0)
        learned_short=float(learned.get("short_term_score") or 0)
        if learned_long>0:
            score+=min(6.0,learned_long*1.5)
            reasons.append("多次播放形成的稳定喜好")
        if learned_short:
            score+=max(-2.0,min(2.0,learned_short*.35))
        if stable_sim and not favorite:
            score+=min(4.0,stable_sim*2)
            reasons.append("与你的长期喜好有稳定关联")
        if tid in legacy_preference_ids:
            score += 2.5
            reasons.append("现有播放偏好")
        novelty = 1.0 if count == 0 else 1.0 / (1.0 + math.log1p(count))
        score += novelty * weights["low_play_explore"]
        if preferred == "曲库探索":
            reasons.extend(["曲库探索", "很少播放" if count <= 1 else "较少播放"])
        candidates.append({
            "id": tid,
            "title": row.get("title", ""),
            "artist": row.get("artist", ""),
            "album": row.get("album", ""),
            "duration": row.get("duration"),
            "score": round(score, 6),
            "reasons": list(dict.fromkeys(reasons))[:3] or ["曲库探索"],
            "song_key": song,
            "source_bucket": preferred,
            "seed_tracks": similar_sources.get(tid, [])[:5],
            "penalties": [],
            "_artists": artists,
            "_album": str(row.get("album_id") or "") or (
                (_normal(row.get("album")) + "|" + ",".join(sorted(artists)))
                if _normal(row.get("album")) else ""
            ),
            "_favorite": favorite,
            "_novelty": novelty,
            "_jitter": _jitter(seed + "|select", tid),
        })

    combined_history = list(history or []) + list(preview_history or [])
    def choose(window_days, artist_cap):
        blocked_history, blocked_songs = _history_keys(combined_history, now, window_days)
        rows = [row for row in candidates if row["id"] not in blocked_history and row["song_key"] not in blocked_songs]
        ranked = {
            "稳定喜好": sorted(rows, key=lambda x: (x["source_bucket"] != "稳定喜好", -x["score"], -x["_jitter"], x["id"])),
            "久未重听": sorted(rows, key=lambda x: (x["source_bucket"] != "久未重听", -x["score"], -x["_jitter"], x["id"])),
            "曲库探索": sorted(rows, key=lambda x: (x["source_bucket"] != "曲库探索", -x["_novelty"], -x["score"], -x["_jitter"], x["id"])),
            "近期口味": sorted(rows, key=lambda x: (x["source_bucket"] != "近期口味", -similar_hits.get(x["id"], 0), -x["score"], -x["_jitter"], x["id"])),
        }
        selected = []
        selected_ids = set()
        selected_songs = set()
        artists_used = Counter()
        albums_used = Counter()
        favorite_used = 0
        cap_skips = 0

        def add(row, bucket):
            nonlocal favorite_used, cap_skips
            if row["id"] in selected_ids or row["song_key"] in selected_songs:
                return False
            if row["_favorite"] and favorite_used >= cfg["favorite_cap"]:
                return False
            if any(artists_used[a] >= artist_cap for a in row["_artists"]):
                cap_skips += 1
                return False
            if row["_album"] and albums_used[row["_album"]] >= cfg["album_cap"]:
                return False
            item = {key: value for key, value in row.items() if not key.startswith("_")}
            item["bucket"] = bucket
            selected.append(item)
            selected_ids.add(row["id"])
            selected_songs.add(row["song_key"])
            artists_used.update(row["_artists"])
            if row["_album"]:
                albums_used[row["_album"]] += 1
            if row["_favorite"]:
                favorite_used += 1
            return True

        retained = []
        candidate_by_id = {row["id"]: row for row in candidates}
        for track_id in preserve_ids:
            row = candidate_by_id.get(track_id)
            if row and add(row, row["source_bucket"]):
                retained.append(track_id)

        targets = _scaled_targets(cfg["size"])
        for bucket in ("稳定喜好", "久未重听", "曲库探索", "近期口味"):
            wanted = targets[bucket]
            for row in ranked[bucket]:
                if sum(item["bucket"] == bucket for item in selected) >= wanted:
                    break
                if row["source_bucket"] == bucket:
                    add(row, bucket)
        fill = sorted(rows, key=lambda x: (-x["score"], -x["_jitter"], x["id"]))
        for row in fill:
            if len(selected) >= cfg["size"]:
                break
            add(row, row["source_bucket"])
        retained_set = set(retained)
        tail = [row for row in selected if row["id"] not in retained_set]
        tail.sort(key=lambda x: (-_jitter(seed + "|order", x["id"]), x["id"]))
        selected = [next(row for row in selected if row["id"] == track_id) for track_id in retained] + tail
        return selected, len(blocked_history)+len(blocked_songs), cap_skips, len(retained)

    window=cfg["daily_avoid_days"];cap=cfg["artist_cap"]
    selected, excluded_history, cap_skips, retained_count = choose(window, cap)
    attempts=[(len(selected),window,cap)]
    selected = selected[: cfg["size"]]
    bucket_counts = dict(Counter(item["bucket"] for item in selected))
    for bucket in _scaled_targets(cfg["size"]):
        bucket_counts.setdefault(bucket, 0)
    warnings = []
    if len(selected) < cfg["size"]:
        warnings.append(f"符合{window}天防重复、单歌手{cap}首和单专辑{cfg['album_cap']}首限制的歌曲只有{len(selected)}首；没有放宽规则凑数。")
    stats = {
        "algorithm_version": POLICY_VERSION,
        "library_count": len(tracks),
        "eligible_count": len(valid),
        "recent_seed_count": len(recent_seed_ids),
        "related_candidate_count": sum(bool(similar_hits.get(row["id"]) or _metadata_similarity(row, seed_rows, features)) for row in candidates),
        "rediscovery_candidate_count": sum(row["source_bucket"] == "久未重听" for row in candidates),
        "favorite_candidate_count": sum(row["_favorite"] for row in candidates),
        "exploration_candidate_count": sum(row["source_bucket"] == "曲库探索" for row in candidates),
        "excluded_never_recommend": excluded_feedback,
        "excluded_recent_plays": excluded_recent,
        "excluded_by_fatigue": excluded_fatigue,
        "excluded_recent_daily": excluded_history,
        "reduced_artist_candidates": 0,
        "artist_cap_skips": cap_skips,
        "dedupe_before": len(valid),
        "dedupe_after": len(dedupe_seen),
        "candidate_count": len(candidates),
        # Compatibility with the existing daily lifecycle and v0.3.4 UI.
        "positive_seed_count": sum(
            str(row["id"]) in favorite_ids
            or _number(row.get("user_rating")) >= 8
            or track_feedback.get(str(row["id"]), {}).get("value") == "like"
            or _number(row.get("view_count")) > 0
            for row in valid
        ),
        "favorite_playlist_seed_count": len(favorite_ids & set(by_id)),
        "favorite_selected_count": sum(
            item["id"] in favorite_ids
            or _number(by_id[item["id"]].get("user_rating")) >= 8
            or track_feedback.get(item["id"], {}).get("value") == "like"
            for item in selected
        ),
        "rated_count": sum(_number(row.get("user_rating")) > 0 for row in valid),
        "played_count": sum(_number(row.get("view_count")) > 0 for row in valid),
        "excluded_by_feedback": excluded_feedback,
        "cooled_count": excluded_recent + excluded_history,
        "missing_rating_fields": sum(row.get("user_rating") is None for row in tracks),
        "missing_playcount_fields": sum(row.get("view_count") is None for row in tracks),
        "requested": cfg["size"],
        "selected": len(selected),
        "bucket_counts": bucket_counts,
        "daily_avoid_window_days": window,
        "artist_cap_used": cap,
        "album_cap_used": cfg["album_cap"],
        "artist_cap_relaxed": False,
        "favorite_percent": cfg.get("favorite_percent", 0),
        "rolling_retained": retained_count,
        "rolling_replenished": max(0, len(selected) - retained_count),
        "rule_relaxed": False,
        "attempts": attempts,
    }
    return {"mode": "plex_history", "items": selected, "stats": stats, "warnings": warnings}


def resolve_plex_account_id(client, username):
    """Resolve the configured Plex Home/user name using the local server only."""
    wanted = str(username or "").strip().casefold()
    root = client._xml("/accounts")
    accounts = []
    for item in list(root.findall("Account")) + list(root.findall("User")):
        labels = {
            str(item.get(key) or "").strip().casefold()
            for key in ("name", "title", "username")
            if str(item.get(key) or "").strip()
        }
        account_id = str(item.get("id") or item.get("accountID") or "").strip()
        if account_id.isdigit():
            accounts.append((account_id, labels))
    accounts = list({account_id: labels for account_id, labels in accounts}.items())
    matches = [account_id for account_id, labels in accounts if wanted in labels] if wanted else [account_id for account_id, _ in accounts]
    if len(matches) != 1:
        if wanted:
            raise PlexHistoryIsolationError("无法把设置中的 Plex 用户唯一对应到本机账户")
        raise PlexHistoryIsolationError("Plex 有多个账户；请在设置中明确选择播放历史用户")
    return matches[0]


def read_plex_history(client, section, now, days=400, page_size=300, maximum=10000, account_id=None, after=None):
    """Read complete Plex history while enforcing a single account boundary."""
    if not str(section).isdigit():
        raise ValueError("音乐资料库ID无效")
    rows = []
    cutoff = int(now - days * DAY)
    if after is not None:
        cutoff = max(cutoff, int(_number(after, cutoff)))
    expected = None
    accounts = set()
    processed = 0
    account_id = str(account_id or "").strip() or None
    for start in range(0, maximum, page_size):
        params = {
            "librarySectionID": str(section),
            "viewedAt>": cutoff,
            "sort": "viewedAt:desc",
            "X-Plex-Container-Start": start,
            "X-Plex-Container-Size": page_size,
        }
        if account_id:
            params["accountID"] = account_id
        root = client._xml(
            "/status/sessions/history/all",
            params=params,
        )
        items = list(root.findall("Track")) + list(root.findall("Video")) + list(root.findall("Metadata"))
        total = int(root.get("totalSize", root.get("size", len(items))))
        if expected is None:
            expected = total
        if total != expected:
            raise RuntimeError("Plex历史分页期间发生变化")
        if not items and processed < total:
            raise RuntimeError("Plex历史分页不完整")
        for item in items:
            processed += 1
            media_type = str(item.get("type") or "track").casefold()
            if media_type not in ("track", "audio"):
                continue
            tid = str(item.get("ratingKey") or "")
            viewed = _number(item.get("viewedAt") or item.get("lastViewedAt"))
            account = str(item.get("accountID") or "").strip()
            if account_id and account and account != account_id:
                raise PlexHistoryIsolationError("Plex历史返回了设置用户之外的记录")
            if account:
                accounts.add(account)
            if tid.isdigit() and viewed and 0 <= now - viewed <= days * DAY:
                rows.append({"id": tid, "viewed_at": viewed, "account_id": account})
        if processed >= expected or start + len(items) >= expected:
            break
    if len(accounts) > 1:
        raise PlexHistoryIsolationError("Plex历史响应包含多个用户，已拒绝作为推荐依据")
    if rows and not account_id and len(accounts) != 1:
        raise PlexHistoryIsolationError("Plex历史未提供可验证的单一用户标识")
    if expected is not None and processed < expected:
        raise RuntimeError("Plex历史超过安全分页上限，已拒绝使用不完整结果")
    return rows


def read_plex_history_cached(engine, client, section, now, account_id, days=400, maximum=10000):
    """Reuse recent history and fetch only rows newer than the saved watermark."""
    history_cache_ttl = 6 * 3600
    scope = f"{engine.daily_scope()}:{section}:{account_id}"
    saved = engine.store.get("plex_history_cache", {}) or {}
    saved_events = saved.get("events", []) if saved.get("scope") == scope else []
    saved_events = [
        row for row in saved_events
        if str(row.get("account_id") or account_id) == str(account_id)
        and 0 <= now - _number(row.get("viewed_at")) <= days * DAY
    ]
    age = now - _number(saved.get("updated_at"))
    if saved_events and 0 <= age < history_cache_ttl:
        return saved_events, saved, "cache"
    history_watermark = max((_number(row.get("viewed_at")) for row in saved_events), default=0)
    after = max(now - days * DAY, history_watermark - DAY) if history_watermark else None
    fresh = read_plex_history(
        client, section, now, days=days, maximum=maximum,
        account_id=account_id, after=after,
    )
    merged = {}
    for row in saved_events + fresh:
        viewed = _number(row.get("viewed_at"))
        if not (0 <= now - viewed <= days * DAY):
            continue
        key = (str(row.get("id") or ""), int(viewed), str(row.get("account_id") or account_id))
        if key[0].isdigit():
            merged[key] = {"id": key[0], "viewed_at": viewed, "account_id": key[2]}
    events = sorted(merged.values(), key=lambda row: (-row["viewed_at"], row["id"]))[:maximum]
    state = {
        "scope": scope,
        "section": str(section),
        "account_id": str(account_id),
        "updated_at": now,
        "history_watermark": max((_number(row.get("viewed_at")) for row in events), default=0),
        "events": events,
    }
    return events, state, "incremental" if history_watermark else "full"


def read_plex_similar(client, seed_id, limit=30):
    if not str(seed_id).isdigit():
        return []
    for suffix in ("nearest", "similar"):
        try:
            root = client._xml(
                f"/library/metadata/{seed_id}/{suffix}",
                params={"type": 10, "limit": int(limit)},
            )
            result = []
            for item in list(root.findall("Track")) + list(root.findall("Metadata")):
                tid = str(item.get("ratingKey") or "")
                if tid.isdigit() and tid != str(seed_id) and tid not in result:
                    result.append(tid)
            if result:
                return result[:limit]
        except Exception:
            continue
    return []


def recommend_rotating_v035(engine, base_recommend, *args, **kwargs):
    """Adapter for the existing v0.3.4 DailyMixin recommendation call."""
    preserve_ids = kwargs.pop("preserve_ids", None)
    bound = inspect.signature(base_recommend).bind(*args, **kwargs)
    bound.apply_defaults()
    required = ("tracks", "features", "feedback", "settings", "history", "seed_ids", "now", "seed")
    if any(key not in bound.arguments for key in required):
        raise RuntimeError("每日推荐接口不兼容，现有 Plex 歌单未修改。")
    values = bound.arguments
    now = values["now"]
    scope = engine.daily_scope()
    generation = int(engine.store.get("daily_generation_counter", 0) or 0) + 1
    batch_seed = f"{values['seed']}|{POLICY_VERSION}|{generation}"
    published_history = list(values["history"] or [])
    previous_ids = set(str(value) for value in published_history[-1].get("ids", [])) if published_history else set()

    warnings = []
    try:
        legacy = base_recommend(*args, **kwargs)
        legacy_ids = [str(item.get("id")) for item in legacy.get("items", []) if str(item.get("id", "")).isdigit()]
    except Exception:
        legacy_ids = []
        warnings.append("旧版偏好评分暂不可用；Plex 历史推荐仍可正常生成。")
    events = None
    similarities = {}
    cache = dict(engine.store.get("daily_similarity_cache", {}) or {})
    plex_mode = "真实播放历史"
    plex_history_account_scoped = False
    history_cache_state = None
    history_cache_mode = "off"
    product_settings = engine.store.get("product_settings", {}) or {}
    if product_settings.get("behavior_enabled", True) is False:
        plex_mode = "播放行为学习已关闭"
        events = []
        plex = None
    else:
      try:
        plex_settings = engine.store.get("settings")
        plex = engine.plex_factory(plex_settings)
        history_user = str(product_settings.get("behavior_user") or "").strip()
        account_id = str(product_settings.get("behavior_account_id") or "").strip() or resolve_plex_account_id(plex, history_user)
        events, history_cache_state, history_cache_mode = read_plex_history_cached(
            engine, plex, plex_settings["section"], now, account_id
        )
        plex_history_account_scoped = True
        plex_mode = "真实播放历史（已限定设置用户）" if history_user else "真实播放历史（服务器唯一账户）"
      except PlexHistoryIsolationError:
        plex_mode = "曲目元数据降级（已拒绝混合用户历史）"
        warnings.append("Plex历史包含多个用户，已安全降级为当前曲库的播放字段。")
        plex = None
      except Exception:
        plex_mode = "曲目元数据降级"
        warnings.append("Plex历史接口暂不可用，已使用曲库内的播放时间和次数。")
        plex = None
    recent = _recent_seed_ids(values["tracks"], events, now, POLICY["recent_days"], POLICY["seed_limit"])
    if plex is None and recent:
        try:
            plex_settings = engine.store.get("settings")
            plex = engine.plex_factory(plex_settings)
        except Exception:
            plex = None
    for seed_id in recent:
        key = scope + ":" + seed_id
        saved = cache.get(key) or {}
        if 0 <= now - _number(saved.get("fetched_at")) < 7 * DAY and isinstance(saved.get("ids"), list):
            similarities[seed_id] = [str(value) for value in saved["ids"] if str(value).isdigit()]
        elif plex is not None:
            ids = read_plex_similar(plex, seed_id, POLICY["similar_per_seed"])
            similarities[seed_id] = ids
            cache[key] = {"fetched_at": now, "ids": ids}
    similarity_source = "Plex 相似歌曲" if any(similarities.values()) else "曲库关系"
    result = select_daily_mix(
        values["tracks"], values["features"], values["feedback"], values["settings"],
        values["history"], values["seed_ids"], now, batch_seed,
        play_events=events, similar_ids=similarities, preview_history=None,
        legacy_preference_ids=legacy_ids, behavior=values.get("behavior") or {},
        preserve_ids=preserve_ids,
    )
    result["warnings"] = warnings + result["warnings"]
    result["stats"]["plex_history_mode"] = plex_mode
    result["stats"]["plex_history_account_scoped"] = plex_history_account_scoped
    result["stats"]["plex_history_cache_mode"] = history_cache_mode
    result["stats"]["plex_similar_seed_count"] = sum(bool(similarities.get(key)) for key in recent)
    result["stats"]["similarity_source"] = similarity_source
    result["stats"]["legacy_preference_candidate_count"] = len(set(legacy_ids))
    selected = result["items"]
    overlap = sum(str(item["id"]) in previous_ids for item in selected)
    result["stats"].update({
        "rotation_overlap": overlap,
        "rotation_previous": len(previous_ids),
        "rotation_window_days": result["stats"]["daily_avoid_window_days"],
        "rotation_excluded": result["stats"]["excluded_recent_daily"],
    })
    result["warnings"].append(
        f"本批{len(selected)}首，与上一批重复{overlap}首；按最近推荐防重复规则选择。"
    )
    result["_v035_state"] = {
        "generation": generation,
        "similarity_cache": dict(list(cache.items())[-1000:]),
        "diagnostics": {"created_at": now, **result["stats"]},
        "history_cache": history_cache_state,
    }
    return result


def save_v035_plan(engine, values):
    values = dict(values)
    plan = values.get("daily_plan")
    if isinstance(plan, dict) and isinstance(plan.get("_v035_state"), dict):
        state = plan.pop("_v035_state")
        diagnostic_history = list(engine.store.get("daily_policy_diagnostic_history", []) or [])
        diagnostic_history.append(state["diagnostics"])
        values.update({
            "daily_generation_counter": state["generation"],
            "daily_similarity_cache": state["similarity_cache"],
            "daily_policy_diagnostics": state["diagnostics"],
            "daily_policy_diagnostic_history": diagnostic_history[-90:],
        })
        if state.get("history_cache") is not None:
            values["plex_history_cache"] = state["history_cache"]
    engine.store.set_many(values)


def attach_policy_routes(app, store, engine, body, ensure_idle):
    """Small settings surface; existing daily settings and database are reused."""

    @app.get("/api/daily/policy")
    def policy_settings():
        saved = store.get("daily_settings", {}) or {}
        values = {key: saved.get(key, POLICY[key]) for key in (
            "size", "rediscovery_days", "artist_cap", "favorite_percent"
        )}
        values["daily_avoid_days"] = POLICY["daily_avoid_days"]
        return values | {
            "hour": saved.get("hour", 6), "algorithm_version": POLICY_VERSION,
            "recent_mode": "activity", "recent_seed_limit": POLICY["recent_seed_limit"],
            "recent_max_days": POLICY["recent_max_days"],
        }

    @app.get("/api/daily/diagnostics")
    def policy_diagnostics():
        latest = store.get("daily_policy_diagnostics", {}) or {}
        return {
            "latest": latest,
            "display": diagnostic_display(latest),
            "history_count": len(store.get("daily_policy_diagnostic_history", []) or []),
        }

    def product_settings_public():
        saved = store.get("product_settings", {}) or {}
        return {
            "behavior_enabled": saved.get("behavior_enabled", True) is not False,
            "behavior_user": str(saved.get("behavior_user") or "").strip()[:120],
        }

    @app.get("/api/product/settings")
    def get_product_settings():
        return product_settings_public()

    @app.post("/api/daily/policy")
    async def save_policy(req: Request):
        data = await body(req)
        ensure_idle()
        limits = {
            "size": (10, 100),
            "rediscovery_days": (30, 3650),
            "artist_cap": (1, 6),
            "hour": (0, 23),
        }
        with engine.exclusive():
            saved = dict(store.get("daily_settings", {}) or {})
            if "daily_avoid_days" in data and data["daily_avoid_days"] != POLICY["daily_avoid_days"]:
                raise ValueError("daily_avoid_days固定为21天")
            for key, (low, high) in limits.items():
                if key not in data:
                    continue
                if isinstance(data[key], bool):
                    raise ValueError(key + "需为整数")
                try:
                    value = int(data[key])
                except (TypeError, ValueError):
                    raise ValueError(key + "需为整数") from None
                if not low <= value <= high:
                    raise ValueError(f"{key}须在{low}～{high}之间")
                saved[key] = value
            if "favorite_percent" in data:
                if isinstance(data["favorite_percent"], bool):
                    raise ValueError("favorite_percent需为整数")
                value = int(data["favorite_percent"])
                if value not in (10, 20, 30, 40):
                    raise ValueError("favorite_percent须为10、20、30或40")
                saved["favorite_percent"] = value
            saved.pop("recent_days", None)
            saved.pop("favorite_cap", None)
            saved["daily_avoid_days"] = POLICY["daily_avoid_days"]
            store.set_many({"daily_settings": saved, "daily_plan": None})
        return {"message": "推荐策略已保存；不会立即修改 Plex。下次生成时生效。"}
