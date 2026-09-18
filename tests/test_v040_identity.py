import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


class _Session:
    def __init__(self):
        self.headers = {}
        self.trust_env = True


class PlexIdentityV040Tests(unittest.TestCase):
    def test_plex_tv_and_pms_share_installation_identity_and_release(self):
        from helper import __version__
        from helper.clients import PlexClient
        from helper.plex_identity import build_plex_auth_url, client_id, plex_headers
        from helper.store import Store

        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root))
            expected_id = client_id(store)
            self.assertEqual(expected_id, client_id(store))
            self.assertTrue(expected_id.startswith("plex-playlist-helper-"))

            official = plex_headers(store, "official-token")
            self.assertEqual(__version__, official["X-Plex-Version"])
            self.assertEqual(expected_id, official["X-Plex-Client-Identifier"])
            self.assertEqual("official-token", official["X-Plex-Token"])

            session = _Session()
            PlexClient("http://127.0.0.1:32400", "12345678", session=session, store=store)
            self.assertEqual(__version__, session.headers["X-Plex-Version"])
            self.assertEqual(expected_id, session.headers["X-Plex-Client-Identifier"])
            self.assertEqual("application/xml", session.headers["Accept"])

            auth = urlsplit(build_plex_auth_url(expected_id, "abcd-1234"))
            query = parse_qs(auth.fragment.split("?", 1)[1])
            self.assertEqual([__version__], query["context[device][version]"])
            self.assertEqual([expected_id], query["clientID"])

    def test_public_package_version_has_semantic_release_format(self):
        from helper import __version__

        self.assertRegex(__version__, r"^\d+\.\d+\.\d+$")


if __name__ == "__main__":
    unittest.main()
