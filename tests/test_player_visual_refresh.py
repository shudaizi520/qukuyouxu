from html.parser import HTMLParser
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src/helper/static"


class _PlayerMarkup(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = {}
        self.stack = []
        self.parents = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        node_id = attrs.get("id")
        if node_id:
            self.ids[node_id] = (tag, attrs)
            self.parents[node_id] = self.stack[-1] if self.stack else None
        self.stack.append(node_id or tag)

    def handle_endtag(self, _tag):
        if self.stack:
            self.stack.pop()


def _rule(css, selector):
    cleaned = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    result = {}
    for selectors, body in re.findall(r"([^{}]+)\{([^{}]*)\}", cleaned):
        if selector not in [value.strip() for value in selectors.split(",")]:
            continue
        result.update(
            part.strip().split(":", 1)
            for part in body.split(";") if ":" in part
        )
    return result


def test_shared_and_playlist_notifications_auto_dismiss_success_and_errors():
    shared = (STATIC / "auth.js").read_text(encoding="utf-8")
    playlists = (STATIC / "playlists.js").read_text(encoding="utf-8")

    notify = shared.split("function notify(text", 1)[1].split("async function run", 1)[0]
    assert "error?8000:4000" in notify
    assert "setTimeout(dismiss" in notify
    assert "inline?.remove()" in notify
    assert "PCHUI.notify(message,{error})" in playlists


def test_compact_player_opens_details_only_from_the_artwork_overlay():
    page = (STATIC / "playlists.html").read_text(encoding="utf-8")
    script = (STATIC / "playlist-now-playing.js").read_text(encoding="utf-8")
    parser = _PlayerMarkup()
    parser.feed(page)

    assert parser.ids["playerDetail"][0] == "button"
    assert parser.parents["playerArtwork"] == "playerDetail"
    assert parser.ids["playerArtworkExpand"][1]["aria-hidden"] == "true"
    assert parser.ids["playerNow"][1].get("role") is None
    assert parser.ids["playerNow"][1].get("tabindex") is None
    assert "byId('playerDetail')" in script
    assert "trigger.onclick=open" in script


def test_player_mode_and_refresh_controls_use_line_svg_icons():
    page = (STATIC / "playlists.html").read_text(encoding="utf-8")
    player = (STATIC / "playlist-player.js").read_text(encoding="utf-8")

    for icon_id in (
        "icon-mode-shuffle", "icon-mode-sequential", "icon-mode-single",
        "icon-mode-list", "icon-refresh", "icon-expand-up",
    ):
        assert f'id="{icon_id}"' in page
    assert 'id="playerModeUse"' in page
    assert 'id="customPlaylistRefresh"' in page and '>↻</button>' not in page
    assert "MODE_ICON_IDS" in player
    assert "setAttribute('href',MODE_ICON_IDS[playbackMode])" in player


def test_fullscreen_player_has_qq_like_proportions_and_a_play_state_visualizer():
    page = (STATIC / "playlists.html").read_text(encoding="utf-8")
    script = (STATIC / "playlist-now-playing.js").read_text(encoding="utf-8")
    visualizer = (STATIC / "playlist-visualizer.js").read_text(encoding="utf-8") if (STATIC / "playlist-visualizer.js").exists() else ""
    css = (STATIC / "playlist-now-playing.css").read_text(encoding="utf-8")

    assert 'id="nowPlayingContent"' in page
    parser = _PlayerMarkup()
    parser.feed(page)
    assert parser.ids["nowPlayingVisualizer"][0] == "canvas"
    assert page.index('id="nowPlayingArtwork"') < page.index('id="nowPlayingContent"')
    assert page.index('id="nowPlayingTitle"') < page.index('id="nowPlayingLyrics"')
    assert "root.dataset.playing=String(!!snapshot?.playing)" in script
    assert "createPlaybackVisualizer" in script
    assert "requestAnimationFrame" in visualizer
    assert "getContext('2d')" in visualizer
    assert "createMediaElementSource(media)" in visualizer
    assert "createAnalyser()" in visualizer
    assert "getByteFrequencyData" in visualizer
    assert "Math.sin" not in visualizer
    assert "Math.cos" not in visualizer
    assert "media:byId('playerAudio')" in script
    assert _rule(css, ".now-playing-artwork")["width"] == "min(430px,35vw)"
    assert _rule(css, ".now-playing-lyric-line")["font-size"] == "clamp(14px,.9vw,17px)"
    assert _rule(css, ".now-playing-lyric-line.active")["font-size"] == "clamp(16px,1.05vw,19px)"
    assert ".now-playing-visualizer{position:absolute;z-index:1;left:50%;bottom:var(--app-player-height,68px);width:min(700px,46vw);height:68px" in css
    assert "buildVisualizerLevels" in visualizer
    assert "smoothVisualizerLevels" in visualizer
    assert "analyser.smoothingTimeConstant=.38" in visualizer
    assert "level*height*.76" in visualizer
    assert "paintGlow" in visualizer
    assert "context.filter='blur(8px)'" in visualizer
    assert "barWidth=4,gap=4" in visualizer
    assert "Math.min(100" in visualizer
    assert "context.beginPath()" not in visualizer
    assert visualizer.count("context.fillRect") >= 2
    assert ".now-playing-visualizer-bar" not in css


def test_player_identity_has_breathing_room_and_visualizer_peaks_at_center():
    foundation = (STATIC / "design-system.css").read_text(encoding="utf-8")
    visualizer = (STATIC / "playlist-visualizer.js").read_text(encoding="utf-8")
    css = (STATIC / "playlist-now-playing.css").read_text(encoding="utf-8")

    player = _rule(foundation, "body[data-view=playlists] .playlist-player")
    assert player["padding"] == "0 22px 4px calc(var(--app-rail-width) + 40px)"
    assert _rule(foundation, "body[data-view=playlists] .playlist-now").get("padding-left", "0") == "0"
    assert "width:min(700px,46vw);height:68px" in css
    assert "groupPosition" not in visualizer
    assert "continuousSweep" in visualizer
    assert "edgeWindow=Math.min(1,position/.08,(1-position)/.08)" in visualizer
    assert "context.fillRect" in visualizer


def test_immersive_background_continues_behind_the_transport():
    foundation = (STATIC / "design-system.css").read_text(encoding="utf-8")
    details = (STATIC / "playlist-now-playing.css").read_text(encoding="utf-8")

    assert _rule(details, ".now-playing")["bottom"] == "0"
    assert _rule(details, ".now-playing-open .playlist-player")["background"] == "transparent"
    assert _rule(
        foundation,
        "body[data-view=playlists].now-playing-open .playlist-player",
    )["background"] == "transparent"
    assert _rule(details, ".now-playing-visualizer")["bottom"] == "var(--app-player-height,68px)"


def test_fullscreen_space_toggles_playback_without_activating_collapse_button():
    page = (STATIC / "playlists.html").read_text(encoding="utf-8")
    script = (STATIC / "playlist-now-playing.js").read_text(encoding="utf-8")
    player = (STATIC / "playlist-player.js").read_text(encoding="utf-8")
    playlists = (STATIC / "playlists.js").read_text(encoding="utf-8")
    parser = _PlayerMarkup()
    parser.feed(page)

    assert parser.ids["nowPlaying"][1]["tabindex"] == "-1"
    assert "event.code==='Space'" in script
    assert "togglePlayback()" in script
    assert "root.focus({preventScroll:true})" in script
    assert "closeButton.focus()" not in script
    assert "function togglePlayback()" in player
    assert "togglePlayback:()=>playlistPlayer.togglePlayback()" in playlists


def test_fullscreen_collapse_is_a_bare_chevron_and_extra_controls_are_hidden():
    page = (STATIC / "playlists.html").read_text(encoding="utf-8")
    css = (STATIC / "playlist-now-playing.css").read_text(encoding="utf-8")
    parser = _PlayerMarkup()
    parser.feed(page)

    assert parser.ids["nowPlayingClose"][0] == "button"
    assert parser.ids["nowPlayingCloseIcon"][0] == "svg"
    close_rule = _rule(css, ".now-playing-close")
    hover_rule = _rule(css, ".now-playing-close:hover")
    assert close_rule["background"] == "transparent"
    assert close_rule["border-radius"] == "0"
    assert hover_rule["background"] == "transparent"
    protected_hover = _rule(css, "body[data-view=playlists] .now-playing .now-playing-close:hover:not(:disabled)")
    assert protected_hover["background"] == "transparent!important"
    assert protected_hover["box-shadow"] == "none!important"
    assert _rule(css, ".now-playing-open .playlist-mode-control")["display"] == "none"
    assert _rule(css, ".now-playing-open .playlist-player-queue")["display"] == "none"


def test_playlist_and_player_chrome_stay_lightweight():
    product = (STATIC / "product.css").read_text(encoding="utf-8")
    details = (STATIC / "playlist-now-playing.css").read_text(encoding="utf-8")
    foundation = (STATIC / "design-system.css").read_text(encoding="utf-8")

    assert _rule(product, ".playlist-track")["border-bottom"] == "0"
    assert _rule(product, ".playlist-track-head")["background"] == "transparent"
    assert "--app-player-height:68px" in foundation
    assert "height:var(--app-player-height);\n grid-template-rows:minmax(0,1fr)" in foundation
    assert "height:80px;grid-template-rows:18px" not in product
    assert "grid-template-rows:64px minmax(0,1fr) var(--app-player-height,68px)" in product
    assert _rule(foundation, "body[data-view=playlists] .playlist-progress-rail")["height"] == "2px"
    progress_target = _rule(foundation, "body[data-view=playlists] .playlist-progress-rail input[type=range]")
    assert progress_target["height"] == "20px"
    assert progress_target["opacity"] == "0"
    assert _rule(product, ".playlist-player-queue")["display"] == "none"
    assert _rule(details, ".now-playing")["bottom"] == "0"
    assert _rule(details, ".now-playing-open .playlist-player")["border-top"] == "0"
    assert _rule(details, ".now-playing-open .playlist-player")["background"] == "transparent"
    fullscreen_progress = _rule(details, ".now-playing-open .playlist-progress-rail")
    assert fullscreen_progress["background"] == "transparent"
    assert fullscreen_progress["--progress-played-color"] == "var(--immersive-accent)"


def test_playlist_rows_and_action_states_use_inset_theme_surfaces():
    foundation = (STATIC / "design-system.css").read_text(encoding="utf-8")

    track = _rule(foundation, "body[data-view=playlists] .playlist-track")
    track_fill = _rule(foundation, "body[data-view=playlists] .playlist-track::before")
    track_hover = _rule(foundation, "body[data-view=playlists] .playlist-track:hover::before")
    action = _rule(foundation, "body[data-view=playlists] .playlist-actions .secondary")
    action_hover = _rule(foundation, "body[data-view=playlists] .playlist-actions .secondary:hover:not(:disabled)")
    action_disabled = _rule(foundation, "body[data-view=playlists] .playlist-actions .secondary:disabled")

    assert track["background"] == "transparent"
    assert track_fill["top"] == "4px"
    assert track_fill["bottom"] == "4px"
    assert track_fill["left"] == "28px"
    assert track_fill["right"] == "8px"
    assert track_fill["border-radius"] == "8px"
    assert track_hover["background"] == "color-mix(in srgb,var(--app-hover) 64%,transparent)"
    assert action["background"] == "transparent"
    assert action_hover["background"] == "var(--app-hover)"
    assert action_disabled["background"] == "transparent"


def test_keyboard_playback_focus_uses_soft_theme_fill_without_a_hard_outline():
    foundation = (STATIC / "design-system.css").read_text(encoding="utf-8")

    playlist_focus = _rule(
        foundation,
        "body[data-view=playlists] .playlist-track:focus-visible",
    )
    track_focus_fill = _rule(
        foundation,
        "body[data-view=playlists] .playlist-track:focus-visible::before",
    )
    search_focus = _rule(
        foundation,
        "body[data-view=playlists] .playlist-search-result:focus-visible",
    )
    import_focus = _rule(
        foundation,
        "body[data-view=external] .external-track-row.external-track-playable:focus-visible",
    )

    assert playlist_focus["outline"] == "0"
    assert track_focus_fill["background"] == "color-mix(in srgb,var(--app-hover) 64%,transparent)"
    assert search_focus["background"] == "color-mix(in srgb,var(--app-hover) 64%,transparent)"
    assert import_focus["outline"] == "0"
    assert import_focus["background"] == "color-mix(in srgb,var(--app-hover) 64%,transparent)"


def test_desktop_transport_is_centered_between_equal_side_columns():
    foundation = (STATIC / "design-system.css").read_text(encoding="utf-8")
    desktop = foundation.split("@media(max-width:700px){", 1)[0]

    player_body = _rule(
        desktop,
        "body[data-view=playlists] .playlist-player-body",
    )

    assert player_body["grid-template-columns"] == (
        "minmax(210px,1fr) auto minmax(210px,1fr)"
    )


def test_playlist_header_keeps_number_column_but_has_no_hash_label():
    page = (STATIC / "playlists.html").read_text(encoding="utf-8")

    assert '<div class="playlist-track-head"><span aria-hidden="true"></span><span>歌曲</span>' in page
    assert '<span>#</span>' not in page


def test_compact_player_does_not_paint_a_large_hover_selection_block():
    css = (STATIC / "playlist-now-playing.css").read_text(encoding="utf-8")

    assert _rule(css, ".playlist-now").get("background", "transparent") == "transparent"
    assert _rule(css, ".playlist-now:hover").get("background", "transparent") == "transparent"
    overlay = _rule(css, ".playlist-artwork-expand")
    assert overlay["opacity"] == "0"
    assert "player-detail:hover .playlist-artwork-expand" in css
