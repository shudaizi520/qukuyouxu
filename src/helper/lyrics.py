"""Normalize Plex-provided lyrics without reading music files directly."""
from __future__ import annotations

import math
from xml.etree import ElementTree as ET


MAX_LYRICS_BYTES = 1024 * 1024
MAX_LYRIC_LINES = 5000
MAX_LYRIC_LINE_LENGTH = 2000


def _text(line):
    spans = line.findall("Span")
    value = "".join(str(span.get("text") or span.text or "") for span in spans)
    if not value:
        value = str(line.get("text") or line.text or "")
    return value.strip()[:MAX_LYRIC_LINE_LENGTH]


def _milliseconds(value):
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(number) or not 0 <= number <= 24 * 60 * 60 * 1000:
        return None
    return int(number)


def parse_plex_lyrics(payload):
    """Return a compact timed/plain lyric model from Plex XML bytes."""
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    if not isinstance(payload, (bytes, bytearray)):
        raise ValueError("Plex 歌词格式无效")
    if len(payload) > MAX_LYRICS_BYTES:
        raise ValueError("Plex 歌词响应过大")
    if b"<!DOCTYPE" in payload or b"<!ENTITY" in payload:
        raise ValueError("拒绝歌词 XML 实体定义")
    try:
        root = ET.fromstring(payload)
    except ET.ParseError:
        raise ValueError("Plex 歌词格式无效") from None
    lyrics = root if root.tag.rsplit("}", 1)[-1] == "Lyrics" else root.find(".//Lyrics")
    if lyrics is None:
        return {"kind": "none", "lines": []}
    timed = str(lyrics.get("timed") or "").strip().lower() in {"1", "true", "yes"}
    rows = []
    for line in lyrics.findall(".//Line")[:MAX_LYRIC_LINES]:
        text = _text(line)
        if not text:
            continue
        if not timed:
            rows.append({"text": text})
            continue
        start = _milliseconds(line.get("startOffset"))
        if start is None:
            continue
        row = {"start_ms": start, "text": text}
        end = _milliseconds(line.get("endOffset"))
        if end is not None and end >= start:
            row["end_ms"] = end
        rows.append(row)
    if not rows and not timed:
        raw_text = "".join(lyrics.itertext())
        rows = [
            {"text": value.strip()[:MAX_LYRIC_LINE_LENGTH]}
            for value in raw_text.splitlines()[:MAX_LYRIC_LINES]
            if value.strip()
        ]
    if not rows:
        return {"kind": "none", "lines": []}
    if timed:
        rows.sort(key=lambda row: row["start_ms"])
    return {"kind": "timed" if timed else "plain", "lines": rows}


def read_track_lyrics(engine, track_id):
    """Read lyrics only when the track belongs to the selected profile library."""
    track_id = str(track_id or "")
    if not track_id.isdigit():
        raise ValueError("曲目标识无效")
    settings = engine.store.get("settings") or {}
    section = str(settings.get("section") or "")
    if not section.isdigit():
        raise ValueError("当前账户尚未选择音乐曲库")
    plex = engine.plex_factory(settings)
    if str(plex.track_section(track_id)) != section:
        raise ValueError("这首歌不属于当前曲库")
    return plex.track_lyrics(track_id)
