from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ContainerContractTests(unittest.TestCase):
    def test_dockerfile_has_a_small_non_root_runtime_contract(self):
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("FROM python:3.12.14-slim-bookworm", dockerfile)
        self.assertIn("USER qukuyouxu", dockerfile)
        self.assertIn("DATA_ROOT=/data", dockerfile)
        self.assertIn("EXPOSE 9511", dockerfile)
        self.assertIn("HEALTHCHECK", dockerfile)
        self.assertIn('CMD ["qukuyouxu"]', dockerfile)
        self.assertIn("COPY requirements.lock", dockerfile)
        self.assertIn("pip install --no-cache-dir -r requirements.lock", dockerfile)
        self.assertIn("pip install --no-cache-dir --no-deps .", dockerfile)
        self.assertIn("APP_UID=10001", dockerfile)
        self.assertIn("APP_GID=10001", dockerfile)

    def test_compose_persists_one_data_directory_without_hard_coded_credentials(self):
        compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
        self.assertIn("/data", compose)
        self.assertIn("9511:9511", compose)
        self.assertIn("restart: unless-stopped", compose)
        self.assertNotIn("PLEX_TOKEN", compose)
        self.assertNotIn("qqmusic_key", compose)
        self.assertIn("ADMIN_TOKEN: ${ADMIN_TOKEN:-}", compose)
        self.assertNotIn("SETUP_TOKEN", compose)


if __name__ == "__main__":
    unittest.main()
