from pathlib import Path
import tempfile
import unittest


class RepositoryHygieneTests(unittest.TestCase):
    def test_detects_runtime_artifacts_and_secret_literals(self):
        from tools.check_repository import find_violations

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "runtime.sqlite3").write_bytes(b"SQLite format 3")
            (root / "old-upgrade.yaml").write_text("services: {}", encoding="utf-8")
            (root / "secret.env").write_text(
                "PLEX_TOKEN=real-secret-value\nSETUP_TOKEN=private-bootstrap-value\n",
                encoding="utf-8",
            )
            (root / "key.pem").write_text(
                "-----BEGIN PRIVATE KEY-----\nprivate-material\n-----END PRIVATE KEY-----\n",
                encoding="utf-8",
            )
            violations = find_violations(root)
            rendered = "\n".join(violations)
            self.assertIn("runtime.sqlite3", rendered)
            self.assertIn("old-upgrade.yaml", rendered)
            self.assertIn("secret.env", rendered)
            self.assertIn("key.pem", rendered)
            self.assertNotIn("real-secret-value", rendered)

    def test_allows_empty_examples_and_normal_source_references(self):
        from tools.check_repository import find_violations

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".env.example").write_text("PLEX_TOKEN=\nADMIN_TOKEN=\n", encoding="utf-8")
            (root / "app.py").write_text(
                "token = os.environ.get('PLEX_TOKEN', '')\n", encoding="utf-8"
            )
            (root / "compose.yaml").write_text(
                "ADMIN_TOKEN: ${ADMIN_TOKEN:-}\nSETUP_TOKEN: ${SETUP_TOKEN:-}\n",
                encoding="utf-8",
            )
            self.assertEqual([], find_violations(root))

    def test_rejects_private_music_cache_archives_and_database_backups(self):
        from tools.check_repository import find_violations

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "music-cache-v2.json").write_text(
                '{"records":[{"title":"private song"}]}', encoding="utf-8"
            )
            (root / "helper.sqlite3.before-cache-import-20260918.bak").write_bytes(b"backup")

            rendered = "\n".join(find_violations(root))

            self.assertIn("music-cache-v2.json", rendered)
            self.assertIn("helper.sqlite3.before-cache-import-20260918.bak", rendered)

    def test_gitignore_excludes_private_cache_and_import_backups(self):
        root = Path(__file__).resolve().parents[1]
        ignore = (root / ".gitignore").read_text(encoding="utf-8")

        self.assertIn("music-cache*.json", ignore)
        self.assertIn("*.before-cache-import-*.bak", ignore)


if __name__ == "__main__":
    unittest.main()
