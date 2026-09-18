import unittest


class ReleaseVersionTests(unittest.TestCase):
    def test_accepts_exact_v_prefixed_application_version(self):
        from tools.check_release_version import check_release_version

        self.assertEqual("1.0.0", check_release_version("v1.0.0", "1.0.0"))

    def test_rejects_mismatched_or_malformed_tags(self):
        from tools.check_release_version import check_release_version

        for tag in ("v0.9.9", "1.0.0", "v1.0", "latest"):
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                check_release_version(tag, "1.0.0")


if __name__ == "__main__":
    unittest.main()
