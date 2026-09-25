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


if __name__ == "__main__":
    unittest.main()
