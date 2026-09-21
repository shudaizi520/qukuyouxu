"""Single orchestration boundary for importing and maintaining external playlists."""
from __future__ import annotations

import hashlib
import json
import time

from .external_match import apply_external_confirmation, match_external_tracks
from .external_playlist_sync import create_or_reconcile_external_playlist, delete_owned_external_playlist
from .external_sources import ExternalSourceError, parse_uploaded_playlist, recognize_source, refresh_needs_confirmation
from .external_store import ExternalRepository
from .match import Catalog


def _safety(message):
    from .engine import SafetyError

    return SafetyError(message)


def _digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def _track_revision(tracks):
    keys = ("id", "title", "artist", "album", "duration", "available", "guid", "paths")
    return _digest([[row.get(key) for key in keys] for row in tracks])


def _counts(rows):
    result = {key: 0 for key in ("matched", "review", "missing", "ignored")}
    for row in rows:
        status = row.get("status")
        if status in result:
            result[status] += 1
    return result


class ExternalPlaylistService:
    def __init__(self, store, plex_factory, providers, clock=time.time):
        self.store = store
        self.profile_id = str(getattr(store, "profile_id", "default") or "default")
        self.repository = ExternalRepository(store)
        self.plex_factory = plex_factory
        self.providers = providers
        self.clock = clock

    def _settings(self):
        settings = self.store.get("settings", {}) or {}
        if not settings.get("plex_url") or not settings.get("plex_token") or not str(settings.get("section") or "").isdigit():
            raise _safety("请先连接 Plex 并选择音乐资料库")
        return settings

    def _catalog(self):
        settings = self._settings()
        plex = self.plex_factory(settings)
        tracks = plex.tracks(settings["section"])
        return plex, tracks, _track_revision(tracks)

    def _record(self, source_id, kind, status, started_at, message="", **payload):
        self.repository.append_run(self.profile_id, source_id, {
            "kind": kind, "status": status, "started_at": started_at,
            "finished_at": self.clock(), "message": str(message or "")[:300], **payload,
        })

    def _match_with_catalog(self, source_id, tracks, catalog_revision):
        source_tracks = self.repository.list_tracks(self.profile_id, source_id)
        previous = {
            row["source_track_key"]: row
            for row in self.repository.list_matches(self.profile_id, source_id)
            if row.get("manual")
        }
        rows = match_external_tracks(source_tracks, tracks, previous, catalog_revision)
        self.repository.replace_matches(self.profile_id, source_id, rows, catalog_revision)
        return rows

    def _apply_snapshot(self, source, snapshot, *, force, started, kind):
        old_count = len(self.repository.list_tracks(self.profile_id, source["id"]))
        new_count = len(snapshot.get("tracks") or [])
        if not force and refresh_needs_confirmation(old_count, new_count):
            self.repository.set_needs_confirmation(self.profile_id, source["id"], True)
            self._record(source["id"], kind, "attention", started, "歌曲减少较多，等待确认")
            return {
                "source_id": source["id"], "status": "confirmation_required",
                "old_count": old_count, "new_count": new_count,
            }
        plex, tracks, catalog_revision = self._catalog()
        previous = {
            row["source_track_key"]: row
            for row in self.repository.list_matches(self.profile_id, source["id"])
            if row.get("manual")
        }
        rows = match_external_tracks(snapshot.get("tracks") or [], tracks, previous, catalog_revision)
        source = self.repository.replace_snapshot_and_matches(
            self.profile_id, source["id"], snapshot, self.clock(), rows, catalog_revision,
        )
        sync = self._sync_managed(source, plex, rows)
        self._record(source["id"], kind, "completed", started, "刷新完成", counts=_counts(rows))
        return {"source_id": source["id"], "status": "updated", "counts": _counts(rows), "sync": sync}

    def _sync_managed(self, source, plex, rows):
        managed = self.repository.get_managed(self.profile_id, source["id"])
        if not managed:
            return None
        if managed.get("order_attention"):
            return {"status": "order_attention", "playlist_id": managed["id"]}
        by_key = {row["source_track_key"]: row for row in rows}
        desired = []
        seen = set()
        for track in self.repository.list_tracks(self.profile_id, source["id"]):
            match_row = by_key.get(track["source_track_key"]) or {}
            track_id = str(match_row.get("plex_track_id") or "")
            if match_row.get("status") == "matched" and track_id and track_id not in seen:
                desired.append(track_id)
                seen.add(track_id)
        if not desired:
            return {"status": "no_matches", "playlist_id": managed["id"]}
        from .playlist_hub import apply_manual_edits
        desired = apply_manual_edits(self.store, "external", source["id"], desired)
        if not desired:
            return {"status": "no_matches", "playlist_id": managed["id"]}
        sync_source = {**source, "title": managed["title"]}
        after, revised = create_or_reconcile_external_playlist(
            plex, self.store.get("installation_id"), sync_source, managed, desired
        )
        self.repository.save_managed(self.profile_id, source["id"], revised)
        return {
            "status": "attention" if revised.get("order_attention") else "updated",
            "playlist_id": after["id"], "count": len(after["items"]),
        }

    def import_source(self, value=None, filename=None, content=None):
        if bool(value) == bool(filename or content is not None):
            raise ValueError("请选择链接或上传文件中的一种")
        started = self.clock()
        if value:
            recognized = (
                self.providers.recognize(value)
                if hasattr(self.providers, "recognize") else recognize_source(value)
            )
            snapshot = self.providers.fetch(recognized)
        else:
            if not filename or not isinstance(content, bytes):
                raise ValueError("上传文件无效")
            snapshot = parse_uploaded_playlist(filename, content)
        existing = next((
            row for row in self.repository.list_sources(self.profile_id)
            if row["provider"] == snapshot["provider"] and row["external_id"] == snapshot["external_id"]
        ), None)
        if existing:
            source = existing
            if existing["revision"] == snapshot["revision"]:
                if not self.repository.list_matches(self.profile_id, source["id"]):
                    self.match(source["id"])
            else:
                try:
                    self._apply_snapshot(source, snapshot, force=False, started=started, kind="import")
                except Exception as exc:
                    self.repository.record_failure(
                        self.profile_id, source["id"], str(exc)[:300] or type(exc).__name__, self.clock()
                    )
                    self._record(source["id"], "import", "error", started, str(exc)[:300])
                    raise
                return self.public_source(source["id"])
        else:
            source = self.repository.upsert_source(self.profile_id, snapshot, self.clock())
            self.match(source["id"])
        self._record(source["id"], "import", "completed", started, "导入完成")
        return self.public_source(source["id"])

    def match(self, source_id):
        started = self.clock()
        source = self.repository.get_source(self.profile_id, source_id)
        _plex, tracks, revision = self._catalog()
        rows = self._match_with_catalog(source["id"], tracks, revision)
        self._record(source["id"], "match", "completed", started, "匹配完成", counts=_counts(rows))
        return self.public_source(source["id"])

    def confirm(self, source_id, track_key, choice):
        started = self.clock()
        source = self.repository.get_source(self.profile_id, source_id)
        _plex, tracks, revision = self._catalog()
        rows = self.repository.list_matches(self.profile_id, source["id"])
        target = next((row for row in rows if row["source_track_key"] == str(track_key)), None)
        if target is None:
            raise ValueError("没有这条待确认歌曲")
        if target.get("catalog_revision") != revision:
            rows = self._match_with_catalog(source["id"], tracks, revision)
            target = next(row for row in rows if row["source_track_key"] == str(track_key))
        confirmed = apply_external_confirmation(target, choice, Catalog(tracks))
        revised = [confirmed if row["source_track_key"] == target["source_track_key"] else row for row in rows]
        self.repository.replace_matches(self.profile_id, source["id"], revised, revision)
        self._record(source["id"], "confirm", "completed", started, "确认完成")
        return self.public_source(source["id"])

    def confirm_many(self, source_id, choices):
        if not isinstance(choices, list) or not 1 <= len(choices) <= 200:
            raise ValueError("请选择 1 至 200 首待确认歌曲")
        selected = {}
        for item in choices:
            if not isinstance(item, dict) or not isinstance(item.get("choice"), dict):
                raise ValueError("歌曲确认内容无效")
            key = str(item.get("track_key") or "")
            if not key:
                raise ValueError("歌曲确认内容无效")
            if key in selected:
                raise ValueError("不能重复确认同一首歌曲")
            selected[key] = item["choice"]
        started = self.clock()
        source = self.repository.get_source(self.profile_id, source_id)
        _plex, tracks, revision = self._catalog()
        rows = self.repository.list_matches(self.profile_id, source["id"])
        review_keys = {row["source_track_key"] for row in rows if row.get("status") == "review"}
        if any(row.get("catalog_revision") != revision for row in rows):
            previous = {row["source_track_key"]: row for row in rows if row.get("manual")}
            source_tracks = self.repository.list_tracks(self.profile_id, source["id"])
            rows = match_external_tracks(source_tracks, tracks, previous, revision)
        by_key = {row["source_track_key"]: row for row in rows}
        catalog = Catalog(tracks)
        confirmed = {}
        for key, choice in selected.items():
            target = by_key.get(key)
            if not target or key not in review_keys:
                raise ValueError("选择的歌曲不再需要确认，请刷新待确认列表")
            if choice.get("status") not in ("matched", "missing"):
                raise ValueError("确认状态无效")
            confirmed[key] = apply_external_confirmation(target, choice, catalog)
        revised = [confirmed.get(row["source_track_key"], row) for row in rows]
        self.repository.replace_matches(self.profile_id, source["id"], revised, revision)
        self._record(source["id"], "confirm", "completed", started, f"已确认 {len(confirmed)} 首")
        return self.public_source(source["id"])

    def publish(self, source_id, title, expected_revision):
        started = self.clock()
        source = self.repository.get_source(self.profile_id, source_id)
        if str(expected_revision or "") != source["revision"]:
            raise _safety("外部歌单已经变化，请刷新后再发布")
        title = str(title or "").strip()
        if not title or len(title) > 80 or any(ord(char) < 32 for char in title):
            raise ValueError("Plex 歌单名称无效")
        plex, tracks, catalog_revision = self._catalog()
        rows = self.repository.list_matches(self.profile_id, source["id"])
        if not rows or any(row.get("catalog_revision") != catalog_revision for row in rows):
            rows = self._match_with_catalog(source["id"], tracks, catalog_revision)
        by_key = {row["source_track_key"]: row for row in rows}
        desired = []
        seen = set()
        for track in self.repository.list_tracks(self.profile_id, source["id"]):
            row = by_key.get(track["source_track_key"]) or {}
            track_id = str(row.get("plex_track_id") or "")
            if row.get("status") == "matched" and track_id and track_id not in seen:
                desired.append(track_id)
                seen.add(track_id)
        if not desired:
            raise _safety("没有可靠匹配的本地歌曲，不能创建 Plex 歌单")
        from .playlist_hub import apply_manual_edits
        desired = apply_manual_edits(self.store, "external", source["id"], desired)
        if not desired:
            raise _safety("个人调整后没有可写入的歌曲")
        managed = self.repository.get_managed(self.profile_id, source["id"])
        after, record = create_or_reconcile_external_playlist(
            plex, self.store.get("installation_id"), {**source, "title": title}, managed, desired
        )
        self.repository.save_managed(self.profile_id, source["id"], record)
        status = "attention" if record.get("order_attention") else "completed"
        self._record(source["id"], "publish", status, started, "发布完成", playlist_id=after["id"])
        return {"playlist_id": after["id"], "count": len(after["items"]), "order_attention": record.get("order_attention", False)}

    def refresh(self, source_id, *, force=False, bypass_retry=False):
        if not isinstance(force, bool) or not isinstance(bypass_retry, bool):
            raise ValueError("刷新方式无效")
        started = self.clock()
        source = self.repository.get_source(self.profile_id, source_id)
        if not (force or bypass_retry) and source.get("next_retry_at") and float(source["next_retry_at"]) > self.clock():
            return {"source_id": source["id"], "status": "waiting", "retry_at": source["next_retry_at"]}
        recognized = {"provider": source["provider"], "external_id": source["external_id"], "url": source["source_url"]}
        try:
            snapshot = self.providers.fetch(recognized)
            return self._apply_snapshot(source, snapshot, force=force, started=started, kind="refresh")
        except Exception as exc:
            self.repository.record_failure(self.profile_id, source["id"], str(exc)[:300] or type(exc).__name__, self.clock())
            self._record(source["id"], "refresh", "error", started, str(exc)[:300])
            raise

    def set_follow_updates(self, source_id, enabled):
        source = self.repository.get_source(self.profile_id, source_id)
        if enabled and source.get("provider") not in ("qq", "netease"):
            raise ValueError("本地文件不能跟随远程更新，需要变化时请重新上传")
        source = self.repository.set_follow_updates(self.profile_id, source_id, enabled)
        return {"id": source["id"], "follow_updates": source["follow_updates"]}

    def remove(self, source_id, confirm_title):
        started = self.clock()
        source = self.repository.get_source(self.profile_id, source_id)
        managed = self.repository.get_managed(self.profile_id, source["id"])
        expected_title = managed["title"] if managed else source["title"]
        if str(confirm_title or "") != expected_title:
            raise _safety("确认名称不一致，不删除")
        if managed:
            plex = self.plex_factory(self._settings())
            delete_owned_external_playlist(
                plex, self.store.get("installation_id"), source["id"], managed, confirm_title
            )
        self._record(source["id"], "remove", "completed", started, "删除完成")
        self.repository.delete_source(self.profile_id, source["id"])
        return {"removed": source["id"]}

    def rematch_missing(self):
        sources = self.repository.list_sources(self.profile_id)
        if not sources:
            return {"sources": [], "matched": 0, "missing": 0, "errors": []}
        plex, tracks, revision = self._catalog()
        result = {"sources": [], "matched": 0, "missing": 0, "errors": []}
        for source in sources:
            try:
                rows = self._match_with_catalog(source["id"], tracks, revision)
                sync = self._sync_managed(source, plex, rows)
                counts = _counts(rows)
                result["matched"] += counts["matched"]
                result["missing"] += counts["missing"]
                result["sources"].append({"source_id": source["id"], "status": "updated", "counts": counts, "sync": sync})
            except Exception as exc:
                result["errors"].append({"source_id": source["id"], "error": type(exc).__name__})
        return result

    def auto_refresh(self):
        result = {"sources": [], "updated": 0, "errors": 0}
        for source in self.repository.list_sources(self.profile_id):
            if not source.get("follow_updates"):
                continue
            try:
                row = self.refresh(source["id"])
                result["sources"].append(row)
                if row.get("status") == "updated":
                    result["updated"] += 1
            except Exception as exc:
                result["errors"] += 1
                result["sources"].append({"source_id": source["id"], "status": "error", "error": type(exc).__name__})
        return result

    def public_source(self, source_id):
        source = self.repository.get_source(self.profile_id, source_id)
        tracks = self.repository.list_tracks(self.profile_id, source["id"])
        matches = {row["source_track_key"]: row for row in self.repository.list_matches(self.profile_id, source["id"])}
        public_tracks = []
        for track in tracks:
            matched = matches.get(track["source_track_key"], {})
            public_tracks.append({**track, **{
                key: matched.get(key, default) for key, default in (
                    ("status", "missing"), ("plex_track_id", ""), ("candidate_ids", []),
                    ("reason", "missing"), ("manual", False),
                )
            }})
        managed = self.repository.get_managed(self.profile_id, source["id"])
        return {
            **source, "counts": _counts(public_tracks), "tracks": public_tracks,
            "managed": ({"id": managed["id"], "title": managed["title"], "order_attention": bool(managed.get("order_attention"))} if managed else None),
        }
