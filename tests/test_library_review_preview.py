import os
import re
from pathlib import Path

from playwright.sync_api import sync_playwright
from tools.playwright_runtime import prepare_playwright_environment


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src" / "helper" / "static"
PLAYWRIGHT_BROWSERS = ROOT / ".playwright"


def _prepare_browser() -> None:
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(PLAYWRIGHT_BROWSERS))
    prepare_playwright_environment(ROOT)


def _library_page_without_assets() -> str:
    html = (STATIC / "home.html").read_text(encoding="utf-8")
    return re.sub(r"<script\b.*?</script>|<link\b[^>]*>", "", html, flags=re.I | re.S)


def test_review_distinguishes_new_updates_and_unchanged_playlists():
    """A no-op managed playlist must never look like another playlist to create."""
    _prepare_browser()
    review = {
        "id": "theme-plan|base-plan",
        "expired": False,
        "groups": [
            {
                "id": "ktv",
                "title": "KTV金曲",
                "kind": "theme",
                "dimension": "主题精选",
                "count": 737,
                "existing_count": 0,
                "add_count": 737,
                "action": "create",
                "blocked": [],
                "default_selected": True,
            },
            {
                "id": "work",
                "title": "工作陪伴",
                "kind": "theme",
                "dimension": "场景",
                "count": 500,
                "existing_count": 481,
                "add_count": 19,
                "action": "append",
                "blocked": [],
                "default_selected": True,
            },
            {
                "id": "sleep",
                "title": "睡前舒缓",
                "kind": "theme",
                "dimension": "场景",
                "count": 20,
                "existing_count": 20,
                "add_count": 0,
                "action": "unchanged",
                "blocked": [],
                "default_selected": True,
            },
        ],
    }

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.set_content(_library_page_without_assets())
        page.add_style_tag(content=(STATIC / "theme-tokens.css").read_text(encoding="utf-8"))
        page.add_style_tag(content=(STATIC / "product.css").read_text(encoding="utf-8"))
        page.add_style_tag(content=(STATIC / "management-shell.css").read_text(encoding="utf-8"))
        page.add_script_tag(path=str(STATIC / "home.js"))
        page.locator("#workspace").evaluate("node => node.hidden = false")
        page.evaluate("review => renderReview(review, false)", review)

        assert page.locator("#reviewTitle").inner_text() == "本次整理预览"
        assert page.locator('[data-review-kind="create"] input[type=checkbox]').count() == 1
        assert page.locator('[data-review-kind="update"] input[type=checkbox]').count() == 1
        assert page.locator('[data-review-kind="unchanged"] input[type=checkbox]').count() == 0
        assert "新建 · 737 首" in page.locator('[data-review-kind="create"]').inner_text()
        assert "现有 481 首 · 新增 19 首" in page.locator('[data-review-kind="update"]').inner_text()
        assert "现有 20 首 · 无需变化" in page.locator('[data-review-kind="unchanged"]').inner_text()
        assert page.locator("#selectedCount").inner_text() == "已选择：新建 1 个，更新 1 个"
        assert page.locator("#confirmReview").inner_text() == "创建 1 个新歌单并更新 1 个已有歌单"

        page.locator("#selectAll").evaluate(
            "node => { node.checked = false; node.dispatchEvent(new Event('change')); }"
        )
        assert page.locator("#confirmReview").is_disabled()
        assert page.locator("#selectedCount").inner_text() == "尚未选择要执行的变化"
        assert page.locator(".review-table th:nth-child(3)").evaluate(
            "node => getComputedStyle(node).width"
        ) == "220px"
        assert page.locator('[data-review-kind="create"] td:nth-child(2) small').evaluate(
            "node => getComputedStyle(node).display"
        ) == "block"
        assert page.locator('[data-review-kind="unchanged"]').evaluate(
            "node => getComputedStyle(node).opacity"
        ) == "0.68"
        assert page.locator('[data-review-kind="update"] .review-change').evaluate(
            "node => getComputedStyle(node).whiteSpace"
        ) == "nowrap"
        browser.close()


