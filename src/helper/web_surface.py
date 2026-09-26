"""Public HTML, health and closed static-asset routes."""
from __future__ import annotations

from pathlib import Path

from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response

from .page_version import render_library_html, render_versioned_html


STATIC_ASSETS = frozenset({
    "home.js", "theme_home.js", "product.css", "product-refinements.css", "daily.js", "refined.js",
    "status.js", "settings.js", "contextual-settings.js", "page-cache.js", "auth.js", "mixes.js",
    "appearance.js", "external.js", "playlists.js", "playlist-artwork.js",
    "playlist-workspace.js", "playlist-search.js", "playlist-player.js",
    "playlist-sections.js", "playlist-playback-mode.js", "playlist-now-playing.js",
    "playlist-now-playing.css", "external-workspace.css", "theme-tokens.css",
    "settings-page.css", "daily-page.css", "mixes-page.css", "external-page.css",
    "ui-components.css", "design-system.css", "management-shell.css",
    "theme-background.css", "playlist-visualizer.js", "management-shell.js",
})


def attach_web_surface(app, static_root: Path, version: str) -> None:
    static_root = Path(static_root)

    def page(name: str, *, library: bool = False) -> HTMLResponse:
        source = (static_root / name).read_text(encoding="utf-8")
        renderer = render_library_html if library else render_versioned_html
        return HTMLResponse(renderer(source, version))

    @app.get("/")
    def index():
        return page("playlists.html")

    @app.get("/daily")
    def daily_page():
        return page("daily.html")

    @app.get("/library")
    def library_home():
        return page("home.html", library=True)

    @app.get("/status")
    def status_page():
        return page("status.html")

    @app.get("/mixes")
    def mixes_page():
        return page("mixes.html")

    @app.get("/external")
    def external_page():
        return page("external.html")

    @app.get("/settings")
    def settings_page():
        return page("settings.html")

    @app.get("/appearance")
    def appearance_page():
        return page("appearance.html")

    @app.get("/advanced")
    def advanced():
        return RedirectResponse(url="/status", status_code=307)

    @app.get("/healthz")
    def health():
        return {"ok": True, "version": version}

    @app.get("/static/{name}")
    def static(name: str):
        if name not in STATIC_ASSETS:
            return Response(status_code=404)
        media_type = "text/javascript" if name.endswith(".js") else "text/css"
        return FileResponse(static_root / name, media_type=media_type)
