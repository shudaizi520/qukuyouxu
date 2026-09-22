"""Authenticated controls for source expansion, internal metadata, and daily lists."""
from collections import Counter
import json
import re
import time
from fastapi import Request
from fastapi.responses import Response
from .engine import SafetyError, digest, safe_error
from .metadata import prepare_catalog, identity_fingerprint, metadata_audit_rows
from .recommend import DEFAULT_DAILY
from .behavior import behavior_profile, recent_behavior_events
from .plex_webhook import active_session_count, webhook_health
from .base_mixin import DEFAULT_BASE
from .base import BASE_POLICY

SIMILARITY_FALLBACK_NOTICE = 'Plex 相似接口没有可用结果，本批使用歌手、专辑、流派和已有分类关系寻找相近歌曲。'

def public_daily(store):
    from .daily import active_daily_blocks
    p = store.get('daily_plan')
    if not p:
        return None
    result = {k: p.get(k) for k in ('id', 'created_at', 'date', 'mode', 'items', 'stats', 'warnings', 'blocked', 'applied', 'result', 'metadata_review_count', 'origin')}
    result['warnings'] = [
        warning for warning in (result.get('warnings') or [])
        if warning != SIMILARITY_FALLBACK_NOTICE
    ]
    result['blocked'] = active_daily_blocks(result)
    return result

def public_daily_published(store):
    """Return the durable, token-free view of the last verified publish."""
    view = store.get('daily_published_view')
    if view:
        items = []
        for row in view.get('items', []) or []:
            public = {
                key: row.get(key)
                for key in ('id', 'title', 'artist', 'album', 'bucket', 'reasons')
                if row.get(key) is not None
            }
            items.append(public)
        return {
            'plan_id': str(view.get('plan_id') or ''),
            'playlist_id': str(view.get('playlist_id') or ''),
            'title': str(view.get('title') or ''),
            'date': str(view.get('date') or ''),
            'published_at': view.get('published_at'),
            'count': int(view.get('count') or len(items)),
            'items': items,
        }

    managed = store.get('daily_managed')
    if not managed:
        return None
    history = store.get('daily_history', []) or []
    latest = history[-1] if history else {}
    ids = latest.get('ids', []) or []
    return {
        'plan_id': str(latest.get('plan_id') or ''),
        'playlist_id': str(managed.get('id') or ''),
        'title': str(managed.get('title') or ''),
        'date': str(managed.get('date') or latest.get('date') or ''),
        'published_at': managed.get('published_at') or latest.get('created_at'),
        'count': len(ids),
        'items': [],
    }

def public_active_profile(store):
    """Expose only the current profile labels needed by the page heading."""
    registry = getattr(store, 'registry', None)
    profile_id = str(getattr(store, 'profile_id', '') or '')
    if registry is None or not profile_id:
        return None
    try:
        profile = registry.get(profile_id)
    except (KeyError, ValueError):
        return None
    account = profile.get('account') or {}
    library = profile.get('library') or {}
    return {
        'id': profile_id,
        'name': str(account.get('username') or account.get('title') or profile.get('name') or profile_id),
        'library': {
            'id': str(library.get('id') or ''),
            'name': str(library.get('name') or ''),
        },
    }

def public_daily_repair(store):
    for snap in reversed(store.get('snapshots') or []):
        if snap.get('kind') != 'daily':
            continue
        if snap.get('status') in ('uncertain', 'prepared') and snap.get('before') and snap.get('add'):
            return {'snapshot_id': snap.get('id'), 'status': snap.get('status'), 'created_at': snap.get('created_at'), 'before_count': len((snap.get('before') or {}).get('items') or []), 'desired_count': len(snap.get('add') or [])}
        return None
    return None

def public_base(store):
    p = store.get('base_plan')
    if not p:
        return None
    if p.get('invalidated_reason') or p.get('policy') != BASE_POLICY:
        return {'id': p.get('id'), 'applied': p.get('applied', False), 'groups': [], 'invalidated_reason': p.get('invalidated_reason') or '年代规则已更新，旧基础预览已失效，请重新生成基础分类预览。'}
    return {**{k: p.get(k) for k in ('id', 'created_at', 'library_count', 'base_covered', 'base_coverage', 'theme_covered', 'theme_coverage', 'union_covered', 'union_coverage', 'unclassified_count', 'applied', 'result', 'metadata_review_count', 'dimensions', 'metadata_diagnostics')}, 'groups': [{'id': g.get('id'), 'title': g.get('title'), 'matched': g.get('matched', 0), 'add_count': len(g.get('add') or []), 'dimension': g.get('dimension'), 'inferred_count': g.get('inferred_count', 0), 'evidence': g.get('evidence', {}), 'action': g.get('action'), 'blocked': g.get('blocked', [])} for g in p.get('groups') or []]}

