"""Regression checks for the user-visible playlist workspace layout."""

from pathlib import Path

from ui_css import page_css
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


def test_sidebar_playlists_place_artwork_beside_titles_without_extra_music_icons():
    sections = (STATIC / "playlist-sections.js").read_text()
    render_custom = sections.split("function renderCustom(", 1)[1].split("function renderSection", 1)[0]
    assert "playlist-side-icon" not in render_custom
    assert "cover:createArtwork(item,'sidebar')" in render_custom
    assert "button.replaceChildren(view.cover,text)" in render_custom
    css = (STATIC / "product.css").read_text()
    assert "#customPlaylistList button{grid-template-columns:40px minmax(0,1fr)}" in css
    assert "@media(max-width:700px){#customPlaylistList button{grid-template-columns:30px minmax(0,1fr)}" in css


def test_heart_click_uses_optimistic_toggle_without_generic_busy_spinner():
    playlists = (STATIC / "playlists.js").read_text()
    assert "heart.onclick=event=>{event.stopPropagation();action(()=>setLiked" not in playlists
    assert "heart.onclick=event=>{event.stopPropagation();setLiked(" in playlists
    assert "onLikedChange:(track,liked)=>action(()=>setLiked(track,liked))" not in playlists


def test_library_organizer_has_no_duplicate_heading_and_keeps_primary_action_in_task_panel():
    html = (STATIC / "home.html").read_text()
    assert html.index('id="task"') < html.index('id="incrementalAction"') < html.index('id="upstreamError"')
    assert "<h1>曲库整理</h1>" not in html
    assert "分类歌单" in html
    assert css_rules(".pch-embedded body[data-view=library] .wrap")["max-width"] == "1120px"


def test_import_controls_are_grouped_in_one_inset_column():
    html = (STATIC / "external.html").read_text()
    assert html.count('class="external-command-actions"') == 3
    assert html.count('class="external-command-bar"') == 2
    css = (STATIC / "external-workspace.css").read_text()
    assert "body[data-view=external] .external-command-bar{position:relative;display:grid;grid-template-columns:92px minmax(0,1fr) auto" in css
    assert "max-width:680px" in css


def test_import_mobile_actions_override_old_full_width_file_control():
    css = page_css("external")
    assert "body[data-view=external] .external-command-actions .external-file-action{width:auto}" in css
    assert "body[data-view=external] .external-import-form>.external-command-actions{flex-wrap:wrap;justify-content:flex-start;padding-left:0}" in css


def test_review_uses_candidate_selection_then_one_global_confirmation():
    """A selected candidate is batch-confirmable without a second row action."""
    script = (STATIC / "external.js").read_text()
    review_actions = script.split("function renderReviewActions", 1)[1].split(
        "function audioTime", 1
    )[0]
    assert "check.checked=true" in review_actions
    assert "confirm.textContent='确认'" not in review_actions
    assert "missing.value='__missing__'" in review_actions
    assert "标记为缺失" in review_actions
    assert "select.onchange" in review_actions
    assert "const checks=[...document.querySelectorAll('#trackList .external-review-check')]" in script


def test_download_menu_closes_when_focus_leaves_it_or_escape_is_pressed():
    html = (STATIC / "external.html").read_text()
    script = (STATIC / "external.js").read_text()
    assert 'id="downloadMenu"' in html
    assert "function closeDownloadMenu" in script
    assert "pointerdown" in script
    assert "event.key==='Escape'" in script
    assert "$('downloadImage').onclick=()=>{closeDownloadMenu();" in script


def test_confirming_the_last_review_page_reloads_a_valid_page():
    """A batch confirmation may shrink the result set below the current page."""
    script = (STATIC / "external.js").read_text()
    open_source = script.split("async function openSource", 1)[1].split(
        "function renderDetail", 1
    )[0]
    assert "const lastPage=Math.max(1,Math.ceil(current.total/PAGE_SIZE))" in open_source
    assert "if(page>lastPage){" in open_source
    assert "page=lastPage;" in open_source
    batch_confirm = script.split("$('confirmSelected').onclick", 1)[1].split(
        "$('previousTracks')", 1
    )[0]
    assert "await openSource(current.id,false);" in batch_confirm
