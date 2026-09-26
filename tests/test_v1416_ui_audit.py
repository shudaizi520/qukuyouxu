"""Regression checks for the compact settings and embedded account flow."""

from html.parser import HTMLParser
from pathlib import Path


STATIC = Path(__file__).resolve().parents[1] / "src/helper/static"


class _SettingsSystem(HTMLParser):
    def __init__(self):
        super().__init__()
        self.inside = False
        self.depth = 0
        self.links = []
        self.ids = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "section" and attrs.get("id") == "settings-system":
            self.inside = True
            self.depth = 1
        elif self.inside and tag == "section":
            self.depth += 1
        if self.inside:
            if tag == "a":
                self.links.append(attrs.get("href"))
            if "id" in attrs:
                self.ids.append(attrs["id"])

    def handle_endtag(self, tag):
        if self.inside and tag == "section":
            self.depth -= 1
            if self.depth == 0:
                self.inside = False


def test_settings_does_not_repeat_navigation_or_header_version():
    page = _SettingsSystem()
    page.feed((STATIC / "settings.html").read_text())
    assert page.links == []
    assert "systemVersion" not in page.ids
    assert "passwordTools" in page.ids


def test_embedded_profile_change_reaches_outer_playlist_workspace():
    auth = (STATIC / "auth.js").read_text()
    playlists = (STATIC / "playlists.js").read_text()
    assert "type:'pch-profile-selected'" in auth
    assert "event.data?.type==='pch-profile-selected'" in playlists
    assert "await loadProfiles()" in playlists


def test_stale_embedded_profile_selection_cannot_override_newer_selection():
    playlists = (STATIC / "playlists.js").read_text()
    assert "let embeddedProfileRequest=0" in playlists
    assert "const sequence=++embeddedProfileRequest" in playlists
    assert "sequence!==embeddedProfileRequest||PCHAuth.profile()!==profileId" in playlists


def test_typing_import_url_clears_stale_selected_file_label():
    script = (STATIC / "external.js").read_text()
    assert "$('selectedFile').hidden=true" in script


def test_import_export_actions_live_beside_the_missing_tab_without_repeating_its_count():
    page = (STATIC / "external.html").read_text()
    tab = page.split('data-match-status="missing"', 1)[1].split("</button>", 1)[0]
    export_row = page.split('id="replenishmentCard"', 1)[1].split('id="reviewToolbar"', 1)[0]
    assert "缺失歌曲" in tab
    assert "下载缺失歌曲" in export_row
    assert "missingCount" not in export_row
    assert "missingSummary" not in export_row


def test_library_task_heading_names_the_stage_not_its_action():
    page = (STATIC / "home.html").read_text()
    script = (STATIC / "home.js").read_text()
    assert '<div class="management-preference-label"><h2>整理任务</h2></div>' in page
    assert 'id="taskTitle" class="library-task-title">整理任务' in page
    assert "$('taskTitle').textContent='整理任务'" in script
    assert 'id="analyzeLibrary">分析曲库' in page


def test_narrow_search_keeps_like_and_add_actions_available():
    styles = (STATIC / "product.css").read_text()
    assert "@media(max-width:720px){body[data-view=playlists] .playlist-search-result .playlist-search-actions" in styles
    assert "body[data-view=playlists] .playlist-search-result .playlist-search-actions button{display:inline-grid}" in styles
    assert "body[data-view=playlists] .playlist-search-result .playlist-search-title-line .playlist-heart{display:grid}" in styles


def test_profile_cache_loads_before_auth_on_every_app_page():
    for name in ("playlists", "home", "mixes", "daily", "status", "settings", "external", "appearance"):
        page = (STATIC / f"{name}.html").read_text()
        assert '/static/page-cache.js?v=app' in page, name
        assert page.index('/static/page-cache.js?v=app') < page.index('/static/auth.js?v=app'), name


def test_auth_exposes_profile_scoped_cache_facade_and_clears_it_on_logout():
    auth = (STATIC / "auth.js").read_text()
    assert "cachedJson" in auth
    assert "invalidateCache" in auth
    assert "PCHPageCache?.setProfiles" in auth
    assert "PCHPageCache?.activate" in auth
    assert "PCHPageCache?.clear" in auth


def test_playlist_navigation_uses_only_the_profile_scoped_summary_cache():
    playlists = (STATIC / "playlists.js").read_text()
    assert "PCHPageCache?.setProfiles(PCHAuth.status()?.username||'',allProfiles)" in playlists
    assert "PCHPageCache?.activate(profileId)" in playlists
    assert "PCHAuth.cachedJson('/api/playlists'" in playlists
    assert "tag:'playlists'" in playlists
    assert "freshMs:60_000" in playlists
    assert "retainMs:900_000" in playlists
    assert "loadedProfileCacheScope!==cacheScope" in playlists
    assert "PCHAuth.cachedJson('/api/playlists/'" not in playlists


def test_profile_switch_still_blanks_the_embedded_workspace_before_loading():
    playlists = (STATIC / "playlists.js").read_text()
    workspace = (STATIC / "playlist-workspace.js").read_text()
    switch = playlists.split("async function switchProfile", 1)[1].split("async function removeTrack", 1)[0]
    assert "resetPlaylistView()" in switch
    assert "renderTracks()" in switch
    assert "renderPlaylistList()" in switch
    assert "frame.src='about:blank'" in workspace


def test_only_safe_management_summaries_use_the_cache_facade():
    mixes = (STATIC / "mixes.js").read_text()
    external = (STATIC / "external.js").read_text()
    settings = (STATIC / "settings.js").read_text()
    status = (STATIC / "status.js").read_text()
    home = (STATIC / "home.js").read_text()
    auth = (STATIC / "auth.js").read_text()
    assert "CACHEABLE_JSON_PATHS=new Set(['/api/playlists','/api/mixes/status','/api/external/sources','/api/plex/saved','/api/automation'])" in auth
    assert "PCHAuth.cachedJson('/api/mixes/status'" in mixes
    assert "PCHAuth.cachedJson('/api/external/sources'" in external
    assert "cachedSettingsJson('/api/plex/saved'" in settings
    assert "cachedSettingsJson('/api/automation'" in settings
    assert "cachedJson('/api/status'" not in status + home + external + settings
    assert "cachedJson('/api/workflow/status" not in home
    assert "cachedJson('/api/qq-auth/" not in home
    assert "PCHAuth.cachedJson('/api/external/sources/'" not in external


def test_management_writes_invalidate_only_related_summary_tags():
    mixes = (STATIC / "mixes.js").read_text()
    external = (STATIC / "external.js").read_text()
    settings = (STATIC / "settings.js").read_text()
    home = (STATIC / "home.js").read_text()
    playlists = (STATIC / "playlists.js").read_text()
    assert "const tags=['smart-mixes']" in mixes and "PCHAuth.invalidateCache(tags)" in mixes
    assert "tags.push('external-sources')" in external and "PCHAuth.invalidateCache(tags)" in external
    assert "const tags=['settings']" in settings and "PCHAuth.invalidateCache(tags)" in settings
    assert "invalidateCache(['library-summary','playlists']" in home
    assert "event.data?.type==='pch-playlists-changed'" in playlists
    assert "PCHAuth.invalidateCache(['playlists'])" in playlists
