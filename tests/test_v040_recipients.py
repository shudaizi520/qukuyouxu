import json
import tempfile
import unittest
from pathlib import Path


class _Response:
    def __init__(self, data, status=200):
        self._data = data
        self.status_code = status
        self.content = json.dumps(data).encode()

    def json(self):
        return self._data


class _XmlResponse:
    def __init__(self, text, status=200):
        self.status_code = status
        self.content = text.encode()
        self.headers = {"Content-Type": "application/xml; charset=utf-8"}

    def json(self):
        raise ValueError("Plex legacy endpoints ignore Accept: application/json")


class _Session:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []
        self.trust_env = True

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        value = self.routes[(method, url)]
        return value if hasattr(value, "status_code") else _Response(value)

    def close(self):
        pass


class _Plex:
    def __init__(self, machine="machine-a"):
        self.machine = machine
        self.reads = []

    def identity(self):
        self.reads.append("identity")
        return {"machine": self.machine, "server": "Main"}

    def sections(self):
        self.reads.append("sections")
        return [{"id": "15", "title": "Music"}]

    def playlists(self):
        self.reads.append("playlists")
        return []


class PlexRecipientsV040Tests(unittest.TestCase):
    def setUp(self):
        from helper.profiles import ProfileRegistry
        from helper.store import Store

        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name))
        self.registry = ProfileRegistry(self.store)
        self.registry.update(
            "default",
            name="Owner",
            token="owner-secret",
            account={"id": "1", "username": "owner"},
            server={"machine": "machine-a", "name": "Main", "url": "https://plex.local:32400"},
            library={"id": "15", "name": "Music"},
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_people_list_marks_existing_profiles_and_explains_the_source(self):
        from helper.plex_recipients import PlexRecipientService

        self.registry.create(
            name="friend",
            kind="shared",
            profile_id="shared-42",
            token="friend-secret",
            account={"id": "42", "username": "friend"},
            server={"machine": "machine-a", "name": "Main", "url": "https://plex.local:32400"},
            library={"id": "15", "name": "Music"},
        )
        service = PlexRecipientService(self.store, self.registry, session=_Session({}))
        service.list_home_users = lambda _owner: [
            {"id": "2", "title": "Kid", "username": "kid"}
        ]
        service.list_shared_users = lambda _owner: [
            {"id": "42", "title": "Friend", "username": "friend"}
        ]

        result = service.list_people("default")

        self.assertEqual([], result["warnings"])
        self.assertEqual(
            [
                {
                    "id": "2", "title": "Kid", "username": "kid",
                    "kind": "home", "kind_label": "家庭成员", "existing_profile_id": "",
                    "archived_profile_id": "",
                },
                {
                    "id": "42", "title": "Friend", "username": "friend",
                    "kind": "shared", "kind_label": "共享朋友",
                    "existing_profile_id": "shared-42",
                    "archived_profile_id": "",
                },
            ],
            result["items"],
        )

    def test_people_list_keeps_working_when_one_plex_source_is_unavailable(self):
        from helper.plex_recipients import PlexRecipientService, RecipientUnsupported

        service = PlexRecipientService(self.store, self.registry, session=_Session({}))

        def unavailable(_owner):
            raise RecipientUnsupported("当前账户没有 Plex Home 管理权限")

        service.list_home_users = unavailable
        service.list_shared_users = lambda _owner: [
            {"id": "42", "title": "Friend", "username": "friend"}
        ]

        result = service.list_people("default")

        self.assertEqual(["家庭成员：当前账户没有 Plex Home 管理权限"], result["warnings"])
        self.assertEqual("shared", result["items"][0]["kind"])

    def test_home_user_import_switches_token_then_performs_read_only_validation(self):
        from helper.plex_recipients import PlexRecipientService

        session = _Session({
            ("GET", "https://plex.tv/api/v2/home/users"): [
                {"id": 2, "title": "Kid", "username": "kid", "admin": False}
            ],
            ("POST", "https://plex.tv/api/v2/home/users/2/switch"): {
                "id": 22, "title": "Kid", "username": "kid", "authToken": "home-secret-token"
            },
        })
        plex = _Plex()
        service = PlexRecipientService(
            self.store, self.registry, session=session,
            client_factory=lambda url, token: plex,
        )
        listed = service.list_home_users("default")
        self.assertEqual("Kid", listed[0]["title"])
        self.assertNotIn("owner-secret", repr(listed))

        profile = service.import_home_user("default", "2", "15")
        self.assertEqual("home", profile["kind"])
        self.assertEqual("machine-a", profile["server"]["machine"])
        self.assertNotIn("home-secret-token", repr(profile))
        self.assertEqual(["identity", "sections", "playlists"], plex.reads)
        for method, url, kwargs in session.calls:
            self.assertFalse(kwargs["allow_redirects"])
            self.assertNotIn("home-secret-token", url)

    def test_shared_user_import_matches_share_token_by_user_id(self):
        from helper.plex_recipients import PlexRecipientService

        session = _Session({
            ("GET", "https://plex.tv/api/servers/machine-a/shared_servers"): {
                "MediaContainer": {"SharedServer": [
                    {"userID": "42", "accessToken": "friend-secret-token"}
                ]}
            },
            ("GET", "https://plex.tv/api/users"): {
                "MediaContainer": {"User": [
                    {"id": "42", "username": "friend", "title": "Friend"}
                ]}
            },
        })
        plex = _Plex()
        service = PlexRecipientService(
            self.store, self.registry, session=session,
            client_factory=lambda url, token: plex,
        )
        listed = service.list_shared_users("default")
        self.assertEqual([{"id": "42", "username": "friend", "title": "Friend"}], listed)
        self.assertNotIn("friend-secret-token", repr(listed))

        profile = service.import_shared_user("default", "42", "15")
        self.assertEqual("shared", profile["kind"])
        self.assertEqual("42", profile["account"]["id"])
        self.assertNotIn("friend-secret-token", repr(profile))

    def test_shared_users_accept_real_plex_xml_even_when_json_was_requested(self):
        from helper.plex_recipients import PlexRecipientService

        session = _Session({
            ("GET", "https://plex.tv/api/servers/machine-a/shared_servers"): _XmlResponse(
                '<MediaContainer size="1"><SharedServer userID="42" '
                'accessToken="friend-secret-token" username="friend" name="Friend"/>'
                '</MediaContainer>'
            ),
            ("GET", "https://plex.tv/api/users"): _XmlResponse(
                '<MediaContainer size="1"><User id="42" username="friend" '
                'title="Friend"/></MediaContainer>'
            ),
        })
        service = PlexRecipientService(
            self.store, self.registry, session=session,
            client_factory=lambda url, token: _Plex(),
        )

        listed = service.list_shared_users("default")

        self.assertEqual([{"id": "42", "username": "friend", "title": "Friend"}], listed)
        self.assertNotIn("friend-secret-token", repr(listed))

    def test_machine_mismatch_does_not_create_profile(self):
        from helper.plex_recipients import PlexRecipientService, RecipientUnsupported

        session = _Session({
            ("POST", "https://plex.tv/api/v2/home/users/2/switch"): {
                "id": 22, "title": "Kid", "username": "kid", "authToken": "home-secret-token"
            },
        })
        service = PlexRecipientService(
            self.store, self.registry, session=session,
            client_factory=lambda url, token: _Plex("wrong-machine"),
        )
        with self.assertRaises(RecipientUnsupported):
            service.import_home_user("default", "2", "15")
        self.assertEqual(1, len(self.registry.list_public()))


if __name__ == "__main__":
    unittest.main()
