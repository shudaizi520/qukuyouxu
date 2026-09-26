"""Private LAN control panel. Background work is performed by Engine, not an LLM."""
import os
import re
import time
import hmac
import threading
import ipaddress
from contextlib import asynccontextmanager, nullcontext
from pathlib import Path
from urllib.parse import urlsplit
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from . import __version__
from .store import Store
from .engine import SafetyError
from .extra_web import attach_routes
from .single_web import attach_single_routes
from .auth import AuthManager, COOKIE_NAME, SESSION_SECONDS
from .auth_web import attach_auth_routes
from .management_web import attach_management_routes
from .web_surface import attach_web_surface
from .profiles import ProfileRegistry
from .profile_web import attach_profile_routes
from .scoped_store import ActiveProfileStore
from .plex_webhook import attach_webhook_route
from .profile_runtime import ActiveEngineProxy, ProfileRuntime
from .status_web import attach_status_routes
from .smart_mix_web import attach_smart_mix_routes
from .automation import attach_automation_routes
from .external_web import attach_external_routes
from .playlist_hub import attach_playlist_hub_routes
from .web_playback import attach_web_playback_route

_ARTWORK_CACHE_PATH = re.compile(
    r'^/api/playlists/(?:library/tracks/\d+/artwork|[^/]+/[^/]+/tracks/\d+/artwork)$'
)
_COVER_CACHE_PATH = re.compile(r'^/api/playlists/[^/]+/[^/]+/cover$')


def artwork_cache_policy(path, method, status_code, *, authenticated=False, scoped=False):
    """Keep scoped images locally, but revalidate authorization on every load."""
    if method == 'GET' and status_code in (200, 304) and authenticated and scoped:
        if _ARTWORK_CACHE_PATH.fullmatch(path):
            return {'Cache-Control': 'private, no-cache',
                    'Vary': 'Cookie, X-Plex-Profile'}
        if _COVER_CACHE_PATH.fullmatch(path):
            return {'Cache-Control': 'private, no-cache',
                    'Vary': 'Cookie, X-Plex-Profile'}
    return {'Cache-Control': 'no-store'}

def _origin(value):
    value = str(value or '').strip().rstrip('/')
    if not value:
        return ''
    parsed = urlsplit(value)
    if parsed.scheme not in ('http', 'https') or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError('PUBLIC_ORIGIN 必须是完整的 http 或 https 访问地址')
    if parsed.path not in ('', '/') or parsed.query or parsed.fragment:
        raise ValueError('PUBLIC_ORIGIN 不能包含路径、查询参数或片段')
    return f'{parsed.scheme}://{parsed.netloc}'


def _private_request_origin(req):
    """Allow a direct LAN URL alongside an explicitly configured HTTPS proxy."""
    origin = _origin(f"{req.scope.get('scheme', 'http')}://{req.headers.get('host', '')}")
    hostname = urlsplit(origin).hostname or ''
    if hostname == 'localhost':
        return origin
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return ''
    return origin if not address.is_global else ''


