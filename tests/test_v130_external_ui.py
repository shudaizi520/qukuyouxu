import re
import unittest
from pathlib import Path

from ui_css import page_css


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src/helper/static"


class ExternalPlaylistUiV130Tests(unittest.TestCase):
    def test_external_page_keeps_primary_and_missing_actions_distinct(self):
        page = (STATIC / "external.html").read_text(encoding="utf-8")
        self.assertIn('aria-label="Plex 歌单设置"', page)
        self.assertIn("缺失歌曲", page)
        self.assertNotIn("发送到 QQ 音乐", page)
        self.assertNotIn("发送到网易云音乐", page)

    def test_playlist_home_exposes_external_import_without_crowding_every_top_nav(self):
        page = (STATIC / "playlists.html").read_text(encoding="utf-8")
        self.assertEqual(1, page.count('data-tool-url="/external"'))
        for name in ("daily.html", "home.html", "mixes.html", "status.html", "settings.html", "external.html"):
            other = (STATIC / name).read_text(encoding="utf-8")
            self.assertEqual(0, other.count('href="/external"'), name)

    def test_page_has_one_clear_import_and_three_result_tabs(self):
        page = (STATIC / "external.html").read_text(encoding="utf-8")
        self.assertIn('id="sourceUrl"', page)
        self.assertIn('id="sourceFile"', page)
        self.assertIn('id="importSource"', page)
        self.assertIn('data-match-status="matched"', page)
        self.assertIn('data-match-status="review"', page)
        self.assertIn('data-match-status="missing"', page)
        self.assertNotIn("external-trust-note", page)

    def test_replenishment_actions_are_explicit_and_never_claim_platform_writes(self):
        page = (STATIC / "external.html").read_text(encoding="utf-8")
        script = (STATIC / "external.js").read_text(encoding="utf-8")
        for label in ("复制清单", "下载", "TXT", "CSV", "长图"):
            self.assertIn(label, page)
        self.assertNotIn("复制清单链接", page)
        self.assertNotIn("去 QQ 音乐搜索", script)
        self.assertNotIn("去网易云音乐搜索", script)
        self.assertNotIn("发送到 QQ", page + script)
        self.assertNotIn("发送到网易云", page + script)

    def test_dom_rendering_is_text_only_and_canvas_is_bounded(self):
        script = (STATIC / "external.js").read_text(encoding="utf-8")
        self.assertIn("textContent", script)
        self.assertNotIn("innerHTML", script)
        self.assertIn("IMAGE_PAGE_ROWS=80", script)
        self.assertIn("encodeURIComponent", script)

    def test_switching_sources_resets_paging_and_managed_title_can_be_safely_renamed(self):
        script = (STATIC / "external.js").read_text(encoding="utf-8")
        self.assertIn("if(!current||current.id!==sourceId){page=1", script)
        self.assertNotIn("$('plexPlaylistTitle').disabled=!!current.managed", script)
        self.assertIn("/api/playlists/rename", script)
        self.assertIn("重命名并更新", script)

    def test_element_ids_are_unique_and_mobile_layout_cannot_overflow_page(self):
        page = (STATIC / "external.html").read_text(encoding="utf-8")
        ids = re.findall(r'\bid="([^"]+)"', page)
        self.assertEqual(len(ids), len(set(ids)))
        styles = page_css("external")
        self.assertIn(".external-shell{min-width:0", styles)
        self.assertIn("overflow-wrap:anywhere", styles)

    def test_page_route_and_versioned_asset_are_served(self):
        from fastapi import FastAPI
        from helper.web_surface import STATIC_ASSETS, attach_web_surface

        app = FastAPI()
        attach_web_surface(app, STATIC, "test")
        routes = {route.path: route for route in app.routes}
        response = routes["/external"].endpoint()
        self.assertEqual(200, response.status_code)
        self.assertIn("external.js", STATIC_ASSETS)
        self.assertIn(b'/static/external.js?v=test', response.body)
        page = (STATIC / "external.html").read_text(encoding="utf-8")
        self.assertNotIn('id="version"', page)
        self.assertIn('/static/external.js?v=app', page)
        self.assertIn('/static/external-workspace.css?v=app', page)


if __name__ == "__main__":
    unittest.main()
