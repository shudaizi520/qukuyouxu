import os
import re
from pathlib import Path

from playwright.sync_api import sync_playwright

import ui_css
from tools.playwright_runtime import prepare_playwright_environment


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src" / "helper" / "static"


def _prepare_browser() -> None:
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(ROOT / ".playwright"))
    prepare_playwright_environment(ROOT)


def _render(page, page_name: str) -> None:
    html = (STATIC / f"{page_name}.html").read_text(encoding="utf-8")
    html = re.sub(r"<script\b.*?</script>|<link\b[^>]*>", "", html, flags=re.I | re.S)
    page.set_content(html)
    page.add_style_tag(content=ui_css.page_css(page_name))
    page.add_script_tag(path=str(STATIC / "management-shell.js"))
    page.locator("#workspace").evaluate("node => node.hidden = false")


def _text_rect(locator) -> dict[str, float]:
    return locator.evaluate(
        """node => {
          const range = document.createRange();
          range.selectNodeContents(node);
          const rect = range.getBoundingClientRect();
          return {x: rect.x, y: rect.y, width: rect.width, height: rect.height};
        }"""
    )


def test_status_disclosure_text_is_aligned_and_not_clipped_at_the_left_edge():
    _prepare_browser()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1200, "height": 900})
        _render(page, "status")

        label = _text_rect(
            page.locator("#statusDetails").locator("xpath=../preceding-sibling::div/h2")
        )
        summary = _text_rect(page.locator("#statusDetails > summary"))
        details = page.locator("#statusDetails").evaluate(
            "node => ({x: node.getBoundingClientRect().x, overflow: getComputedStyle(node).overflow})"
        )

        assert abs(label["y"] - summary["y"]) < 0.5
        assert summary["x"] >= details["x"] + 2
        assert details["overflow"] == "visible"
        browser.close()


def test_custom_mix_disclosure_uses_the_same_top_aligned_row_geometry():
    _prepare_browser()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1200, "height": 900})
        _render(page, "mixes")

        section = page.locator('section[aria-label="自定义精选"]')
        label = _text_rect(section.locator(".management-preference-label h2"))
        prompt = _text_rect(section.locator(".custom-mix > summary span:first-child"))
        toggle = _text_rect(section.locator(".custom-mix-toggle"))

        assert abs(label["y"] - prompt["y"]) < 0.5
        assert abs(prompt["y"] - toggle["y"]) < 0.5
        assert prompt["x"] >= section.locator(".custom-mix").bounding_box()["x"] + 2
        browser.close()


def test_inline_disclosures_stack_to_one_full_width_column_on_mobile():
    _prepare_browser()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 390, "height": 844})

        for page_name, selector in (
            ("status", "#statusDetails"),
            ("home", ".compact-diagnostics details"),
        ):
            _render(page, page_name)
            details = page.locator(selector)
            section = details.locator("xpath=../..")
            label = section.locator(":scope > .management-preference-label")
            content = section.locator(":scope > .management-preference-content")

            layout = section.evaluate(
                "node => ({columns: getComputedStyle(node).gridTemplateColumns, gap: getComputedStyle(node).gap})"
            )
            section_box = section.bounding_box()
            label_box = label.bounding_box()
            content_box = content.bounding_box()

            assert " " not in layout["columns"]
            assert layout["gap"] == "8px"
            assert abs(label_box["x"] - content_box["x"]) < 0.5
            assert content_box["y"] >= label_box["y"] + label_box["height"] + 7.5
            assert abs(content_box["width"] - section_box["width"]) < 0.5

        browser.close()


def test_mobile_status_history_wraps_long_unbroken_event_messages():
    _prepare_browser()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 390, "height": 844})
        _render(page, "status")
        page.locator("#eventHistory").evaluate("node => node.open = true")
        page.locator("#events").evaluate(
            """(node) => {
              const row = document.createElement('div');
              row.className = 'event-row';
              const dot = document.createElement('span');
              dot.className = 'event-dot error';
              const text = document.createElement('div');
              const message = document.createElement('strong');
              message.textContent = 'x'.repeat(300);
              text.append(message);
              row.append(dot, text);
              node.append(row);
            }"""
        )

        widths = page.evaluate(
            """() => ({
              documentClient: document.documentElement.clientWidth,
              documentScroll: document.documentElement.scrollWidth,
              messageRight: document.querySelector('#events strong').getBoundingClientRect().right,
              messageWrap: getComputedStyle(document.querySelector('#events strong')).overflowWrap,
            })"""
        )

        assert widths["documentScroll"] <= widths["documentClient"]
        assert widths["messageRight"] <= widths["documentClient"]
        assert widths["messageWrap"] == "anywhere"
        browser.close()