def extensions_status(store):
    counts = Counter((x.get('status') for x in store.get('metadata_audit', [])))
    managed = store.get('daily_managed')
    now = time.time()
    base_store = getattr(store, 'base', store)
    profile_id = str(getattr(store, 'profile_id', '') or '')
    if profile_id and hasattr(base_store, '_db'):
        from .behavior_store import BehaviorRepository
        from .plex_webhook import load_behavior_snapshot
        profile = load_behavior_snapshot(store, now)
        event_count = BehaviorRepository(base_store).event_stats(profile_id, now)["count"]
    else:
        events = recent_behavior_events(store.get('behavior_events', []) or [], now)
        profile = behavior_profile(events, now)
        event_count = len(events)
    behavior_status = store.get('behavior_status') or {}
    product = store.get('product_settings') or {}
    active_sessions = active_session_count(store.get('behavior_sessions', {}) or {})
    preferred = sum(1 for row in profile.values() if (row.get('affinity', row.get('score', 0)) or 0) > 0)
    cooled = sum(1 for row in profile.values() if (row.get('cooldown_until') or 0) > now)
    behavior = {
        'enabled': product.get('behavior_enabled', True),
        'event_count': event_count,
        'learned_tracks': len(profile),
        'preferred_tracks': preferred,
        'cooled_tracks': cooled,
        # Compatibility aliases remain for one release while the UI switches.
        'positive_tracks': preferred,
        'negative_tracks': cooled,
        'active_sessions': active_sessions,
        'updated_at': behavior_status.get('updated_at'),
        'status': behavior_status.get('status', 'waiting'),
    }
    return {'source_settings': store.get('source_settings', {'reference_limit': 12}), 'metadata_summary': {'review': sum((counts.get(k, 0) for k in ('conflict', 'incomplete', 'stale_correction'))), 'confirmed': counts.get('confirmed', 0), 'breakdown': dict(counts)}, 'base_settings': {**DEFAULT_BASE, **(store.get('base_settings') or {})}, 'base_plan': public_base(store), 'base_notice': store.get('base_notice', ''), 'daily_settings': {**DEFAULT_DAILY, **store.get('daily_settings', {})}, 'daily_plan': public_daily(store), 'daily_published': public_daily_published(store), 'daily_managed': {k: managed.get(k) for k in ('id', 'title', 'date')} if managed else None, 'daily_repair': public_daily_repair(store), 'daily_notice': store.get('daily_notice', ''), 'behavior': behavior, 'status_refresh_ms': 5000 if behavior['active_sessions'] else 45000, 'webhook': webhook_health(base_store, store), 'active_profile': public_active_profile(store), 'product_settings': {'behavior_enabled': product.get('behavior_enabled', True)}, 'feedback': store.get('feedback', {'tracks': {}, 'artists': {}})}

