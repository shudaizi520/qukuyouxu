from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class GithubWorkflowTests(unittest.TestCase):
    def test_test_workflow_uses_supported_python_versions_and_read_only_permissions(self):
        workflow = (ROOT / ".github/workflows/test.yml").read_text(encoding="utf-8")
        self.assertIn("contents: read", workflow)
        self.assertIn('python-version: ["3.11", "3.12"]', workflow)
        self.assertIn("cache: pip", workflow)
        self.assertIn("unittest discover -s tests -q", workflow)
        self.assertIn("pip install -r requirements.lock", workflow)
        self.assertIn("pip install --no-deps -e .", workflow)
        self.assertIn("python -m pip check", workflow)
        self.assertIn("python tools/check_repository.py", workflow)

    def test_release_workflow_publishes_images_and_a_github_release(self):
        workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        self.assertIn("v*.*.*", workflow)
        self.assertIn("contents: write", workflow)
        self.assertIn("packages: write", workflow)
        self.assertIn("linux/amd64,linux/arm64", workflow)
        self.assertIn("docker/build-push-action@v6", workflow)
        self.assertIn('python tools/check_release_version.py "$GITHUB_REF_NAME"', workflow)
        self.assertIn('gh release create "$GITHUB_REF_NAME"', workflow)
        self.assertIn("--generate-notes", workflow)
        self.assertIn("--latest", workflow)
        self.assertNotIn("build-args: |", workflow)


if __name__ == "__main__":
    unittest.main()
