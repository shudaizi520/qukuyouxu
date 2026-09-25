import re
import os
from pathlib import Path

import ui_css
from playwright.sync_api import sync_playwright
from tools.playwright_runtime import prepare_playwright_environment


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src/helper/static"
PAGES = {
    "settings": ("settings-page.css", ("body[data-view=settings]", ".settings-", ".profile-", ".webhook-")),
    "daily": ("daily-page.css", ("body[data-view=daily]", ".daily-")),
    "mixes": ("mixes-page.css", ("body[data-view=mixes]", ".mix-", ".smart-")),
    "external": ("external-page.css", ("body[data-view=external]", ".external-")),
}


def _split_selectors(header):
    selectors = []
    start = 0
    depth = 0
    quote = None
    for index, character in enumerate(header):
        if quote:
            if character == "\\":
                continue
            if character == quote:
                quote = None
        elif character in "\"'":
            quote = character
        elif character in "([":
            depth += 1
        elif character in ")]":
            depth -= 1
        elif character == "," and depth == 0:
            selectors.append(header[start:index])
            start = index + 1
    selectors.append(header[start:])
    return [" ".join(value.strip().split()) for value in selectors]


def _rules(css, context=()):
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    cursor = 0
    while cursor < len(css):
        opening = css.find("{", cursor)
        if opening < 0:
            return
        header = css[cursor:opening].strip()
        depth = 1
        quote = None
        index = opening + 1
        while index < len(css) and depth:
            character = css[index]
            if quote:
                if character == "\\":
                    index += 2
                    continue
                if character == quote:
                    quote = None
            elif character in "\"'":
                quote = character
            elif character == "{":
                depth += 1
            elif character == "}":
                depth -= 1
            index += 1
        body = css[opening + 1:index - 1]
        if header.startswith("@"):
            yield from _rules(body, context + (" ".join(header.split()),))
        elif header:
            yield context, _split_selectors(header), body
        cursor = index


def test_shared_product_sheet_has_no_page_owned_selectors():
    selectors = [
        selector
        for _context, names, _body in _rules((STATIC / "product.css").read_text(encoding="utf-8"))
        for selector in names
    ]
    for _page, (_sheet, prefixes) in PAGES.items():
        assert not [selector for selector in selectors if selector.startswith(prefixes)]


def test_each_page_loads_one_owned_sheet_in_the_shared_bundle_order():
    for page, (sheet, _prefixes) in PAGES.items():
        html = (STATIC / f"{page}.html").read_text(encoding="utf-8")
        target = f'/static/{sheet}?v=app'
        assert html.count(target) == 1
        positions = [
            html.index('/static/appearance.js?v=app'),
            html.index('/static/product.css?v=app'),
            html.index('/static/product-refinements.css?v=app'),
            html.index('/static/theme-tokens.css?v=app'),
            html.index('/static/theme-background.css?v=app'),
            html.index('/static/ui-components.css?v=app'),
            html.index('/static/design-system.css?v=app'),
            html.index(target),
        ]
        assert positions == sorted(positions)
        for later in ('/static/management-shell.css?v=app', '/static/external-workspace.css?v=app'):
            if later in html:
                assert html.index(target) < html.index(later)
        assert ui_css.page_css(page).count((STATIC / sheet).read_text(encoding="utf-8")) == 1


def test_page_sheets_have_no_literal_black_or_white_icon_colors():
    literal = re.compile(r"(?:color|stroke|fill)\s*:\s*(?:black|white|#000(?:000)?|#fff(?:fff)?)\b", re.I)
    for sheet, _prefixes in PAGES.values():
        for _context, selectors, body in _rules((STATIC / sheet).read_text(encoding="utf-8")):
            icon_rule = any(re.search(r"(?:icon|svg|::?before|::?after)", selector, re.I) for selector in selectors)
            assert not (icon_rule and literal.search(body)), selectors


