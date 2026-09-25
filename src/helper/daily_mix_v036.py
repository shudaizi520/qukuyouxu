"""Compatibility imports for the historical v0.3.6 lifecycle module."""

from .plex_lifecycle import (
    PLEX_TV,
    PROTECTED_TITLES,
    attach_v036_routes,
    calculate_category_overlaps,
    disable_managed_playlist,
    enable_managed_playlist,
    managed_playlist_rows,
    parse_plex_resources,
    reconcile_managed_playlist,
    remove_managed_playlist,
    restore_removed_playlist,
    valid_plex_pin,
)

__all__ = [
    "PLEX_TV",
    "PROTECTED_TITLES",
    "attach_v036_routes",
    "calculate_category_overlaps",
    "disable_managed_playlist",
    "enable_managed_playlist",
    "managed_playlist_rows",
    "parse_plex_resources",
    "reconcile_managed_playlist",
    "remove_managed_playlist",
    "restore_removed_playlist",
    "valid_plex_pin",
]
