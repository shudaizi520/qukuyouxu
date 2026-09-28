from html.parser import HTMLParser
import os
import base64
from pathlib import Path
import re

from PIL import Image

from playwright.sync_api import sync_playwright

from tools.playwright_runtime import prepare_playwright_environment
from ui_css import page_css, product_css


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src/helper/static"


class _PlayerMarkup(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = {}
        self.stack = []
        self.parents = {}
        self.class_parents = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        node_id = attrs.get("id")
        classes = attrs.get("class", "").split()
        if node_id:
            self.ids[node_id] = (tag, attrs)
            self.parents[node_id] = self.stack[-1] if self.stack else None
        for class_name in classes:
            self.class_parents[class_name] = self.stack[-1] if self.stack else None
        self.stack.append(node_id or (classes[0] if classes else tag))

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


def test_playlist_heading_icons_are_large_click_targets_with_color_only_hover():
    document = re.sub(
        r"<script\b.*?</script>|<link\b[^>]*>",
        "",
        (STATIC / "playlists.html").read_text(encoding="utf-8"),
        flags=re.I | re.S,
    )

    prepare_playwright_environment(ROOT)
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(ROOT / ".playwright"))
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.set_content(document)
        page.add_style_tag(content=page_css("playlists"))
        page.evaluate("""() => {
          document.querySelector('#bootScreen')?.remove();
          document.querySelector('#workspace').hidden = false;
          document.body.style.setProperty('--playlist-muted', 'rgb(120, 130, 140)');
          document.body.style.setProperty('--playlist-accent', 'rgb(25, 199, 131)');
          document.body.style.setProperty('--app-accent', 'rgb(25, 199, 131)');
          document.body.style.setProperty('--icon-default', 'rgb(120, 130, 140)');
          document.body.style.setProperty('--icon-hover', 'rgb(25, 199, 131)');
        }""")

        for selector in ("#customPlaylistCreate", "#importHubButton", "#customPlaylistRefresh"):
            control = page.locator(selector)
            icon = control.locator(".sidebar-control-icon")
            assert icon.count() == 1
            size = control.evaluate(
                "node => [getComputedStyle(node).width, getComputedStyle(node).height]"
            )
            assert size == ["36px", "36px"]
            before = control.evaluate("node => getComputedStyle(node).color")
            before_icon = icon.evaluate("node => getComputedStyle(node).color")
            control.hover()
            hovered = control.evaluate("""node => {
              const style = getComputedStyle(node);
              return {
                color: style.color,
                background: style.backgroundColor,
                border: style.borderStyle,
                shadow: style.boxShadow,
                outline: style.outlineStyle,
              };
            }""")
            assert before == "rgb(120, 130, 140)"
            assert before_icon == "rgb(120, 130, 140)"
            assert hovered == {
                "color": "rgb(25, 199, 131)",
                "background": "rgba(0, 0, 0, 0)",
                "border": "none",
                "shadow": "none",
                "outline": "none",
            }
            assert icon.evaluate("node => getComputedStyle(node).color") == "rgb(25, 199, 131)"
            assert icon.evaluate(
                "node => [getComputedStyle(node).width, getComputedStyle(node).height]"
            ) == ["20px", "20px"]

        assert page.locator(".playlist-side-heading-actions .sidebar-control-icon").count() == 3

        page.mouse.move(500, 400)
        page.locator("#customPlaylistRefresh").focus()
        assert page.locator("#customPlaylistRefresh").evaluate(
            "node => getComputedStyle(node).outlineStyle"
        ) == "solid"

        refresh = page.locator("#customPlaylistRefresh")
        refresh.evaluate("node => node.classList.add('pch-pending')")
        assert refresh.evaluate(
            "node => getComputedStyle(node, '::before').content"
        ) == "none"
        assert refresh.locator(".sidebar-control-icon").evaluate(
            "node => getComputedStyle(node).animationName"
        ) == "pch-spin"
        browser.close()


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
    assert "getByteTimeDomainData" not in visualizer
    assert "Math.sin" not in visualizer
    assert "Math.cos" not in visualizer
    assert "media:byId('playerAudio')" in script
    assert _rule(css, ".now-playing-turntable")["width"] == "min(500px,38vw)"
    assert _rule(css, ".now-playing-lyric-line")["font-size"] == "clamp(14px,.9vw,17px)"
    assert _rule(css, ".now-playing-lyric-line.active")["font-size"] == "clamp(16px,1.05vw,19px)"
    assert ".now-playing-visualizer{position:absolute;z-index:1;left:50%;bottom:var(--app-player-height,68px);width:min(680px,48vw);height:64px" in css
    assert "buildVisualizerLevels" in visualizer
    assert "smoothVisualizerLevels" in visualizer
    assert "analyser.smoothingTimeConstant=.27" in visualizer
    assert "level*height*.86" in visualizer
    assert "paintGlow" in visualizer
    assert "context.filter='blur(5px)'" in visualizer
    assert "visualizerGeometry(width)" in visualizer
    assert "context.beginPath()" not in visualizer
    assert visualizer.count("context.fillRect") >= 2
    assert ".now-playing-visualizer-bar" not in css


