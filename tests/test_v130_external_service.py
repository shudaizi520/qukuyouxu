import copy
import tempfile
import unittest
from pathlib import Path

from tests.test_v130_external_playlist_sync import FakePlex


QQ_URL = "https://y.qq.com/n/ryqq/playlist/123"


def local(track_id, title, artist, duration=180, album=""):
    return {
        "id": str(track_id), "title": title, "artist": artist, "album": album,
        "duration": duration, "available": True, "guid": f"local://{track_id}", "paths": [],
    }


def source_track(key, title, artists, position, duration_ms=180000):
    return {
        "source_track_key": key, "position": position, "source_track_id": key,
        "title": title, "artists": artists, "album": "", "duration_ms": duration_ms,
        "version_flags": [], "version_label": "", "source_url": "",
    }


def snapshot(tracks=None, revision="source-r1"):
    return {
        "provider": "qq", "external_id": "123", "url": QQ_URL,
        "title": "百万收藏", "revision": revision,
        "tracks": tracks or [
            source_track("a", "已有一", ["歌手甲"], 0),
            source_track("b", "缺失歌", ["歌手乙"], 1),
            source_track("c", "已有三", ["歌手丙"], 2),
            source_track("d", "同名歌", ["歌手丁"], 3),
        ],
    }


class FakeProviders:
    def __init__(self, results):
        self.results = list(results)
        self.calls = 0

    def fetch(self, recognized):
        self.calls += 1
        if not self.results:
            raise AssertionError("unexpected provider fetch")
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return copy.deepcopy(result)


class CatalogPlex(FakePlex):
    def __init__(self, catalog):
        super().__init__()
        self.catalog = list(catalog)

    def tracks(self, section):
        if str(section) != "11":
            raise ValueError("wrong section")
        return copy.deepcopy(self.catalog)

    def playlist_ids(self, playlist_id):
        return [row["id"] for row in self.states[str(playlist_id)]["items"]]


class ExternalServiceV130Tests(unittest.TestCase):
    def setUp(self):
        from helper.external_service import ExternalPlaylistService
        from helper.profiles import ProfileRegistry
        from helper.scoped_store import ScopedStore
        from helper.store import Store

        self.temp = tempfile.TemporaryDirectory()
        self.base = Store(Path(self.temp.name))
        self.registry = ProfileRegistry(self.base)
        self.registry.create("长辈", "home", profile_id="parent")
        self.store = ScopedStore(self.base, "default", registry=self.registry)
        settings = self.store.get("settings")
        settings.update(plex_url="http://plex", plex_token="token-token", section="11")
        self.store.set("settings", settings)
        self.plex = CatalogPlex([
            local("10", "已有一", "歌手甲"),
            local("30", "已有三", "歌手丙"),
            local("40", "同名歌", "歌手丁", album="A"),
            local("41", "同名歌", "歌手丁", album="B"),
        ])
        self.providers = FakeProviders([snapshot()])
        self.now = 2_000_000_000.0
        self.service = ExternalPlaylistService(
            self.store, lambda cfg: self.plex, self.providers, clock=lambda: self.now
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_import_match_publish_and_later_rematch_preserve_source_order(self):
        imported = self.service.import_source(value=QQ_URL)
        self.assertEqual({"matched": 2, "review": 1, "missing": 1, "ignored": 0}, imported["counts"])

        published = self.service.publish(imported["id"], "百万收藏", imported["revision"])
        self.assertEqual(["10", "30"], self.plex.playlist_ids(published["playlist_id"]))

        self.plex.catalog.append(local("20", "缺失歌", "歌手乙"))
        result = self.service.rematch_missing()
        self.assertEqual(["10", "20", "30"], self.plex.playlist_ids(published["playlist_id"]))
        self.assertEqual(0, result["missing"])

    def test_stale_revision_and_no_reliable_matches_never_create(self):
        from helper.engine import SafetyError

        imported = self.service.import_source(value=QQ_URL)
        with self.assertRaisesRegex(SafetyError, "变化"):
            self.service.publish(imported["id"], "百万收藏", "stale")
        self.assertEqual([], self.plex.created)

        self.plex.catalog = []
        self.service.match(imported["id"])
        with self.assertRaisesRegex(SafetyError, "可靠匹配"):
            self.service.publish(imported["id"], "百万收藏", imported["revision"])
        self.assertEqual([], self.plex.created)

    def test_same_import_is_deduplicated_without_erasing_managed_state(self):
        first = self.service.import_source(value=QQ_URL)
        self.service.publish(first["id"], "百万收藏", first["revision"])
        self.providers.results.append(snapshot())

        second = self.service.import_source(value=QQ_URL)

        self.assertEqual(first["id"], second["id"])
        self.assertEqual(1, len(self.service.repository.list_sources("default")))
        self.assertIsNotNone(self.service.repository.get_managed("default", first["id"]))

    def test_confirmation_and_source_ids_are_profile_isolated(self):
        imported = self.service.import_source(value=QQ_URL)
        confirmed = self.service.confirm(imported["id"], "d", {"status": "matched", "plex_track_id": "40"})
        self.assertEqual("40", next(row for row in confirmed["tracks"] if row["source_track_key"] == "d")["plex_track_id"])

        from helper.external_service import ExternalPlaylistService
        from helper.scoped_store import ScopedStore

        parent = ExternalPlaylistService(
            ScopedStore(self.base, "parent", registry=self.registry), lambda cfg: self.plex,
            FakeProviders([]), clock=lambda: self.now,
        )
        with self.assertRaises(ValueError):
            parent.public_source(imported["id"])

    def test_refresh_adds_tracks_and_large_removal_pauses_without_overwriting(self):
        imported = self.service.import_source(value=QQ_URL)
        old_tracks = self.service.repository.list_tracks("default", imported["id"])
        many = [source_track(f"k{i}", f"歌曲{i}", [f"歌手{i}"], i) for i in range(50)]
        accepted = snapshot(many, revision="many")
        self.providers.results.append(accepted)
        self.service.refresh(imported["id"], force=True)
        self.assertEqual(50, len(self.service.repository.list_tracks("default", imported["id"])))

        reduced = snapshot(many[:39], revision="reduced")
        self.providers.results.append(reduced)
        result = self.service.refresh(imported["id"])
        self.assertEqual("confirmation_required", result["status"])
        self.assertEqual(50, len(self.service.repository.list_tracks("default", imported["id"])))
        self.assertTrue(self.service.repository.get_source("default", imported["id"])["needs_confirmation"])
        self.assertNotEqual(old_tracks, self.service.repository.list_tracks("default", imported["id"]))

    def test_failed_refresh_preserves_prior_counts(self):
        from helper.external_sources import ExternalSourceError

        imported = self.service.import_source(value=QQ_URL)
        before = imported["counts"]
        self.providers.results.append(ExternalSourceError("限流", retryable=True, kind="upstream"))
        with self.assertRaises(ExternalSourceError):
            self.service.refresh(imported["id"], force=True)
        after = self.service.public_source(imported["id"])
        self.assertEqual(before, after["counts"])
        self.assertEqual(1, after["failure_count"])


if __name__ == "__main__":
    unittest.main()
