"""The shared topbar menu keeps one consistent footprint."""

from pathlib import Path
import re


STATIC = Path(__file__).resolve().parents[1] / "src/helper/static"


def rule(selector):
    css = re.sub(r"/\*.*?\*/", "", (STATIC / "product.css").read_text(), flags=re.S)
    found = {}
    for names, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        if selector not in (name.strip() for name in names.split(",")):
            continue
        found.update(
            piece.strip().split(":", 1)
            for piece in body.split(";") if ":" in piece
        )
    return found


def test_collapsed_top_navigation_cannot_leave_empty_space():
    assert rule(".topbar nav[hidden]")["display"] == "none!important"
    assert rule(".topbar-menu-picker .appearance-trigger")["min-height"] == "38px"


def test_menu_logout_retains_warning_color_inside_playlist_workspace():
    assert rule("body[data-view=playlists] .topbar .topbar-menu-picker .logout")["color"] == "var(--theme-danger)"
    hover = rule("body[data-view=playlists] .topbar .topbar-menu-picker .logout:hover:not(:disabled)")
    assert hover["background"] == "var(--theme-danger-soft)"
    assert hover["color"] == "var(--theme-danger)"


def test_current_page_is_identifiable_inside_collapsed_menu():
    assert rule(".topbar-menu-picker .topbar-menu-action.active")["color"] == "var(--theme-accent)"