def create_app(store=None, admin_token=None, start_scheduler=True, engine=None,
               public_origin=None):
    base_store = store or Store(os.environ.get('DATA_ROOT', '/data'))
    profiles = ProfileRegistry(base_store)
    store = ActiveProfileStore(base_store, profiles)
    admin_token = admin_token if admin_token is not None else os.environ.get('ADMIN_TOKEN', '')
    if admin_token and (len(admin_token) < 32 or not admin_token.isascii()):
        raise ValueError('旧版 ADMIN_TOKEN 如保留，至少需要32位 ASCII 字符')
    auth = AuthManager(base_store)
    public_origin = _origin(public_origin if public_origin is not None else os.environ.get('PUBLIC_ORIGIN', ''))
    if base_store.get('auth_bootstrap_token') is not None:
        base_store.set('auth_bootstrap_token', None)
    runtime = ProfileRuntime(base_store, profiles)
    engine = engine or ActiveEngineProxy(runtime, profiles)
    background_automation_enabled = bool(start_scheduler) and str(
        os.environ.get('DISABLE_BACKGROUND_AUTOMATION', '')
    ).strip().lower() not in {'1', 'true', 'yes', 'on'}
    attempts = {}
    attempt_lock = threading.Lock()

    @asynccontextmanager
    async def lifespan(app):
        # Starlette runs sync handlers in AnyIO's worker pool. Its default of
        # 40 threads creates one glibc arena per busy worker; those arenas keep
        # large temporary Plex/XML/image allocations resident after requests.
        import anyio.to_thread
        anyio.to_thread.current_default_thread_limiter().total_tokens = 8
        lockfile = None
        try:
            if background_automation_enabled:
                import fcntl
                lockfile = open(store.root / '.instance.lock', 'a')
                fcntl.flock(lockfile, fcntl.LOCK_EX | fcntl.LOCK_NB)
                runtime.start_scheduler()
            yield
        except BlockingIOError:
            raise RuntimeError('已有实例使用本数据目录，禁止双开') from None
        finally:
            runtime.close()
            if lockfile:
                lockfile.close()
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.store = store
    app.state.base_store = base_store
    app.state.profiles = profiles
    app.state.profile_runtime = runtime
    app.state.engine = engine

    @app.middleware('http')
    async def security(req, call_next):
        path = str(req.scope.get('path') or '')
        public_api = ('/api/auth/status', '/api/auth/login', '/api/auth/setup', '/api/auth/logout')
        profile_context = profiles.fixed_active()
        requested_profile = ''
        session_user = None
        if path.startswith('/api/') and path != '/api/plex/webhook':
            if path not in public_api:
                session_user = auth.session_user(req.cookies.get(COOKIE_NAME))
                legacy_ok = False
                if not session_user and auth.setup_required() and admin_token:
                    value = req.headers.get('authorization', '')
                    legacy_ok = hmac.compare_digest(value.encode(), ('Bearer ' + admin_token).encode())
                if not session_user and (not legacy_ok):
                    return JSONResponse({'error': '请先登录'}, status_code=401)
                if path == '/api/plex/profiles':
                    profile_context = nullcontext()
                else:
                    requested_profile = str(req.headers.get('x-plex-profile') or '').strip()
                    profile_context = profiles.fixed_active(
                        requested_profile or None,
                        enabled_only=bool(requested_profile),
                    )
            origin = req.headers.get('origin')
            try:
                expected_origin = public_origin or _origin(f"{req.scope.get('scheme', 'http')}://{req.headers.get('host', '')}")
                request_origin = _origin(origin) if origin else ''
                allowed_origins = {expected_origin}
                if public_origin:
                    private_origin = _private_request_origin(req)
                    if private_origin:
                        allowed_origins.add(private_origin)
            except ValueError:
                return JSONResponse({'error': '请求地址无效'}, status_code=400)
            if request_origin and request_origin not in allowed_origins:
                return JSONResponse({'error': '不允许跨站管理请求；请直接使用本应用地址'}, status_code=403)
            try:
                if int(req.headers.get('content-length', '0')) > 2 * 1024 * 1024:
                    return JSONResponse({'error': '请求超过2MB'}, status_code=413)
            except ValueError:
                return JSONResponse({'error': '无效请求长度'}, status_code=400)
        try:
            with profile_context:
                r = await call_next(req)
        except ValueError as exc:
            message = str(exc)[:300]
            payload = {'error': message}
            if requested_profile and message in ('Plex 档案已停用', 'Plex 档案不存在'):
                payload['code'] = 'profile_unavailable'
            return JSONResponse(payload, status_code=400)
        scoped = bool(requested_profile or req.query_params.get('profile_id'))
        for name, value in artwork_cache_policy(
            path, req.method, r.status_code,
            authenticated=bool(session_user), scoped=scoped,
        ).items():
            r.headers[name] = value
        r.headers['X-Content-Type-Options'] = 'nosniff'
        embedded = req.query_params.get('embedded') == '1' and path in (
            '/daily', '/mixes', '/external', '/library', '/status', '/settings', '/appearance',
        )
        r.headers['X-Frame-Options'] = 'SAMEORIGIN' if embedded else 'DENY'
        r.headers['Referrer-Policy'] = 'no-referrer'
        frame_ancestors = "'self'" if embedded else "'none'"
        r.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors " + frame_ancestors
        return r

    @app.exception_handler(ValueError)
    async def bad_input(req, exc):
        return JSONResponse({'error': str(exc)[:300]}, status_code=400)

    async def body(req):
        b = bytearray()
        async for part in req.stream():
            b.extend(part)
            if len(b) > 2 * 1024 * 1024:
                raise ValueError('请求超过2MB')
        import json
        try:
            d = json.loads(b)
        except (ValueError, UnicodeError):
            raise ValueError('需要JSON请求') from None
        if not isinstance(d, dict):
            raise ValueError('需要JSON对象')
        return d

    def ensure_idle():
        if engine.job['running']:
            raise SafetyError('任务运行中，等完成后再修改配置')

    def _auth_rate_limit(req):
        peer = req.client.host if req.client else 'unknown'
        now = time.monotonic()
        with attempt_lock:
            count, until = attempts.get(peer, (0, now + 60))
            if now > until:
                count, until = (0, now + 60)
            if len(attempts) > 1000:
                attempts.clear()
            attempts[peer] = (count + 1, until)
        if count > 20:
            raise SafetyError('登录尝试过多，请稍后再试')

    def _set_session_cookie(response, token, req, max_age=SESSION_SECONDS):
        browser_origin = _origin(req.headers.get('origin')) if req.headers.get('origin') else ''
        proxy_host = urlsplit(public_origin).netloc if public_origin else ''
        secure = public_origin.startswith('https://') and (
            browser_origin == public_origin or req.headers.get('host', '').lower() == proxy_host.lower()
        )
        response.set_cookie(COOKIE_NAME, token, max_age=max_age, httponly=True, samesite='strict', secure=secure, path='/')
        return response

    attach_auth_routes(app, auth, store, body, _set_session_cookie, _auth_rate_limit)
    attach_web_surface(app, Path(__file__).with_name('static'), __version__)
    attach_status_routes(app, store, engine, runtime)

    attach_management_routes(app, store, engine, body, ensure_idle)
    attach_profile_routes(app, base_store, profiles, body, ensure_idle, engine=engine)
    from .library_sharing import attach_library_share_routes
    attach_library_share_routes(app, store, runtime, body, ensure_idle)
    attach_automation_routes(app, base_store, profiles, runtime, body)
    attach_external_routes(app, store, engine, runtime, profiles, body, ensure_idle)
    attach_playlist_hub_routes(app, store, runtime, profiles, body, ensure_idle)
    attach_web_playback_route(app, base_store, profiles, body, runtime)
    attach_webhook_route(app, base_store, profiles)
    attach_smart_mix_routes(app, store, engine, runtime, profiles, body, ensure_idle)
    attach_routes(app, store, engine, body, ensure_idle)
    attach_single_routes(app, store, engine, body, ensure_idle)
    return app
def main():
    import uvicorn
    os.umask(0o077)
    uvicorn.run(
        create_app(),
        host='0.0.0.0',
        port=int(os.environ.get('PORT', '9511')),
        access_log=False,
        proxy_headers=False,
        limit_concurrency=32,
        timeout_keep_alive=10,
    )


if __name__ == '__main__':
    main()
