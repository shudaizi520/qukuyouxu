"""Safe, explicit replenishment-list exports for currently missing tracks."""
from __future__ import annotations

import csv
import io
import re


FORMULA_PREFIXES = ("=", "+", "-", "@")


def _clean(value, name):
    value = re.sub(r"[\r\n\t]+", " ", str(value or ""))
    value = re.sub(r"\s+", " ", value).strip()
    if not value or len(value) > 300:
        raise ValueError(f"{name}无效")
    return value


def _missing_rows(rows):
    if not isinstance(rows, list) or len(rows) > 10_000:
        raise ValueError("缺失歌曲清单无效或超过 10000 首")
    result = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("缺失歌曲记录无效")
        if row.get("status") != "missing":
            continue
        artists = row.get("artists")
        if not isinstance(artists, list) or not artists:
            raise ValueError("缺失歌曲歌手无效")
        title = _clean(row.get("title"), "歌名")
        artists = [_clean(value, "歌手") for value in artists]
        key = (title.casefold(), tuple(value.casefold() for value in artists))
        if key in seen:
            continue
        seen.add(key)
        result.append((title, artists))
    return result


def format_missing_text(rows: list[dict]) -> str:
    return "".join(f"{title} - {' / '.join(artists)}\n" for title, artists in _missing_rows(rows))


def _csv_cell(value):
    return "'" + value if value.startswith(FORMULA_PREFIXES) else value


def format_missing_csv(rows: list[dict]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\r\n")
    writer.writerow(("歌名", "歌手"))
    for title, artists in _missing_rows(rows):
        writer.writerow((_csv_cell(title), _csv_cell(" / ".join(artists))))
    return b"\xef\xbb\xbf" + output.getvalue().encode("utf-8")


def missing_download_name(title: str, extension: str) -> str:
    extension = str(extension or "").lower()
    if extension not in ("txt", "csv"):
        raise ValueError("下载格式无效")
    title = re.sub(r"[\r\n\t]+", " ", str(title or ""))
    title = re.sub(r'[<>:"/\\|?*]+', "", title)
    title = re.sub(r"^\.+", "", title)
    title = re.sub(r"\s+", " ", title).strip(" .")[:100] or "外部歌单"
    return f"{title}-缺失歌曲.{extension}"