def test_fullscreen_artwork_is_a_playback_driven_turntable():
    source = (STATIC / "playlists.html").read_text(encoding="utf-8")
    css = (STATIC / "playlist-now-playing.css").read_text(encoding="utf-8")
    start = source.index('<section id="nowPlaying"')
    end = source.index("</section>", start) + len("</section>")
    document = f'<style>{css}</style><body data-view="playlists">{source[start:end]}</body>'

    prepare_playwright_environment(ROOT)
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(ROOT / ".playwright"))
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1600, "height": 900})
        page.set_content(document)
        page.locator("#nowPlaying").evaluate("node => node.hidden=false")

        assert page.locator(".now-playing-turntable").count() == 1
        assert page.locator(".now-playing-platter").count() == 1
        assert page.locator(".now-playing-vinyl").count() == 1
        assert page.locator(".now-playing-tonearm").count() == 1
        assert page.locator(".now-playing-tonearm-asset").count() == 1
        assert page.locator(".now-playing-vinyl #nowPlayingArtwork").count() == 1

        geometry = page.evaluate(
            """() => {
              const rect = selector => document.querySelector(selector).getBoundingClientRect();
              const deck=rect('.now-playing-turntable'),platter=rect('.now-playing-platter');
              const artwork=rect('#nowPlayingArtwork'),tonearmNode=document.querySelector('.now-playing-tonearm');
              const tonearmAssetNode=document.querySelector('.now-playing-tonearm-asset');
              const tonearmStyle=getComputedStyle(document.querySelector('.now-playing-tonearm'));
              const assetStyle=getComputedStyle(document.querySelector('.now-playing-tonearm-asset'));
              return {
                deck:[deck.width,deck.height],platter:[platter.width,platter.height],
                artwork:[artwork.width,artwork.height],tonearm:[tonearmNode.offsetWidth,tonearmNode.offsetHeight],
                tonearmAsset:[tonearmAssetNode.offsetWidth,tonearmAssetNode.offsetHeight],
                tonearmImage:assetStyle.backgroundImage,
                platterCenterOffset:Math.abs((platter.left+platter.width/2)-(deck.left+deck.width/2)),
                platterVerticalCenter:(platter.top+platter.height/2-deck.top)/deck.height,
                platterBottom:(platter.bottom-deck.top)/deck.height,
                tonearmTransition:parseFloat(tonearmStyle.transitionDuration),
                deckRadius:getComputedStyle(document.querySelector('.now-playing-turntable')).borderRadius,
                platterRadius:getComputedStyle(document.querySelector('.now-playing-platter')).borderRadius,
                artworkRadius:getComputedStyle(document.querySelector('#nowPlayingArtwork')).borderRadius,
              };
            }"""
        )
        assert 410 <= geometry["deck"][0] <= 500
        assert abs(geometry["deck"][0] - geometry["deck"][1]) <= 1
        assert geometry["deck"][0] * 0.63 <= geometry["platter"][0] <= geometry["deck"][0] * 0.67
        assert geometry["platterCenterOffset"] <= geometry["deck"][0] * 0.012
        assert 0.465 <= geometry["platterVerticalCenter"] <= 0.49
        assert geometry["platterBottom"] <= 0.82
        assert geometry["platter"][0] * 0.66 <= geometry["artwork"][0] <= geometry["platter"][0] * 0.70
        assert geometry["deck"][0] * 0.22 <= geometry["tonearm"][0] <= geometry["deck"][0] * 0.34
        assert geometry["tonearmAsset"] == geometry["tonearm"]
        assert "turntable-tonearm.png" in geometry["tonearmImage"]
        assert geometry["tonearmTransition"] >= 1.2
        assert geometry["deckRadius"] != "0px"
        assert geometry["platterRadius"] == "50%"
        assert geometry["artworkRadius"] == "50%"

        vinyl = page.locator(".now-playing-vinyl")
        assert vinyl.evaluate("node => getComputedStyle(node).animationName") != "none"
        assert vinyl.evaluate("node => getComputedStyle(node).animationPlayState") == "paused"
        paused_tonearm = page.locator(".now-playing-tonearm").evaluate(
            "node => getComputedStyle(node).transform"
        )
        page.locator("#nowPlaying").evaluate("node => node.dataset.playing='true'")
        page.wait_for_timeout(420)
        assert vinyl.evaluate("node => getComputedStyle(node).animationPlayState") == "running"
        entering_tonearm = page.locator(".now-playing-tonearm").evaluate(
            "node => getComputedStyle(node).transform"
        )
        assert entering_tonearm != paused_tonearm
        page.wait_for_timeout(1600)
        playing_tonearm = page.locator(".now-playing-tonearm").evaluate(
            "node => getComputedStyle(node).transform"
        )
        assert playing_tonearm != entering_tonearm
        page.locator("#nowPlaying").evaluate("node => node.dataset.playing='false'")
        page.wait_for_timeout(350)
        leaving_tonearm = page.locator(".now-playing-tonearm").evaluate(
            "node => getComputedStyle(node).transform"
        )
        assert leaving_tonearm not in (playing_tonearm, paused_tonearm)
        page.wait_for_timeout(1350)
        stopped_tonearm = page.locator(".now-playing-tonearm").evaluate(
            "node => getComputedStyle(node).transform"
        )
        assert stopped_tonearm == paused_tonearm

        page.set_viewport_size({"width": 390, "height": 844})
        mobile_width = page.locator(".now-playing-turntable").evaluate(
            "node => node.getBoundingClientRect().width"
        )
        assert mobile_width <= 300
        assert mobile_width <= 390 - 36
        browser.close()


def test_turntable_uses_a_realistic_project_asset_without_duplicate_deck_controls():
    source = (STATIC / "playlists.html").read_text(encoding="utf-8")
    css = (STATIC / "playlist-now-playing.css").read_text(encoding="utf-8")
    asset = STATIC / "turntable-chassis.png"

    assert asset.is_file()
    assert asset.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert 'url("turntable-chassis.png")' in css
    assert "now-playing-deck-mark" not in source
    assert "now-playing-power" not in source
    assert "now-playing-speed" not in source


