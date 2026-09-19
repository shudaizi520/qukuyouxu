"""Policies for deriving useful category playlists from the active library."""
from __future__ import annotations

import math


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
