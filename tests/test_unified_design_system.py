from pathlib import Path
from html.parser import HTMLParser
import re


STATIC = Path(__file__).resolve().parents[1] / "src/helper/static"


def _text(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def _rule(css: str, selector: str) -> dict[str, str]:
    declarations: dict[str, str] = {}
    cleaned = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    for selectors, body in re.findall(r"([^{}]+)\{([^{}]*)\}", cleaned):
        if selector not in [item.strip() for item in selectors.split(",")]:
            continue
        for part in body.split(";"):
            if ":" in part:
                key, value = part.strip().split(":", 1)
                declarations[key] = value
    return declarations


class _Node:
    def __init__(self, tag="root", attrs=None, parent=None):
        self.tag = tag
        self.attrs = dict(attrs or [])
        self.parent = parent
        self.children = []


class _Tree(HTMLParser):
    def __init__(self):
        super().__init__()
        self.root = _Node()
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = _Node(tag, attrs, self.stack[-1])
        self.stack[-1].children.append(node)
        if tag not in {"meta", "link", "input", "img", "br", "hr"}:
            self.stack.append(node)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def all(self):
        pending = list(self.root.children)
        while pending:
            node = pending.pop(0)
            yield node
            pending[0:0] = node.children

    def by_id(self, value):
        return next(node for node in self.all() if node.attrs.get("id") == value)


def test_every_product_page_loads_the_canonical_design_system_last():
    pages = sorted(STATIC.glob("*.html"))
    themed = [page for page in pages if "product.css" in page.read_text(encoding="utf-8")]
    assert themed
    for page in themed:
        source = page.read_text(encoding="utf-8")
        stylesheets = re.findall(r'<link[^>]+href="([^"]+\.css[^\"]*)"', source)
        assert stylesheets, page.name
        assert "design-system.css" in stylesheets[-1], page.name


def test_palette_and_typography_have_one_canonical_owner():
    tokens = _text("theme-tokens.css")
    foundation = _text("design-system.css")
    product = _text("product.css")

    assert '--app-rail:' in tokens
    assert '--app-main:' in tokens
    assert '--app-control:' in tokens
    assert 'html[data-appearance="warm"]' in tokens
    assert 'html[data-appearance="night"]' in tokens
    assert '--app-rail:' not in foundation
    assert 'html[data-appearance="warm"]' not in foundation
    assert 'html[data-appearance="night"]' not in foundation
    assert '"Microsoft YaHei UI"' in foundation
    assert "Appearance palettes" not in product
    assert "--playlist-player-bg" not in product + foundation


def test_playlist_shell_is_two_regions_and_search_belongs_to_the_main_region():
    css = _text("design-system.css")
    search = _rule(css, "body[data-view=playlists] .playlist-global-search")

    assert "body[data-view=playlists] .topbar{\n display:grid;" in css
    assert "grid-template-columns:var(--app-rail-width) minmax(280px,1fr) auto auto" in css
    assert css.count("background:linear-gradient(to right,var(--app-rail)") == 2
    assert search.get("grid-column") == "2"


def test_playlist_page_uses_a_non_scrolling_root_shell():
    page = _text("playlists.html")
    foundation = _text("design-system.css")
    product = _text("product.css")
    details = _text("playlist-now-playing.css")
    root = _rule(foundation, "html.playlist-shell")

    assert '<html class="playlist-shell"' in page
    assert root.get("width") == "100%"
    assert root.get("height") == "100%"
    assert root.get("overflow") == "clip"
    assert root.get("scrollbar-gutter") == "auto"
    assert _rule(foundation, "html.playlist-shell body[data-view=playlists]").get("overflow") == "clip"
    assert _rule(foundation, "html.playlist-shell body[data-view=playlists] #workspace").get("overflow") == "clip"
    assert _rule(foundation, "html.playlist-shell body[data-view=playlists] .now-playing").get("overflow") == "clip"
    assert _rule(product, ".playlist-track-scroll").get("overflow") == "auto"
    assert _rule(details, ".now-playing-lyrics").get("overflow") == "auto"


def test_progress_visual_is_a_two_pixel_line_and_the_drag_target_cannot_paint_a_band():
    css = _text("design-system.css")
    rail = _rule(css, "body[data-view=playlists] .playlist-progress-rail")
    visible_line = _rule(css, "body[data-view=playlists] .playlist-progress-rail::before")
    slider = _rule(css, "body[data-view=playlists] .playlist-progress-rail input[type=range]")

    assert rail.get("top") == "-1px"
    assert rail.get("height") == "2px"
    assert rail.get("background") == "transparent"
    assert visible_line.get("height") == "2px"
    assert "linear-gradient(to right" in visible_line.get("background", "")
    assert slider.get("position") == "absolute"
    assert slider.get("top") == "-9px"
    assert slider.get("height") == "20px"
    assert slider.get("opacity") == "0"
    assert slider.get("background") == "transparent"
    assert slider.get("border") == "0"
    assert slider.get("appearance") == "none"


def test_version_lives_only_in_settings_and_sidebar_has_no_current_user_label():
    playlists = _text("playlists.html")
    settings = _text("settings.html")
    pages = [page for page in STATIC.glob("*.html") if "product.css" in page.read_text(encoding="utf-8")]

    assert "当前用户" not in playlists
    assert "settings-about" in settings
    assert 'id="version"' in settings
    for page in pages:
        if page.name == "settings.html":
            continue
        assert 'id="version"' not in page.read_text(encoding="utf-8"), page.name


def test_section_title_is_the_only_settings_action_and_has_no_extra_button():
    tree = _Tree()
    tree.feed(_text("playlists.html"))
    title = tree.by_id("playlistSectionTitle")

    assert title.tag == "button"
    assert "playlist-section-title-link" in title.attrs.get("class", "").split()
    assert not any(node.attrs.get("id") == "playlistSectionSettings" for node in tree.all())


def test_generated_appearance_grid_lives_only_on_the_dedicated_appearance_page():
    tree = _Tree()
    tree.feed(_text("appearance.html"))
    choices = [node for node in tree.all() if "data-appearance-choice" in node.attrs]
    grids = [node for node in tree.all() if "data-appearance-grid" in node.attrs]

    assert "data-appearance-choice" not in _text("settings.html")
    assert choices == []
    assert len(grids) == 1


def test_direct_menu_button_is_neutral_instead_of_using_a_saturated_accent_fill():
    css = _text("product.css")
    trigger = _rule(css, ".topbar-settings-trigger")
    hover = _rule(css, ".topbar>.topbar-settings-trigger:hover")

    assert trigger.get("background") == "var(--theme-surface)"
    assert trigger.get("color") == "var(--theme-text)"
    assert hover.get("background") == "var(--theme-soft)"
    assert hover.get("color") == "var(--theme-text)"
