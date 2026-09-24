from pathlib import Path

from tools.capture_theme_matrix import _prepare_screenshot, compare_images, matrix_cases
from PIL import Image


def test_matrix_contains_six_pages_three_themes_and_two_viewports():
    cases = matrix_cases()

    assert len(cases) == 36
    assert {case.theme for case in cases} == {"light", "warm", "night"}
    assert {case.page for case in cases} == {
        "settings",
        "external",
        "mixes",
        "library",
        "status",
        "appearance",
    }
    assert {case.viewport for case in cases} == {(1366, 768), (1920, 1080)}


def test_pixel_comparison_writes_a_diff_and_reports_changed_ratio(tmp_path: Path):
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    diff = tmp_path / "diff.png"
    Image.new("RGB", (4, 4), "white").save(first)
    changed = Image.new("RGB", (4, 4), "white")
    changed.putpixel((0, 0), (0, 0, 0))
    changed.save(second)

    assert compare_images(first, second, diff, threshold=12) == 1 / 16
    assert diff.exists()


def test_screenshot_preparation_waits_for_async_page_data():
    calls = []

    class PageStub:
        def wait_for_load_state(self, state):
            calls.append(("state", state))

        def wait_for_timeout(self, milliseconds):
            calls.append(("timeout", milliseconds))

    _prepare_screenshot(PageStub())

    assert calls == [("state", "networkidle"), ("timeout", 150)]