def test_tonearm_uses_one_optimized_transparent_project_asset():
    source = (STATIC / "playlists.html").read_text(encoding="utf-8")
    css = (STATIC / "playlist-now-playing.css").read_text(encoding="utf-8")
    asset = STATIC / "turntable-tonearm.png"

    assert asset.is_file()
    assert asset.stat().st_size < 500_000
    with Image.open(asset) as image:
        assert image.format == "PNG"
        assert image.mode == "RGBA"
        assert image.height <= 800
        assert image.width <= 320
        assert image.getchannel("A").getextrema() == (0, 255)

    start = source.index('<section id="nowPlaying"')
    end = source.index("</section>", start) + len("</section>")
    document = f'<style>{css}</style><body data-view="playlists">{source[start:end]}</body>'

    prepare_playwright_environment(ROOT)
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(ROOT / ".playwright"))
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1600, "height": 900})
        page.set_content(document)
        tonearm = page.locator(".now-playing-tonearm")
        asset_layer = page.locator(".now-playing-tonearm-asset")

        assert tonearm.locator(":scope > *").count() == 1
        assert asset_layer.count() == 1
        assert "turntable-tonearm.png" in asset_layer.evaluate(
            "node => getComputedStyle(node).backgroundImage"
        )
        browser.close()


def test_tonearm_drifts_gently_only_while_music_is_playing():
    source = (STATIC / "playlists.html").read_text(encoding="utf-8")
    css = (STATIC / "playlist-now-playing.css").read_text(encoding="utf-8")
    start = source.index('<section id="nowPlaying"')
    end = source.index("</section>", start) + len("</section>")
    document = f'<style>{css}</style><body data-view="playlists">{source[start:end]}</body>'

    prepare_playwright_environment(ROOT)
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(ROOT / ".playwright"))
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1600, "height": 900})
        page.set_content(document)
        page.locator("#nowPlaying").evaluate("node => node.hidden=false")
        arm = page.locator(".now-playing-tonearm-asset")

        assert arm.evaluate("node => getComputedStyle(node).animationName") == "none"
        page.locator("#nowPlaying").evaluate("node => node.dataset.playing='true'")
        animation = arm.evaluate(
            """node => ({
              name:getComputedStyle(node).animationName,
              duration:parseFloat(getComputedStyle(node).animationDuration),
              delay:parseFloat(getComputedStyle(node).animationDelay),
            })"""
        )
        assert animation["name"] == "now-playing-tonearm-drift"
        assert 4 <= animation["duration"] <= 6
        assert animation["delay"] >= 1.2

        page.wait_for_timeout(1750)
        first = arm.evaluate("node => getComputedStyle(node).transform")
        page.wait_for_timeout(900)
        second = arm.evaluate("node => getComputedStyle(node).transform")
        assert first != second

        page.locator("#nowPlaying").evaluate("node => node.dataset.playing='false'")
        assert arm.evaluate("node => getComputedStyle(node).animationName") == "none"

        page.emulate_media(reduced_motion="reduce")
        page.locator("#nowPlaying").evaluate("node => node.dataset.playing='true'")
        assert arm.evaluate("node => getComputedStyle(node).animationName") == "none"
        browser.close()


def test_rotating_vinyl_fits_the_chassis_platter_instead_of_leaking_below_it():
    css = (STATIC / "playlist-now-playing.css").read_text(encoding="utf-8")
    platter = _rule(css, ".now-playing-platter")
    vinyl = _rule(css, ".now-playing-vinyl")
    artwork = _rule(css, ".now-playing-artwork")

    assert platter["left"] == "17.2%"
    assert platter["top"] == "15.2%"
    assert platter["width"] == "65.4%"
    assert vinyl["inset"] == "0"
    assert artwork["width"] == "68%"


def test_visualizer_uses_distinct_frequency_bands_without_a_fixed_middle_spike():
    source = (STATIC / "playlist-visualizer.js").read_text(encoding="utf-8")
    module_url = "data:text/javascript;base64," + base64.b64encode(source.encode()).decode()
    prepare_playwright_environment(ROOT)
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(ROOT / ".playwright"))
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        metrics = page.evaluate("""async url => {
          const module = await import(url);
          const makeSpectrum = (peakBin,peakStrength=70) => {
            const bins = new Uint8Array(2048);
            let seed=17;
            for (let index=2; index<bins.length; index++) {
              seed=(seed*48271)%2147483647;
              const jitter=(seed/2147483647-.5)*30;
              const tilt=196-31*Math.log10(index+1);
              const peak=peakStrength*Math.exp(-Math.pow((Math.log(index)-Math.log(peakBin))/.12,2));
              bins[index]=Math.max(0,Math.min(255,Math.round(tilt+jitter+peak)));
            }
            return bins;
          };
          const levels = module.buildVisualizerLevels(makeSpectrum(180),64);
          const ordered = [...levels].sort((a,b)=>a-b);
          const median = ordered[Math.floor(ordered.length/2)];
          const peaks = levels.filter((value,index) => index>1 && index<levels.length-2
            && value>levels[index-1] && value>=levels[index+1]
            && value>(levels[index-2]+levels[index+2])*.5+0.018).length;
          const half=Math.floor(levels.length/2);
          const average = values => values.reduce((sum,value)=>sum+value,0)/values.length;
          return {
            max:Math.max(...levels),median,peaks,geometry:module.visualizerGeometry(900),
            active:levels.filter(value=>value>.025).length,
            leftAverage:average(levels.slice(0,half)),
            rightAverage:average(levels.slice(-half)),
            centerAverage:average(levels.slice(half-8,half+8)),
            edgeAverage:average([...levels.slice(0,8),...levels.slice(-8)]),
          };
        }""", module_url)
        browser.close()

    assert 1.1 <= metrics["max"] / metrics["median"] <= 2.8
    assert 4 <= metrics["peaks"] <= 14
    assert metrics["active"] >= 54
    assert 0.9 <= metrics["leftAverage"] / metrics["rightAverage"] <= 1.1
    assert metrics["centerAverage"] > metrics["edgeAverage"] * 1.15
    assert 60 <= metrics["geometry"]["count"] <= 64
    assert metrics["geometry"]["span"] <= 625
    assert 3 <= metrics["geometry"]["barWidth"] <= 4
    assert 5.5 <= metrics["geometry"]["gap"] <= 7


