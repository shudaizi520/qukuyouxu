"""Capture and compare the management-page theme screenshot matrix."""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin

from PIL import Image, ImageChops
from playwright.sync_api import Page, sync_playwright

if __package__:
    from .playwright_runtime import prepare_playwright_environment
else:
    from playwright_runtime import prepare_playwright_environment

SCREENSHOT_STYLE = "*{animation:none!important;transition:none!important}"
MASK_SELECTORS = (
    "#version",
    "#progressText",
    "#progressPercent",
    "#progressDetail",
    "#lastRun",
    "#behaviorStatus",
    "#dailyDiagnostics",
    "#taskBadge",
    "#taskMessage",
    "#dailyContextState",
    "#plexLoginStatus",
    "#webhookUrl",
    "#webhookMessage",
    "#webhookLast",
    "#notice",
    "#playlistNotice",
    "[data-timestamp]",
    "[data-live-status]",
    ".pch-inline-feedback",
    ".pch-toast",
    "time",
)


@dataclass(frozen=True)
class VisualCase:
    page: str
    route: str
    theme: str
    viewport: tuple[int, int]


def matrix_cases() -> list[VisualCase]:
    routes = {
        "settings": "/settings",
        "external": "/external",
        "mixes": "/mixes",
        "library": "/library",
        "status": "/status",
        "appearance": "/appearance",
    }
    return [
        VisualCase(page, route, theme, viewport)
        for page, route in routes.items()
        for theme in ("light", "warm", "night")
        for viewport in ((1366, 768), (1920, 1080))
    ]


def compare_images(
    baseline: Path,
    current: Path,
    diff: Path,
    threshold: int = 12,
) -> float:
    before = Image.open(baseline).convert("RGB")
    after = Image.open(current).convert("RGB")
    if before.size != after.size:
        raise ValueError(f"image sizes differ: {before.size} != {after.size}")
    delta = ImageChops.difference(before, after)
    changed = sum(
        1 for pixel in delta.get_flattened_data() if max(pixel) > threshold
    )
    diff.parent.mkdir(parents=True, exist_ok=True)
    delta.save(diff)
    return changed / (before.width * before.height)


def _url(base_url: str, route: str) -> str:
    return urljoin(base_url.rstrip("/") + "/", route.lstrip("/"))


def _wait_for_auth_state(page: Page) -> None:
    page.locator("body[data-auth-state]").wait_for(state="attached")
    page.locator(
        'body[data-auth-state]:not([data-auth-state="checking"])'
    ).wait_for(state="attached", timeout=30_000)


def _ensure_authenticated(page: Page, base_url: str) -> None:
    page.goto(_url(base_url, "/"), wait_until="domcontentloaded")
    _wait_for_auth_state(page)
    form = page.locator("#loginForm")
    if not form.is_visible():
        return
    username = os.environ.get("PCH_VISUAL_USERNAME")
    password = os.environ.get("PCH_VISUAL_PASSWORD")
    if not username or not password:
        raise RuntimeError(
            "PCH_VISUAL_USERNAME and PCH_VISUAL_PASSWORD are required when login is visible"
        )
    page.locator("#loginUser").fill(username)
    page.locator("#loginPassword").fill(password)
    form.locator("button[type=submit]").click()
    page.locator('body[data-auth-state="ready"]').wait_for(
        state="attached", timeout=30_000
    )


def _prepare_screenshot(page: Page) -> None:
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(150)


def _mask_locators(page: Page):
    return [page.locator(selector) for selector in MASK_SELECTORS]


def _capture_case(
    page: Page,
    base_url: str,
    root: Path,
    mode: str,
    case: VisualCase,
) -> Path:
    width, height = case.viewport
    page.set_viewport_size({"width": width, "height": height})
    page.evaluate(
        "theme => localStorage.setItem('pch-appearance-theme', theme)",
        case.theme,
    )
    page.goto(_url(base_url, case.route), wait_until="domcontentloaded")
    _wait_for_auth_state(page)
    _prepare_screenshot(page)
    target = root / mode / f"{width}x{height}" / case.theme / f"{case.page}.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(
        path=str(target),
        animations="disabled",
        style=SCREENSHOT_STYLE,
        mask=_mask_locators(page),
        mask_color="#808080",
    )
    return target


def _capture_embedded_settings(
    page: Page,
    base_url: str,
    root: Path,
    mode: str,
) -> Path:
    width, height = (1920, 1080)
    page.set_viewport_size({"width": width, "height": height})
    page.evaluate(
        "theme => localStorage.setItem('pch-appearance-theme', theme)",
        "light",
    )
    page.goto(_url(base_url, "/"), wait_until="domcontentloaded")
    _wait_for_auth_state(page)
    page.locator("#openSettings").click()
    frame = page.frame_locator("#playlistToolFrame")
    frame.locator("body[data-auth-state=ready]").wait_for(timeout=30_000)
    _prepare_screenshot(page)
    target = root / mode / f"{width}x{height}" / "light" / "embedded-settings.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(
        path=str(target),
        animations="disabled",
        style=SCREENSHOT_STYLE,
        mask=_mask_locators(page),
        mask_color="#808080",
    )
    return target


def capture_matrix(base_url: str, output: Path, mode: str) -> list[Path]:
    captures: list[Path] = []
    prepare_playwright_environment()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(device_scale_factor=1)
        page = context.new_page()
        page.emulate_media(reduced_motion="reduce")
        _ensure_authenticated(page, base_url)
        for case in matrix_cases():
            captures.append(_capture_case(page, base_url, output, mode, case))
        captures.append(_capture_embedded_settings(page, base_url, output, mode))
        context.close()
        browser.close()
    return captures


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", required=True)
    parser.add_argument("--compare")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(".artifacts/theme-foundation"),
    )
    parser.add_argument("--base-url", default=os.environ.get("PCH_VISUAL_BASE_URL"))
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if not args.base_url:
        raise SystemExit("PCH_VISUAL_BASE_URL or --base-url is required")
    captures = capture_matrix(args.base_url, args.output, args.mode)
    print(f"captured={len(captures)} mode={args.mode}")
    if args.compare:
        for current in captures:
            relative = current.relative_to(args.output / args.mode)
            baseline = args.output / args.compare / relative
            diff = args.output / "diff" / relative
            ratio = compare_images(baseline, current, diff)
            print(f"{relative.as_posix()} changed_ratio={ratio:.8f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
