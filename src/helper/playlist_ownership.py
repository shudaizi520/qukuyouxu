"""Small helpers for recognizing assistant-owned Plex playlist summaries."""
from __future__ import annotations

import re


def legacy_pch_marker(summary, category_id):
    """Return a structurally valid historical PCH marker for this category."""
    category_id = str(category_id or "")
    if not category_id:
        return ""
    pattern = (
        r"(?:^|\n)(\[PCH:[A-Za-z0-9_-]{8,64}:"
        + re.escape(category_id)
        + r"\])(?:\n|$)"
    )
    match = re.search(pattern, str(summary or ""))
    return match.group(1) if match else ""


def legacy_external_marker(summary, source_id):
    """Return a structurally valid historical external-playlist marker."""
    source_id = str(source_id or "")
    if not source_id:
        return ""
    pattern = (
        r"(?:^|\n)(\[QKYX:external:[A-Za-z0-9._-]{1,96}:"
        + re.escape(source_id)
        + r"\])(?:\n|$)"
    )
    match = re.search(pattern, str(summary or ""))
    return match.group(1) if match else ""


def replace_marker(summary, old_marker, new_marker):
    summary, old_marker, new_marker = map(str, (summary or "", old_marker or "", new_marker or ""))
    if not old_marker or old_marker not in summary or not new_marker:
        raise ValueError("歌单管理标记无法迁移")
    return summary.replace(old_marker, new_marker, 1)