def test_visualizer_active_region_keeps_real_local_relief_instead_of_forming_a_platform():
    source = (STATIC / "playlist-visualizer.js").read_text(encoding="utf-8")
    module_url = "data:text/javascript;base64," + base64.b64encode(source.encode()).decode()
    prepare_playwright_environment(ROOT)
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(ROOT / ".playwright"))
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        frames = page.evaluate("""async url => {
          const module = await import(url);
          const makeSpectrum = peakBin => {
            const bins = new Uint8Array(2048);
            let seed=peakBin*7919;
            for (let index=3; index<bins.length; index++) {
              seed=(seed*48271)%2147483647;
              const jitter=(seed/2147483647-.5)*28;
              const tilt=210-38*Math.log10(index+1);
              const primary=55*Math.exp(-Math.pow((Math.log(index)-Math.log(peakBin))/.10,2));
              const harmonic=32*Math.exp(-Math.pow((Math.log(index)-Math.log(peakBin*2.4))/.15,2));
              bins[index]=Math.max(0,Math.min(255,Math.round(tilt+primary+harmonic+jitter)));
            }
            return bins;
          };
          return [24,55,130,310].map(peakBin => {
            const levels=module.buildVisualizerLevels(makeSpectrum(peakBin),64);
            const peakIndex=levels.indexOf(Math.max(...levels));
            const start=Math.max(0,Math.min(levels.length-24,peakIndex-12));
            const activeRegion=levels.slice(start,start+24);
            const mean=activeRegion.reduce((sum,value)=>sum+value,0)/activeRegion.length;
            const deviation=Math.sqrt(activeRegion.reduce((sum,value)=>sum+Math.pow(value-mean,2),0)/activeRegion.length);
            const visibleThreshold=Math.max(...activeRegion)*.35;
            let run=1,longestFlatRun=1;
            for(let index=1;index<activeRegion.length;index++){
              const visible=activeRegion[index]>=visibleThreshold||activeRegion[index-1]>=visibleThreshold;
              run=visible&&Math.abs(activeRegion[index]-activeRegion[index-1])<.025?run+1:1;
              longestFlatRun=Math.max(longestFlatRun,run);
            }
            return {coefficientOfVariation:deviation/mean,longestFlatRun};
          });
        }""", module_url)
        browser.close()

    assert max(frame["longestFlatRun"] for frame in frames) <= 8, frames
    assert sum(frame["coefficientOfVariation"] >= 0.045 for frame in frames) >= 3, frames


def test_visualizer_frequency_regions_move_independently_without_global_pulsing():
    source = (STATIC / "playlist-visualizer.js").read_text(encoding="utf-8")
    module_url = "data:text/javascript;base64," + base64.b64encode(source.encode()).decode()
    prepare_playwright_environment(ROOT)
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(ROOT / ".playwright"))
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        metrics = page.evaluate("""async url => {
          const module = await import(url);
          const makeSpectrum = () => {
            const bins = new Uint8Array(2048);
            for(let index=3;index<bins.length;index++)bins[index]=72;
            for(let index=3;index<=6;index++)bins[index]=185;
            return bins;
          };
          const boosted = (start,end) => {
            const bins=makeSpectrum();
            for(let index=start;index<=end;index++)bins[index]=205;
            return bins;
          };
          const base=module.buildVisualizerLevels(makeSpectrum(),88);
          const bass=module.buildVisualizerLevels(boosted(8,28),88);
          const treble=module.buildVisualizerLevels(boosted(420,850),88);
          const deltas=next=>next.map((value,index)=>Math.abs(value-base[index]));
          const bassDelta=deltas(bass),trebleDelta=deltas(treble);
          const bassChanged=bassDelta.map((value,index)=>value>.025?index:-1).filter(index=>index>=0);
          const trebleChanged=trebleDelta.map((value,index)=>value>.025?index:-1).filter(index=>index>=0);
          const trebleSet=new Set(trebleChanged);
          return {
            bassPeak:Math.max(...bassDelta),
            treblePeak:Math.max(...trebleDelta),
            bassChanged:bassChanged.length,
            trebleChanged:trebleChanged.length,
            overlap:bassChanged.filter(index=>trebleSet.has(index)).length,
          };
        }""", module_url)
        browser.close()

    assert metrics["bassPeak"] >= 0.08, metrics
    assert metrics["treblePeak"] >= 0.08, metrics
    assert 2 <= metrics["bassChanged"] <= 36, metrics
    assert 2 <= metrics["trebleChanged"] <= 36, metrics
    assert metrics["overlap"] <= 4, metrics


