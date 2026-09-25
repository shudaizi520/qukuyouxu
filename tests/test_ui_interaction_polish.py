from pathlib import Path

from ui_css import page_css


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src/helper/static"


def test_custom_mix_toggle_tracks_open_state():
    page = (STATIC / "mixes.html").read_text()
    script = (STATIC / "mixes.js").read_text()
    assert 'class="custom-mix-toggle"' in page
    assert "box.open?'收起':'展开'" in script
    assert "addEventListener('toggle'" in script


def test_managed_playlist_actions_are_inline_instead_of_independent_menus():
    script = (STATIC / "theme_home.js").read_text()
    assert "line.append(info,actions)" in script
    assert "actions.append(open)" in script
    assert "managed-playlist-more" not in script


def test_metadata_review_offers_safe_internal_confirmation():
    page = (STATIC / "home.html").read_text()
    script = (STATIC / "home.js").read_text()
    api = (ROOT / "src/helper/extra_web.py").read_text()
    assert "采用文件名信息" in script
    assert "/api/metadata/corrections" in script
    assert "不会修改 Plex 标签和音乐文件" in script
    assert "只修正助手内部资料" not in page
    assert "invalidated_reason" in api
    assert "'base_plan': base_plan" in api


def test_unclassified_tracks_have_a_clear_count_and_read_only_view():
    page = (STATIC / "home.html").read_text()
    script = (STATIC / "home.js").read_text()
    api = (ROOT / "src/helper/extra_web.py").read_text()
    workflow = (ROOT / "src/helper/workflow_v0317.py").read_text()
    assert 'id="unclassifiedCount"' in page
    assert 'id="unclassifiedPanel"' in page
    assert "/api/base/unclassified" in script
    assert "@app.get('/api/base/unclassified')" in api
    assert '"unclassified": max(0, library_count - matched)' in workflow


def test_managed_playlist_has_a_reversible_maintenance_control():
    script = (STATIC / "theme_home.js").read_text(encoding="utf-8")
    api = (ROOT / "src/helper/plex_lifecycle.py").read_text(encoding="utf-8")
    assert "恢复维护" in script
    assert "/api/managed/enable" in script
    assert '@app.post("/api/managed/enable")' in api


def test_external_result_tabs_do_not_use_global_pending_button_animation():
    script = (STATIC / "external.js").read_text()
    css = page_css("external")
    assert "switchMatchStatus" in script
    assert "button.onclick=()=>action(async()=>{activeStatus" not in script
    assert ".external-count-tab:hover" in css
    assert "box-shadow:none" in css
    assert ".external-count-tab.pch-pending:before" in css
