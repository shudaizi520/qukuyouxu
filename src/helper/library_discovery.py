"""Policies for deriving useful category playlists from the active library."""
from __future__ import annotations

import math


DISCOVERY_POLICY = "v1.1.4-library-derived-1"


def discovery_min_tracks(library_count: int) -> int:
    """Return the adaptive creation threshold for a Plex music library."""
    count = max(0, int(library_count or 0))
    return max(10, min(30, math.ceil(count * 0.005)))


def eligible_discovery_groups(plan: dict | None, managed: dict | None) -> list[dict]:
    """Expose useful candidates while keeping already managed groups visible."""
    plan = plan or {}
    managed = managed or {}
    threshold = discovery_min_tracks(plan.get("library_count", 0))
    return [
        row
        for row in (plan.get("groups", []) or [])
        if str(row.get("id") or "") in managed
        or (
            not row.get("blocked")
            and len(row.get("desired", []) or []) >= threshold
        )
    ]


def prepare_discovery_sources(current, tags, managed):
    """Build discovery scope from the live catalog, not removed picker state.

    A disabled unmanaged source came from the old theme picker and is made
    discoverable again. A disabled managed source is an explicit maintenance
    opt-out and remains disabled.
    """
    from .theme import BY_KEY, DEFAULT_THEME, provision_sources, theme_key

    sources, missing = provision_sources(current or [], tags or [], DEFAULT_THEME["selected"])
    managed_ids = {str(value) for value in (managed or {})}
    usable = []
    for source in sources:
        identifier = str(source.get("id") or "")
        if identifier not in managed_ids:
            source["enabled"] = True
        if source.get("kind") == "local_theme" and source.get("theme_generated"):
            key = theme_key(source, tags or [])
            if key:
                missing.append(key)
            continue
        usable.append(source)
    unavailable = [
        {"key": key, "name": BY_KEY[key]["name"], "reason": "当前版本没有可靠来源，暂不生成该歌单"}
        for key in dict.fromkeys(missing) if key in BY_KEY
    ]
    return usable, unavailable
