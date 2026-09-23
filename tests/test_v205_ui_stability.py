from pathlib import Path
import re


STATIC = Path(__file__).resolve().parents[1] / "src/helper/static"


def css_rules(selector):
    css = re.sub(r"/\*.*?\*/", "", (STATIC / "product.css").read_text(), flags=re.S)
    result = {}
    for names, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        if selector in [name.strip() for name in names.split(",")]:
            result.update(
                part.strip().split(":", 1)
                for part in body.split(";") if ":" in part
            )
    return result


def test_result_tabs_keep_the_viewport_width_stable_when_scrollbar_changes():
    assert css_rules("html")["scrollbar-gutter"] == "stable"


def test_playlist_hearts_never_draw_a_large_circle_or_hover_shadow():
    base = css_rules(".playlist-heart")
    hover = css_rules(".playlist-heart:not([aria-pressed=true]):hover")
    pressed = css_rules(".playlist-heart[aria-pressed=true]")
    focus = css_rules(".playlist-heart:focus-visible")

    assert base["border-radius"] == "0"
    assert base["background"] == "transparent"
    assert base["box-shadow"] == "none"
    for state in (hover, pressed, focus):
        assert state["background"] == "transparent"
        assert state["box-shadow"] == "none"


def test_playlist_rerenders_reuse_loaded_artwork_in_the_same_profile_scope():
    artwork = (STATIC / "playlist-artwork.js").read_text()
    sections = (STATIC / "playlist-sections.js").read_text()
    playlists = (STATIC / "playlists.js").read_text()

    assert "function create(item,variant='card',existingNode=null)" in artwork
    assert "const node=existingNode||document.createElement('span')" in artwork
    assert "if(!existingNode)placeholder(node)" in artwork
    assert "function setItems(items,nextScope='')" in sections
    assert "createArtwork(item,'sidebar',view.cover)" in sections
    assert "createArtwork(item,'card',view.cover)" in sections
    assert "playlistSections.setItems(playlists," in playlists
    assert "createArtwork:(item,variant,node)=>playlistArtwork.create(item,variant,node)" in playlists


def test_artwork_reuse_scope_includes_the_full_plex_identity_and_revision():
    playlists = (STATIC / "playlists.js").read_text()
    sections = (STATIC / "playlist-sections.js").read_text()

    assert "function profileArtworkScope(id)" in playlists
    assert "p.account.id,p.server.machine,p.library.id,p.created_at" in playlists
    assert ":'';" in playlists.split("function profileArtworkScope(id)", 1)[1].split("const playlistArtwork", 1)[0]
    assert "playlistSections.setItems(playlists,profileArtworkScope(loadedProfileId))" in playlists
    assert "if(!scope||scope!==selectedScope)" in sections
    returning = playlists.split("async function returnFromWorkspace()", 1)[1].split(
        "function openWorkspacePage", 1
    )[0]
    assert "await loadProfiles()" in returning
    assert returning.index("await loadProfiles()") < returning.index("await loadPlaylists(")


def test_interrupted_artwork_loads_are_invalidated_before_node_reuse():
    artwork = (STATIC / "playlist-artwork.js").read_text()

    reset = artwork.split("function reset(nextProfile)", 1)[1].split(
        "return {create", 1
    )[0]
    assert "const interrupted=new Set()" in reset
    assert "task.image.remove()" in reset
    assert "delete node.dataset.coverIds" in reset
    assert "node.children[index]?.getAttribute('src')===artworkUrl(id,profile)" in artwork


def test_library_playlist_rows_have_balanced_horizontal_insets():
    rules = css_rules("body[data-view=library] .managed-playlist-row")

    assert rules["padding"] == "14px 20px"
    assert css_rules("body[data-view=library] .managed-playlist-more .managed-playlist-actions")["right"] == "20px"


def test_truenas_install_uses_mutable_latest_tag_for_one_click_updates():
    guide = (STATIC.parents[2] / "docs/install/truenas.md").read_text()

    assert "ghcr.io/shudaizi520/qukuyouxu:latest" in guide
    assert "右侧" in guide and "更新" in guide
