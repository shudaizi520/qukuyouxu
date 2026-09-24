"""Verify the theme background layer in a real browser.

The check deliberately injects a high-contrast synthetic background so stacking,
pointer handling, reduced motion, and embedded single-layer behavior are visible
and deterministic.  Supply the same disposable visual-test credentials used by
the isolated verification server; never point this tool at production data.
"""

from __future__ import annotations

import io
import os

from PIL import Image
from playwright.sync_api import sync_playwright

if __package__:
    from .playwright_runtime import prepare_playwright_environment
else:
    from playwright_runtime import prepare_playwright_environment


BASE_URL = os.environ["PCH_VISUAL_BASE_URL"].rstrip("/")


def main() -> None:
    prepare_playwright_environment()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(
            viewport={"width": 800, "height": 600}, reduced_motion="reduce"
        )
        page = context.new_page()
        page.goto(BASE_URL + "/", wait_until="domcontentloaded")
        page.locator("body[data-auth-state]").wait_for()
        if page.locator("#loginForm").is_visible():
            page.locator("#loginUser").fill(os.environ["PCH_VISUAL_USERNAME"])
            page.locator("#loginPassword").fill(os.environ["PCH_VISUAL_PASSWORD"])
            page.locator("#loginForm button[type=submit]").click()
            page.locator('body[data-auth-state="ready"]').wait_for(timeout=30_000)

        page.goto(BASE_URL + "/appearance", wait_until="domcontentloaded")
        page.locator('body[data-auth-state="ready"]').wait_for(timeout=30_000)
        page.wait_for_load_state("networkidle")
        assert page.locator(".app-theme-background").count() == 1
        page.evaluate(
            """() => {
              const root = document.documentElement.style;
              root.setProperty('--app-main', 'transparent');
              root.setProperty('--background-color', 'rgb(0, 0, 255)');
              root.setProperty(
                '--background-image',
                'linear-gradient(rgb(255, 0, 0), rgb(255, 0, 0))'
              );
              root.setProperty('--background-overlay', 'transparent');

              const pointerProbe = document.createElement('button');
              pointerProbe.id = 'pointerProbe';
              Object.assign(pointerProbe.style, {
                position: 'fixed', left: '0', bottom: '0', width: '24px',
                height: '24px', zIndex: '1'
              });
              document.body.append(pointerProbe);

              const stackProbe = document.createElement('div');
              stackProbe.id = 'stackProbe';
              Object.assign(stackProbe.style, {
                position: 'fixed', right: '0', bottom: '0', width: '24px',
                height: '24px', background: 'rgb(0, 255, 0)'
              });
              document.body.append(stackProbe);
            }"""
        )

        screenshot = Image.open(io.BytesIO(page.screenshot())).convert("RGB")
        background_pixel = screenshot.getpixel((760, 560))
        content_pixel = screenshot.getpixel((770, 590))
        assert background_pixel[0] > 240 and max(background_pixel[1:]) < 20
        assert content_pixel[1] > 240 and max(
            content_pixel[0], content_pixel[2]
        ) < 20
        assert page.evaluate("document.elementFromPoint(12, 588).id") == "pointerProbe"
        assert (
            page.locator(".app-theme-background").evaluate(
                "element => getComputedStyle(element).pointerEvents"
            )
            == "none"
        )
        assert page.evaluate(
            "matchMedia('(prefers-reduced-motion: reduce)').matches"
        )
        motion_styles = page.locator(".app-theme-background").evaluate(
            """element => {
              element.style.animation = 'background-contract-probe 1s infinite';
              element.style.transition = 'opacity 1s';
              return ['', '::before', '::after'].map(pseudo => {
                const style = getComputedStyle(element, pseudo || null);
                return {
                  animationName: style.animationName,
                  transitionDuration: style.transitionDuration
                };
              });
            }"""
        )
        assert all(style["animationName"] == "none" for style in motion_styles)
        assert all(
            set(style["transitionDuration"].split(", ")) <= {"0s"}
            for style in motion_styles
        )

        page.goto(BASE_URL + "/", wait_until="domcontentloaded")
        page.locator('body[data-auth-state="ready"]').wait_for(timeout=30_000)
        page.locator("#openSettings").click()
        frame = page.frame_locator("#playlistToolFrame")
        frame.locator('body[data-auth-state="ready"]').wait_for(timeout=30_000)
        assert page.locator(".app-theme-background").count() == 1
        assert frame.locator(".app-theme-background").count() == 0

        print("background-visibility=PASS")
        print("background-behind-content=PASS")
        print("background-pointer-pass-through=PASS")
        print("background-reduced-motion=PASS")
        print("background-embedded-single-layer=PASS")
        context.close()
        browser.close()


if __name__ == "__main__":
    main()
