from html.parser import HTMLParser
from pathlib import Path
import re


STATIC = Path(__file__).resolve().parents[1] / "src/helper/static"


class _Markup(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack = []
        self.nodes = {}

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        node_id = values.get("id")
        if node_id:
            self.nodes[node_id] = {
                "tag": tag,
                "attrs": values,
                "parent_id": next((node for node in reversed(self.stack) if node), None),
            }
        self.stack.append(node_id)

    def handle_endtag(self, _tag):
        if self.stack:
            self.stack.pop()


def _rule(css, selector):
    cleaned = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    result = {}
    for selectors, body in re.findall(r"([^{}]+)\{([^{}]*)\}", cleaned):
        if selector not in [value.strip() for value in selectors.split(",")]:
            continue
        result.update(part.strip().split(":", 1) for part in body.split(";") if ":" in part)
    return result


def test_import_entry_is_an_icon_action_beside_playlist_controls():
    page = (STATIC / "playlists.html").read_text(encoding="utf-8")
    markup = _Markup()
    markup.feed(page)

    button = markup.nodes["importHubButton"]
    assert button["tag"] == "button"
    assert button["parent_id"] == "playlistHeadingActions"
    assert button["attrs"]["aria-label"] == "导入歌单"
    assert 'href="#icon-import-down"' in page


def test_playlist_shell_uses_tonal_layers_instead_of_divider_lines():
    css = (STATIC / "design-system.css").read_text(encoding="utf-8")
    tokens = (STATIC / "theme-tokens.css").read_text(encoding="utf-8")

    sidebar = _rule(css, "body[data-view=playlists] .playlist-sidebar")

    assert "--app-rail:#f2f4f3" in tokens and "--app-main:#ffffff" in tokens
    assert "--app-rail:#eee8dc" in tokens and "--app-main:#fbf8f1" in tokens
    assert "--app-rail:#171717" in tokens and "--app-main:#202020" in tokens
    assert sidebar["background"] == "var(--app-rail)"
    assert sidebar["border"] == "0"
    assert css.count("background:linear-gradient(to right,var(--app-rail)") == 2
    assert "--playlist-player-bg" not in css


def test_import_workspace_is_full_width_flat_and_matches_playlist_rows():
    page = (STATIC / "external.html").read_text(encoding="utf-8")
    css = (STATIC / "external-workspace.css").read_text(encoding="utf-8")

    shell = _rule(css, ".pch-embedded body[data-view=external] .external-shell")
    import_card = _rule(css, "body[data-view=external] .external-import-card")
    workspace = _rule(css, "body[data-view=external] .external-workspace")
    commands = _rule(css, "body[data-view=external] .external-command-bar")
    track = _rule(css, "body[data-view=external] .external-track-row")

    assert shell["max-width"] == "none"
    for surface in (import_card, workspace):
        assert surface["border"] == "0"
        assert surface["border-radius"] == "0"
        assert surface["background"] == "transparent"
        assert surface["box-shadow"] == "none"
    assert commands["border"] == "0"
    assert track["border"] == "0"
    assert "grid-template-columns:44px minmax(220px,1.3fr) minmax(140px,.8fr) minmax(170px,1fr) 64px" in css
    assert 'id="trackActionHeading"' not in page
    assert 'id="auditionPlayer"' not in page


def test_import_results_do_not_create_audition_controls():
    script = (STATIC / "external.js").read_text(encoding="utf-8")

    assert "function auditionButton" not in script
    assert "external-preview-button" not in script
    assert "pch-player-preview" not in script
    assert "stopAudition" not in script


def test_matched_import_rows_send_a_normal_player_queue_to_the_parent():
    external = (STATIC / "external.js").read_text(encoding="utf-8")
    playlists = (STATIC / "playlists.js").read_text(encoding="utf-8")

    assert "pch-play-imported-queue" in external
    assert "row.tabIndex=0" in external
    assert "event.key==='Enter'||event.key===' '" in external
    assert "pch-play-imported-queue" in playlists
    assert "profileId!==loadedProfileId" in playlists
    assert "playlistPlayer.playAt(queue,index,{kind:'external',key:sourceId,profileId:loadedProfileId})" in playlists
    assert "pch-player-preview" not in external
    assert "/api/external/" not in external.split("function playImportedTrack", 1)[-1].split("function renderTracks", 1)[0]
