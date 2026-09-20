"""Balanced, explainable Daily Mix V2 selection.

This module only builds a preview.  It never writes to Plex and it never reads
audio files.  Plex Sonic is an optional source of neighbours; metadata remains
the bounded fallback.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import inspect

from .behavior_store import BehaviorRepository
from .daily_mix_v035 import (
    DAY,
    PlexHistoryIsolationError,
    _artists,
    _history_keys,
    _jitter,
    _metadata_similarity,
    _normal,
    _number,
    _recent_seed_ids,
    _song_key,
    _valid,
    read_plex_history_cached,
    read_plex_similar,
    resolve_profile_playback_account,
)


POLICY_VERSION = "daily-mix-v2.0.0"
BUCKETS = ("稳定偏好", "近期口味", "新鲜发现", "久未重听", "跨口味探索", "恢复观察")
BASE_TARGETS = {
    "稳定偏好": 14,
    "近期口味": 8,
    "新鲜发现": 14,
    "久未重听": 8,
    "跨口味探索": 4,
    "恢复观察": 2,
}
ORDINARY_REPEAT_DAYS = 21
STABLE_REPEAT_DAYS = 14


def discovery_target(user_state: dict | None) -> int:
    """Return the fresh-discovery share for a 50-track mix."""
    state = user_state or {}
    valid = int(_number(state.get("discovery_valid")))
    if valid < 30:
        return 18
    completed = _number(state.get("discovery_completed")) / max(1, valid)
    skipped = _number(state.get("discovery_early_skips")) / max(1, valid)
    if completed >= 0.70 and skipped <= 0.20:
        return 24
    if completed <= 0.20 or skipped >= 0.50:
        return 14
    quality = max(0.0, min(1.0, (completed - skipped + 0.5) / 1.2))
    return max(14, min(24, round(14 + quality * 10)))


def _scaled_targets(size: int, user_state: dict | None) -> dict[str, int]:
    size = max(1, min(100, int(size)))
    discovery = discovery_target(user_state)
    fresh = discovery - BASE_TARGETS["跨口味探索"]
    stable = BASE_TARGETS["稳定偏好"] - (discovery - 18)
    raw50 = dict(BASE_TARGETS, **{"稳定偏好": stable, "新鲜发现": fresh})
    raw = {key: size * value / 50 for key, value in raw50.items()}
    targets = {key: int(value) for key, value in raw.items()}
    missing = size - sum(targets.values())
    order = sorted(BUCKETS, key=lambda key: (-(raw[key] - targets[key]), BUCKETS.index(key)))
    for key in order[:missing]:
        targets[key] += 1
    targets["恢复观察"] = min(2, targets["恢复观察"])
    return targets


def _is_new_or_unplayed(track: dict, now: float) -> bool:
    if _number(track.get("view_count")) <= 0:
        return True
    added = _number(track.get("added_at"))
    return bool(added and 0 <= now - added <= 30 * DAY)


def _is_recovery_candidate(learned: dict, now: float) -> bool:
    cooldown = _number(learned.get("cooldown_until"))
    return (
        _number(learned.get("skip_evidence")) >= 0.25
        and 0 < cooldown <= now
        and not learned.get("hard_avoid")
    )


def bucket_for(track: dict, learned: dict, now: float, recent_similarity: set[str],
               rediscovery_days: int = 90) -> str:
    if _is_new_or_unplayed(track, now):
        return "新鲜发现"
    if _is_recovery_candidate(learned, now):
        return "恢复观察"
    affinity = float(learned.get("affinity", learned.get("long_term_score", 0)) or 0)
    confidence = float(learned.get("confidence", 1 if learned.get("long_term_score") else 0) or 0)
    if affinity > 0.20 and confidence >= 0.20:
        return "稳定偏好"
    if str(track.get("id")) in recent_similarity:
        return "近期口味"
    last = _number(track.get("last_viewed_at"))
    was_positive = (
        affinity > 0
        or _number(learned.get("positive_evidence")) > 0
        or _number(track.get("user_rating")) >= 6
    )
    if was_positive and _number(track.get("view_count")) > 0 and last and now - last >= rediscovery_days * DAY:
        return "久未重听"
    return "跨口味探索"


def _history_blocked(history, now, bucket):
    days = STABLE_REPEAT_DAYS if bucket == "稳定偏好" else ORDINARY_REPEAT_DAYS
    return _history_keys(history, now, days)


def _recent_play_blocked(play_events, by_id, now, bucket):
    days = STABLE_REPEAT_DAYS if bucket == "稳定偏好" else ORDINARY_REPEAT_DAYS
    ids, songs = set(), set()
    for event in play_events or []:
        at = _number(event.get("viewed_at") or event.get("at"))
        if not (0 <= now - at < days * DAY):
            continue
        tid = str(event.get("id") or event.get("track_id") or "")
        ids.add(tid)
        if tid in by_id:
            songs.add(_song_key(by_id[tid]))
    return ids, songs


def _ordered_mix(rows: list[dict], preserve_ids: list[str], seed: str) -> list[dict]:
    by_id = {row["id"]: row for row in rows}
    retained = [by_id[tid] for tid in preserve_ids if tid in by_id]
    rest = [row for row in rows if row["id"] not in set(preserve_ids)]
    rest.sort(key=lambda row: (-_jitter(seed + "|order", row["id"]), row["id"]))
    ordered = list(retained)
    while rest:
        previous = ordered[-1] if ordered else None
        index = next((
            idx for idx, row in enumerate(rest)
            if previous is None or (
                not (row["_artists"] & previous["_artists"])
                and row["bucket"] != previous["bucket"]
            )
        ), 0)
        ordered.append(rest.pop(index))
    return ordered


def select_daily_mix_v2(
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
    user_state=None,
):
    """Build a deterministic Daily Mix V2 preview without mutating Plex."""
    cfg = dict(settings or {})
    size = max(1, min(100, int(cfg.get("size", 30))))
    rediscovery_days = max(30, min(3650, int(cfg.get("rediscovery_days", 90))))
    artist_cap = max(1, min(6, int(cfg.get("artist_cap", 2))))
    album_cap = max(1, min(2, int(cfg.get("album_cap", 1))))
    behavior = behavior or {}
    feedback = feedback or {}
    track_feedback = feedback.get("tracks", {}) or {}
    artist_feedback = feedback.get("artists", {}) or {}
    preserve_ids = [str(value) for value in preserve_ids or []]
    preserve_set = set(preserve_ids)

    valid = [row for row in tracks if _valid(row)]
    by_id = {str(row["id"]): row for row in valid}
    hard_ids = {
        str(key) for key, value in track_feedback.items()
        if (value or {}).get("value") == "avoid"
    }
    hard_ids.update(tid for tid, state in behavior.items() if (state or {}).get("hard_avoid"))
    hard_songs = {_song_key(by_id[tid]) for tid in hard_ids if tid in by_id}
    avoided_artists = {
        artist
        for label, value in artist_feedback.items()
        if (value or {}).get("value") == "avoid"
        for artist in _artists(label)
    }
    favorite_ids = {str(value) for value in seed_ids or []}
    legacy_ids = {str(value) for value in legacy_preference_ids or []}
    similarities = similar_ids or {}
    similar_hits = Counter()
    similar_sources = defaultdict(list)
    for source, values in similarities.items():
        for value in values or []:
            tid = str(value)
            if tid in by_id and tid != str(source):
                similar_hits[tid] += 1
                similar_sources[tid].append(str(source))
    recent_similarity = set(similar_hits)
    combined_history = list(history or []) + list(preview_history or [])

    candidates = []
    excluded_feedback = excluded_fatigue = excluded_history = excluded_recent_plays = 0
    seen_ids, seen_songs = set(), set()
    for row in valid:
        tid = str(row["id"])
        song = _song_key(row)
        if tid in seen_ids or song in seen_songs:
            continue
        seen_ids.add(tid)
        seen_songs.add(song)
        learned = behavior.get(tid, {}) or {}
        artists = _artists(row.get("artist"))
        if tid in hard_ids or song in hard_songs or artists & avoided_artists:
            excluded_feedback += 1
            continue
        if _number(learned.get("cooldown_until")) > now:
            excluded_fatigue += 1
            continue
        bucket = bucket_for(row, learned, now, recent_similarity, rediscovery_days)
        blocked_ids, blocked_songs = _history_blocked(combined_history, now, bucket)
        play_ids, play_songs = _recent_play_blocked(play_events, by_id, now, bucket)
        if tid not in preserve_set and (tid in blocked_ids or song in blocked_songs):
            excluded_history += 1
            continue
        if tid not in preserve_set and (tid in play_ids or song in play_songs):
            excluded_recent_plays += 1
            continue
        affinity = float(learned.get("affinity", learned.get("long_term_score", 0)) or 0)
        confidence = float(learned.get("confidence", 0) or 0)
        fatigue = _number(learned.get("fatigue"))
        count = _number(row.get("view_count"))
        last = _number(row.get("last_viewed_at"))
        stale_days = (now - last) / DAY if last else 99999
        rating = _number(row.get("user_rating"))
        liked = (track_feedback.get(tid) or {}).get("value") == "like"
        favorite = tid in favorite_ids or rating >= 8 or liked
        score = 1.0 + _jitter(seed + "|rank", tid)
        score += max(-2.0, min(6.0, affinity * 5.0)) * max(0.25, confidence)
        score -= min(3.0, fatigue)
        if bucket in ("新鲜发现", "近期口味"):
            score += min(4.0, similar_hits[tid] * 2.0)
        score += 4.0 if favorite else 0.0
        score += 1.5 if tid in legacy_ids else 0.0
        if bucket == "新鲜发现":
            score += 2.5 / (1.0 + count)
        elif bucket == "久未重听":
            score += min(4.0, stale_days / 180)
        elif bucket == "恢复观察":
            score -= min(2.0, _number(learned.get("skip_evidence")))
        reasons = {
            "稳定偏好": ["长期偏好稳定"],
            "近期口味": ["与近期播放相近"],
            "新鲜发现": ["曲库中的新鲜选择"],
            "久未重听": ["很久没有听过"],
            "跨口味探索": ["跨口味探索"],
            "恢复观察": ["冷却结束后少量复查"],
        }[bucket]
        album = str(row.get("album_id") or "") or (
            _normal(row.get("album")) + "|" + ",".join(sorted(artists))
            if _normal(row.get("album")) else ""
        )
        candidates.append({
            "id": tid,
            "title": row.get("title", ""),
            "artist": row.get("artist", ""),
            "album": row.get("album", ""),
            "duration": row.get("duration"),
            "score": round(score, 6),
            "reasons": reasons,
            "song_key": song,
            "source_bucket": bucket,
            "seed_tracks": similar_sources.get(tid, [])[:5],
            "penalties": [],
            "_artists": artists,
            "_album": album,
            "_jitter": _jitter(seed + "|select", tid),
        })

    targets = _scaled_targets(size, user_state)
    ranked = {
        bucket: sorted(
            (row for row in candidates if row["source_bucket"] == bucket),
            key=lambda row: (-row["score"], -row["_jitter"], row["id"]),
        )
        for bucket in BUCKETS
    }

    def choose(current_artist_cap, current_album_cap):
        selected, selected_ids, selected_songs = [], set(), set()
        artists_used, albums_used = Counter(), Counter()
        bucket_counts = Counter()

        def add(row):
            bucket = row["source_bucket"]
            if row["id"] in selected_ids or row["song_key"] in selected_songs:
                return False
            if bucket == "恢复观察" and bucket_counts[bucket] >= 2:
                return False
            if any(artists_used[key] >= current_artist_cap for key in row["_artists"]):
                return False
            if row["_album"] and albums_used[row["_album"]] >= current_album_cap:
                return False
            copy = dict(row)
            copy["bucket"] = bucket
            selected.append(copy)
            selected_ids.add(row["id"])
            selected_songs.add(row["song_key"])
            artists_used.update(row["_artists"])
            if row["_album"]:
                albums_used[row["_album"]] += 1
            bucket_counts[bucket] += 1
            return True

        by_candidate_id = {row["id"]: row for row in candidates}
        for tid in preserve_ids:
            row = by_candidate_id.get(tid)
            if row:
                add(row)
        for bucket in BUCKETS:
            for row in ranked[bucket]:
                if bucket_counts[bucket] >= targets[bucket]:
                    break
                add(row)
        fill = sorted(candidates, key=lambda row: (-row["score"], -row["_jitter"], row["id"]))
        for row in fill:
            if len(selected) >= size:
                break
            add(row)
        return selected

    attempts = []
    cap_steps = [(artist_cap, album_cap)]
    if album_cap < 2:
        cap_steps.append((artist_cap, 2))
    if artist_cap < 3:
        cap_steps.append((3, 2))
    selected, used_artist_cap, used_album_cap = [], artist_cap, album_cap
    for current_artist_cap, current_album_cap in cap_steps:
        attempt = choose(current_artist_cap, current_album_cap)
        attempts.append((len(attempt), current_artist_cap, current_album_cap))
        selected, used_artist_cap, used_album_cap = attempt, current_artist_cap, current_album_cap
        if len(selected) >= size:
            break
    selected = _ordered_mix(selected[:size], preserve_ids, seed)
    bucket_counts = Counter(row["bucket"] for row in selected)
    for bucket in BUCKETS:
        bucket_counts.setdefault(bucket, 0)
    warnings = []
    if len(selected) < size:
        warnings.append(f"符合排除、防重复与多样性规则的歌曲只有{len(selected)}首；没有突破安全规则凑数。")
    retained = sum(row["id"] in preserve_set for row in selected)
    public_items = [{key: value for key, value in row.items() if not key.startswith("_")} for row in selected]
    stats = {
        "algorithm_version": POLICY_VERSION,
        "library_count": len(tracks),
        "eligible_count": len(valid),
        "candidate_count": len(candidates),
        "selected": len(public_items),
        "requested": size,
        "bucket_counts": dict(bucket_counts),
        "discovery_target": discovery_target(user_state),
        "excluded_never_recommend": excluded_feedback,
        "excluded_by_feedback": excluded_feedback,
        "excluded_by_fatigue": excluded_fatigue,
        "excluded_recent_daily": excluded_history,
        "excluded_recent_plays": excluded_recent_plays,
        "cooled_count": excluded_fatigue + excluded_history + excluded_recent_plays,
        "daily_avoid_window_days": ORDINARY_REPEAT_DAYS,
        "stable_repeat_days": STABLE_REPEAT_DAYS,
        "artist_cap_used": used_artist_cap,
        "album_cap_used": used_album_cap,
        "artist_cap_relaxed": used_artist_cap > artist_cap,
        "rule_relaxed": (used_artist_cap, used_album_cap) != (artist_cap, album_cap),
        "attempts": attempts,
        "rolling_retained": retained,
        "rolling_replenished": max(0, len(public_items) - retained),
        "positive_seed_count": sum(
            float((behavior.get(str(row["id"])) or {}).get("affinity", 0) or 0) > 0
            or str(row["id"]) in favorite_ids
            for row in valid
        ),
        "favorite_playlist_seed_count": len(favorite_ids & set(by_id)),
        "favorite_selected_count": sum(row["id"] in favorite_ids for row in public_items),
        "rated_count": sum(_number(row.get("user_rating")) > 0 for row in valid),
        "played_count": sum(_number(row.get("view_count")) > 0 for row in valid),
        "missing_rating_fields": sum(row.get("user_rating") is None for row in tracks),
        "missing_playcount_fields": sum(row.get("view_count") is None for row in tracks),
        "recent_seed_count": len(similarities),
        "related_candidate_count": len(recent_similarity & set(by_id)),
        "rediscovery_candidate_count": sum(row["source_bucket"] == "久未重听" for row in candidates),
        "exploration_candidate_count": sum(row["source_bucket"] in ("新鲜发现", "跨口味探索") for row in candidates),
        "favorite_candidate_count": sum(str(row["id"]) in favorite_ids for row in valid),
        "dedupe_before": len(valid),
        "dedupe_after": len(seen_ids),
    }
    return {"mode": "adaptive_v2", "items": public_items, "stats": stats, "warnings": warnings}


def _metadata_neighbors(tracks, features, seed_ids, limit=30):
    by_id = {str(row.get("id")): row for row in tracks if _valid(row)}
    result = {}
    for seed_id in seed_ids:
        seed_row = by_id.get(str(seed_id))
        if not seed_row:
            continue
        ranked = sorted(
            (
                (_metadata_similarity(row, [seed_row], features), str(row["id"]))
                for row in by_id.values() if str(row["id"]) != str(seed_id)
            ),
            key=lambda value: (-value[0], value[1]),
        )
        result[str(seed_id)] = [tid for score, tid in ranked if score > 0][:limit]
    return result


def recommend_rotating_v2(engine, base_recommend, *args, **kwargs):
    """Adapter for the existing DailyMixin recommendation call."""
    preserve_ids = kwargs.pop("preserve_ids", None)
    user_state = kwargs.pop("user_state", None)
    injected_similarities = kwargs.pop("similar_ids", None)
    bound = inspect.signature(base_recommend).bind(*args, **kwargs)
    bound.apply_defaults()
    required = ("tracks", "features", "feedback", "settings", "history", "seed_ids", "now", "seed")
    if any(key not in bound.arguments for key in required):
        raise RuntimeError("每日推荐接口不兼容，现有 Plex 歌单未修改。")
    values = bound.arguments
    now = values["now"]
    generation = int(engine.store.get("daily_generation_counter", 0) or 0) + 1
    batch_seed = f"{values['seed']}|{POLICY_VERSION}|{generation}"
    warnings = []
    try:
        legacy = base_recommend(*args, **kwargs)
        legacy_ids = [str(row.get("id")) for row in legacy.get("items", []) if str(row.get("id", "")).isdigit()]
    except Exception:
        legacy_ids = []
        warnings.append("旧版偏好评分暂不可用；现有播放数据仍可生成推荐。")

    base_store = getattr(engine.store, "base", engine.store)
    profile_id = str(getattr(engine.store, "profile_id", "") or "")
    if user_state is None and profile_id and hasattr(base_store, "_db"):
        user_state = BehaviorRepository(base_store).load_user_state(profile_id)
    user_state = user_state or {}
    events = []
    similarities = dict(injected_similarities or {})
    cache = dict(engine.store.get("daily_similarity_cache", {}) or {})
    plex_mode = "播放行为学习已关闭"
    scoped = False
    history_state = None
    history_cache_mode = "off"
    plex = None
    if (engine.store.get("product_settings", {}) or {}).get("behavior_enabled", True) is not False:
        try:
            plex_settings = engine.store.get("settings")
            plex = engine.plex_factory(plex_settings)
            account_id, identity = resolve_profile_playback_account(plex, engine.store)
            events, history_state, history_cache_mode = read_plex_history_cached(
                engine, plex, plex_settings["section"], now, account_id,
                profile_identity=identity,
            )
            scoped = True
            plex_mode = "真实播放历史（已限定设置用户）"
        except PlexHistoryIsolationError:
            plex_mode = "曲目元数据降级（已拒绝混合用户历史）"
            warnings.append("Plex历史无法安全限定到当前用户，已改用当前曲库数据。")
            plex = None
        except Exception:
            plex_mode = "曲目元数据降级"
            warnings.append("Plex历史接口暂不可用，已使用曲库内的播放时间和次数。")
            plex = None
    recent = _recent_seed_ids(values["tracks"], events, now, 180, 20)
    scope = engine.daily_scope()
    if injected_similarities is None:
        for seed_id in recent:
            key = scope + ":" + seed_id
            saved = cache.get(key) or {}
            if 0 <= now - _number(saved.get("fetched_at")) < 7 * DAY and isinstance(saved.get("ids"), list):
                similarities[seed_id] = [str(value) for value in saved["ids"] if str(value).isdigit()]
            elif plex is not None:
                ids = read_plex_similar(plex, seed_id, 30)
                similarities[seed_id] = ids
                cache[key] = {"fetched_at": now, "ids": ids}
    sonic_found = any(similarities.values())
    if not sonic_found:
        similarities = _metadata_neighbors(values["tracks"], values["features"], recent)
    result = select_daily_mix_v2(
        values["tracks"], values["features"], values["feedback"], values["settings"],
        values["history"], values["seed_ids"], now, batch_seed,
        play_events=events, similar_ids=similarities, legacy_preference_ids=legacy_ids,
        behavior=values.get("behavior") or {}, preserve_ids=preserve_ids,
        user_state=user_state,
    )
    previous = list(values["history"] or [])
    previous_ids = set(str(value) for value in previous[-1].get("ids", [])) if previous else set()
    result["warnings"] = warnings + result["warnings"]
    result["stats"].update({
        "plex_history_mode": plex_mode,
        "plex_history_account_scoped": scoped,
        "plex_history_cache_mode": history_cache_mode,
        "plex_similar_seed_count": sum(bool(similarities.get(key)) for key in recent) if sonic_found else 0,
        "similarity_source": "Plex 相似歌曲" if sonic_found else "曲库关系",
        "legacy_preference_candidate_count": len(set(legacy_ids)),
        "rotation_overlap": sum(row["id"] in previous_ids for row in result["items"]),
        "rotation_previous": len(previous_ids),
        "rotation_window_days": ORDINARY_REPEAT_DAYS,
        "rotation_excluded": result["stats"]["excluded_recent_daily"],
    })
    result["_v2_state"] = {
        "generation": generation,
        "similarity_cache": dict(list(cache.items())[-1000:]),
        "diagnostics": {"created_at": now, **result["stats"]},
        "history_cache": history_state,
    }
    return result


def save_v2_plan(engine, values):
    values = dict(values)
    plan = values.get("daily_plan")
    if isinstance(plan, dict) and isinstance(plan.get("_v2_state"), dict):
        state = plan.pop("_v2_state")
        history = list(engine.store.get("daily_policy_diagnostic_history", []) or [])
        history.append(state["diagnostics"])
        values.update({
            "daily_generation_counter": state["generation"],
            "daily_similarity_cache": state["similarity_cache"],
            "daily_policy_diagnostics": state["diagnostics"],
            "daily_policy_diagnostic_history": history[-90:],
        })
        if state.get("history_cache") is not None:
            values["plex_history_cache"] = state["history_cache"]
    engine.store.set_many(values)
