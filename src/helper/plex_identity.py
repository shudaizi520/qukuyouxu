"""One Plex application identity for plex.tv and every PMS connection."""
from __future__ import annotations

import re
import uuid
from urllib.parse import urlencode

from . import __version__


PRODUCT = "Plex Playlist Helper"
APP_VERSION = __version__


def client_id(store) -> str:
    raw = re.sub(r"[^A-Za-z0-9_-]", "", str(store.get("installation_id") or ""))
    if not raw:
        raw = uuid.uuid4().hex
        store.set("installation_id", raw)
    return "plex-playlist-helper-" + raw[:64]


def plex_headers(store, token: str = "", accept: str = "application/json") -> dict[str, str]:
    result = {
        "Accept": accept,
        "X-Plex-Product": PRODUCT,
        "X-Plex-Version": APP_VERSION,
        "X-Plex-Platform": "TrueNAS",
        "X-Plex-Device": "Docker",
        "X-Plex-Client-Identifier": client_id(store),
    }
    if token:
        result["X-Plex-Token"] = str(token)
    return result


def build_plex_auth_url(identifier: str, code: str) -> str:
    values = {
        "clientID": str(identifier),
        "code": str(code),
        "context[device][product]": PRODUCT,
        "context[device][version]": APP_VERSION,
        "context[device][platform]": "TrueNAS",
    }
    return "https://app.plex.tv/auth#?" + urlencode(values)