def _install_preview_runtime(page, responses):
    page.evaluate(
        """responses => {
            document.getElementById('workspace').hidden = false;
            window.__previewRequests = [];
            window.request = async url => {
                window.__previewRequests.push(url);
                const match = responses.find(row => url.startsWith(row.prefix));
                if (!match || match.error) throw new Error(match?.error || 'unexpected request');
                return {json: async () => match.body};
            };
            window.post = async () => ({message: 'ok'});
            window.action = async fn => fn();
            window.note = () => {};
            window.PCHUI = {confirm: async () => true};
        }""",
        responses,
    )
    page.add_script_tag(path=str(STATIC / "theme_home.js"))


def test_song_preview_routes_base_groups_to_base_evidence_and_stays_compact():
    _prepare_browser()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.set_content(_library_page_without_assets())
        page.add_style_tag(content=(STATIC / "theme-tokens.css").read_text(encoding="utf-8"))
        page.add_style_tag(content=(STATIC / "management-shell.css").read_text(encoding="utf-8"))
        _install_preview_runtime(page, [{
            "prefix": "/api/base/details",
            "body": {
                "title": "粤语",
                "total": 1,
                "items": [{
                    "track_id": "12", "title": "女儿国", "artist": "布朗熊先森",
                    "album_title": "女儿国", "reason": "QQ单曲详情语种",
                }],
                "next": None,
            },
        }])

        page.evaluate("() => window.openThemeEvidence('base:粤语', false, 'base')")
        page.wait_for_selector("#themeEvidence:not([hidden])")

        requests = page.evaluate("window.__previewRequests")
        assert requests == ["/api/base/details?category_id=base%3A%E7%B2%A4%E8%AF%AD&offset=0&limit=100"]
        assert page.locator("#themeEvidenceTitle").inner_text() == "粤语"
        assert page.locator(".song-preview-row").count() == 1
        assert "女儿国" in page.locator(".song-preview-row").inner_text()
        assert "布朗熊先森" in page.locator(".song-preview-row").inner_text()
        assert "女儿国" in page.locator(".song-preview-row").inner_text()
        assert page.locator(".song-preview-source-details").count() == 0
        assert page.locator("#themeEvidence").evaluate("node => getComputedStyle(node).position") == "fixed"
        assert page.locator("#themeEvidenceRows").evaluate("node => getComputedStyle(node).overflowY") == "auto"
        for theme, expected in {
            "light": ("rgb(255, 255, 255)", "rgb(32, 37, 40)"),
            "warm": ("rgb(251, 248, 241)", "rgb(41, 39, 36)"),
            "night": ("rgb(35, 40, 41)", "rgb(237, 237, 237)"),
        }.items():
            page.evaluate("theme => document.documentElement.dataset.appearance = theme", theme)
            colors = page.locator("#themeEvidence").evaluate(
                "node => { const style = getComputedStyle(node); return [style.backgroundColor, style.color]; }"
            )
            assert tuple(colors) == expected
        browser.close()


