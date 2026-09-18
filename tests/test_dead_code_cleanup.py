from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class DeadCodeCleanupTests(unittest.TestCase):
    def test_unreachable_legacy_route_stacks_and_console_assets_are_removed(self):
        obsolete = (
            "src/helper/workflow.py",
            "src/helper/workflow_web.py",
            "src/helper/theme_web.py",
            "src/helper/qq_auth_web.py",
            "src/helper/qq_auth.py",
            "src/helper/static/index.html",
            "src/helper/static/app.js",
            "src/helper/static/style.css",
            "src/helper/static/advanced.js",
            "src/helper/static/single.js",
        )

        self.assertEqual([], [name for name in obsolete if (ROOT / name).exists()])


if __name__ == "__main__":
    unittest.main()
