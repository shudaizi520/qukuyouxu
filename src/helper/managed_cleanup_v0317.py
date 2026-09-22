"""Safe cleanup for assistant records whose Plex playlist is already gone."""
from __future__ import annotations


def is_confirmed_missing(exc):
    """Only a definite Plex 404 is allowed to unlock local-record cleanup."""
    from .clients import PlexNotFound
    if isinstance(exc, PlexNotFound):
        return True
    status = getattr(exc, "status_code", None)
    text = str(exc or "").casefold()
    return status == 404 or "http 404" in text or "status 404" in text


def forget_missing_managed_playlist(engine, category_id, playlist_id, confirm_title):
    """Forget a stale assistant record without sending a delete request to Plex."""
    store = engine.store
    managed = dict(store.get("managed", {}) or {})
    category_id = str(category_id or "")
    playlist_id = str(playlist_id or "")
    confirm_title = str(confirm_title or "")
    record = managed.get(category_id)
    if not record:
        raise ValueError("这条助手记录已经不存在，请刷新页面")
    saved_id = str(record.get("id") or "")
    saved_title = str(record.get("title") or "")
    if not saved_id or saved_id != playlist_id or saved_title != confirm_title:
        raise ValueError("记录已经变化，请刷新后重新确认")

    plex = engine.plex_factory(store.get("settings", {}) or {})
    try:
        plex.playlist_state(saved_id)
    except Exception as exc:
        if not is_confirmed_missing(exc):
            raise ValueError("暂时无法确认 Plex 歌单是否已删除，未清除任何记录") from None
    else:
        raise ValueError("该歌单仍存在于 Plex；如需删除请使用正常的移除流程")

    managed.pop(category_id, None)
    sources = list(store.get("sources", []) or [])
    for source in sources:
        if str(source.get("id") or "") == category_id:
            source["enabled"] = False
            source["approved"] = False
    changes = {"managed": managed, "sources": sources, "plan": None}
    store.set_many(changes)
    store.log("已只清除不存在的 Plex 歌单本地记录：" + saved_title)
    return {"message": "Plex 中的歌单已不存在；已只清除助手本地记录，歌曲和文件未作任何修改。"}