def test_page_sheets_do_not_repeat_exact_selectors_in_one_media_context():
    for sheet, _prefixes in PAGES.values():
        seen = set()
        duplicates = []
        for context, selectors, _body in _rules((STATIC / sheet).read_text(encoding="utf-8")):
            for selector in selectors:
                key = (context, selector)
                if key in seen:
                    duplicates.append(key)
                seen.add(key)
        assert not duplicates, f"{sheet}: {duplicates[:8]}"


def test_page_bundles_keep_the_accepted_desktop_geometry_and_theme_contrast():
    # Named computed-style baselines captured from the accepted 2.0.8 pages.
    themes = {
        "light": ("rgb(255, 255, 255)", "rgb(32, 37, 40)"),
        "warm": ("rgb(251, 248, 241)", "rgb(41, 39, 36)"),
        "night": ("rgb(35, 40, 41)", "rgb(237, 237, 237)"),
    }
    geometry = {
        "settings": (".settings-workspace", 820, 0),
        "daily": (".daily-main-card", 1132, 146.5),
        "mixes": (".mix-list", 676, 144),
        "external": (".external-import-card", 680, 0),
    }
    prepare_playwright_environment(ROOT)
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(ROOT / ".playwright"))
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        for name, (selector, width, x) in geometry.items():
            html = (STATIC / f"{name}.html").read_text(encoding="utf-8")
            html = re.sub(r"<script\b.*?</script>|<link\b[^>]*>", "", html, flags=re.I | re.S)
            page.set_content(html)
            page.add_style_tag(content=ui_css.page_css(name))
            page.evaluate("""() => {
              const workspace = document.getElementById('workspace');
              if (workspace) workspace.hidden = false;
            }""")
            for theme, (background, color) in themes.items():
                page.evaluate("value => document.documentElement.dataset.appearance = value", theme)
                body = page.locator("body").evaluate(
                    "node => ({background:getComputedStyle(node).backgroundColor,color:getComputedStyle(node).color})"
                )
                rect = page.locator(selector).first.evaluate(
                    "node => ({width:node.getBoundingClientRect().width,x:node.getBoundingClientRect().x})"
                )
                assert body == {"background": background, "color": color}
                assert rect == {"width": width, "x": x}

        page.set_content(re.sub(
            r"<script\b.*?</script>|<link\b[^>]*>", "",
            (STATIC / "settings.html").read_text(encoding="utf-8"), flags=re.I | re.S,
        ))
        page.add_style_tag(content=ui_css.page_css("settings"))
        page.locator("#workspace").evaluate("node => node.hidden = false")
        settings_row = page.locator(".settings-layout-row").first.evaluate(
            "node => ({columns:getComputedStyle(node).gridTemplateColumns,gap:getComputedStyle(node).gap,padding:getComputedStyle(node).padding})"
        )
        assert settings_row == {"columns": "120px 676px", "gap": "24px", "padding": "18px 0px"}

        page.set_content(re.sub(
            r"<script\b.*?</script>|<link\b[^>]*>", "",
            (STATIC / "daily.html").read_text(encoding="utf-8"), flags=re.I | re.S,
        ))
        page.add_style_tag(content=ui_css.page_css("daily"))
        page.locator("#workspace").evaluate("node => node.hidden = false")
        assert page.locator(".daily-controls").evaluate("node => getComputedStyle(node).padding") == "18px 22px 8px"

        page.set_content(re.sub(
            r"<script\b.*?</script>|<link\b[^>]*>", "",
            (STATIC / "mixes.html").read_text(encoding="utf-8"), flags=re.I | re.S,
        ))
        page.add_style_tag(content=ui_css.page_css("mixes"))
        page.locator("#workspace").evaluate("node => node.hidden = false")
        mix_row = page.locator(".mix-row").first.evaluate(
            "node => ({columns:getComputedStyle(node).gridTemplateColumns,padding:getComputedStyle(node).padding,radius:getComputedStyle(node).borderRadius})"
        )
        assert mix_row == {
            "columns": "minmax(240px, 1fr) auto auto",
            "padding": "12px 8px",
            "radius": "5px",
        }
        browser.close()