def test_visualizer_preserves_the_difference_between_low_and_high_pitch_spectra():
    source = (STATIC / "playlist-visualizer.js").read_text(encoding="utf-8")
    module_url = "data:text/javascript;base64," + base64.b64encode(source.encode()).decode()
    prepare_playwright_environment(ROOT)
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(ROOT / ".playwright"))
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        metrics = page.evaluate("""async url => {
          const module=await import(url);
          const makeVoice=fundamental=>{
            const bins=new Uint8Array(2048);bins.fill(12);
            for(let harmonic=1;harmonic<=10;harmonic++){
              const center=fundamental*harmonic;
              const strength=190/Math.pow(harmonic,.72);
              for(let index=Math.max(1,Math.floor(center*.88));index<=Math.min(1260,Math.ceil(center*1.12));index++){
                const distance=(Math.log(index)-Math.log(center))/.045;
                bins[index]=Math.min(255,Math.round(bins[index]+strength*Math.exp(-distance*distance)));
              }
            }
            return bins;
          };
          const correlation=(first,second)=>{
            const firstMean=first.reduce((sum,value)=>sum+value,0)/first.length;
            const secondMean=second.reduce((sum,value)=>sum+value,0)/second.length;
            let covariance=0,firstVariance=0,secondVariance=0;
            for(let index=0;index<first.length;index++){
              const left=first[index]-firstMean,right=second[index]-secondMean;
              covariance+=left*right;firstVariance+=left*left;secondVariance+=right*right;
            }
            return covariance/Math.sqrt(firstVariance*secondVariance);
          };
          const weightedRadius=levels=>{
            const center=(levels.length-1)/2;
            const total=levels.reduce((sum,value)=>sum+value,0);
            return levels.reduce((sum,value,index)=>sum+value*Math.abs(index-center),0)/total;
          };
          const low=module.buildVisualizerLevels(makeVoice(10),64);
          const high=module.buildVisualizerLevels(makeVoice(28),64);
          return {
            correlation:correlation(low,high),
            meanDifference:low.reduce((sum,value,index)=>sum+Math.abs(value-high[index]),0)/64,
            lowRadius:weightedRadius(low),
            highRadius:weightedRadius(high),
          };
        }""", module_url)
        browser.close()

    assert metrics["correlation"] <= 0.25, metrics
    assert metrics["meanDifference"] >= 0.09, metrics
    assert metrics["highRadius"] >= metrics["lowRadius"] + 3, metrics


def test_low_frequency_energy_does_not_wrap_into_detached_side_islands():
    source = (STATIC / "playlist-visualizer.js").read_text(encoding="utf-8")
    module_url = "data:text/javascript;base64," + base64.b64encode(source.encode()).decode()
    prepare_playwright_environment(ROOT)
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(ROOT / ".playwright"))
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        metrics = page.evaluate("""async url => {
          const module=await import(url);
          const bins=new Uint8Array(2048);bins.fill(10);
          for(let index=7;index<=70;index++){
            const distance=(Math.log(index)-Math.log(18))/.36;
            bins[index]=Math.min(255,Math.round(16+210*Math.exp(-distance*distance)));
          }
          const levels=module.buildVisualizerLevels(bins,64);
          const active=levels.map(level=>level>.075);
          let islands=0,inside=false;
          for(const value of active){
            if(value&&!inside)islands++;
            inside=value;
          }
          return {
            islands,
            pattern:active.map(value=>value?'#':'.').join(''),
          };
        }""", module_url)
        browser.close()

    assert metrics["islands"] == 1, metrics["pattern"]


def test_visualizer_center_is_not_permanently_reserved_for_weakest_low_bands():
    source = (STATIC / "playlist-visualizer.js").read_text(encoding="utf-8")
    module_url = "data:text/javascript;base64," + base64.b64encode(source.encode()).decode()
    prepare_playwright_environment(ROOT)
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(ROOT / ".playwright"))
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        metrics = page.evaluate("""async url => {
          const module=await import(url);
          const bins=new Uint8Array(2048);
          for(let index=7;index<=18;index++)bins[index]=14;
          let seed=41;
          for(let index=19;index<=320;index++){
            seed=(seed*48271)%2147483647;
            bins[index]=Math.round(145+(seed/2147483647-.5)*34);
          }
          for(let index=321;index<=1260;index++)bins[index]=52;
          const levels=module.buildVisualizerLevels(bins,64);
          const average=values=>values.reduce((sum,value)=>sum+value,0)/values.length;
          const center=levels.slice(28,36);
          const neighbors=[...levels.slice(16,28),...levels.slice(36,48)];
          return {
            centerAverage:average(center),
            neighborAverage:average(neighbors),
            activeCenter:center.filter(value=>value>.12).length,
          };
        }""", module_url)
        browser.close()

    assert metrics["centerAverage"] >= 0.16, metrics
    assert metrics["activeCenter"] >= 6, metrics


def test_short_visualizer_does_not_spread_sparse_music_across_a_long_canvas():
    source = (STATIC / "playlist-visualizer.js").read_text(encoding="utf-8")
    module_url = "data:text/javascript;base64," + base64.b64encode(source.encode()).decode()
    prepare_playwright_environment(ROOT)
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(ROOT / ".playwright"))
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        metrics = page.evaluate("""async url => {
          const module=await import(url);
          const geometry=module.visualizerGeometry(900);
          const bins=new Uint8Array(2048);
          for(let index=7;index<=1260;index++)bins[index]=18;
          for(const [start,end,strength] of [[20,62,145],[88,205,132],[300,610,118]]){
            for(let index=start;index<=end;index++){
              bins[index]=Math.min(255,strength+Math.round(18*Math.sin(index*.37)));
            }
          }
          const levels=module.buildVisualizerLevels(bins,geometry.count);
          const active=levels.map(level=>level>.075);
          const first=active.indexOf(true),last=active.lastIndexOf(true);
          let islands=0,longestGap=0,gap=0,inside=false;
          for(let index=0;index<active.length;index++){
            if(active[index]){
              if(!inside)islands++;
              inside=true;gap=0;
            }else if(index>first&&index<last){
              inside=false;gap++;longestGap=Math.max(longestGap,gap);
            }
          }
          return {
            count:geometry.count,span:geometry.span,islands,longestGap,
            pattern:active.map(value=>value?'#':'.').join(''),
          };
        }""", module_url)
        browser.close()

    assert metrics["count"] <= 64, metrics
    assert metrics["span"] <= 625, metrics
    assert metrics["islands"] <= 4, metrics["pattern"]
    assert metrics["longestGap"] <= 5, metrics["pattern"]


