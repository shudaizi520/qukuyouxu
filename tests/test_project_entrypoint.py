import unittest
import os
from pathlib import Path
import subprocess
import sys


class ProjectEntrypointTests(unittest.TestCase):
    def test_public_release_version_is_current(self):
        from helper import __version__

        self.assertEqual("1.4.5", __version__)

    def test_project_uses_one_modern_license_declaration(self):
        root = Path(__file__).resolve().parents[1]
        metadata = (root / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('license = "AGPL-3.0-only"', metadata)
        self.assertNotIn("License :: OSI Approved", metadata)

    def test_public_entrypoint_exists(self):
        root = Path(__file__).resolve().parents[1]
        environment = {**os.environ, "PYTHONPATH": str(root / "src")}
        result = subprocess.run(
            [sys.executable, "-c", "from helper.web import main; assert callable(main)"],
            cwd=root,
            env=environment,
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(0, result.returncode, result.stderr)

    def test_music_cache_tool_is_installed_with_the_application(self):
        root = Path(__file__).resolve().parents[1]
        metadata = (root / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('qukuyouxu-cache = "helper.cache_cli:main"', metadata)
        environment = {**os.environ, "PYTHONPATH": str(root / "src")}
        result = subprocess.run(
            [sys.executable, "-m", "helper.cache_cli", "--help"],
            cwd=root,
            env=environment,
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("歌曲缓存迁移工具", result.stdout)


if __name__ == "__main__":
    unittest.main()
