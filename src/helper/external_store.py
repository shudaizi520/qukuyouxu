"""Profile-isolated storage for imported playlists and their Plex matches."""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any

from .scoped_store import validate_profile_id


RETRY_DELAYS = (900, 3600, 21600, 86400)
SOURCE_PROVIDERS = frozenset({"qq", "netease", "m3u", "m3u8", "txt", "csv", "text"})
MATCH_STATUSES = frozenset({"matched", "review", "missing", "ignored"})


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def ensure_external_schema(db) -> None:
    db.execute(
        """CREATE TABLE IF NOT EXISTS external_source (
            id TEXT PRIMARY KEY,
            profile_id TEXT NOT NULL,
            provider TEXT NOT NULL,
            external_id TEXT NOT NULL,
            source_url TEXT NOT NULL,
            title TEXT NOT NULL,
            follow_updates INTEGER NOT NULL DEFAULT 0,
            revision TEXT NOT NULL,
            fetched_at REAL NOT NULL,
            last_error TEXT NOT NULL DEFAULT '',
            failure_count INTEGER NOT NULL DEFAULT 0,
            next_retry_at REAL,
            needs_confirmation INTEGER NOT NULL DEFAULT 0,
            UNIQUE(profile_id, provider, external_id)
        )"""
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS external_source_profile "
        "ON external_source(profile_id, fetched_at DESC)"
    )
    db.execute(
        """CREATE TABLE IF NOT EXISTS external_track (
            profile_id TEXT NOT NULL,
            source_id TEXT NOT NULL,
            source_track_key TEXT NOT NULL,
            position INTEGER NOT NULL,
            source_track_id TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL,
            artists TEXT NOT NULL,
            album TEXT NOT NULL DEFAULT '',
            duration_ms INTEGER NOT NULL DEFAULT 0,
            version_flags TEXT NOT NULL DEFAULT '[]',
            version_label TEXT NOT NULL DEFAULT '',
            source_url TEXT NOT NULL DEFAULT '',
            PRIMARY KEY(profile_id, source_id, source_track_key)
        )"""
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS external_track_order "
        "ON external_track(profile_id, source_id, position)"
    )
    db.execute(
        """CREATE TABLE IF NOT EXISTS external_match (
            profile_id TEXT NOT NULL,
            source_id TEXT NOT NULL,
            source_track_key TEXT NOT NULL,
            status TEXT NOT NULL,
            plex_track_id TEXT NOT NULL DEFAULT '',
            candidate_ids TEXT NOT NULL DEFAULT '[]',
            reason TEXT NOT NULL DEFAULT '',
            manual INTEGER NOT NULL DEFAULT 0,
            catalog_revision TEXT NOT NULL,
            PRIMARY KEY(profile_id, source_id, source_track_key)
        )"""
    )
    db.execute(
        """CREATE TABLE IF NOT EXISTS external_managed (
            profile_id TEXT NOT NULL,
            source_id TEXT NOT NULL,
            record TEXT NOT NULL,
            PRIMARY KEY(profile_id, source_id)
        )"""
    )
    db.execute(
        """CREATE TABLE IF NOT EXISTS external_run (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            profile_id TEXT NOT NULL,
            source_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            status TEXT NOT NULL,
            started_at REAL NOT NULL,
            finished_at REAL,
            message TEXT NOT NULL DEFAULT '',
            payload TEXT NOT NULL DEFAULT '{}'
        )"""
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS external_run_profile "
        "ON external_run(profile_id, id DESC)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS external_run_source "
        "ON external_run(profile_id, source_id, id DESC)"
    )


def delete_external_profile_rows(db, profile_id: str) -> None:
    profile_id = validate_profile_id(profile_id)
    for table in ("external_match", "external_track", "external_managed", "external_run", "external_source"):
        db.execute(f"DELETE FROM {table} WHERE profile_id=?", (profile_id,))


def _bounded_text(value, name, limit, *, required=False):
    value = str(value or "").strip()
    if required and not value:
        raise ValueError(f"{name}不能为空")
    if len(value) > limit or any(ord(char) < 32 for char in value):
        raise ValueError(f"{name}无效")
    return value


def _number(value, name, *, integer=False, minimum=0, maximum=10**15):
    if isinstance(value, bool):
        raise ValueError(f"{name}无效")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f"{name}无效") from None
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise ValueError(f"{name}无效")
    if integer and result != int(result):
        raise ValueError(f"{name}无效")
    return int(result) if integer else result