def test_player_identity_has_breathing_room_and_visualizer_keeps_independent_frequency_detail():
    foundation = (STATIC / "design-system.css").read_text(encoding="utf-8")
    visualizer = (STATIC / "playlist-visualizer.js").read_text(encoding="utf-8")
    css = (STATIC / "playlist-now-playing.css").read_text(encoding="utf-8")

    player = _rule(foundation, "body[data-view=playlists] .playlist-player")
    assert player["padding"] == "0 22px 4px calc(var(--app-rail-width) + 40px)"
    assert _rule(foundation, "body[data-view=playlists] .playlist-now").get("padding-left", "0") == "0"
    assert "width:min(680px,48vw);height:64px" in css
    assert "frequencyData" in visualizer
    assert "edgeWindow=Math.min(1,position/.16,(1-position)/.16)" in visualizer
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
    assert _rule(css, ".now-playing-open .playlist-mode-control")["display"] == "flex"
    assert _rule(css, ".now-playing-open .playlist-player-queue")["display"] == "none"


def test_immersive_icons_keep_their_semantic_contrast_across_app_themes():
    product = (STATIC / "product.css").read_text(encoding="utf-8")
    components = (STATIC / "ui-components.css").read_text(encoding="utf-8")
    immersive = (STATIC / "playlist-now-playing.css").read_text(encoding="utf-8")
    document = f"""
    <style>
      :root{{--app-text:rgb(31,32,33);--immersive-muted:rgb(170,180,190);
        --immersive-text:rgb(240,250,255);--immersive-control-text:rgb(8,18,24);
        --immersive-accent:rgb(20,210,160);--playlist-muted:var(--icon-default)}}
      {product}
      {immersive}
      {components}
    </style>
    <body data-view="playlists" class="now-playing-open">
      <div class="now-playing">
        <button class="now-playing-close"><svg id="collapse" class="ui-icon"></svg></button>
      </div>
      <div class="playlist-player">
        <div class="playlist-now">
          <button class="player-detail">
            <span class="playlist-artwork-expand"><svg id="expand" class="ui-icon"></svg></span>
          </button>
          <div class="playlist-now-info">
            <span>artist</span>
            <button id="playerLiked" class="playlist-heart" aria-pressed="false">♡</button>
          </div>
        </div>
        <div class="playlist-player-buttons">
          <button><svg id="transport" class="ui-icon"></svg></button>
          <button class="playlist-player-toggle"><svg id="toggle" class="ui-icon"></svg></button>
        </div>
      </div>
    </body>
    """

    prepare_playwright_environment(ROOT)
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(ROOT / ".playwright"))
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.set_content(document)

        for ordinary_theme_icon in (
            "rgb(20, 20, 20)",
            "rgb(105, 75, 45)",
            "rgb(225, 230, 235)",
        ):
            page.evaluate(
                "color => document.documentElement.style.setProperty('--icon-default', color)",
                ordinary_theme_icon,
            )
            colors = page.locator("#collapse,#expand,#transport,#toggle,#playerLiked").evaluate_all(
                "controls => Object.fromEntries(controls.map(control => [control.id, getComputedStyle(control).color]))"
            )
            assert colors == {
                "collapse": "rgb(170, 180, 190)",
                "expand": "rgb(255, 255, 255)",
                "transport": "rgb(240, 250, 255)",
                "toggle": "rgb(8, 18, 24)",
                "playerLiked": "rgb(240, 250, 255)",
            }

        browser.close()


def test_compact_player_artwork_arrow_keeps_contrast_in_every_theme():
    tokens = (STATIC / "theme-tokens.css").read_text(encoding="utf-8")
    immersive = (STATIC / "playlist-now-playing.css").read_text(encoding="utf-8")
    components = (STATIC / "ui-components.css").read_text(encoding="utf-8")
    document = f"""
    <style>{tokens}{immersive}{components}</style>
    <body data-view="playlists">
      <button class="player-detail">
        <span class="playlist-artwork-expand">
          <svg id="expand" class="ui-icon"></svg>
        </span>
      </button>
    </body>
    """

    prepare_playwright_environment(ROOT)
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(ROOT / ".playwright"))
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.set_content(document)

        for theme in (None, "warm", "night"):
            page.evaluate(
                "theme => theme ? document.documentElement.dataset.appearance = theme : "
                "delete document.documentElement.dataset.appearance",
                theme,
            )
            style = page.locator("#expand").evaluate(
                "icon => ({color:getComputedStyle(icon).color,filter:getComputedStyle(icon).filter})"
            )
            assert style["color"] == "rgb(255, 255, 255)"
            assert style["filter"] != "none"

        browser.close()


def test_playlist_headers_align_with_the_visible_track_values():
    product = (STATIC / "product.css").read_text(encoding="utf-8")
    document = f"""
    <style>{product}</style>
    <body data-view="playlists">
      <div class="playlist-track-head">
        <span></span><span id="song-head">歌曲</span><span id="artist-head">歌手</span>
        <span id="album-head">专辑</span><span id="duration-head">时长</span><span></span>
      </div>
      <div class="playlist-track">
        <span class="playlist-track-number">1</span>
        <span class="playlist-track-identity">
          <button class="playlist-heart">♡</button><strong id="song-value">测试歌曲</strong>
        </span>
        <span id="artist-value" class="playlist-track-artist">测试歌手</span>
        <span id="album-value" class="playlist-track-album">测试专辑</span>
        <span id="duration-value" class="playlist-track-duration">4:28</span>
        <span class="playlist-track-actions"></span>
      </div>
    </body>
    """

    prepare_playwright_environment(ROOT)
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(ROOT / ".playwright"))
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1600, "height": 900})
        page.set_content(document)
        offsets = page.evaluate(
            """() => {
              const textX = selector => {
                const range = document.createRange();
                range.selectNodeContents(document.querySelector(selector));
                return range.getBoundingClientRect().x;
              };
              return Object.fromEntries(
                ['song', 'artist', 'album', 'duration'].map(column => [
                  column,
                  textX(`#${column}-head`) - textX(`#${column}-value`),
                ])
              );
            }"""
        )
        assert offsets == {"song": 0, "artist": 0, "album": 0, "duration": 0}
        browser.close()


