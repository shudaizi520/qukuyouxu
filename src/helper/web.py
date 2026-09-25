"""Private LAN control panel. Background work is performed by Engine, not an LLM."""
import csv
import io
import os
import re
import time
import uuid
import hmac
import threading
import ipaddress
from contextlib import asynccontextmanager, nullcontext
from pathlib import Path
from urllib.parse import urlsplit
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, Response, RedirectResponse
from . import __version__
from .store import Store, DEFAULT_SETTINGS
from .engine import Engine, SafetyError, safe_error, digest
from .clients import validate_base, parse_playlist_id
from .match import CONVERSION_AVAILABLE, normalize
from .extra_web import attach_routes, extensions_status
from .single_web import attach_single_routes
from .auth import AuthManager, COOKIE_NAME, SESSION_SECONDS
from .page_version import render_library_html, render_versioned_html
from .profiles import ProfileRegistry
from .profile_web import attach_profile_routes
from .scoped_store import ActiveProfileStore
from .plex_webhook import attach_webhook_route
from .profile_runtime import ActiveEngineProxy, ProfileRuntime, current_qq_status
from .smart_mix_web import attach_smart_mix_routes
from .automation import attach_automation_routes
from .external_web import attach_external_routes
from .playlist_hub import attach_playlist_hub_routes
from .web_playback import attach_web_playback_route
STATIC = Path(__file__).with_name('static')

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
    supplied_store = store is not None
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
        if start_scheduler:
            import fcntl
            lockfile = open(store.root / '.instance.lock', 'a')
            try:
                fcntl.flock(lockfile, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError('已有实例使用本数据目录，禁止双开') from None
            threading.Thread(target=runtime.scheduler, daemon=True, name='profile-scheduler').start()
        yield
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

    def public_settings():
        cfg = store.get('settings')
        return {**{k: v for k, v in cfg.items() if k != 'plex_token'}, 'token_present': bool(cfg.get('plex_token'))}

    def connection_scope(cfg):
        return digest([cfg.get('plex_url', ''), cfg.get('plex_token', '')])

    def public_connection():
        cached = store.get('plex_connection')
        if not cached:
            return None
        cfg = store.get('settings')
        if not cfg.get('plex_url') or not cfg.get('plex_token'):
            return None
        if cached.get('scope') != connection_scope(cfg):
            pending = store.get('plex_login_pending', {}) or {}
            if cached.get('source') not in (None, 'official_login') or pending.get('status') != 'connected':
                return None
        return cached.get('result')

    def public_name_plan():
        plan = store.get('name_plan')
        if not plan:
            return None
        return {**{k: plan.get(k) for k in ('id', 'created_at', 'applied', 'result')}, 'groups': [{**{k: g.get(k) for k in ('category_id', 'playlist_id', 'old_title', 'new_title', 'blocked', 'action')}, 'count': len((g.get('before') or {}).get('items', []))} for g in plan['groups']]}

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

    @app.get('/api/auth/status')
    def auth_status(req: Request):
        user = auth.session_user(req.cookies.get(COOKIE_NAME))
        cred = auth.credentials()
        return {'authenticated': bool(user), 'username': user or (cred.get('username') if cred else None), 'setup_required': cred is None, 'bootstrap_required': False, 'legacy_upgrade_required': False}

    @app.post('/api/auth/setup')
    async def auth_setup(req: Request):
        _auth_rate_limit(req)
        if not auth.setup_required():
            raise ValueError('管理员账户已经建立')
        d = await body(req)
        if str(d.get('password', '')) != str(d.get('confirm_password', '')):
            raise ValueError('两次输入的密码不一致')
        username = auth.create_account(d.get('username', 'admin'), d.get('password', ''))
        token, _ = auth.create_session(username)
        store.log('管理员账户已建立；旧版验证信息不再用于日常登录')
        return _set_session_cookie(JSONResponse({'authenticated': True, 'username': username, 'message': '管理员账户已建立'}), token, req)

    @app.post('/api/auth/login')
    async def auth_login(req: Request):
        _auth_rate_limit(req)
        d = await body(req)
        username = str(d.get('username', ''))
        password = str(d.get('password', ''))
        if not auth.verify(username, password):
            return JSONResponse({'error': '用户名或密码不正确'}, status_code=401)
        token, _ = auth.create_session(username)
        return _set_session_cookie(JSONResponse({'authenticated': True, 'username': username}), token, req)

    @app.post('/api/auth/logout')
    def auth_logout(req: Request):
        auth.revoke_session(req.cookies.get(COOKIE_NAME))
        r = JSONResponse({'message': '已退出'})
        r.delete_cookie(COOKIE_NAME, path='/')
        return r

    @app.post('/api/auth/password')
    async def auth_password(req: Request):
        user = auth.session_user(req.cookies.get(COOKIE_NAME))
        if not user:
            return JSONResponse({'error': '请先登录'}, status_code=401)
        d = await body(req)
        if str(d.get('new_password', '')) != str(d.get('confirm_password', '')):
            raise ValueError('两次输入的新密码不一致')
        username = auth.change_password(user, d.get('current_password', ''), d.get('new_password', ''))
        token, _ = auth.create_session(username)
        store.log('管理员密码已修改，旧登录会话已撤销')
        return _set_session_cookie(JSONResponse({'message': '密码已更新', 'username': username}), token, req)

    @app.get('/')
    def index():
        return HTMLResponse(render_versioned_html((STATIC / 'playlists.html').read_text(encoding='utf-8'), __version__))

    @app.get('/daily')
    def daily_page():
        return HTMLResponse(render_versioned_html((STATIC / 'daily.html').read_text(encoding='utf-8'), __version__))

    @app.get('/library')
    def library_home():
        return HTMLResponse(render_library_html((STATIC / 'home.html').read_text(encoding='utf-8'), __version__))

    @app.get('/status')
    def status_page():
        return HTMLResponse(render_versioned_html((STATIC / 'status.html').read_text(encoding='utf-8'), __version__))

    @app.get('/mixes')
    def mixes_page():
        return HTMLResponse(render_versioned_html((STATIC / 'mixes.html').read_text(encoding='utf-8'), __version__))

    @app.get('/external')
    def external_page():
        return HTMLResponse(render_versioned_html((STATIC / 'external.html').read_text(encoding='utf-8'), __version__))

    @app.get('/settings')
    def settings_page():
        return HTMLResponse(render_versioned_html((STATIC / 'settings.html').read_text(encoding='utf-8'), __version__))

    @app.get('/appearance')
    def appearance_page():
        return HTMLResponse(render_versioned_html((STATIC / 'appearance.html').read_text(encoding='utf-8'), __version__))

    @app.get('/advanced')
    def advanced():
        # The old expert console mixed unrelated and obsolete maintenance tools.
        # Keep old bookmarks safe, but send users to the page that owns runtime
        # health and history instead of exposing that console again.
        return RedirectResponse(url='/status', status_code=307)

    @app.get('/healthz')
    def health():
        return {'ok': True, 'version': __version__}

    @app.get('/static/{name}')
    def static(name):
        if name not in (
            'home.js', 'theme_home.js', 'product.css', 'daily.js',
            'refined.js', 'status.js', 'settings.js', 'contextual-settings.js', 'auth.js', 'mixes.js',
            'appearance.js', 'external.js', 'playlists.js', 'playlist-artwork.js', 'playlist-workspace.js',
            'playlist-search.js', 'playlist-player.js', 'playlist-sections.js',
            'playlist-playback-mode.js', 'playlist-now-playing.js', 'playlist-now-playing.css',
            'external-workspace.css', 'theme-tokens.css', 'ui-components.css', 'design-system.css',
            'management-shell.css', 'theme-background.css',
            'playlist-visualizer.js', 'management-shell.js',
        ):
            return Response(status_code=404)
        return FileResponse(STATIC / name, media_type='text/javascript' if name.endswith('.js') else 'text/css')

    @app.get('/api/status')
    def status():
        src = [{k: v for k, v in s.items() if k != 'csv_tracks'} for s in store.get('sources')]
        plan = store.get('plan')
        return {'version': __version__, 'settings': public_settings(), 'sources': src, 'job': dict(engine.job), 'managed': store.get('managed'), 'conversion': CONVERSION_AVAILABLE, 'last_run': store.get('last_run'), 'summary': {k: plan.get(k) for k in ('id', 'created_at', 'library_count', 'covered', 'coverage', 'applied', 'result')} if plan else None, 'events': store.get('events')[-30:], 'plex_connection': public_connection(), 'qq_auth': current_qq_status(app, engine), 'single': engine.single_status() if hasattr(engine, 'single_status') else {'running': False}, 'qq_tags': store.get('qq_tags', []), 'name_plan': public_name_plan(), **extensions_status(store)}

    @app.post('/api/settings')
    async def settings(req: Request):
        d = await body(req)
        ensure_idle()
        with engine.exclusive():
            old = store.get('settings')
            cfg = dict(old)
            for k in ('plex_url', 'section', 'account_label'):
                if k in d:
                    cfg[k] = str(d[k]).strip()
            if cfg['plex_url']:
                cfg['plex_url'] = validate_base(cfg['plex_url'])
            incoming_token = str(d.get('plex_token') or '').strip()
            if incoming_token:
                cfg['plex_token'] = incoming_token
            token = cfg['plex_token']
            if token and (not 8 <= len(token) <= 512 or not token.isascii() or any((ord(c) < 33 for c in token))):
                raise ValueError('Plex Token格式不正确')
            if cfg['section'] and (not cfg['section'].isdigit()):
                raise ValueError('资料库ID必须为数字')
            if len(cfg['account_label']) > 80:
                raise ValueError('账户备注过长')
            for k, lo, hi in [('interval_minutes', 5, 1440), ('source_hours', 1, 168), ('min_tracks', 1, 100)]:
                if k in d:
                    try:
                        v = int(d[k])
                    except (ValueError, TypeError):
                        raise ValueError(k + '需要整数') from None
                    if not lo <= v <= hi:
                        raise ValueError(f'{k}必须在{lo}到{hi}之间')
                    cfg[k] = v
            changed = any((old[k] != cfg[k] for k in ('plex_url', 'plex_token', 'section', 'account_label')))
            from .profile_web import connection_is_protected
            if changed and connection_is_protected(store):
                raise SafetyError('已有托管歌单，不能直接切换账户/服务器/资料库；请让Codex核对迁移，避免误改其他账户')
            if changed:
                cfg['auto_enabled'] = False
                daily = store.get('daily_settings')
                daily['enabled'] = False
                store.set_many({'daily_settings': daily, 'daily_plan': None})
                if any((old.get(k) != cfg.get(k) for k in ('plex_url', 'plex_token', 'section'))):
                    store.set_many({'feedback': {'tracks': {}, 'artists': {}}, 'metadata_overrides': {}, 'daily_history': [], 'catalog': [], 'metadata_audit': [], 'behavior_events': [], 'behavior_sessions': {}, 'behavior_status': {}})
            if any((old.get(k) != cfg.get(k) for k in ('plex_url', 'plex_token'))):
                store.set('plex_connection', None)
            store.set('settings', cfg)
            if old != cfg:
                store.set('plan', None)
        return {'message': '配置已保存；还未写入Plex歌单'}

    @app.post('/api/plex/check/legacy')
    def check():
        ensure_idle()
        with engine.exclusive():
            try:
                p = engine.plex_factory(store.get('settings'))
                r = p.identity()
                r['sections'] = p.sections()
                r['existing_playlists'] = [x.get('title') for x in p.playlists()][:20]
                r['checked_at'] = time.time()
                shown = {k: r[k] for k in ('server', 'machine', 'version', 'sections', 'existing_playlists', 'checked_at') if k in r}
                store.set('plex_connection', {'scope': connection_scope(store.get('settings')), 'result': shown})
                return shown
            except Exception as exc:
                raise SafetyError(safe_error(exc)) from None

    @app.post('/api/qq/tags')
    def tags():
        ensure_idle()
        with engine.exclusive():
            try:
                tags = engine.qq.tags()
                store.set('qq_tags', tags)
                return {'items': tags}
            except Exception as exc:
                raise SafetyError(safe_error(exc)) from None

    @app.post('/api/sources')
    async def add_source(req: Request):
        d = await body(req)
        ensure_idle()
        with engine.exclusive():
            name = str(d.get('name', '')).strip()
            if not name or len(name) > 60:
                raise ValueError('分类名称需1—60个字')
            kind = d.get('kind')
            src = {'id': uuid.uuid4().hex, 'name': name, 'kind': kind, 'enabled': True, 'approved': False}
            if kind == 'qq_playlist':
                src['value'] = parse_playlist_id(d.get('value', ''))
            elif kind == 'qq_category':
                val = str(d.get('value', ''))
                if not any((t['id'] == val for t in store.get('qq_tags', []))):
                    raise ValueError('先读取真实QQ分类，再选择分类')
                src.update(value=val, limit=store.get('source_settings', {}).get('reference_limit', 12))
            elif kind == 'csv':
                text = str(d.get('csv', '')).lstrip('\ufeff')
                rows = []
                for i, r in enumerate(csv.DictReader(io.StringIO(text))):
                    if i >= 10000:
                        raise ValueError('CSV最多10000首')
                    t = {'title': str(r.get('title') or r.get('歌名') or '').strip(), 'artist': str(r.get('artist') or r.get('歌手') or '').strip(), 'album': str(r.get('album') or r.get('专辑') or '').strip()}
                    if not t['title'] or not t['artist']:
                        raise ValueError(f'CSV第{i + 2}行缺少歌名/歌手')
                    if max(map(len, t.values())) > 500:
                        raise ValueError('CSV字段过长')
                    try:
                        t['duration'] = float(r.get('duration') or r.get('时长秒') or 0)
                    except ValueError:
                        raise ValueError('CSV时长需为秒数') from None
                    import math
                    if not math.isfinite(t['duration']) or not 0 <= t['duration'] <= 86400:
                        raise ValueError('CSV时长不合理')
                    from .engine import query_key
                    t['id'] = query_key(t)
                    rows.append(t)
                if not rows:
                    raise ValueError('CSV为空；首行需title,artist,album,duration')
                src.update(value='user-csv', csv_tracks=rows)
            else:
                raise ValueError('不支持的来源类型')
            sources = store.get('sources')
            if len(sources) >= 40:
                raise ValueError('本版最多40个分类，避免生成过多重复歌单')
            if any((s['name'] == name for s in sources)):
                raise ValueError('分类名称重复，请复用现有分类或改名')
            sources.append(src)
            store.set('sources', sources)
            store.set('plan', None)
        return {'message': '分类已添加；只保存来源，尚未创建Plex歌单', 'id': src['id']}

    @app.post('/api/sources/{cid}/toggle')
    async def toggle(cid, req: Request):
        d = await body(req)
        ensure_idle()
        with engine.exclusive():
            src = store.get('sources')
            found = False
            for s in src:
                if s['id'] == cid:
                    s['enabled'] = bool(d.get('enabled'))
                    s['approved'] = False
                    found = True
            if not found:
                raise ValueError('分类不存在')
            store.set('sources', src)
            store.set('plan', None)
        return {'message': '已切换；已有Plex歌单保留，重新启用后需再次预览确认'}

    @app.post('/api/schedule')
    async def schedule(req: Request):
        d = await body(req)
        ensure_idle()
        with engine.exclusive():
            enable = bool(d.get('enabled'))
            if enable:
                raise SafetyError('独立定时开关已合并，请在首页开启“自动整理新歌”。')
            if enable and (not any((s.get('approved') and s.get('enabled') for s in store.get('sources')))):
                raise SafetyError('先完成一次预览并确认写入，再开启自动维护')
            cfg = store.get('settings')
            cfg['auto_enabled'] = enable
            store.set('settings', cfg)
            store.set('last_run', time.time())
        return {'message': '自动维护已开启' if enable else '自动维护已暂停'}

    @app.get('/api/plan')
    def get_plan():
        return store.get('plan') or {}

    @app.post('/api/jobs/{kind}')
    async def jobs(kind, req: Request):
        d = await body(req)
        if kind not in ('preview', 'apply', 'restore', 'name_preview', 'name_apply', 'daily_preview', 'daily_apply', 'daily_repair', 'base_preview', 'base_apply', 'single_check', 'single_enrich'):
            raise ValueError('未知任务')
        if kind == 'single_enrich' and d.get('confirm') is not True:
            raise SafetyError('请确认向QQ查询曲目资料，不发送音频或Plex凭据')
        if kind == 'apply' and d.get('confirm') is not True:
            raise SafetyError('请明确确认写入Plex歌单')
        if kind == 'restore' and d.get('confirm') is not True:
            raise SafetyError('请明确确认恢复变更')
        if kind == 'name_apply' and d.get('confirm') is not True:
            raise SafetyError('请明确确认原地修改歌单名称')
        if kind == 'daily_apply' and d.get('confirm') is not True:
            raise SafetyError('请明确确认发布每日推荐（仅更新本助手的每日歌单）')
        if kind == 'daily_repair' and d.get('confirm') is not True:
            raise SafetyError('请明确确认修复上次每日推荐（只处理本助手自己的每日歌单）')
        if kind == 'base_apply' and d.get('confirm') is not True:
            raise SafetyError('请明确确认写入全库基础分类歌单')
        return engine.start_job(kind, force_sources=bool(d.get('force_sources')), force_full=bool(d.get('force_full')), plan_id=str(d.get('plan_id', '')), snapshot_id=str(d.get('snapshot_id', '')))

    @app.get('/api/snapshots')
    def snapshots():
        return {'items': [{k: v for k, v in s.items() if k not in ('before', 'after', 'add', 'marker')} | {'before_count': len(s['before']['items']) if s['before'] else 0, 'after_count': len(s['after']['items']) if s['after'] else None} for s in reversed(store.get('snapshots'))]}

    @app.get('/api/library')
    def library(q: str=''):
        if len(q) > 100:
            raise ValueError('查询过长')
        items = [t for t in store.get('catalog', []) if normalize(q) in normalize(t['title'] + ' ' + t['artist'])]
        return {'items': items[:100], 'total': len(items)}

    @app.post('/api/overrides')
    async def override(req: Request):
        d = await body(req)
        ensure_idle()
        with engine.exclusive():
            key = str(d.get('query_key', ''))
            tid = str(d.get('id', ''))
            note = str(d.get('note', '')).strip()
            if not re.fullmatch('[a-f0-9]{64}', key) or not note or len(note) > 200:
                raise ValueError('需有效请求标识和简短人工核对说明')
            if tid != 'skip' and (not any((t['id'] == tid and t.get('available', True) for t in store.get('catalog', [])))):
                raise ValueError('请选择Plex中真实存在的可用曲目ID')
            rules = store.get('overrides')
            rules[key] = {'id': tid, 'note': note}
            store.set('overrides', rules)
            store.set('plan', None)
        return {'message': '修正已保存；请重新预览，不会自动立即写入'}

    @app.get('/api/report')
    def report():
        import json
        plan = store.get('plan') or {}
        export = {k: v for k, v in plan.items() if k not in ('signature', 'track_fingerprints')}
        return Response(json.dumps(export, ensure_ascii=False, indent=2), media_type='application/json', headers={'Content-Disposition': 'attachment; filename="classification-report.json"'})
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