def attach_routes(app, store, engine, body, ensure_idle):

    @app.get('/api/plex/webhook/status')
    def plex_webhook_status():
        return webhook_health(getattr(store, 'base', store), store)

    @app.post('/api/sources/expand')
    async def expand(req: Request):
        d = await body(req)
        ensure_idle()
        try:
            limit = int(d.get('limit', 12))
        except (ValueError, TypeError):
            raise ValueError('参考歌单数量需整数') from None
        if isinstance(d.get('limit'), bool) or not 1 <= limit <= 20:
            raise ValueError('每分类参考歌单数量须为1～20')
        with engine.exclusive():
            sources = store.get('sources')
            cache = store.get('cache')
            changed = 0
            for src in sources:
                if src['kind'] != 'qq_category' or src.get('limit', 3) >= limit:
                    continue
                if src['id'] in cache and cache[src['id']].get('data'):
                    cache[src['id']].setdefault('source_signature', digest({k: v for k, v in src.items() if k not in ('approved', 'enabled')}))
                src['limit'] = limit
                src['approved'] = False
                changed += 1
            store.set_many({'sources': sources, 'cache': cache, 'source_settings': {'reference_limit': limit}})
            if changed:
                plan = store.get('plan')
                if plan:
                    plan['invalidated_reason'] = '参考歌单范围已扩大，请更新QQ来源并重新预览确认。'
                    store.set('plan', plan)
            store.log(f'已将{changed}个QQ分类的参考范围扩到{limit}个歌单；尚未写入Plex')
        return {'changed': changed, 'message': f'已扩大{changed}个分类。接下来点“更新 QQ 来源并预览”，检查后再确认写入。原歌单和缓存保留。'}

    @app.get('/api/metadata')
    def metadata(q: str='', offset: int=0, limit: int=50, review_only: bool=False):
        if len(q) > 100 or not 0 <= offset <= 100000 or (not 1 <= limit <= 100):
            raise ValueError('查询范围无效')
        rows = metadata_audit_rows(
            store.get('catalog', []), store.get('metadata_overrides', {}),
            query=q, review_only=review_only,
        )
        return {'items': rows[offset:offset + limit], 'total': len(rows), 'offset': offset, 'next': offset + limit if offset + limit < len(rows) else None}

    @app.post('/api/metadata/corrections')
    async def correction(req: Request):
        d = await body(req)
        ensure_idle()
        if d.get('confirm') is not True:
            raise SafetyError('请先核对曲目信息，并明确确认只在助手内部纠正')
        tid = str(d.get('id', ''))
        if not tid.isdigit():
            raise ValueError('Plex曲目ID无效')
        with engine.exclusive():
            rules = store.get('metadata_overrides', {})
            tracks = store.get('catalog', [])
            if d.get('clear') is True:
                rules.pop(tid, None)
            else:
                t = next((x for x in tracks if x['id'] == tid), None)
                if not t:
                    raise SafetyError('曲目不在最近读取的资料库中，请先生成预览')
                if d.get('fingerprint') != identity_fingerprint(t):
                    raise SafetyError('曲目或校对页面已变化，请重新读取待校对列表')
                values = {k: str(d.get(k, '')).strip() for k in ('title', 'artist', 'note')}
                if not all(values.values()) or any((len(v) > 300 or any((ord(c) < 32 for c in v)) for v in values.values())):
                    raise ValueError('请填写歌名、歌手和核对说明，每项不超过300字')
                if 'album' in d:
                    album = str(d['album']).strip()
                    if len(album) > 300:
                        raise ValueError('专辑名称过长')
                    values['album'] = album
                rules[tid] = {**values, 'fingerprint': identity_fingerprint(t), 'confirmed_at': time.time()}
            _, audit = prepare_catalog(tracks, rules)
            store.set_many({'metadata_overrides': rules, 'metadata_audit': audit, 'plan': None, 'daily_plan': None})
            store.log('助手内部曲目信息已更新，未修改音乐文件/Plex标签：ID ' + tid)
        return {'message': '已保存到助手内部；原文件和Plex标签未改。请重新生成分类/每日预览。'}

    @app.post('/api/base/schedule')
    async def base_schedule(req: Request):
        d = await body(req)
        ensure_idle()
        if d.get('enabled') is True:
            raise SafetyError('独立定时开关已合并，请在首页开启“自动整理新歌”。')
        with engine.exclusive():
            return engine.set_base_schedule(False)

    @app.get('/api/base/details')
    def base_details(category_id: str, offset: int=0, limit: int=50):
        if len(category_id) > 120 or not 0 <= offset <= 100000 or (not 1 <= limit <= 100):
            raise ValueError('查询范围无效')
        p = store.get('base_plan') or {}
        if p.get('policy') != BASE_POLICY or p.get('invalidated_reason'):
            raise SafetyError('请先生成当前规则的基础分类预览')
        g = next((g for g in p.get('groups', []) if g['id'] == category_id), None)
        if not g:
            raise ValueError('基础分类不存在')
        rows = g.get('evidence_rows', [])
        return {'plan_id': p['id'], 'title': g['title'], 'total': len(rows), 'items': rows[offset:offset + limit], 'next': offset + limit if offset + limit < len(rows) else None}

    @app.get('/api/base/report')
    def base_report():
        data = store.get('base_plan') or {}
        export = {k: v for k, v in data.items() if k not in ('signature', 'track_fingerprints', 'album_evidence_fingerprints')}
        return Response(json.dumps(export, ensure_ascii=False, indent=2), media_type='application/json', headers={'Content-Disposition': 'attachment; filename="base-classification-report.json"'})

    @app.get('/api/daily/favorites')
    def favorites():
        ensure_idle()
        with engine.exclusive():
            try:
                p = engine.plex_factory(store.get('settings'))
                generated = {x['id'] for x in store.get('managed').values()}
                daily = store.get('daily_managed')
                if daily:
                    generated.add(daily['id'])
                items = [{'id': str(x['ratingKey']), 'title': x.get('title', '')} for x in p.playlists() if str(x.get('ratingKey', '')).isdigit() and str(x['ratingKey']) not in generated]
                return {'items': items, 'message': '仅选择你真正收藏的歌单，不要把全曲库/随机歌单当作喜欢。'}
            except Exception as exc:
                raise SafetyError(safe_error(exc)) from None

    @app.post('/api/daily/settings')
    async def daily_settings(req: Request):
        d = await body(req)
        ensure_idle()
        with engine.exclusive():
            old = {**DEFAULT_DAILY, **store.get('daily_settings', {})}
            cfg = dict(old)
            for k, lo, hi in [('size', 1, 100), ('artist_cap', 1, 10), ('album_cap', 1, 10), ('favorite_cap', 0, 10), ('repeat_days', 1, 90), ('cooldown_hours', 0, 168), ('hour', 0, 23)]:
                if k in d:
                    try:
                        v = int(d[k])
                    except (ValueError, TypeError):
                        raise ValueError(k + '需整数') from None
                    if isinstance(d[k], bool) or not lo <= v <= hi:
                        raise ValueError(f'{k}须在{lo}～{hi}之间')
                    cfg[k] = v
            if 'seed_playlist_ids' in d:
                ids = d['seed_playlist_ids']
                if not isinstance(ids, list) or len(ids) > 10 or any((not re.fullmatch('[0-9]{1,20}', str(x)) for x in ids)):
                    raise ValueError('收藏歌单ID需为数字，最多10个')
                generated = {x['id'] for x in store.get('managed').values()}
                m = store.get('daily_managed')
                if m:
                    generated.add(m['id'])
                if set(map(str, ids)) & generated:
                    raise ValueError('不能把助手生成的分类/每日歌单作为个人收藏')
                cfg['seed_playlist_ids'] = list(dict.fromkeys(map(str, ids)))
            store.set('daily_settings', cfg)
            if cfg != old:
                store.set('daily_plan', None)
        return {'message': '每日推荐设置已保存；不立即改歌单。不使用 Sonic Analysis。'}

    @app.post('/api/daily/schedule')
    async def daily_schedule(req: Request):
        d = await body(req)
        ensure_idle()
        with engine.exclusive():
            enabled = d.get('enabled') is True
            managed = store.get('daily_managed')
            if enabled and (not managed or managed.get('scope') != engine.daily_scope()):
                raise SafetyError('先预览并发布一次每日推荐，再启用每日更新')
            if enabled and store.get('daily_auto_suspension'):
                reason = str((store.get('daily_auto_suspension') or {}).get('reason') or '自动更新已暂停')
                raise SafetyError(reason + '；请先手动预览并发布确认')
            if enabled and store.get('daily_auto_opt_out'):
                raise SafetyError('该用户已退出自动更新；请先手动预览并发布确认')
            if enabled and any((s.get('category_id') == 'daily' and s.get('status') in ('prepared', 'uncertain', 'restoring') for s in store.get('snapshots'))):
                raise SafetyError('有待核对的每日推荐变更，不能开启自动更新')
            cfg = {**DEFAULT_DAILY, **store.get('daily_settings', {})}
            cfg['enabled'] = enabled
            store.set('daily_settings', cfg)
        return {'message': '每日推荐自动更新已开启（北京时间，每天最多自动发布一次）' if enabled else '每日推荐自动更新已暂停'}

    @app.post('/api/feedback')
    async def feedback(req: Request):
        d = await body(req)
        ensure_idle()
        kind = d.get('kind')
        value = d.get('value')
        if kind not in ('track', 'artist') or value not in ('like', 'avoid', 'reset'):
            raise ValueError('反馈类型无效')
        with engine.exclusive():
            f = store.get('feedback', {'tracks': {}, 'artists': {}})
            f.setdefault('tracks', {})
            f.setdefault('artists', {})
            raw = store.get('catalog', [])
            effective, _ = prepare_catalog(raw, store.get('metadata_overrides', {}))
            if kind == 'track':
                key = str(d.get('id', ''))
                t = next((t for t in effective if t['id'] == key), None)
                if not t and (not (value == 'reset' and key in f['tracks'])):
                    raise ValueError('请先从本地曲库中选择真实曲目')
                bucket = f['tracks']
                label = t['title'] if t else key
            else:
                key = str(d.get('artist', '')).strip()
                if not key or len(key) > 300 or (not (any((t.get('artist') == key for t in effective)) or (value == 'reset' and key in f['artists']))):
                    raise ValueError('请选择本地曲库中已存在的歌手')
                bucket = f['artists']
                label = key
            if value == 'reset':
                bucket.pop(key, None)
            else:
                if len(bucket) >= 10000 and key not in bucket:
                    raise ValueError('反馈记录已达上限')
                bucket[key] = {'value': value, 'label': label, 'updated_at': time.time()}
            store.set_many({'feedback': f, 'daily_plan': None})
        return {'message': '偏好已保存，仅影响助手每日推荐；未修改Plex评分或原文件。下次生成时生效。'}

    @app.get('/api/daily/report')
    def daily_report():
        data = public_daily(store) or {}
        return Response(json.dumps(data, ensure_ascii=False, indent=2), media_type='application/json', headers={'Content-Disposition': 'attachment; filename="daily-recommendation-report.json"'})
    from .daily_mix_v035 import attach_policy_routes
    attach_policy_routes(app, store, engine, body, ensure_idle)
    from .daily_mix_v036 import attach_v036_routes
    attach_v036_routes(app, store, engine, body, ensure_idle)