def _validated_snapshot(snapshot):
    if not isinstance(snapshot, dict):
        raise ValueError("外部歌单快照无效")
    provider = _bounded_text(snapshot.get("provider"), "来源类型", 24, required=True)
    if provider not in SOURCE_PROVIDERS:
        raise ValueError("不支持的外部歌单来源")
    external_id = _bounded_text(snapshot.get("external_id"), "来源标识", 300, required=True)
    source_url = _bounded_text(snapshot.get("url"), "来源地址", 2000)
    title = _bounded_text(snapshot.get("title"), "歌单名称", 300, required=True)
    revision = _bounded_text(snapshot.get("revision"), "来源修订", 128, required=True)
    raw_tracks = snapshot.get("tracks")
    if not isinstance(raw_tracks, list) or not raw_tracks or len(raw_tracks) > 10_000:
        raise ValueError("外部歌单曲目数量无效")
    tracks = []
    keys = set()
    positions = set()
    for raw in raw_tracks:
        if not isinstance(raw, dict):
            raise ValueError("外部歌单曲目无效")
        key = _bounded_text(raw.get("source_track_key"), "来源歌曲标识", 300, required=True)
        position = _number(raw.get("position"), "歌曲位置", integer=True, maximum=9_999)
        if key in keys or position in positions:
            raise ValueError("外部歌单包含重复的歌曲标识或位置")
        artists = raw.get("artists")
        if not isinstance(artists, list) or not artists or len(artists) > 20:
            raise ValueError("来源歌曲缺少歌手")
        artists = [_bounded_text(item, "歌手", 300, required=True) for item in artists]
        version_flags = raw.get("version_flags") or []
        if not isinstance(version_flags, (list, tuple, set)) or len(version_flags) > 20:
            raise ValueError("歌曲版本标记无效")
        version_flags = sorted({_bounded_text(item, "歌曲版本标记", 60, required=True) for item in version_flags})
        tracks.append({
            "source_track_key": key,
            "position": position,
            "source_track_id": _bounded_text(raw.get("source_track_id"), "平台歌曲标识", 300),
            "title": _bounded_text(raw.get("title"), "歌名", 300, required=True),
            "artists": artists,
            "album": _bounded_text(raw.get("album"), "专辑", 300),
            "duration_ms": _number(raw.get("duration_ms") or 0, "歌曲时长", integer=True, maximum=86_400_000),
            "version_flags": version_flags,
            "version_label": _bounded_text(raw.get("version_label"), "歌曲版本", 120),
            "source_url": _bounded_text(raw.get("source_url"), "歌曲地址", 2000),
        })
        keys.add(key)
        positions.add(position)
    tracks.sort(key=lambda row: row["position"])
    if [row["position"] for row in tracks] != list(range(len(tracks))):
        raise ValueError("外部歌单歌曲位置不连续")
    return {
        "provider": provider,
        "external_id": external_id,
        "url": source_url,
        "title": title,
        "revision": revision,
        "tracks": tracks,
    }


