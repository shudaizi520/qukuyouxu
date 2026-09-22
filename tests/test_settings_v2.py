from pathlib import Path


STATIC = Path(__file__).parents[1] / "src/helper/static"


def test_settings_has_one_user_control_surface():
    html = (STATIC / "settings.html").read_text()
    for label in ("Plex 连接", "用户管理", "播放学习", "每日推荐", "智能歌单",
                  "复制地址", "打开 Webhook"):
        assert label in html
    for obsolete in ("批量每日推荐", "每日推荐（全部账户）", 'id="plexProfile"',
                     'id="dailyAutomationEnabled"', 'id="smartAutomationEnabled"'):
        assert obsolete not in html


def test_settings_toggle_posts_one_scoped_control_and_rolls_back():
    js = (STATIC / "settings.js").read_text()
    assert "/api/plex/profiles/control" in js
    assert "profile_id:profile.id" in js
    assert "input.checked=!next" in js
    assert "/api/profiles/daily/batch-preview" not in js
    assert "/api/profiles/daily/batch-publish" not in js
    assert "/api/profiles/daily/schedule" not in js


def test_business_page_does_not_write_second_enablement_switch():
    js = (STATIC / "contextual-settings.js").read_text()
    assert "/api/plex/profiles/control" in js
    assert "next.daily={enabled:" not in js
    assert "next.smart={enabled:" not in js
