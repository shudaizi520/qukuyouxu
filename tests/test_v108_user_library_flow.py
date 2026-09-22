import asyncio
import copy
import json
import tempfile
import time
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlsplit
from unittest.mock import patch


async def _asgi_request(app, target, method="GET", headers=None, body=None):
    parsed = urlsplit(target)
    payload = b"" if body is None else json.dumps(body).encode("utf-8")
    supplied = {str(key).lower(): str(value) for key, value in (headers or {}).items()}
    host = supplied.pop("host", "testserver")
    raw_headers = [(b"host", host.encode("latin-1"))]
    raw_headers.extend((key.encode("latin-1"), value.encode("latin-1")) for key, value in supplied.items())
    if payload:
        raw_headers.extend([(b"content-type", b"application/json"), (b"content-length", str(len(payload)).encode("ascii"))])
    request_sent = False
    response_complete = asyncio.Event()
    messages = []

    async def receive():
        nonlocal request_sent
        if not request_sent:
            request_sent = True
            return {"type": "http.request", "body": payload, "more_body": False}
        await response_complete.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        messages.append(message)
        if message["type"] == "http.response.body" and not message.get("more_body", False):
            response_complete.set()

    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": method, "scheme": "http", "path": parsed.path,
        "raw_path": parsed.path.encode("ascii"), "query_string": parsed.query.encode("ascii"),
        "headers": raw_headers, "client": ("127.0.0.1", 12345), "server": ("testserver", 80),
    }
    try:
        await asyncio.wait_for(app(scope, receive, send), timeout=5)
    except TimeoutError as exc:
        raise AssertionError(f"ASGI request did not finish: {method} {target}; messages={messages!r}") from exc
    start = next(message for message in messages if message["type"] == "http.response.start")
    response_body = b"".join(message.get("body", b"") for message in messages if message["type"] == "http.response.body")
    return start["status"], dict(start.get("headers", [])), json.loads(response_body or b"{}")


class _Response:
    def __init__(self, value, status=200):
        self.value = value
        self.status_code = status
        self.content = json.dumps(value).encode("utf-8")

    def json(self):
        return copy.deepcopy(self.value)


class _Plex:
    def __init__(self, token, state):
        self.token = token
        self.state = state.setdefault(token, {"playlists": {}, "next": 900})

    def identity(self):
        return {"machine": "machine-a", "server": "Home Plex"}

    def sections(self):
        return [
            {"id": "11", "title": "音乐", "type": "artist"},
            {"id": "15", "title": "经典音乐", "type": "artist"},
            {"id": "99", "title": "电影", "type": "movie"},
        ]

    def tracks(self, section):
        offset = 0 if str(section) == "11" else 100
        return [{
            "id": str(offset + index), "title": f"歌曲 {offset + index}",
            "artist": f"歌手 {index}", "album": f"专辑 {index}",
            "duration": 180, "available": True, "guid": f"guid-{offset + index}", "paths": [],
        } for index in range(1, 41)]

    def playlists(self):
        return [copy.deepcopy(row) for row in self.state["playlists"].values()]

    def playlist_state(self, playlist_id):
        return copy.deepcopy(self.state["playlists"][str(playlist_id)])

    def playlist_track_ids(self, playlist_id):
        return [row["id"] for row in self.state["playlists"][str(playlist_id)]["items"]]

    def create(self, title, ids, marker, description=None):
        playlist_id = str(self.state["next"])
        self.state["next"] += 1
        row = {
            "id": playlist_id, "title": title,
            "summary": marker + "\n" + (description or ""),
            "items": [{"id": str(value), "item_id": f"{playlist_id}-{index}"} for index, value in enumerate(ids, 1)],
        }
        self.state["playlists"][playlist_id] = row
        return copy.deepcopy(row)

    def append(self, playlist_id, ids):
        row = self.state["playlists"][str(playlist_id)]
        start = len(row["items"]) + 1
        row["items"].extend({"id": str(value), "item_id": f"{playlist_id}-{start + index}"} for index, value in enumerate(ids))

    def remove_items(self, playlist_id, item_ids):
        removed = set(map(str, item_ids))
        row = self.state["playlists"][str(playlist_id)]
        row["items"] = [item for item in row["items"] if str(item["item_id"]) not in removed]

    def move_item(self, playlist_id, item_id, after=None):
        row = self.state["playlists"][str(playlist_id)]
        moving = next(item for item in row["items"] if item["item_id"] == str(item_id))
        row["items"].remove(moving)
        if after is None:
            row["items"].insert(0, moving)
        else:
            index = next(index for index, item in enumerate(row["items"]) if item["item_id"] == str(after))
            row["items"].insert(index + 1, moving)

    def _xml(self, path, params=None):
        if path == "/accounts":
            root = ET.Element("MediaContainer")
            ET.SubElement(root, "Account", id="10" if self.token == "owner-token" else "248098626", name="shudaizi" if self.token == "owner-token" else "shudai6")
            return root
        return ET.Element("MediaContainer", size="0", totalSize="0")


