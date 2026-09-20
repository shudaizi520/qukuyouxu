"""Conservative three-state matching for external playlist tracks."""
from __future__ import annotations

from .match import Catalog, artist_key, flags, match, title_key, version


STATUS_MAP = {
    "matched": "matched",
    "manual_match": "matched",
    "ambiguous": "review",
    "artist_mismatch": "review",
    "version_mismatch": "review",
    "duration_mismatch": "review",
    "metadata_conflict": "review",
    "unavailable": "review",
    "missing": "missing",
    "missing_tags": "missing",
}


def _text(value, name, limit=300, *, required=True):
    value = str(value or "").strip()
    if required and not value:
        raise ValueError(f"{name}不能为空")
    if len(value) > limit:
        raise ValueError(f"{name}无效")
    return value


def _base_row(source_track_key, catalog_revision):
    return {
        "source_track_key": source_track_key,
        "status": "missing",
        "plex_track_id": "",
        "candidate_ids": [],
        "reason": "missing",
        "manual": False,
        "catalog_revision": catalog_revision,
    }


def _manual_override(row, override, catalog):
    if not isinstance(override, dict):
        return None
    status = str(override.get("status") or "")
    if status == "matched":
        track_id = str(override.get("plex_track_id") or "")
        track = catalog.by_id.get(track_id)
        if track and track.get("available", True):
            return {
                **row, "status": "matched", "plex_track_id": track_id,
                "candidate_ids": [track_id], "reason": "manual_match", "manual": True,
            }
        return None
    if status == "missing":
        override_revision = str(override.get("catalog_revision") or "")
        if override_revision and override_revision != row["catalog_revision"]:
            return None
        return {**row, "status": "missing", "reason": "manual_missing", "manual": True}
    if status == "ignored":
        return {**row, "status": "ignored", "reason": "manual_ignored", "manual": True}
    return None


def match_external_tracks(source_tracks, plex_tracks, overrides, catalog_revision):
    if not isinstance(source_tracks, list) or not isinstance(plex_tracks, list):
        raise ValueError("歌曲列表无效")
    if not isinstance(overrides, dict):
        raise ValueError("人工匹配记录无效")
    catalog_revision = _text(catalog_revision, "曲库修订", 128)
    catalog = Catalog(plex_tracks)
    rows = []
    seen = set()
    for source in source_tracks:
        if not isinstance(source, dict):
            raise ValueError("外部歌曲无效")
        source_key = _text(source.get("source_track_key"), "来源歌曲标识")
        if source_key in seen:
            raise ValueError("外部歌单包含重复歌曲标识")
        seen.add(source_key)
        row = _base_row(source_key, catalog_revision)
        overridden = _manual_override(row, overrides.get(source_key), catalog)
        if overridden is not None:
            rows.append(overridden)
            continue
        artists = source.get("artists")
        if not isinstance(artists, list):
            artists = []
        duration_ms = source.get("duration_ms") or 0
        if isinstance(duration_ms, bool):
            raise ValueError("歌曲时长无效")
        try:
            duration = float(duration_ms) / 1000
        except (TypeError, ValueError, OverflowError):
            raise ValueError("歌曲时长无效") from None
        if not 0 <= duration <= 86_400:
            raise ValueError("歌曲时长无效")
        query = {
            "title": str(source.get("title") or ""),
            "artist": "、".join(str(item) for item in artists),
            "album": str(source.get("album") or ""),
            "duration": duration,
        }
        original_flags = set(source.get("version_flags") or [])
        original_flags.update(version(source.get("version_label") or ""))
        query["_original_version"] = sorted(original_flags)
        # These calls intentionally share the same identity rules as the existing
        # library matcher; no fuzzy title-only fallback is introduced here.
        title_key(query["title"])
        artist_key(query["artist"])
        flags(query)
        found = match(query, catalog)
        reason = str(found.get("status") or "missing")
        status = STATUS_MAP.get(reason)
        if status is None:
            raise ValueError("匹配器返回了未知状态")
        chosen = str(found.get("id") or "") if status == "matched" else ""
        rows.append({
            **row,
            "status": status,
            "plex_track_id": chosen,
            "candidate_ids": [str(item) for item in found.get("candidates") or []],
            "reason": reason,
        })
    return rows


def apply_external_confirmation(match_row, choice, catalog):
    if not isinstance(match_row, dict) or not isinstance(choice, dict) or not isinstance(catalog, Catalog):
        raise ValueError("确认内容无效")
    source_key = _text(match_row.get("source_track_key"), "来源歌曲标识")
    revision = _text(match_row.get("catalog_revision"), "曲库修订", 128)
    base = _base_row(source_key, revision)
    status = str(choice.get("status") or "")
    if status == "matched":
        track_id = _text(choice.get("plex_track_id"), "Plex歌曲标识", 80)
        track = catalog.by_id.get(track_id)
        if not track or not track.get("available", True):
            raise ValueError("选择的 Plex 歌曲不存在或当前不可播放")
        return {
            **base, "status": "matched", "plex_track_id": track_id,
            "candidate_ids": [track_id], "reason": "manual_match", "manual": True,
        }
    if status == "missing":
        return {**base, "status": "missing", "reason": "manual_missing", "manual": True}
    if status == "ignored":
        return {**base, "status": "ignored", "reason": "manual_ignored", "manual": True}
    raise ValueError("确认状态无效")
