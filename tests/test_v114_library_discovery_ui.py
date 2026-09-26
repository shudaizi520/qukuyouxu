from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src" / "helper" / "static"


def test_library_page_starts_with_analysis_and_has_no_theme_or_auto_picker():
    html = (STATIC / "home.html").read_text(encoding="utf-8")

    assert 'id="analyzeLibrary"' in html
    assert 'id="themeChoices"' not in html
    assert 'id="themeEnabled"' not in html
    assert 'id="autoToggle"' not in html
    assert 'id="incrementalAction"' in html


def test_candidate_interface_uses_direct_names_counts_and_actions():
    html = (STATIC / "home.html").read_text(encoding="utf-8")
    script = (STATIC / "home.js").read_text(encoding="utf-8")

    assert "本次整理预览" in html
    assert "确认所选变化" in html
    assert "查看歌曲" in script
    assert "算法版本" not in html
    assert "缺来源" not in html


def test_managed_view_keeps_manual_new_song_check_and_safe_playlist_actions():
    html = (STATIC / "home.html").read_text(encoding="utf-8")
    managed = (STATIC / "theme_home.js").read_text(encoding="utf-8")

    assert "分类歌单" in html
    assert "检查新增歌曲" in html
    assert "停止维护" in managed
    assert "移除歌单" in managed
    assert "恢复歌单" in managed


def test_analysis_entry_remains_available_for_managed_profiles_and_uses_resumable_scan():
    script = (STATIC / "home.js").read_text(encoding="utf-8")

    assert "discovery.phase==='managed'" not in script
    assert "'/api/workflow/incremental'" in script
