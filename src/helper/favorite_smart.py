"""Conservatively reconcile one profile's Plex-side favorite smart playlist."""
from __future__ import annotations

import re
from urllib.parse import parse_qs, unquote, urlsplit

from .clients import PlexNotFound, PlexWriteRejected


def _favorite_title(value):
    return re.sub(r"^[\s❤♥💖️]+", "", str(value or "")).strip() == "我的最爱"


def _content_section(content, machine):
    """Return the source section only for this Plex server's canonical URI."""
    parsed = urlsplit(str(content or ""))
    path = unquote(parsed.path)
    match = re.fullmatch(r"/com\.plexapp\.plugins\.library/library/sections/(\d+)/all", path)
    if parsed.scheme != "server" or parsed.netloc != machine or not match:
        return ""
    return match.group(1)


def _rule_kind(content, section, machine):
    """Recognize only a pure rating rule; unknown filters must never be overwritten."""
    if _content_section(content, machine) != section:
        return "unknown"
    parsed = urlsplit(str(content or ""))
    params = parse_qs(parsed.query, keep_blank_values=True)
    if params.get("type") != ["10"]:
        return "unknown"
    rating = {key: value for key, value in params.items() if key != "type"}
    if rating == {"track.userRating>": ["8"]}:
        return "current"
    if rating in ({"track.userRating": ["10"]},
                  {"track.userRating>>": ["9"]}):
        return "five_star_only"
    return "unknown"


def _needs_review(store, previous, section, message):
    state = {"status": "needs_review", "playlist_id": str(previous.get("playlist_id") or ""),
             "section": section, "message": message}
    store.set("favorite_smart_v1", state)
    return state


def ensure_profile_favorites(engine):
    """Create/reuse/migrate only a uniquely verified playlist in this music library."""
    store = engine.store
    section = str((store.get("settings") or {}).get("section") or "")
    if not section.isdigit():
        return {"status": "needs_review", "playlist_id": "", "message": "请先选择音乐曲库"}
    plex = engine.plex_factory(store.get("settings"))
    machine = str(getattr(plex, "machine", "") or "")
    if not machine:
        machine = str(plex.identity().get("machine") or "")
    if not machine:
        return {"status": "needs_review", "playlist_id": "", "message": "无法确认 Plex 服务器身份"}
    previous = store.get("favorite_smart_v1") or {}
    candidates = []
    for row in plex.playlists():
        if not _favorite_title(row.get("title")) or row.get("playlistType") != "audio":
            continue
        pid = str(row.get("ratingKey") or "")
        if not pid.isdigit():
            continue
        if str(row.get("smart") or "0") != "1":
            # A plain playlist with the same name is ambiguous, even if it is
            # impossible to determine its source library from Plex metadata.
            candidates.append({"id": pid, "plain": True})
            continue
        info = plex.smart_playlist_info(pid)
        if str(info.get("id") or "") != pid or not _favorite_title(info.get("title")):
            candidates.append({"id": pid, "unverified": True})
            continue
        source_section = _content_section(info.get("content"), machine)
        if source_section == section:
            candidates.append(info)
        elif not source_section:
            candidates.append({"id": pid, "unverified": True})

    if len(candidates) > 1 or any(row.get("plain") or row.get("unverified") for row in candidates):
        return _needs_review(store, previous, section, "已有同名歌单需要人工核对")

    if candidates:
        info = candidates[0]
        pid = str(info["id"])
        if previous.get("playlist_id") and str(previous["playlist_id"]) != pid:
            return _needs_review(store, previous, section, "已记录的歌单编号与现有歌单不一致")
        kind = _rule_kind(info.get("content"), section, machine)
        if kind == "unknown":
            return _needs_review(store, previous, section, "现有智能歌单包含无法确认的筛选规则")
        if kind == "five_star_only":
            store.set("favorite_smart_previous_rule_v1", {"playlist_id": pid, "content": info["content"]})
            info = plex.replace_favorite_smart_rule(
                pid, section, expected_content=info["content"], expected_title=info["title"],
            )
            if _rule_kind(info.get("content"), section, machine) != "current":
                raise ValueError("Plex 歌单规则修改后回读不一致，请人工核对")
        state = {"status": "synced", "playlist_id": pid, "section": section}
    else:
        if previous.get("status") == "pending":
            return {"status": "needs_review", "playlist_id": "", "message": "Plex 创建结果不确定，请先人工核对，勿重复创建"}
        # A listing can lag or fail. Recreate only when direct lookup confirms
        # that the previously recorded playlist was actually removed.
        if previous.get("playlist_id"):
            try:
                plex.smart_playlist_info(str(previous["playlist_id"]))
            except PlexNotFound:
                pass
            except Exception:
                return _needs_review(store, previous, section, "已记录的 Plex 歌单暂时无法找到")
            else:
                return _needs_review(store, previous, section, "Plex 歌单列表与详情不一致")
        # Persist the intent *before* the network write. A timeout may happen
        # after Plex creates the playlist but before it returns its ID.
        store.set("favorite_smart_v1", {"status": "pending", "playlist_id": "", "section": section})
        try:
            info = plex.create_favorite_smart(section)
        except PlexWriteRejected:
            # The POST was rejected before a playlist could be created. Do not
            # leave an unrecoverable pending marker when permissions change.
            _needs_review(store, {}, section, "Plex 拒绝创建歌单，请核对当前用户权限")
            raise
        if str(info.get("section") or "") != section or _rule_kind(info.get("content"), section, machine) != "current":
            raise ValueError("新建 Plex 歌单规则回读不一致，请人工核对")
        state = {"status": "synced", "playlist_id": str(info["id"]), "section": section}
    store.set("favorite_smart_v1", state)
    return state
