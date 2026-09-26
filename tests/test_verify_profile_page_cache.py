from tools.verify_profile_page_cache import SAFE_NAVIGATION_PATHS, is_allowed_request


def test_profile_cache_verifier_is_read_only_except_for_login():
    assert is_allowed_request("GET", "http://preview/api/status")
    assert is_allowed_request("POST", "http://preview/api/auth/login")
    assert not is_allowed_request("POST", "http://preview/api/playlists/favorite/ensure")
    assert not is_allowed_request("DELETE", "http://preview/api/external/sources/1")


def test_profile_cache_verifier_visits_only_known_read_only_pages():
    assert SAFE_NAVIGATION_PATHS == ("/settings", "/external", "/mixes", "/status")
