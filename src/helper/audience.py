"""Conservative audience isolation for children's music.

The helper never guesses from listening duration or a generic word such as
"孩子" in a song title.  Only explicit library metadata, curated-category
evidence, known children's catalog labels, or an explicit folder name may
separate a track from the owner's personal listening profile.
"""
from __future__ import annotations

from pathlib import PurePosixPath

from .match import normalize


CHILDREN_TAGS = frozenset({
    "儿歌", "兒歌", "儿童音乐", "兒童音樂", "儿童歌曲", "兒童歌曲",
    "童谣", "童謠", "亲子", "親子", "幼儿音乐", "幼兒音樂",
    "childrensmusic", "childrenmusic", "kidsmusic", "kidsfamily",
    "nurseryrhyme", "nurseryrhymes",
})
STRONG_LABELS = tuple(normalize(value) for value in (
    "儿歌", "兒歌", "儿童音乐", "兒童音樂", "儿童歌曲", "兒童歌曲",
    "童谣", "童謠", "宝宝巴士", "贝瓦儿歌", "貝瓦兒歌", "巧虎",
    "children's music", "kids music", "nursery rhyme", "nursery rhymes",
))
EXPLICIT_FOLDERS = frozenset(normalize(value) for value in (
    "儿歌", "兒歌", "儿童音乐", "兒童音樂", "儿童歌曲", "兒童歌曲",
    "童谣", "童謠", "kids music", "children's music", "nursery rhymes",
))


def _explicit_label(value):
    key = normalize(value)
    return bool(key and any(marker in key for marker in STRONG_LABELS))


def is_childrens_track(track, features=()):
    """Return True only when a track has explicit children's-audience evidence."""
    tags = list(track.get("genres") or []) + list(track.get("styles") or []) + list(track.get("moods") or [])
    if any(normalize(value) in {normalize(tag) for tag in CHILDREN_TAGS} for value in tags):
        return True
    if _explicit_label(track.get("album")) or _explicit_label(track.get("audience")):
        return True
    if any(_explicit_label(value) for value in features or []):
        return True
    for value in track.get("paths") or []:
        path = PurePosixPath(str(value).replace("\\", "/"))
        if any(normalize(part) in EXPLICIT_FOLDERS for part in path.parts[:-1]):
            return True
    return False


def filter_childrens_context(tracks, features=None, events=None, seed_ids=None):
    """Remove children's tracks and every preference input that points at them."""
    features = dict(features or {})
    rows = list(tracks or [])
    excluded = {
        str(row.get("id")) for row in rows
        if str(row.get("id", "")).isdigit()
        and is_childrens_track(row, features.get(str(row.get("id")), ()))
    }
    kept = [row for row in rows if str(row.get("id")) not in excluded]
    kept_features = {str(key): value for key, value in features.items() if str(key) not in excluded}
    kept_events = [
        row for row in (events or [])
        if str(row.get("track_id") or row.get("id") or "") not in excluded
    ]
    kept_seeds = [str(value) for value in (seed_ids or []) if str(value) not in excluded]
    return {
        "tracks": kept,
        "features": kept_features,
        "events": kept_events,
        "seed_ids": kept_seeds,
        "excluded_ids": excluded,
    }