def test_playlist_headers_stay_aligned_when_scrollbar_reserves_width():
    page_markup = (STATIC / "playlists.html").read_text(encoding="utf-8")
    product = (STATIC / "product.css").read_text(encoding="utf-8")

    prepare_playwright_environment(ROOT)
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(ROOT / ".playwright"))
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1400, "height": 800})
        page.set_content(page_markup)
        page.add_style_tag(content=product)
        page.add_style_tag(
            content="""
              #workspace{display:block!important}
              #playlistView{display:grid!important;width:1120px;height:420px}
              .playlist-track-scroll{overflow-y:scroll!important;scrollbar-gutter:stable}
            """
        )
        page.evaluate(
            """() => {
              const tracks = document.querySelector('#playlistTracks');
              tracks.replaceChildren();
              for (let index = 0; index < 30; index += 1) {
                const row = document.createElement('div');
                row.className = 'playlist-track';
                row.innerHTML = `<span class="playlist-track-number">${index + 1}</span>
                  <span class="playlist-track-identity"><button class="playlist-heart">♡</button><strong>测试歌曲</strong></span>
                  <span class="playlist-track-artist">测试歌手</span>
                  <span class="playlist-track-album">测试专辑</span>
                  <span class="playlist-track-duration">4:28</span>
                  <span class="playlist-track-actions"></span>`;
                tracks.append(row);
              }
            }"""
        )
        result = page.evaluate(
            """() => {
              const textX = element => {
                const range = document.createRange();
                range.selectNodeContents(element);
                return range.getBoundingClientRect().x;
              };
              const head = document.querySelector('.playlist-track-head');
              const row = document.querySelector('.playlist-track');
              const scroll = document.querySelector('.playlist-track-scroll');
              const headColumns = [head.children[1], head.children[2], head.children[3], head.children[4]];
              const rowColumns = [row.children[1].querySelector('strong'), row.children[2], row.children[3], row.children[4]];
              return {
                scrollbarWidth: scroll.offsetWidth - scroll.clientWidth,
                offsets: headColumns.map((column, index) => textX(column) - textX(rowColumns[index])),
              };
            }"""
        )
        assert result["scrollbarWidth"] > 0
        assert result["offsets"] == [0, 0, 0, 0]
        browser.close()


def test_playlist_and_player_chrome_stay_lightweight():
    product = product_css()
    details = (STATIC / "playlist-now-playing.css").read_text(encoding="utf-8")
    foundation = (STATIC / "design-system.css").read_text(encoding="utf-8")
    tokens = (STATIC / "theme-tokens.css").read_text(encoding="utf-8")
    components = (STATIC / "ui-components.css").read_text(encoding="utf-8")

    assert _rule(product, ".playlist-track")["border-bottom"] == "0"
    track_head = _rule(product, ".playlist-track-head")
    assert track_head["position"] == "sticky"
    assert track_head["top"] == "0"
    assert track_head["background"] == "var(--playlist-surface)"
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


def test_player_transport_controls_match_full_size_music_app_proportions():
    shipped_css = page_css("playlists")
    document = f"""
    <style>{shipped_css}</style>
    <body data-view="playlists">
      <footer class="playlist-player">
        <div class="playlist-player-center">
          <div class="playlist-mode-control"><button><svg id="mode" class="ui-icon player-mode-icon"></svg></button></div>
          <div class="playlist-player-buttons">
            <button id="previous"><svg id="previousIcon" class="ui-icon player-icon"></svg></button>
            <button id="toggle" class="playlist-player-toggle"><svg id="toggleIcon" class="ui-icon player-icon"></svg></button>
            <button id="next"><svg id="nextIcon" class="ui-icon player-icon"></svg></button>
          </div>
          <div class="playlist-volume-control"><button id="mute"><svg id="muteIcon" class="ui-icon player-icon"></svg></button></div>
        </div>
      </footer>
    </body>
    """

    prepare_playwright_environment(ROOT)
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(ROOT / ".playwright"))
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 720})
        page.set_content(document)

        def sizes():
            return page.locator(
                "#mode,#previous,#previousIcon,#toggle,#toggleIcon,#next,#nextIcon,#mute,#muteIcon"
            ).evaluate_all(
                "nodes => Object.fromEntries(nodes.map(node => [node.id, "
                "[getComputedStyle(node).width, getComputedStyle(node).height]]))"
            )

        expected = {
            "mode": ["22px", "22px"],
            "previous": ["28px", "36px"],
            "previousIcon": ["22px", "22px"],
            "toggle": ["34px", "34px"],
            "toggleIcon": ["18px", "18px"],
            "next": ["28px", "36px"],
            "nextIcon": ["22px", "22px"],
            "mute": ["28px", "36px"],
            "muteIcon": ["20px", "20px"],
        }
        assert sizes() == expected
        gaps = page.locator(".playlist-player-center").evaluate(
            "node => { const mode=node.querySelector('.playlist-mode-control').getBoundingClientRect();"
            "const buttons=node.querySelector('.playlist-player-buttons').getBoundingClientRect();"
            "const volume=node.querySelector('.playlist-volume-control').getBoundingClientRect();"
            "return [buttons.left-mode.right,volume.left-buttons.right]; }"
        )
        assert gaps == [6, 6]
        button_gap = page.locator(".playlist-player-buttons").evaluate(
            "node => Number.parseFloat(getComputedStyle(node).gap)"
        )
        assert button_gap == 10
        page.locator("html").evaluate("node => node.dataset.appearance='night'")
        assert page.locator("#toggleIcon").evaluate(
            "node => getComputedStyle(node).color"
        ) == "rgb(13, 42, 32)"
        page.locator("body").evaluate("node => node.classList.add('now-playing-open')")
        assert sizes() == expected
        page.set_viewport_size({"width": 700, "height": 760})
        assert sizes() == expected
        browser.close()


