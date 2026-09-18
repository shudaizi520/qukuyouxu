"""Portable archive for expensive QQ music evidence.

The archive deliberately excludes credentials, playlists, sessions and other
application state.  Values are kept as their original JSON strings so a
round-trip does not silently change numeric or Unicode representations.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
from typing import Iterable


ARCHIVE_FORMAT = "qukuyouxu-music-cache-v2"
LEGACY_ARCHIVE_FORMAT = "qukuyouxu-music-cache-v1"
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_RECORDS = 1_000_000
EXACT_KEYS = frozenset({"single_revision", "single_scope"})
KEY_PREFIXES = ("single_result:", "single_detail:", "single_search:")
PROFILE_KEY = re.compile(r"^profile:([a-z0-9](?:[a-z0-9_-]{0,46}[a-z0-9])?):(.+)$")
PROFILE_ID = re.compile(r"^[a-z0-9](?:[a-z0-9_-]{0,46}[a-z0-9])?$")


class CacheArchiveError(ValueError):
    """The archive or requested import is unsafe."""


@dataclass(frozen=True)
class CacheArchiveSummary:
    count: int
    sha256: str
    path: Path
    backup: Path | None = None


def _allowed_inner_key(key: object) -> bool:
    return isinstance(key, str) and (
        key in EXACT_KEYS or any(key.startswith(prefix) for prefix in KEY_PREFIXES)
    )


def _profile_key(key: object) -> tuple[str, str] | None:
    if not isinstance(key, str):
        return None
    matched = PROFILE_KEY.fullmatch(key)
    if not matched or not _allowed_inner_key(matched.group(2)):
        return None
    return matched.group(1), matched.group(2)


def _allowed_key(key: object, archive_format: str) -> bool:
    if archive_format == LEGACY_ARCHIVE_FORMAT:
        return _allowed_inner_key(key)
    return _profile_key(key) is not None


def _canonical_records(records: list[dict[str, str]]) -> bytes:
    return json.dumps(
        records,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _digest(records: list[dict[str, str]]) -> str:
    return hashlib.sha256(_canonical_records(records)).hexdigest()


def _validate_records(records: object, archive_format: str) -> list[dict[str, str]]:
    if not isinstance(records, list) or len(records) > MAX_RECORDS:
        raise CacheArchiveError("缓存记录数量无效")
    checked: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in records:
        if not isinstance(item, dict) or set(item) != {"key", "value_json"}:
            raise CacheArchiveError("缓存记录格式无效")
        key = item.get("key")
        value_json = item.get("value_json")
        if not _allowed_key(key, archive_format):
            raise CacheArchiveError(f"缓存键不允许导入：{key!r}")
        if key in seen:
            raise CacheArchiveError(f"缓存包含重复键：{key}")
        if not isinstance(value_json, str):
            raise CacheArchiveError(f"缓存值格式无效：{key}")
        try:
            json.loads(value_json)
        except (TypeError, ValueError) as exc:
            raise CacheArchiveError(f"缓存值不是有效 JSON：{key}") from exc
        seen.add(key)
        checked.append({"key": key, "value_json": value_json})
    return sorted(checked, key=lambda item: item["key"])


def _load_archive(path: Path) -> tuple[str, list[dict[str, str]], str]:
    path = Path(path)
    try:
        if path.stat().st_size > MAX_ARCHIVE_BYTES:
            raise CacheArchiveError("缓存归档过大")
        document = json.loads(path.read_text(encoding="utf-8"))
    except CacheArchiveError:
        raise
    except (OSError, ValueError) as exc:
        raise CacheArchiveError("无法读取缓存归档") from exc
    archive_format = document.get("format") if isinstance(document, dict) else None
    if archive_format not in (ARCHIVE_FORMAT, LEGACY_ARCHIVE_FORMAT):
        raise CacheArchiveError("缓存归档版本不受支持")
    records = _validate_records(document.get("records"), archive_format)
    expected = document.get("sha256")
    actual = _digest(records)
    if not isinstance(expected, str) or expected != actual:
        raise CacheArchiveError("缓存归档校验失败")
    return archive_format, records, actual


def _read_rows(database: Path) -> Iterable[tuple[str, str]]:
    database = Path(database).resolve()
    if not database.is_file():
        raise CacheArchiveError("找不到源数据库")
    uri = f"{database.as_uri()}?mode=ro"
    try:
        with closing(sqlite3.connect(uri, uri=True)) as connection:
            return list(connection.execute("SELECT k, v FROM state ORDER BY k"))
    except sqlite3.Error as exc:
        raise CacheArchiveError("无法读取源数据库的状态表") from exc


def export_music_cache(
    database: Path,
    destination: Path,
    *,
    profile_id: str | None = None,
) -> CacheArchiveSummary:
    if profile_id is not None and not PROFILE_ID.fullmatch(str(profile_id)):
        raise CacheArchiveError("来源档案标识无效")
    rows = list(_read_rows(Path(database)))
    scoped = {
        key: value for key, value in rows
        if _allowed_key(key, ARCHIVE_FORMAT)
        and (profile_id is None or _profile_key(key)[0] == profile_id)
    }
    # Databases upgraded from the pre-profile release retain their original
    # unscoped cache.  Normalize only legacy-only rows into the default profile;
    # a newer scoped value always wins over its stale migration source.
    if profile_id in (None, "default"):
        for key, value in rows:
            if not _allowed_inner_key(key):
                continue
            scoped.setdefault(f"profile:default:{key}", value)
    records = _validate_records(
        [{"key": key, "value_json": value} for key, value in scoped.items()],
        ARCHIVE_FORMAT,
    )
    digest = _digest(records)
    document = {
        "format": ARCHIVE_FORMAT,
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "records": records,
        "sha256": digest,
    }
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.chmod(temporary, 0o600)
    temporary.replace(destination)
    return CacheArchiveSummary(len(records), digest, destination)


def inspect_music_cache(archive: Path) -> CacheArchiveSummary:
    _archive_format, records, digest = _load_archive(Path(archive))
    return CacheArchiveSummary(len(records), digest, Path(archive))


def _backup_database(database: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    backup = database.with_name(f"{database.name}.before-cache-import-{stamp}.bak")
    try:
        with closing(sqlite3.connect(database)) as source, closing(sqlite3.connect(backup)) as target:
            source.backup(target)
            result = target.execute("PRAGMA integrity_check").fetchone()
            if not result or result[0] != "ok":
                raise CacheArchiveError("导入前数据库备份校验失败")
    except sqlite3.Error as exc:
        raise CacheArchiveError("无法创建导入前数据库备份") from exc
    os.chmod(backup, 0o600)
    return backup


def import_music_cache(
    archive: Path,
    database: Path,
    *,
    replace: bool = False,
    target_profile: str | None = None,
) -> CacheArchiveSummary:
    archive_format, records, digest = _load_archive(Path(archive))
    if target_profile is not None:
        if not PROFILE_ID.fullmatch(target_profile):
            raise CacheArchiveError("目标档案标识无效")
        profiles = {parts[0] for item in records if (parts := _profile_key(item["key"]))}
        if archive_format == ARCHIVE_FORMAT and len(profiles) > 1:
            raise CacheArchiveError("归档包含多个档案，不能全部映射到同一目标档案")
    transformed = []
    for item in records:
        if archive_format == LEGACY_ARCHIVE_FORMAT:
            key = f"profile:{target_profile or 'default'}:{item['key']}"
        elif target_profile is not None:
            _source_profile, inner = _profile_key(item["key"])
            key = f"profile:{target_profile}:{inner}"
        else:
            key = item["key"]
        transformed.append({"key": key, "value_json": item["value_json"]})
    records = transformed
    database = Path(database)
    database.parent.mkdir(parents=True, exist_ok=True)
    existed = database.is_file()
    try:
        with closing(sqlite3.connect(database)) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS state (k TEXT PRIMARY KEY, v TEXT NOT NULL)"
            )
            existing = {
                key: value
                for key, value in connection.execute(
                    "SELECT k, v FROM state WHERE k IN ({})".format(
                        ",".join("?" for _ in records)
                    ),
                    [item["key"] for item in records],
                )
            } if records else {}
            connection.commit()
    except sqlite3.Error as exc:
        raise CacheArchiveError("无法检查目标数据库") from exc

    conflicts = [
        item["key"]
        for item in records
        if item["key"] in existing and existing[item["key"]] != item["value_json"]
    ]
    if conflicts and not replace:
        raise CacheArchiveError(f"目标数据库存在 {len(conflicts)} 个缓存冲突；未执行导入")

    backup = _backup_database(database) if existed else None
    try:
        with closing(sqlite3.connect(database)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.executemany(
                "INSERT INTO state(k, v) VALUES (?, ?) "
                "ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                [(item["key"], item["value_json"]) for item in records],
            )
            connection.commit()
    except sqlite3.Error as exc:
        raise CacheArchiveError("缓存导入失败，事务已回滚") from exc
    os.chmod(database, 0o600)
    return CacheArchiveSummary(len(records), digest, Path(archive), backup)