class UserLibraryFlowV108Tests(unittest.TestCase):
    def setUp(self):
        from helper.store import Store
        from helper.web import create_app

        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Store(Path(self.temp.name))
        self.plex_state = {}
        self.app = create_app(store=self.base, start_scheduler=False)
        self.loop = asyncio.new_event_loop()
        self.addCleanup(self.loop.close)

    def http(self, target, method="GET", headers=None, body=None):
        return self.loop.run_until_complete(
            _asgi_request(self.app, target, method=method, headers=headers, body=body)
        )

    def direct(self, path, profile="default", **kwargs):
        route = next(row for row in self.app.routes if getattr(row, "path", None) == path)
        with self.app.state.profiles.fixed_active(profile, enabled_only=True):
            return route.endpoint(**kwargs)

    def external_request(self, _session, method, url, **_kwargs):
        path = urlsplit(url).path
        if path == "/api/v2/pins" and method == "POST":
            return _Response({"id": 77, "code": "abcd-1234", "expiresIn": 300})
        if path == "/api/v2/pins/77":
            return _Response({"authToken": "owner-token"})
        if path == "/api/v2/user":
            return _Response({"id": "10", "username": "shudaizi"})
        if path == "/api/v2/resources":
            return _Response([{
                "provides": "server", "clientIdentifier": "machine-a", "name": "Home Plex",
                "owned": True, "accessToken": "owner-token",
                "connections": [{"uri": "http://plex:32400", "local": True, "relay": False, "protocol": "http"}],
            }])
        if path == "/api/v2/home/users":
            return _Response({"users": []})
        if path == "/api/servers/machine-a/shared_servers":
            return _Response({"MediaContainer": {"SharedServer": [{"userID": "248098626", "accessToken": "friend-token"}]}})
        if path == "/api/users":
            return _Response({"MediaContainer": {"User": [{"id": "248098626", "username": "shudai6", "title": "shudai6"}]}})
        return _Response({}, status=404)

    def fake_plex(self, _url, token, **_kwargs):
        return _Plex(str(token), self.plex_state)

    def authed(self, cookie, profile="default"):
        return {"cookie": cookie, "X-Plex-Profile": profile}

    def wait_for_job(self, cookie, profile):
        deadline = time.time() + 5
        while time.time() < deadline:
            value = self.direct("/api/status", profile)
            if not (value.get("job") or {}).get("running"):
                return value
            time.sleep(0.01)
        self.fail("background job did not finish")

    def test_fresh_owner_shared_user_and_second_library_remain_isolated(self):
        from helper.scoped_store import ScopedStore

        with patch("requests.sessions.Session.request", autospec=True, side_effect=self.external_request), \
                patch("helper.plex_recipients.PlexClient", side_effect=self.fake_plex):
            status, headers, _ = self.http("/api/auth/setup", method="POST", body={
                "username": "admin", "password": "safe-password", "confirm_password": "safe-password",
            })
            self.assertEqual(200, status)
            cookie = headers[b"set-cookie"].decode("latin-1").split(";", 1)[0]

            started = self.direct("/api/plex/login/start")
            pin_id = started["pin_id"]
            authorized = self.direct("/api/plex/login/status", pin_id=pin_id)
            self.assertEqual("authorized", authorized["status"])

            self.app.state.profile_runtime.engine("default").plex_factory = lambda cfg: self.fake_plex(cfg["plex_url"], cfg["plex_token"])
            status, _, connected = self.http("/api/plex/login/connect", method="POST", headers=self.authed(cookie), body={"confirm": True, "pin_id": pin_id, "machine": "machine-a"})
            self.assertEqual(200, status)
            self.assertEqual("", connected["section"])

            status, _, owner_library = self.http("/api/plex/profiles/library", method="POST", headers=self.authed(cookie), body={"profile_id": "default", "library_id": "11"})
            self.assertEqual(200, status)
            self.assertEqual("音乐", owner_library["profile"]["library"]["name"])

            people = self.direct("/api/plex/recipients", owner_profile_id="default")
            self.assertEqual(["shudaizi", "shudai6"], [row["username"] for row in people["items"]])
            status, _, choices = self.http("/api/plex/recipients/libraries", method="POST", headers=self.authed(cookie), body={"owner_profile_id": "default", "kind": "shared", "user_id": "248098626"})
            self.assertEqual(["11", "15"], [row["id"] for row in choices["libraries"]])
            status, _, imported = self.http("/api/plex/recipients/shared/import", method="POST", headers=self.authed(cookie), body={"owner_profile_id": "default", "user_id": "248098626", "library_id": "15"})
            self.assertEqual(200, status)
            friend_id = imported["profile"]["id"]
            self.app.state.profile_runtime.engine(friend_id).plex_factory = lambda cfg: self.fake_plex(cfg["plex_url"], cfg["plex_token"])

            owner_store = ScopedStore(self.base, "default")
            owner_store.set("product_settings", {"behavior_enabled": False, "behavior_user": ""})
            status, _, _ = self.http("/api/jobs/daily_preview", method="POST", headers=self.authed(cookie), body={})
            self.assertEqual(200, status)
            owner_status = self.wait_for_job(cookie, "default")
            self.assertTrue(owner_status["daily_plan"]["id"])
            plan_id = owner_status["daily_plan"]["id"]
            status, _, _ = self.http("/api/jobs/daily_apply", method="POST", headers=self.authed(cookie), body={"confirm": True, "plan_id": plan_id})
            self.assertEqual(200, status)
            owner_status = self.wait_for_job(cookie, "default")
            playlist_id = owner_status["daily_published"]["playlist_id"]
            history_length = len(owner_store.get("daily_history"))

            friend_status = self.direct("/api/status", friend_id)
            self.assertIsNone(friend_status["daily_plan"])
            self.assertIsNone(friend_status["daily_published"])
            self.assertEqual("经典音乐", friend_status["active_profile"]["library"]["name"])

            owner_again = self.direct("/api/status", "default")
            self.assertEqual(playlist_id, owner_again["daily_published"]["playlist_id"])

            status, _, second_library = self.http("/api/plex/profiles/library", method="POST", headers=self.authed(cookie, "default"), body={"profile_id": "default", "library_id": "15"})
            self.assertEqual(200, status)
            self.assertEqual("created", second_library["mode"])
            self.assertNotEqual("default", second_library["profile"]["id"])
            self.assertIsNone(ScopedStore(self.base, second_library["profile"]["id"]).get("daily_published_view"))
            self.assertEqual(playlist_id, owner_store.get("daily_managed")["id"])
            self.assertEqual(history_length, len(owner_store.get("daily_history")))

    def test_registry_reinitialization_preserves_legacy_profile_state_exactly(self):
        from helper.profiles import ProfileRegistry
        from helper.scoped_store import ScopedStore

        registry = self.app.state.profiles
        registry.create(name="shudai6", kind="shared", profile_id="shared-248098626", token="friend-token")
        fixtures = {
            "default": {"managed": {"a": {"id": "11"}}, "daily_history": [{"ids": ["1"]}], "cache": {"x": {"data": [1]}}, "snapshots": [{"id": "s1"}]},
            "shared-248098626": {"managed": {"b": {"id": "15"}}, "daily_history": [{"ids": ["2", "3"]}], "cache": {"y": {"data": [2, 3]}}, "snapshots": [{"id": "s2"}, {"id": "s3"}]},
        }
        for profile_id, values in fixtures.items():
            ScopedStore(self.base, profile_id).set_many(values)

        def snapshot(current):
            profiles = {row["id"]: row for row in current.list_public()}
            return {
                profile_id: {
                    "id": profiles[profile_id]["id"],
                    "token_present": profiles[profile_id]["token_present"],
                    "managed_ids": sorted(row["id"] for row in ScopedStore(self.base, profile_id).get("managed", {}).values()),
                    "history": len(ScopedStore(self.base, profile_id).get("daily_history", [])),
                    "cache": len(ScopedStore(self.base, profile_id).get("cache", {})),
                    "snapshots": len(ScopedStore(self.base, profile_id).get("snapshots", [])),
                } for profile_id in fixtures
            }

        before = snapshot(registry)
        after = snapshot(ProfileRegistry(self.base))
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
