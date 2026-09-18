import asyncio
import contextlib
import sys
import tempfile
import types
import fastapi
import unittest
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
if "fastapi" not in sys.modules:
    fastapi = types.ModuleType("fastapi")
    fastapi.Request = object
    sys.modules["fastapi"] = fastapi

from helper.daily_mix_v035 import attach_policy_routes, select_daily_mix
from helper.profiles import ProfileRegistry
from helper.scoped_store import ActiveProfileStore
from helper.store import Store


class _SettingsMarkup(HTMLParser):
    def __init__(self):
        super().__init__()
        self.elements = {}
        self.text = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        element_id = values.get("id")
        if element_id:
            self.elements[element_id] = (tag, values)

    def handle_data(self, data):
        self.text.append(data)


class _Routes:
    def __init__(self):
        self.handlers = {}

    def _add(self, method, path):
        def decorator(handler):
            self.handlers[(method, path)] = handler
            return handler
        return decorator

    def get(self, path):
        return self._add("GET", path)

    def post(self, path):
        return self._add("POST", path)


class _Engine:
    def __init__(self, store):
        self.store = store

    def exclusive(self):
        return contextlib.nullcontext()


class SettingsV042Tests(unittest.TestCase):
    def _policy_fixture(self):
        root = tempfile.TemporaryDirectory()
        base = Store(Path(root.name))
        registry = ProfileRegistry(base)
        active = ActiveProfileStore(base, registry)
        routes = _Routes()
        payload = {}

        async def body(_request):
            return dict(payload)

        attach_policy_routes(routes, active, _Engine(active), body, lambda: None)
        return root, registry, routes, payload

    def test_settings_make_friend_profiles_visible_and_daily_size_editable(self):
        page = _SettingsMarkup()
        page.feed((ROOT / "src/helper/static/settings.html").read_text(encoding="utf-8"))

        profile_tag, profile_attrs = page.elements["profileManager"]
        self.assertEqual("details", profile_tag)
        self.assertNotIn("open", profile_attrs)

        input_tag, size_attrs = page.elements["dailySize"]
        self.assertEqual("input", input_tag)
        self.assertNotIn("disabled", size_attrs)
        self.assertEqual("10", size_attrs.get("min"))
        self.assertEqual("100", size_attrs.get("max"))
        self.assertNotIn("固定生成 30 首", "".join(page.text))

        script = (ROOT / "src/helper/static/settings.js").read_text(encoding="utf-8")
        self.assertIn("size:Number($('dailySize').value)", script)

    def test_daily_mix_honors_configured_size(self):
        tracks = [
            {
                "id": str(index),
                "title": f"Song {index}",
                "artist": f"Artist {index}",
                "album": f"Album {index}",
                "duration": 180,
                "available": True,
                "view_count": 0,
                "last_viewed_at": 0,
                "user_rating": 0,
                "genres": [],
            }
            for index in range(1, 41)
        ]
        result = select_daily_mix(
            tracks,
            {},
            {"tracks": {}, "artists": {}},
            {
                "size": 10,
                "recent_days": 7,
                "rediscovery_days": 90,
                "daily_avoid_days": 7,
                "artist_cap": 2,
                "favorite_cap": 4,
            },
            [],
            [],
            1_800_000_000,
            "ten-tracks",
        )
        self.assertEqual(10, result["stats"]["requested"])
        self.assertEqual(10, len(result["items"]))

    def test_daily_size_round_trips_and_is_isolated_per_profile(self):
        root, registry, routes, payload = self._policy_fixture()
        self.addCleanup(root.cleanup)
        get_policy = routes.handlers[("GET", "/api/daily/policy")]
        save_policy = routes.handlers[("POST", "/api/daily/policy")]

        payload.update(size=47)
        asyncio.run(save_policy(object()))
        self.assertEqual(47, get_policy()["size"])

        friend = registry.create(
            name="朋友甲", kind="shared", profile_id="friend-a", token="friend-token"
        )
        registry.select(friend["id"])
        payload.clear()
        payload.update(size=23)
        asyncio.run(save_policy(object()))
        self.assertEqual(23, get_policy()["size"])

        registry.select("default")
        self.assertEqual(47, get_policy()["size"])

    def test_daily_size_rejects_values_outside_supported_range(self):
        root, _registry, routes, payload = self._policy_fixture()
        self.addCleanup(root.cleanup)
        save_policy = routes.handlers[("POST", "/api/daily/policy")]

        for invalid in (9, 101, True):
            with self.subTest(invalid=invalid):
                payload.clear()
                payload.update(size=invalid)
                with self.assertRaises(ValueError):
                    asyncio.run(save_policy(object()))


if __name__ == "__main__":
    unittest.main()