def test_volume_panel_has_soft_floating_surface_and_pointer_tail():
    page_html = (STATIC / "playlists.html").read_text(encoding="utf-8")
    prepare_playwright_environment(ROOT)
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(ROOT / ".playwright"))
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 720})
        page.set_content(page_html)
        page.add_style_tag(content=page_css("playlists"))
        page.locator("#bootScreen").evaluate("node => node.hidden=true")
        page.locator("#workspace").evaluate("node => node.hidden=false")
        page.locator("#playerVolumeControl").evaluate("node => node.dataset.open='true'")

        surface = page.locator("#playerVolumePanel").evaluate(
            "node => { const style=getComputedStyle(node); return {"
            "radius:style.borderRadius,shadow:style.boxShadow,"
            "background:style.backgroundImage}; }"
        )
        assert surface["radius"] == "14px"
        assert surface["shadow"] != "none"
        assert surface["background"] != "none"
        tail = page.locator("#playerVolumePanel").evaluate(
            "node => { const style=getComputedStyle(node,'::before'); return {"
            "content:style.content,width:style.width,height:style.height,"
            "transform:style.transform}; }"
        )
        assert tail["content"] != "none"
        assert tail["width"] == "12px"
        assert tail["height"] == "12px"
        assert tail["transform"] != "none"
        browser.close()


def test_transport_hover_keeps_bare_icons_without_a_round_surface():
    page_html = (STATIC / "playlists.html").read_text(encoding="utf-8")
    product = (STATIC / "product.css").read_text(encoding="utf-8")
    details = (STATIC / "playlist-now-playing.css").read_text(encoding="utf-8")
    foundation = (STATIC / "design-system.css").read_text(encoding="utf-8")
    tokens = (STATIC / "theme-tokens.css").read_text(encoding="utf-8")
    components = (STATIC / "ui-components.css").read_text(encoding="utf-8")
    prepare_playwright_environment(ROOT)
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(ROOT / ".playwright"))
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 720})
        page.set_content(page_html)
        page.add_style_tag(content=(
            product + details + tokens + components + foundation
            + ":root{--playlist-text:rgb(255,255,255);--playlist-muted:rgb(255,255,255);"
            "--app-accent:rgb(30,210,150);--playlist-accent:rgb(30,210,150);"
            "--playlist-surface-soft:rgb(90,90,90)}"
        ))
        page.locator("#bootScreen").evaluate("node => node.hidden=true")
        page.locator("#workspace").evaluate("node => node.hidden=false")

        for selector in ("#playerMode", "#playerPrevious", "#playerNext", "#playerVolumeToggle"):
            before = page.locator(selector).evaluate(
                "node => ({background:getComputedStyle(node).backgroundColor,"
                "color:getComputedStyle(node.querySelector('.ui-icon')).color})"
            )
            page.locator(selector).hover(force=True)
            after = page.locator(selector).evaluate(
                "node => ({background:getComputedStyle(node).backgroundColor,"
                "color:getComputedStyle(node.querySelector('.ui-icon')).color})"
            )
            assert before["background"] == "rgba(0, 0, 0, 0)", selector
            assert after["background"] == before["background"], selector
            assert after["color"] == "rgb(30, 210, 150)", selector
            assert after["color"] != before["color"], selector

        browser.close()


def test_transport_svg_glyphs_fill_their_control_canvas():
    page_html = (STATIC / "playlists.html").read_text(encoding="utf-8")
    product = (STATIC / "product.css").read_text(encoding="utf-8")
    details = (STATIC / "playlist-now-playing.css").read_text(encoding="utf-8")
    prepare_playwright_environment(ROOT)
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(ROOT / ".playwright"))
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.set_content(page_html)
        page.add_style_tag(content=product + details)
        page.locator("#workspace").evaluate("node => node.hidden=false")
        glyphs = page.locator(
            "#playerPrevious .player-icon,#playerToggle .player-icon-play,#playerNext .player-icon"
        ).evaluate_all(
            "nodes => nodes.map(node => { const box=node.getBBox(); "
            "const style=getComputedStyle(node); "
            "return {width:box.width,height:box.height,fill:style.fill,stroke:style.stroke}; })"
        )
        assert glyphs == [
            {"width": 16, "height": 18, "fill": "rgb(0, 0, 0)", "stroke": "none"},
            {"width": 14, "height": 16, "fill": "rgb(0, 0, 0)", "stroke": "none"},
            {"width": 16, "height": 18, "fill": "rgb(0, 0, 0)", "stroke": "none"},
        ]
        page.locator("#playerToggle").evaluate("node => node.dataset.state='playing'")
        pause = page.locator("#playerToggle .player-icon-pause").evaluate(
            "node => { const box=node.getBBox(); const style=getComputedStyle(node); "
            "return {width:box.width,height:box.height,fill:style.fill,stroke:style.stroke}; }"
        )
        assert pause == {"width": 14, "height": 16, "fill": "rgb(0, 0, 0)", "stroke": "none"}

        mode = page.locator("#playerModeIcon").evaluate(
            "node => { const box=node.getBBox(); return {width:box.width,height:box.height}; }"
        )
        assert mode == {"width": 20, "height": 17}
        browser.close()


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
