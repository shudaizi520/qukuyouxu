import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class VersionCacheV0332Tests(unittest.TestCase):
    def test_library_status_request_cannot_reuse_an_old_release_response(self):
        from helper import __version__

        auth = (ROOT / "src/helper/static/auth.js").read_text(encoding="utf-8")
        home = (ROOT / "src/helper/static/home.js").read_text(encoding="utf-8")

        self.assertIn("cache:'no-store'", auth)
        self.assertIn(f"/api/workflow/status?release={__version__}", home)

if __name__ == "__main__":
    unittest.main()
