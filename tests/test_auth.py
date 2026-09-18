import tempfile
import unittest
from pathlib import Path


class AuthenticationTests(unittest.TestCase):
    def test_account_password_rotation_and_session_revocation(self):
        from helper.auth import AuthManager
        from helper.store import Store

        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root))
            auth = AuthManager(store)

            self.assertTrue(auth.setup_required())
            self.assertEqual("admin", auth.create_account("admin", "correct-horse"))
            self.assertFalse(auth.setup_required())
            self.assertTrue(auth.verify("admin", "correct-horse"))
            self.assertFalse(auth.verify("admin", "wrong-password"))

            first_token, _ = auth.create_session("admin")
            self.assertEqual("admin", auth.session_user(first_token))
            auth.change_password("admin", "correct-horse", "new-correct-horse")

            self.assertIsNone(auth.session_user(first_token))
            self.assertFalse(auth.verify("admin", "correct-horse"))
            self.assertTrue(auth.verify("admin", "new-correct-horse"))
            replacement_token, _ = auth.create_session("admin")
            auth.revoke_session(replacement_token)
            self.assertIsNone(auth.session_user(replacement_token))
            self.assertNotIn("correct-horse", repr(store.get("auth_credentials")))

    def test_account_validation_rejects_weak_credentials(self):
        from helper.auth import AuthManager
        from helper.store import Store

        with tempfile.TemporaryDirectory() as root:
            auth = AuthManager(Store(Path(root)))
            with self.assertRaises(ValueError):
                auth.create_account("a", "correct-horse")
            with self.assertRaises(ValueError):
                auth.create_account("admin", "short")


if __name__ == "__main__":
    unittest.main()