def test_song_preview_is_dense_keeps_shared_sources_once_and_auto_loads_every_song():
    _prepare_browser()
    shared_origins = [{"title": "KTV参考歌单", "basis": "主题来源"}]
    first_page = [
        {
            "id": str(index), "title": f"歌曲 {index}", "artist": "歌手",
            "album": "专辑", "reason": "歌名、歌手和版本信息匹配",
            "origins": shared_origins,
        }
        for index in range(1, 101)
    ]
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.set_content(_library_page_without_assets())
        page.add_style_tag(content=(STATIC / "theme-tokens.css").read_text(encoding="utf-8"))
        page.add_style_tag(content=(STATIC / "management-shell.css").read_text(encoding="utf-8"))
        _install_preview_runtime(page, [{"prefix": "/unused", "body": {}}])
        page.evaluate(
            """payload => {
                window.__previewRequests = [];
                window.request = async url => {
                    window.__previewRequests.push(url);
                    const second = url.includes('offset=100');
                    return {json: async () => second
                        ? {title: 'KTV金曲', total: 101, items: [{
                            id: '101', title: '最后一首', artist: '歌手', album: '专辑',
                            reason: '歌名、歌手和版本信息匹配', origins: payload.origins,
                          }], next: null}
                        : {title: 'KTV金曲', total: 101, items: payload.items, next: 100}};
                };
            }""",
            {"items": first_page, "origins": shared_origins},
        )

        page.evaluate("() => window.openThemeEvidence('ktv', true, 'theme')")
        page.wait_for_function("document.querySelectorAll('.song-preview-row').length === 100")

        assert page.locator(".song-preview-sources").count() == 1
        assert page.locator(".song-preview-sources").is_visible()
        assert page.locator(".song-preview-sources summary").inner_text() == "参考来源（1）"
        assert page.locator(".song-preview-source-details").count() == 0
        assert page.locator(".song-preview-exclude").count() == 0
        assert page.locator("#themeEvidenceProgress").inner_text() == "已显示 100 / 101 首"
        assert page.locator(".song-preview-row").first.evaluate(
            "node => node.getBoundingClientRect().height"
        ) <= 64

        page.locator("#themeEvidenceRows").evaluate(
            "node => { node.scrollTop = node.scrollHeight; node.dispatchEvent(new Event('scroll')); }"
        )
        page.wait_for_function("document.querySelectorAll('.song-preview-row').length === 101")
        assert page.locator("#themeEvidenceProgress").inner_text() == "已显示全部 101 首"
        assert page.evaluate("window.__previewRequests") == [
            "/api/themes/evidence?category_id=ktv&offset=0&limit=100&added_only=true",
            "/api/themes/evidence?category_id=ktv&offset=100&limit=100&added_only=true",
        ]
        browser.close()


def test_song_preview_clears_previous_results_before_showing_a_new_error():
    _prepare_browser()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.set_content(_library_page_without_assets())
        _install_preview_runtime(page, [
            {
                "prefix": "/api/themes/evidence",
                "body": {
                    "title": "睡前舒缓", "total": 1,
                    "items": [{"id": "9", "title": "旧内容", "artist": "旧歌手", "origins": []}],
                    "next": None,
                },
            },
            {"prefix": "/api/base/details", "error": "基础分类预览已失效"},
        ])

        page.evaluate("() => window.openThemeEvidence('sleep', false, 'theme')")
        page.wait_for_selector("text=旧内容")
        page.evaluate("() => window.openThemeEvidence('base:粤语', false, 'base')")
        page.wait_for_selector("#themeEvidenceState:not([hidden])")

        assert page.locator("text=旧内容").count() == 0
        assert page.locator("#themeEvidenceState").inner_text() == "基础分类预览已失效"
        assert page.locator("#themeEvidenceRows").count() == 1
        assert page.locator("#themeEvidenceRows").inner_text() == ""
        browser.close()


def test_review_passes_group_kind_to_the_song_preview():
    _prepare_browser()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.set_content(_library_page_without_assets())
        page.add_script_tag(path=str(STATIC / "home.js"))
        page.evaluate(
            """() => {
                document.getElementById('workspace').hidden = false;
                window.__openedPreview = null;
                window.action = async fn => fn();
                window.openThemeEvidence = (...args) => { window.__openedPreview = args; };
                renderReview({id: 'review-base', expired: false, groups: [{
                    id: 'base:粤语', title: '粤语', kind: 'base', dimension: '语种',
                    count: 20, existing_count: 20, add_count: 0, action: 'unchanged',
                    blocked: [], default_selected: false,
                }]}, false);
            }"""
        )

        page.locator('[data-review-kind="unchanged"] .evidence-button').click()
        assert page.evaluate("window.__openedPreview") == ["base:粤语", False, "base"]
        browser.close()
