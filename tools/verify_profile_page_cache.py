"""Read-only browser verification for the profile-scoped page cache.

The verifier blocks every non-read API request except the explicit login POST.
Set PCH_PREVIEW_USERNAME and PCH_PREVIEW_PASSWORD in the environment; credentials
are never accepted on the command line or included in the report.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from time import perf_counter
from urllib.parse import urlparse

from playwright.sync_api import Page, Route, sync_playwright

if __package__:
    from .playwright_runtime import prepare_playwright_environment
else:
    from playwright_runtime import prepare_playwright_environment


SAFE_NAVIGATION_PATHS = ("/settings", "/external", "/mixes", "/status")
DEFAULT_BASE_URL = "http://192.168.50.99:9513"


def is_allowed_request(method: str, url: str) -> bool:
    """Permit reads and the one authentication request needed by this tool."""

    verb = str(method or "").upper()
    if verb in {"GET", "HEAD", "OPTIONS"}:
        return True
    return verb == "POST" and urlparse(url).path == "/api/auth/login"


def _scope_digest(scope: str) -> str:
    return hashlib.sha256(scope.encode("utf-8")).hexdigest()[:12] if scope else ""


def _wait_ready(page: Page) -> None:
    page.locator('body[data-auth-state="ready"]').wait_for(timeout=30_000)
    page.locator("#playlistProfile option").first.wait_for(timeout=30_000)


def _login(page: Page, base_url: str, username: str, password: str) -> None:
    page.goto(base_url + "/", wait_until="domcontentloaded")
    page.locator("body[data-auth-state]").wait_for(timeout=30_000)
    if page.locator("#loginForm").is_visible():
        page.locator("#loginUser").fill(username)
        page.locator("#loginPassword").fill(password)
        page.locator("#loginForm button[type=submit]").click()
    _wait_ready(page)


def _open_embedded(page: Page, path: str) -> float:
    selectors = {
        "/settings": "#openSettings",
        "/external": '[data-tool-url="/external"]',
        "/status": '[data-workspace-url="/status"]',
    }
    started = perf_counter()
    if path == "/mixes":
        page.locator("#workspaceHome").click()
        page.locator("#smartHubButton").click()
        page.locator("#playlistSectionTitle").click()
    else:
        page.locator(selectors[path]).click()
    frame = page.frame_locator("#playlistToolFrame")
    frame.locator(f'body[data-management-page="{path[1:]}"][data-auth-state="ready"]').wait_for(
        timeout=30_000
    )
    page.wait_for_timeout(150)
    return round((perf_counter() - started) * 1000, 1)


def verify(base_url: str, username: str, password: str) -> dict:
    prepare_playwright_environment(Path(__file__).resolve().parents[1])
    blocked_writes: list[str] = []
    api_requests: list[tuple[str, str]] = []
    console_errors: list[str] = []
    page_errors: list[str] = []
    network_errors: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(viewport={"width": 1440, "height": 900})

        def guard(route: Route) -> None:
            request = route.request
            path = urlparse(request.url).path
            if path.startswith("/api/"):
                api_requests.append((request.method, path))
            if is_allowed_request(request.method, request.url):
                route.continue_()
                return
            blocked_writes.append(f"{request.method} {path}")
            route.abort("blockedbyclient")

        context.route("**/*", guard)
        page = context.new_page()
        page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        page.on(
            "requestfailed",
            lambda request: network_errors.append(f"{request.method} {urlparse(request.url).path}: {request.failure}"),
        )
        _login(page, base_url, username, password)
        initial_stats = page.evaluate("PCHPageCache.stats()")

        first_timings = {path: _open_embedded(page, path) for path in SAFE_NAVIGATION_PATHS}
        first_stats = page.evaluate("PCHPageCache.stats()")
        status_reads_before = sum(1 for method, path in api_requests if method == "GET" and path == "/api/status")
        second_timings = {path: _open_embedded(page, path) for path in SAFE_NAVIGATION_PATHS}
        second_stats = page.evaluate("PCHPageCache.stats()")
        status_reads_after = sum(1 for method, path in api_requests if method == "GET" and path == "/api/status")
        if status_reads_after <= status_reads_before:
            raise AssertionError("运行状态页没有发起新的实时 /api/status 请求")

        options = page.locator("#playlistProfile option").evaluate_all(
            "rows => rows.map(row => ({id: row.value, label: row.textContent.trim()}))"
        )
        profile_check: dict = {"available": len(options), "status": "skipped-single-profile"}
        if len(options) >= 2:
            first, second = options[:2]
            page.locator("#playlistProfile").select_option(first["id"])
            page.wait_for_timeout(250)
            scope_a = page.evaluate("id => PCHPageCache.activate(id)", first["id"])
            label_a = page.locator("#playlistProfile option:checked").inner_text().strip()
            page.locator("#playlistProfile").select_option(second["id"])
            page.wait_for_timeout(250)
            scope_b = page.evaluate("id => PCHPageCache.activate(id)", second["id"])
            label_b = page.locator("#playlistProfile option:checked").inner_text().strip()
            page.locator("#playlistProfile").select_option(first["id"])
            page.wait_for_timeout(250)
            scope_a_return = page.evaluate("id => PCHPageCache.activate(id)", first["id"])
            label_a_return = page.locator("#playlistProfile option:checked").inner_text().strip()
            if not scope_a or not scope_b or scope_a == scope_b or scope_a != scope_a_return:
                raise AssertionError("A → B → A 的缓存范围隔离校验失败")
            if (label_a, label_b, label_a_return) != (first["label"], second["label"], first["label"]):
                raise AssertionError("A → B → A 的可见账户标签校验失败")
            profile_check = {
                "available": len(options),
                "status": "passed",
                "scope_a": _scope_digest(scope_a),
                "scope_b": _scope_digest(scope_b),
                "scope_a_return": _scope_digest(scope_a_return),
            }

        final_stats = page.evaluate("PCHPageCache.stats()")
        limits = final_stats["limits"]
        if final_stats["entries"] > limits["maxEntries"] or final_stats["bytes"] > limits["maxBytes"]:
            raise AssertionError("浏览器缓存超过设计上限")
        unexpected_network_errors = [
            row for row in network_errors if not any(row.startswith(f"{item}:") for item in blocked_writes)
        ]
        report = {
            "base_url": base_url,
            "cache": {
                "initial": initial_stats,
                "first_cycle": first_stats,
                "second_cycle": second_stats,
                "final": final_stats,
            },
            "timings_ms": {"first_cycle": first_timings, "second_cycle": second_timings},
            "profiles": profile_check,
            "status_live_reads": status_reads_after - status_reads_before,
            "blocked_business_writes": sorted(set(blocked_writes)),
            "console_errors": console_errors,
            "page_errors": page_errors,
            "network_errors": unexpected_network_errors,
        }
        context.close()
        browser.close()
        return report


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.environ.get("PCH_PREVIEW_BASE_URL", DEFAULT_BASE_URL))
    return parser.parse_args()


def main() -> int:
    args = _args()
    username = os.environ.get("PCH_PREVIEW_USERNAME", "")
    password = os.environ.get("PCH_PREVIEW_PASSWORD", "")
    if not username or not password:
        raise SystemExit("PCH_PREVIEW_USERNAME and PCH_PREVIEW_PASSWORD are required")
    report = verify(args.base_url.rstrip("/"), username, password)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
