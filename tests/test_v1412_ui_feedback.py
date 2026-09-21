"""Regression checks for the user-visible playlist workspace layout."""

from pathlib import Path
import re


STATIC = Path(__file__).resolve().parents[1] / "src/helper/static"


def css_rules(selector):
    css = re.sub(r"/\*.*?\*/", "", (STATIC / "product.css").read_text(), flags=re.S)
    result = {}
    for names, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        if selector in [name.strip() for name in names.split(",")]:
            result.update(
                part.strip().split(":", 1)
                for part in body.split(";") if ":" in part
            )
    return result


def test_sidebar_playlists_do_not_stack_decorative_music_icons_above_titles():
    sections = (STATIC / "playlist-sections.js").read_text()
    render_custom = sections.split("function renderCustom(){", 1)[1].split("function renderSection", 1)[0]
    assert "playlist-side-icon" not in render_custom
    assert css_rules("#customPlaylistList button")["grid-template-columns"] == "minmax(0,1fr)"


def test_heart_click_uses_optimistic_toggle_without_generic_busy_spinner():
    playlists = (STATIC / "playlists.js").read_text()
    assert "heart.onclick=event=>{event.stopPropagation();action(()=>setLiked" not in playlists
    assert "heart.onclick=event=>{event.stopPropagation();setLiked(" in playlists
    assert "onLikedChange:(track,liked)=>action(()=>setLiked(track,liked))" not in playlists


def test_library_organizer_has_single_heading_and_primary_action_above_summary():
    html = (STATIC / "home.html").read_text()
    assert html.index('id="incrementalAction"') < html.index('id="libraryOverview"')
    assert html.count("<h1>曲库整理</h1>") == 1
    assert "分类歌单" in html
    assert css_rules(".pch-embedded body[data-view=library] .wrap")["max-width"] == "1120px"


def test_import_controls_are_grouped_in_one_inset_column():
    html = (STATIC / "external.html").read_text()
    assert html.count('class="external-command-actions"') == 4
    assert html.count('class="external-command-bar"') == 3
    assert css_rules(".pch-embedded body[data-view=external] .external-shell")["max-width"] == "1040px"
    css = (STATIC / "product.css").read_text()
    assert "body[data-view=external] .external-command-bar{display:grid;grid-template-columns:100px minmax(0,1fr) auto" in css


def test_import_mobile_actions_override_old_full_width_file_control():
    css = (STATIC / "product.css").read_text()
    assert "body[data-view=external] .external-command-actions .external-file-action{width:auto}" in css
    assert "body[data-view=external] .external-import-form>.external-command-actions{flex-wrap:wrap;justify-content:flex-start;padding-left:0}" in css
