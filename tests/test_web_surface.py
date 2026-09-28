import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI


class WebSurfaceTests(unittest.TestCase):
    def test_public_surface_registers_pages_static_assets_and_health(self):
        from helper.web_surface import attach_web_surface

        app = FastAPI()
        with tempfile.TemporaryDirectory() as directory:
            attach_web_surface(app, Path(directory), "9.8.7")

        routes = {getattr(route, "path", ""): route for route in app.routes}
        expected = {
            "/", "/daily", "/library", "/status", "/mixes", "/external",
            "/settings", "/appearance", "/advanced", "/healthz",
            "/static/{name}",
        }
        self.assertEqual(set(), expected - set(routes))
        self.assertEqual({"ok": True, "version": "9.8.7"}, routes["/healthz"].endpoint())

    def test_turntable_png_is_allowlisted_and_served_with_the_image_media_type(self):
        from helper.web_surface import STATIC_ASSETS, attach_web_surface

        static = Path(__file__).resolve().parents[1] / "src/helper/static"
        app = FastAPI()
        attach_web_surface(app, static, "test")
        routes = {getattr(route, "path", ""): route for route in app.routes}

        for name in ("turntable-chassis.png", "turntable-tonearm.png"):
            self.assertIn(name, STATIC_ASSETS)
            response = routes["/static/{name}"].endpoint(name)
            self.assertEqual(200, response.status_code)
            self.assertEqual("image/png", response.media_type)
            self.assertTrue(Path(response.path).read_bytes().startswith(b"\x89PNG\r\n\x1a\n"))


if __name__ == "__main__":
    unittest.main()
