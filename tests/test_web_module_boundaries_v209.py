import ast
import asyncio
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "src/helper/web.py"
ROTATION = ROOT / "src/helper/rotation.py"
ENGINE = ROOT / "src/helper/engine.py"
STORE = ROOT / "src/helper/store.py"
WORKFLOW = ROOT / "src/helper/workflow_v0317.py"


def _function(tree, name):
    return next(node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name)


def _route_paths(function):
    paths = set()
    for node in ast.walk(function):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call) or not decorator.args:
                continue
            value = decorator.args[0]
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                paths.add(value.value)
    return paths


def _imported_names(tree):
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            names.update(alias.asname or alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            names.update(alias.asname or alias.name for alias in node.names)
    return names


def _assigns_attribute(tree, owner, attribute):
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            if (
                isinstance(target, ast.Attribute)
                and target.attr == attribute
                and isinstance(target.value, ast.Attribute)
                and target.value.attr == owner
            ):
                return True
    return False


def _class_has_method(tree, class_name, method_name):
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)
    return any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == method_name for node in cls.body)


def test_web_create_app_composes_auth_and_management_modules():
    tree = ast.parse(WEB.read_text(encoding="utf-8"))
    create = _function(tree, "create_app")
    routes = _route_paths(create)

    assert "/api/auth/login" not in routes
    assert "/api/settings" not in routes
    assert {"attach_auth_routes", "attach_management_routes"} <= _imported_names(tree)


def test_daily_policy_has_an_explicit_entry_without_monkey_patching():
    tree = ast.parse(ROTATION.read_text(encoding="utf-8"))

    assert not _assigns_attribute(tree, "DailyMixin", "daily_signature")
    assert any(
        isinstance(node, ast.FunctionDef) and node.name == "current_daily_signature"
        for node in tree.body
    )


def test_daily_signature_is_independent_of_import_order(tmp_path):
    suffix = (
        "from helper.store import Store; from helper.engine import Engine; "
        "from pathlib import Path; import sys; "
        "print(Engine(Store(Path(sys.argv[1]))).daily_signature())"
    )
    orders = [
        "import helper.daily; import helper.rotation; import helper.daily_mix_v2; ",
        "import helper.daily_mix_v2; import helper.rotation; import helper.daily; ",
    ]
    values = []
    for index, imports in enumerate(orders):
        root = tmp_path / str(index)
        values.append(subprocess.check_output(
            [sys.executable, "-c", imports + suffix, str(root)],
            text=True,
        ).strip())
    assert values[0] == values[1]


def test_engine_has_no_second_scheduler_and_new_state_stops_writing_old_interval():
    assert not _class_has_method(ast.parse(ENGINE.read_text(encoding="utf-8")), "Engine", "scheduler")
    assert "interval_minutes" not in STORE.read_text(encoding="utf-8").split("DEFAULT_SETTINGS=", 1)[1].split("\n", 1)[0]
    assert "settings[\"interval_minutes\"]" not in WORKFLOW.read_text(encoding="utf-8")


def test_auth_and_status_http_response_keys_remain_compatible(tmp_path):
    import inspect
    from starlette.requests import Request

    from helper.auth import COOKIE_NAME
    from helper.store import Store
    from helper.web import create_app

    app = create_app(store=Store(tmp_path), start_scheduler=False)

    async def request(path, method="GET", body=None, cookie=""):
        payload = b"" if body is None else json.dumps(body).encode()
        headers = [(b"host", b"testserver")]
        if payload:
            headers.extend([(b"content-type", b"application/json"), (b"content-length", str(len(payload)).encode())])
        if cookie:
            headers.append((b"cookie", f"{COOKIE_NAME}={cookie}".encode()))

        async def receive():
            return {"type": "http.request", "body": payload, "more_body": False}

        req = Request({
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
            "method": method, "scheme": "http", "path": path,
            "raw_path": path.encode(), "query_string": b"", "headers": headers,
            "client": ("127.0.0.1", 1234), "server": ("testserver", 80),
        }, receive)
        route = next(row for row in app.routes if getattr(row, "path", None) == path and method in getattr(row, "methods", set()))
        result = route.endpoint(req) if inspect.signature(route.endpoint).parameters else route.endpoint()
        if inspect.isawaitable(result):
            result = await result
        if isinstance(result, dict):
            return 200, {}, result
        return result.status_code, dict(result.raw_headers), json.loads(result.body or b"{}")

    status, _, auth_status = asyncio.run(request("/api/auth/status"))
    assert status == 200
    assert {"authenticated", "username", "setup_required", "bootstrap_required", "legacy_upgrade_required"} <= auth_status.keys()

    credentials = {"username": "admin", "password": "safe-password", "confirm_password": "safe-password"}
    status, headers, setup = asyncio.run(request("/api/auth/setup", "POST", credentials))
    assert status == 200
    assert {"authenticated", "username", "message"} <= setup.keys()
    cookie = headers[b"set-cookie"].decode().split(";", 1)[0].split("=", 1)[1]

    status, _, summary = asyncio.run(request("/api/status", cookie=cookie))
    assert status == 200
    assert {"version", "settings", "job", "managed", "scheduler", "behavior"} <= summary.keys()

    password = {
        "current_password": "safe-password", "new_password": "new-safe-password",
        "confirm_password": "new-safe-password",
    }
    status, _, changed = asyncio.run(request("/api/auth/password", "POST", password, cookie))
    assert status == 200
    assert {"message", "username"} <= changed.keys()

    status, _, logout = asyncio.run(request("/api/auth/logout", "POST", cookie=cookie))
    assert status == 200 and "message" in logout
