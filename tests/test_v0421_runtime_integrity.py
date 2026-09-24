import re
import sys
import ast
import types
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def _specificity(selector):
    ids = selector.count("#")
    classes = len(re.findall(r"\.[\w-]+|:(?!:)[\w-]+", selector))
    tags = len(re.findall(r"(?:^|[\s>,+~])([a-z][\w-]*)", selector))
    return ids, classes, tags


def _matches_logout_hover(selector):
    selector = selector.strip()
    # The unified menu has a separate, theme-variable hover treatment.
    # This legacy contrast check covers the standalone topbar button only.
    if ".topbar-menu-picker" in selector:
        return False
    if "[disabled]" in selector and ":not(:disabled)" not in selector:
        return False
    if any(token in selector for token in (" nav ", " a", ".primary", ".secondary", ".danger")):
        return False
    return all(token in selector for token in (".topbar", ".logout", ":hover"))


def _logout_hover_colors(css):
    winners = {}
    order = 0
    for selectors, declarations in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        for selector in selectors.split(","):
            if not _matches_logout_hover(selector):
                continue
            score = _specificity(selector)
            for prop, value in re.findall(r"([\w-]+)\s*:\s*([^;]+)", declarations):
                if prop not in ("color", "background", "background-color"):
                    continue
                key = "background" if prop.startswith("background") else prop
                candidate = (score, order, value.strip())
                if key not in winners or candidate[:2] >= winners[key][:2]:
                    winners[key] = candidate
            order += 1
    return {key: value[2] for key, value in winners.items()}


def _luminance(color):
    raw = color.lstrip("#")
    if len(raw) == 3:
        raw = "".join(char * 2 for char in raw)
    channels = [int(raw[index:index + 2], 16) / 255 for index in (0, 2, 4)]
    channels = [value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4 for value in channels]
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def _resolve_default_theme_color(value):
    css = (ROOT / "src/helper/static/theme-tokens.css").read_text(encoding="utf-8")
    default = re.search(r"html\{([^}]*)\}", css, re.S).group(1)
    tokens = dict(re.findall(r"(--[\w-]+)\s*:\s*([^;]+)", default))
    while value.startswith("var("):
        value = tokens[value[4:-1].strip()].strip()
    return value


class RuntimeIntegrityV0421Tests(unittest.TestCase):
    def test_logout_hover_keeps_accessible_text_contrast(self):
        css = (ROOT / "src/helper/static/product.css").read_text(encoding="utf-8")
        colors = _logout_hover_colors(css)

        foreground = _luminance(_resolve_default_theme_color(colors["color"]))
        background = _luminance(_resolve_default_theme_color(colors["background"]))
        contrast = (max(foreground, background) + 0.05) / (min(foreground, background) + 0.05)

        self.assertGreaterEqual(contrast, 4.5, colors)

    def test_served_pages_do_not_reference_blocked_static_assets(self):
        web = ast.parse((ROOT / "src/helper/web.py").read_text(encoding="utf-8"))
        allowed = None
        for node in ast.walk(web):
            if isinstance(node, ast.FunctionDef) and node.name == "static":
                comparison = next(item for item in ast.walk(node) if isinstance(item, ast.Compare))
                allowed = set(ast.literal_eval(comparison.comparators[0]))
                break
        self.assertIsNotNone(allowed)

        referenced = set()
        for name in ("daily.html", "home.html", "status.html", "mixes.html", "settings.html"):
            html = (ROOT / "src/helper/static" / name).read_text(encoding="utf-8")
            referenced.update(re.findall(r"/static/([\w.-]+)", html))

        self.assertEqual(set(), referenced - allowed)

    def test_mix_preview_explains_truncation_before_the_song_list(self):
        script = (ROOT / "src/helper/static/mixes.js").read_text(encoding="utf-8")
        function = script[script.index("function renderPlan"):script.index("function renderCardState")]

        explanation = function.index("下面仅展示前 10 首")
        list_insert = function.index("result.append(list)")

        self.assertLess(explanation, list_insert)

    def test_web_does_not_register_shadow_legacy_route_stacks(self):
        tree = ast.parse((ROOT / "src/helper/web.py").read_text(encoding="utf-8"))
        imported_modules = {
            node.module for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.level == 1
        }

        self.assertTrue({"extra_web", "single_web"}.issubset(imported_modules))
        self.assertTrue(
            {"workflow_web", "theme_web", "qq_auth_web"}.isdisjoint(imported_modules),
            "这些旧路由已由 extra_web → daily_mix_v036 → workflow_v0317 统一注册；重复注册会产生同路径影子处理器",
        )

    def test_current_route_stack_has_no_duplicate_method_and_path(self):
        fastapi = types.ModuleType("fastapi")
        fastapi.Request = object
        responses = types.ModuleType("fastapi.responses")
        for name in ("Response", "HTMLResponse", "JSONResponse", "FileResponse", "RedirectResponse"):
            setattr(responses, name, type(name, (), {}))
        module_patch = patch.dict(sys.modules, {"fastapi": fastapi, "fastapi.responses": responses})
        module_patch.start()
        self.addCleanup(module_patch.stop)
        from helper.extra_web import attach_routes

        class State:
            pass

        class Routes:
            def __init__(self):
                self.state = State()
                self.items = []

            def _register(self, method, path):
                def decorator(handler):
                    self.items.append((method, path, handler.__module__, handler.__name__))
                    return handler
                return decorator

            def get(self, path):
                return self._register("GET", path)

            def post(self, path):
                return self._register("POST", path)

        class Store:
            def __init__(self):
                self.values = {}

            def get(self, key, default=None):
                return self.values.get(key, default)

            def set(self, key, value):
                self.values[key] = value

            def set_many(self, values):
                self.values.update(values)

        class Cookies:
            def update(self, *_args, **_kwargs):
                pass

        engine = types.SimpleNamespace(
            qq=types.SimpleNamespace(session=types.SimpleNamespace(cookies=Cookies())),
            _preview=lambda force_sources=False: {},
        )
        routes = Routes()
        attach_routes(routes, Store(), engine, lambda _request: None, lambda: None)
        counts = Counter((method, path) for method, path, _module, _name in routes.items)

        self.assertEqual({}, {key: count for key, count in counts.items() if count > 1})


if __name__ == "__main__":
    unittest.main()
