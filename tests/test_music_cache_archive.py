import json
from contextlib import closing
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest


def make_database(path: Path, values: dict):
    with closing(sqlite3.connect(path)) as database:
        database.execute("CREATE TABLE state (k TEXT PRIMARY KEY, v TEXT NOT NULL)")
        database.executemany(
            "INSERT INTO state(k, v) VALUES (?, ?)",
            [(key, json.dumps(value, ensure_ascii=False)) for key, value in values.items()],
        )
        database.commit()
    return path


def read_values(path: Path):
    with closing(sqlite3.connect(path)) as database:
        return {
            key: json.loads(value)
            for key, value in database.execute("SELECT k, v FROM state ORDER BY k")
        }


class MusicCacheArchiveTests(unittest.TestCase):
    def test_v2_round_trip_preserves_scoped_cache_and_excludes_private_state(self):
        from helper.cache_archive import export_music_cache, import_music_cache

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = make_database(
                root / "source.sqlite3",
                {
                    "profile:default:single_result:scope:1": {"status": "matched", "id": "1"},
                    "profile:default:single_detail:mid": {"detail": {"mid": "mid"}},
                    "profile:friend-42:single_search:hash": {"items": []},
                    "profile:friend-42:single_revision": 8,
                    "profile:friend-42:plex_token": "must-not-export",
                    "auth_credentials": {"password": "must-not-export"},
                },
            )
            archive = root / "music-cache.json"
            exported = export_music_cache(source, archive)
            document = json.loads(archive.read_text(encoding="utf-8"))
            target = make_database(root / "target.sqlite3", {})
            imported = import_music_cache(archive, target)

            self.assertEqual("qukuyouxu-music-cache-v2", document["format"])
            self.assertEqual(4, exported.count)
            self.assertEqual(exported.sha256, imported.sha256)
            self.assertEqual(
                {
                    "profile:default:single_result:scope:1": {"status": "matched", "id": "1"},
                    "profile:default:single_detail:mid": {"detail": {"mid": "mid"}},
                    "profile:friend-42:single_search:hash": {"items": []},
                    "profile:friend-42:single_revision": 8,
                },
                read_values(target),
            )

    def test_single_scoped_profile_can_be_remapped_on_import(self):
        from helper.cache_archive import export_music_cache, import_music_cache

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = make_database(
                root / "source.sqlite3",
                {"profile:old-owner:single_result:scope:1": {"status": "matched"}},
            )
            archive = root / "music-cache.json"
            export_music_cache(source, archive)
            target = make_database(root / "target.sqlite3", {})

            import_music_cache(archive, target, target_profile="default")

            self.assertEqual(
                {"profile:default:single_result:scope:1": {"status": "matched"}},
                read_values(target),
            )

    def test_export_can_select_only_the_owner_profile(self):
        from helper.cache_archive import export_music_cache, import_music_cache

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = make_database(
                root / "source.sqlite3",
                {
                    "single_revision": 1,
                    "profile:default:single_revision": 7,
                    "profile:default:single_result:scope:1": {"status": "matched"},
                    "profile:friend-42:single_result:scope:2": {"status": "matched"},
                },
            )
            archive = root / "music-cache.json"

            exported = export_music_cache(source, archive, profile_id="default")
            target = make_database(root / "target.sqlite3", {})
            import_music_cache(archive, target)

            self.assertEqual(2, exported.count)
            self.assertEqual(
                {
                    "profile:default:single_revision": 7,
                    "profile:default:single_result:scope:1": {"status": "matched"},
                },
                read_values(target),
            )

    def test_legacy_v1_archive_imports_into_default_profile(self):
        from helper.cache_archive import import_music_cache

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            records = [{"key": "single_revision", "value_json": "7"}]
            canonical = json.dumps(
                records, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
            import hashlib

            archive = root / "legacy.json"
            archive.write_text(
                json.dumps(
                    {
                        "format": "qukuyouxu-music-cache-v1",
                        "created_at": "2026-09-18T00:00:00Z",
                        "records": records,
                        "sha256": hashlib.sha256(canonical).hexdigest(),
                    }
                ),
                encoding="utf-8",
            )
            target = make_database(root / "target.sqlite3", {})

            import_music_cache(archive, target)

            self.assertEqual({"profile:default:single_revision": 7}, read_values(target))

    def test_round_trip_preserves_only_music_evidence(self):
        from helper.cache_archive import export_music_cache, import_music_cache

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = make_database(
                root / "source.sqlite3",
                {
                    "single_result:scope:1": {"status": "matched", "id": "1"},
                    "single_detail:mid": {"detail": {"mid": "mid"}},
                    "single_search:hash": {"items": []},
                    "single_revision": 7,
                    "single_scope": "scope",
                    "plex_token": "must-not-export",
                },
            )
            archive = root / "music-cache.json"
            exported = export_music_cache(source, archive)
            target = make_database(root / "target.sqlite3", {})
            imported = import_music_cache(archive, target)

            self.assertEqual(5, exported.count)
            self.assertEqual(exported.sha256, imported.sha256)
            self.assertEqual(
                {
                    "profile:default:single_result:scope:1": {"status": "matched", "id": "1"},
                    "profile:default:single_detail:mid": {"detail": {"mid": "mid"}},
                    "profile:default:single_search:hash": {"items": []},
                    "profile:default:single_revision": 7,
                    "profile:default:single_scope": "scope",
                },
                read_values(target),
            )

    def test_corrupted_archive_is_rejected(self):
        from helper.cache_archive import CacheArchiveError, export_music_cache, inspect_music_cache

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = make_database(root / "source.sqlite3", {"single_revision": 3})
            archive = root / "music-cache.json"
            export_music_cache(source, archive)
            document = json.loads(archive.read_text(encoding="utf-8"))
            document["records"][0]["value_json"] = "4"
            archive.write_text(json.dumps(document), encoding="utf-8")

            with self.assertRaisesRegex(CacheArchiveError, "校验"):
                inspect_music_cache(archive)

    def test_conflicting_target_requires_explicit_replace(self):
        from helper.cache_archive import CacheArchiveError, export_music_cache, import_music_cache

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = make_database(root / "source.sqlite3", {"single_revision": 9})
            archive = root / "music-cache.json"
            export_music_cache(source, archive)
            target = make_database(root / "target.sqlite3", {"profile:default:single_revision": 2})

            with self.assertRaisesRegex(CacheArchiveError, "冲突"):
                import_music_cache(archive, target)
            self.assertEqual(2, read_values(target)["profile:default:single_revision"])

            imported = import_music_cache(archive, target, replace=True)
            self.assertEqual(1, imported.count)
            self.assertEqual(9, read_values(target)["profile:default:single_revision"])
            self.assertIsNotNone(imported.backup)
            self.assertTrue(imported.backup.is_file())

    def test_unknown_cache_key_is_rejected_even_with_valid_shape(self):
        from helper.cache_archive import CacheArchiveError, inspect_music_cache

        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "music-cache.json"
            archive.write_text(
                json.dumps(
                    {
                        "format": "qukuyouxu-music-cache-v1",
                        "created_at": "2026-09-18T00:00:00Z",
                        "records": [{"key": "plex_token", "value_json": '"secret"'}],
                        "sha256": "0" * 64,
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(CacheArchiveError, "不允许"):
                inspect_music_cache(archive)

    def test_cli_reports_summary_without_exposing_values(self):
        from helper.cache_archive import export_music_cache

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = make_database(
                root / "source.sqlite3",
                {"single_result:scope:1": {"status": "matched", "title": "秘密歌名"}},
            )
            archive = root / "music-cache.json"
            export_music_cache(source, archive)
            environment = {
                **__import__("os").environ,
                "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
            }
            result = subprocess.run(
                [sys.executable, "-m", "tools.music_cache", "inspect", "--archive", str(archive)],
                cwd=Path(__file__).resolve().parents[1],
                env=environment,
                capture_output=True,
                text=True,
                timeout=20,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn("记录数: 1", result.stdout)
            self.assertNotIn("秘密歌名", result.stdout)


if __name__ == "__main__":
    unittest.main()