class ExternalRepository:
    def __init__(self, base_store):
        self.store = getattr(base_store, "base", base_store)

    @staticmethod
    def _profile(profile_id):
        return validate_profile_id(profile_id)

    @staticmethod
    def _source_id(profile_id, provider, external_id):
        raw = f"{profile_id}\0{provider}\0{external_id}".encode("utf-8")
        return "x-" + hashlib.sha256(raw).hexdigest()[:24]

    @staticmethod
    def _source_row(row):
        if not row:
            return None
        keys = (
            "id", "profile_id", "provider", "external_id", "source_url", "title",
            "follow_updates", "revision", "fetched_at", "last_error", "failure_count",
            "next_retry_at", "needs_confirmation",
        )
        result = dict(zip(keys, row))
        result["follow_updates"] = bool(result["follow_updates"])
        result["needs_confirmation"] = bool(result["needs_confirmation"])
        return result

    def _require_source(self, db, profile_id, source_id):
        row = db.execute(
            "SELECT id,profile_id,provider,external_id,source_url,title,follow_updates,revision,"
            "fetched_at,last_error,failure_count,next_retry_at,needs_confirmation "
            "FROM external_source WHERE profile_id=? AND id=?",
            (profile_id, str(source_id)),
        ).fetchone()
        if not row:
            raise ValueError("当前档案中没有这个外部歌单")
        return self._source_row(row)

    @staticmethod
    def _replace_tracks(db, profile_id, source_id, tracks):
        db.execute("DELETE FROM external_match WHERE profile_id=? AND source_id=?", (profile_id, source_id))
        db.execute("DELETE FROM external_track WHERE profile_id=? AND source_id=?", (profile_id, source_id))
        db.executemany(
            """INSERT INTO external_track(
                profile_id,source_id,source_track_key,position,source_track_id,title,
                artists,album,duration_ms,version_flags,version_label,source_url
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            [(
                profile_id, source_id, row["source_track_key"], row["position"], row["source_track_id"],
                row["title"], _json(row["artists"]), row["album"], row["duration_ms"],
                _json(row["version_flags"]), row["version_label"], row["source_url"],
            ) for row in tracks],
        )

    def upsert_source(self, profile_id: str, snapshot: dict, now: float) -> dict:
        profile_id = self._profile(profile_id)
        snapshot = _validated_snapshot(snapshot)
        now = _number(now, "抓取时间")
        source_id = self._source_id(profile_id, snapshot["provider"], snapshot["external_id"])
        with self.store.lock, self.store._db() as db:
            existing = db.execute(
                "SELECT follow_updates FROM external_source WHERE profile_id=? AND provider=? AND external_id=?",
                (profile_id, snapshot["provider"], snapshot["external_id"]),
            ).fetchone()
            follow = int(existing[0]) if existing else 0
            db.execute(
                """INSERT INTO external_source(
                    id,profile_id,provider,external_id,source_url,title,follow_updates,
                    revision,fetched_at,last_error,failure_count,next_retry_at,needs_confirmation
                ) VALUES (?,?,?,?,?,?,?,?,?,'',0,NULL,0)
                ON CONFLICT(profile_id,provider,external_id) DO UPDATE SET
                    source_url=excluded.source_url,title=excluded.title,revision=excluded.revision,
                    fetched_at=excluded.fetched_at,last_error='',failure_count=0,
                    next_retry_at=NULL,needs_confirmation=0""",
                (source_id, profile_id, snapshot["provider"], snapshot["external_id"], snapshot["url"],
                 snapshot["title"], follow, snapshot["revision"], now),
            )
            self._replace_tracks(db, profile_id, source_id, snapshot["tracks"])
            return self._require_source(db, profile_id, source_id)

    def replace_snapshot(self, profile_id: str, source_id: str, snapshot: dict, now: float) -> dict:
        profile_id = self._profile(profile_id)
        snapshot = _validated_snapshot(snapshot)
        now = _number(now, "抓取时间")
        with self.store.lock, self.store._db() as db:
            current = self._require_source(db, profile_id, source_id)
            if (snapshot["provider"], snapshot["external_id"]) != (current["provider"], current["external_id"]):
                raise ValueError("刷新结果与原外部歌单不一致")
            db.execute(
                """UPDATE external_source SET source_url=?,title=?,revision=?,fetched_at=?,
                    last_error='',failure_count=0,next_retry_at=NULL,needs_confirmation=0
                    WHERE profile_id=? AND id=?""",
                (snapshot["url"], snapshot["title"], snapshot["revision"], now, profile_id, source_id),
            )
            self._replace_tracks(db, profile_id, source_id, snapshot["tracks"])
            return self._require_source(db, profile_id, source_id)

    def record_failure(self, profile_id: str, source_id: str, message: str, now: float) -> dict:
        profile_id = self._profile(profile_id)
        message = _bounded_text(message, "来源错误", 300, required=True)
        now = _number(now, "失败时间")
        with self.store.lock, self.store._db() as db:
            current = self._require_source(db, profile_id, source_id)
            failures = int(current["failure_count"] or 0) + 1
            delay = RETRY_DELAYS[min(failures - 1, len(RETRY_DELAYS) - 1)]
            db.execute(
                "UPDATE external_source SET last_error=?,failure_count=?,next_retry_at=? "
                "WHERE profile_id=? AND id=?",
                (message, failures, now + delay, profile_id, source_id),
            )
            return self._require_source(db, profile_id, source_id)

    def list_sources(self, profile_id: str) -> list[dict]:
        profile_id = self._profile(profile_id)
        with self.store.lock, self.store._db() as db:
            rows = db.execute(
                "SELECT id,profile_id,provider,external_id,source_url,title,follow_updates,revision,"
                "fetched_at,last_error,failure_count,next_retry_at,needs_confirmation "
                "FROM external_source WHERE profile_id=? ORDER BY fetched_at DESC,id",
                (profile_id,),
            ).fetchall()
            return [self._source_row(row) for row in rows]

    def get_source(self, profile_id: str, source_id: str) -> dict:
        profile_id = self._profile(profile_id)
        with self.store.lock, self.store._db() as db:
            return self._require_source(db, profile_id, source_id)

    def set_follow_updates(self, profile_id: str, source_id: str, enabled: bool) -> dict:
        profile_id = self._profile(profile_id)
        if not isinstance(enabled, bool):
            raise ValueError("自动刷新开关无效")
        with self.store.lock, self.store._db() as db:
            self._require_source(db, profile_id, source_id)
            db.execute(
                "UPDATE external_source SET follow_updates=? WHERE profile_id=? AND id=?",
                (int(enabled), profile_id, source_id),
            )
            return self._require_source(db, profile_id, source_id)

    def set_needs_confirmation(self, profile_id: str, source_id: str, enabled: bool) -> dict:
        profile_id = self._profile(profile_id)
        if not isinstance(enabled, bool):
            raise ValueError("确认状态无效")
        with self.store.lock, self.store._db() as db:
            self._require_source(db, profile_id, source_id)
            db.execute(
                "UPDATE external_source SET needs_confirmation=? WHERE profile_id=? AND id=?",
                (int(enabled), profile_id, source_id),
            )
            return self._require_source(db, profile_id, source_id)

    def delete_source(self, profile_id: str, source_id: str) -> None:
        profile_id = self._profile(profile_id)
        with self.store.lock, self.store._db() as db:
            self._require_source(db, profile_id, source_id)
            for table in ("external_match", "external_track", "external_managed", "external_run"):
                db.execute(
                    f"DELETE FROM {table} WHERE profile_id=? AND source_id=?",
                    (profile_id, source_id),
                )
            db.execute(
                "DELETE FROM external_source WHERE profile_id=? AND id=?",
                (profile_id, source_id),
            )

    def list_tracks(self, profile_id: str, source_id: str) -> list[dict]:
        profile_id = self._profile(profile_id)
        with self.store.lock, self.store._db() as db:
            self._require_source(db, profile_id, source_id)
            rows = db.execute(
                """SELECT source_track_key,position,source_track_id,title,artists,album,
                    duration_ms,version_flags,version_label,source_url
                    FROM external_track WHERE profile_id=? AND source_id=? ORDER BY position""",
                (profile_id, source_id),
            ).fetchall()
        keys = ("source_track_key", "position", "source_track_id", "title", "artists", "album",
                "duration_ms", "version_flags", "version_label", "source_url")
        result = []
        for row in rows:
            item = dict(zip(keys, row))
            item["artists"] = json.loads(item["artists"])
            item["version_flags"] = json.loads(item["version_flags"])
            result.append(item)
        return result

    def replace_matches(self, profile_id: str, source_id: str, rows: list[dict], catalog_revision: str) -> None:
        profile_id = self._profile(profile_id)
        catalog_revision = _bounded_text(catalog_revision, "曲库修订", 128, required=True)
        if not isinstance(rows, list):
            raise ValueError("匹配结果无效")
        clean = []
        seen = set()
        for row in rows:
            key = _bounded_text(row.get("source_track_key"), "来源歌曲标识", 300, required=True)
            status = _bounded_text(row.get("status"), "匹配状态", 20, required=True)
            if key in seen or status not in MATCH_STATUSES:
                raise ValueError("匹配结果包含重复歌曲或无效状态")
            candidates = row.get("candidate_ids") or []
            if not isinstance(candidates, list) or len(candidates) > 8:
                raise ValueError("匹配候选无效")
            clean.append((
                profile_id, str(source_id), key, status,
                _bounded_text(row.get("plex_track_id"), "Plex歌曲标识", 80),
                _json([_bounded_text(value, "Plex候选标识", 80, required=True) for value in candidates]),
                _bounded_text(row.get("reason"), "匹配原因", 120), int(bool(row.get("manual"))), catalog_revision,
            ))
            seen.add(key)
        with self.store.lock, self.store._db() as db:
            self._require_source(db, profile_id, source_id)
            valid = {row[0] for row in db.execute(
                "SELECT source_track_key FROM external_track WHERE profile_id=? AND source_id=?",
                (profile_id, source_id),
            )}
            if not seen.issubset(valid):
                raise ValueError("匹配结果包含不属于当前歌单的歌曲")
            db.execute("DELETE FROM external_match WHERE profile_id=? AND source_id=?", (profile_id, source_id))
            db.executemany(
                """INSERT INTO external_match(profile_id,source_id,source_track_key,status,
                    plex_track_id,candidate_ids,reason,manual,catalog_revision)
                    VALUES (?,?,?,?,?,?,?,?,?)""",
                clean,
            )

    def list_matches(self, profile_id: str, source_id: str) -> list[dict]:
        profile_id = self._profile(profile_id)
        with self.store.lock, self.store._db() as db:
            self._require_source(db, profile_id, source_id)
            rows = db.execute(
                """SELECT source_track_key,status,plex_track_id,candidate_ids,reason,manual,catalog_revision
                    FROM external_match WHERE profile_id=? AND source_id=? ORDER BY source_track_key""",
                (profile_id, source_id),
            ).fetchall()
        keys = ("source_track_key", "status", "plex_track_id", "candidate_ids", "reason", "manual", "catalog_revision")
        result = []
        for row in rows:
            item = dict(zip(keys, row))
            item["candidate_ids"] = json.loads(item["candidate_ids"])
            item["manual"] = bool(item["manual"])
            result.append(item)
        return result

    def save_managed(self, profile_id: str, source_id: str, record: dict | None) -> None:
        profile_id = self._profile(profile_id)
        with self.store.lock, self.store._db() as db:
            self._require_source(db, profile_id, source_id)
            if record is None:
                db.execute("DELETE FROM external_managed WHERE profile_id=? AND source_id=?", (profile_id, source_id))
                return
            if not isinstance(record, dict):
                raise ValueError("托管歌单记录无效")
            clean = dict(record)
            clean["id"] = _bounded_text(clean.get("id"), "Plex歌单标识", 80, required=True)
            clean["title"] = _bounded_text(clean.get("title"), "Plex歌单名称", 300, required=True)
            db.execute(
                "INSERT INTO external_managed(profile_id,source_id,record) VALUES (?,?,?) "
                "ON CONFLICT(profile_id,source_id) DO UPDATE SET record=excluded.record",
                (profile_id, source_id, _json(clean)),
            )

    def get_managed(self, profile_id: str, source_id: str) -> dict | None:
        profile_id = self._profile(profile_id)
        with self.store.lock, self.store._db() as db:
            self._require_source(db, profile_id, source_id)
            row = db.execute(
                "SELECT record FROM external_managed WHERE profile_id=? AND source_id=?",
                (profile_id, source_id),
            ).fetchone()
            return json.loads(row[0]) if row else None

    def has_managed(self, profile_id: str) -> bool:
        profile_id = self._profile(profile_id)
        with self.store.lock, self.store._db() as db:
            return db.execute(
                "SELECT 1 FROM external_managed WHERE profile_id=? LIMIT 1", (profile_id,)
            ).fetchone() is not None

    def append_run(self, profile_id: str, source_id: str, run: dict) -> None:
        profile_id = self._profile(profile_id)
        if not isinstance(run, dict):
            raise ValueError("运行记录无效")
        kind = _bounded_text(run.get("kind"), "运行类型", 40, required=True)
        status = _bounded_text(run.get("status"), "运行状态", 40, required=True)
        started_at = _number(run.get("started_at"), "开始时间")
        finished = run.get("finished_at")
        finished_at = None if finished is None else _number(finished, "完成时间")
        message = _bounded_text(run.get("message"), "运行消息", 300)
        extras = {key: value for key, value in run.items() if key not in {"kind", "status", "started_at", "finished_at", "message"}}
        with self.store.lock, self.store._db() as db:
            self._require_source(db, profile_id, source_id)
            db.execute(
                "INSERT INTO external_run(profile_id,source_id,kind,status,started_at,finished_at,message,payload) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (profile_id, source_id, kind, status, started_at, finished_at, message, _json(extras)),
            )
            db.execute(
                """DELETE FROM external_run WHERE profile_id=? AND id NOT IN (
                    SELECT id FROM external_run WHERE profile_id=? ORDER BY id DESC LIMIT 500)""",
                (profile_id, profile_id),
            )
            db.execute(
                """DELETE FROM external_run WHERE profile_id=? AND source_id=? AND id NOT IN (
                    SELECT id FROM external_run WHERE profile_id=? AND source_id=? ORDER BY id DESC LIMIT 100)""",
                (profile_id, source_id, profile_id, source_id),
            )

    def list_runs(self, profile_id: str, source_id: str, limit: int = 20) -> list[dict]:
        profile_id = self._profile(profile_id)
        limit = _number(limit, "运行记录数量", integer=True, minimum=1, maximum=100)
        with self.store.lock, self.store._db() as db:
            self._require_source(db, profile_id, source_id)
            rows = db.execute(
                """SELECT kind,status,started_at,finished_at,message,payload FROM external_run
                    WHERE profile_id=? AND source_id=? ORDER BY id DESC LIMIT ?""",
                (profile_id, source_id, limit),
            ).fetchall()
        keys = ("kind", "status", "started_at", "finished_at", "message", "payload")
        result = []
        for row in rows:
            item = dict(zip(keys, row))
            item.update(json.loads(item.pop("payload")))
            result.append(item)
        return result
