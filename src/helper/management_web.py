"""Core configuration, source, job and report HTTP routes."""
import csv
import io
import json
import math
import re
import time
import uuid

from fastapi import Request
from fastapi.responses import Response

from .clients import parse_playlist_id, validate_base
from .engine import SafetyError, digest, query_key, safe_error
from .match import normalize
from .profile_web import connection_is_protected


def _connection_scope(settings):
    return digest([settings.get('plex_url', ''), settings.get('plex_token', '')])


def _csv_tracks(text):
    rows = []
    for index, row in enumerate(csv.DictReader(io.StringIO(str(text).lstrip('\ufeff')))):
        if index >= 10000:
            raise ValueError('CSV最多10000首')
        track = {
            'title': str(row.get('title') or row.get('歌名') or '').strip(),
            'artist': str(row.get('artist') or row.get('歌手') or '').strip(),
            'album': str(row.get('album') or row.get('专辑') or '').strip(),
        }
        if not track['title'] or not track['artist']:
            raise ValueError(f'CSV第{index + 2}行缺少歌名/歌手')
        if max(map(len, track.values())) > 500:
            raise ValueError('CSV字段过长')
        try:
            track['duration'] = float(row.get('duration') or row.get('时长秒') or 0)
        except ValueError:
            raise ValueError('CSV时长需为秒数') from None
        if not math.isfinite(track['duration']) or not 0 <= track['duration'] <= 86400:
            raise ValueError('CSV时长不合理')
        track['id'] = query_key(track)
        rows.append(track)
    if not rows:
        raise ValueError('CSV为空；首行需title,artist,album,duration')
    return rows


def attach_management_routes(app, store, engine, body, ensure_idle) -> None:
    @app.post('/api/settings')
    async def settings(req: Request):
        data = await body(req)
        ensure_idle()
        with engine.exclusive():
            old = store.get('settings')
            config = dict(old)
            for key in ('plex_url', 'section', 'account_label'):
                if key in data:
                    config[key] = str(data[key]).strip()
            if config['plex_url']:
                config['plex_url'] = validate_base(config['plex_url'])
            incoming_token = str(data.get('plex_token') or '').strip()
            if incoming_token:
                config['plex_token'] = incoming_token
            token = config['plex_token']
            if token and (
                not 8 <= len(token) <= 512
                or not token.isascii()
                or any(ord(character) < 33 for character in token)
            ):
                raise ValueError('Plex Token格式不正确')
            if config['section'] and not config['section'].isdigit():
                raise ValueError('资料库ID必须为数字')
            if len(config['account_label']) > 80:
                raise ValueError('账户备注过长')
            for key, lower, upper in [('source_hours', 1, 168), ('min_tracks', 1, 100)]:
                if key not in data:
                    continue
                try:
                    value = int(data[key])
                except (ValueError, TypeError):
                    raise ValueError(key + '需要整数') from None
                if not lower <= value <= upper:
                    raise ValueError(f'{key}必须在{lower}到{upper}之间')
                config[key] = value
            changed = any(
                old[key] != config[key]
                for key in ('plex_url', 'plex_token', 'section', 'account_label')
            )
            if changed and connection_is_protected(store):
                raise SafetyError(
                    '已有托管歌单，不能直接切换账户/服务器/资料库；请让Codex核对迁移，避免误改其他账户'
                )
            if changed:
                config['auto_enabled'] = False
                daily = store.get('daily_settings')
                daily['enabled'] = False
                store.set_many({'daily_settings': daily, 'daily_plan': None})
                if any(old.get(key) != config.get(key) for key in ('plex_url', 'plex_token', 'section')):
                    store.set_many({
                        'feedback': {'tracks': {}, 'artists': {}},
                        'metadata_overrides': {}, 'daily_history': [], 'catalog': [],
                        'metadata_audit': [], 'behavior_events': [],
                        'behavior_sessions': {}, 'behavior_status': {},
                    })
            if any(old.get(key) != config.get(key) for key in ('plex_url', 'plex_token')):
                store.set('plex_connection', None)
            store.set('settings', config)
            if old != config:
                store.set('plan', None)
        return {'message': '配置已保存；还未写入Plex歌单'}

    @app.post('/api/plex/check/legacy')
    def check():
        ensure_idle()
        with engine.exclusive():
            try:
                plex = engine.plex_factory(store.get('settings'))
                result = plex.identity()
                result['sections'] = plex.sections()
                result['existing_playlists'] = [row.get('title') for row in plex.playlists()][:20]
                result['checked_at'] = time.time()
                shown = {
                    key: result[key]
                    for key in (
                        'server', 'machine', 'version', 'sections',
                        'existing_playlists', 'checked_at',
                    )
                    if key in result
                }
                store.set('plex_connection', {
                    'scope': _connection_scope(store.get('settings')), 'result': shown,
                })
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
        data = await body(req)
        ensure_idle()
        with engine.exclusive():
            name = str(data.get('name', '')).strip()
            if not name or len(name) > 60:
                raise ValueError('分类名称需1—60个字')
            kind = data.get('kind')
            source = {
                'id': uuid.uuid4().hex, 'name': name, 'kind': kind,
                'enabled': True, 'approved': False,
            }
            if kind == 'qq_playlist':
                source['value'] = parse_playlist_id(data.get('value', ''))
            elif kind == 'qq_category':
                value = str(data.get('value', ''))
                if not any(tag['id'] == value for tag in store.get('qq_tags', [])):
                    raise ValueError('先读取真实QQ分类，再选择分类')
                source.update(
                    value=value,
                    limit=store.get('source_settings', {}).get('reference_limit', 12),
                )
            elif kind == 'csv':
                source.update(value='user-csv', csv_tracks=_csv_tracks(data.get('csv', '')))
            else:
                raise ValueError('不支持的来源类型')
            sources = store.get('sources')
            if len(sources) >= 40:
                raise ValueError('本版最多40个分类，避免生成过多重复歌单')
            if any(row['name'] == name for row in sources):
                raise ValueError('分类名称重复，请复用现有分类或改名')
            sources.append(source)
            store.set('sources', sources)
            store.set('plan', None)
        return {'message': '分类已添加；只保存来源，尚未创建Plex歌单', 'id': source['id']}

    @app.post('/api/sources/{cid}/toggle')
    async def toggle(cid, req: Request):
        data = await body(req)
        ensure_idle()
        with engine.exclusive():
            sources = store.get('sources')
            found = False
            for source in sources:
                if source['id'] == cid:
                    source['enabled'] = bool(data.get('enabled'))
                    source['approved'] = False
                    found = True
            if not found:
                raise ValueError('分类不存在')
            store.set('sources', sources)
            store.set('plan', None)
        return {'message': '已切换；已有Plex歌单保留，重新启用后需再次预览确认'}

    @app.post('/api/schedule')
    async def schedule(req: Request):
        data = await body(req)
        ensure_idle()
        with engine.exclusive():
            enabled = bool(data.get('enabled'))
            if enabled:
                raise SafetyError('独立定时开关已合并，请在首页开启“自动整理新歌”。')
            config = store.get('settings')
            config['auto_enabled'] = enabled
            store.set('settings', config)
            store.set('last_run', time.time())
        return {'message': '自动维护已开启' if enabled else '自动维护已暂停'}

    @app.get('/api/plan')
    def get_plan():
        return store.get('plan') or {}

    @app.post('/api/jobs/{kind}')
    async def jobs(kind, req: Request):
        data = await body(req)
        allowed = {
            'preview', 'apply', 'restore', 'name_preview', 'name_apply',
            'daily_preview', 'daily_apply', 'daily_repair', 'base_preview',
            'base_apply', 'single_check', 'single_enrich',
        }
        if kind not in allowed:
            raise ValueError('未知任务')
        confirmations = {
            'single_enrich': '请确认向QQ查询曲目资料，不发送音频或Plex凭据',
            'apply': '请明确确认写入Plex歌单',
            'restore': '请明确确认恢复变更',
            'name_apply': '请明确确认原地修改歌单名称',
            'daily_apply': '请明确确认发布每日推荐（仅更新本助手的每日歌单）',
            'daily_repair': '请明确确认修复上次每日推荐（只处理本助手自己的每日歌单）',
            'base_apply': '请明确确认写入全库基础分类歌单',
        }
        if kind in confirmations and data.get('confirm') is not True:
            raise SafetyError(confirmations[kind])
        return engine.start_job(
            kind,
            force_sources=bool(data.get('force_sources')),
            force_full=bool(data.get('force_full')),
            plan_id=str(data.get('plan_id', '')),
            snapshot_id=str(data.get('snapshot_id', '')),
        )

    @app.get('/api/snapshots')
    def snapshots():
        return {'items': [
            {
                **{key: value for key, value in snapshot.items() if key not in ('before', 'after', 'add', 'marker')},
                'before_count': len(snapshot['before']['items']) if snapshot['before'] else 0,
                'after_count': len(snapshot['after']['items']) if snapshot['after'] else None,
            }
            for snapshot in reversed(store.get('snapshots'))
        ]}

    @app.get('/api/library')
    def library(q: str = ''):
        if len(q) > 100:
            raise ValueError('查询过长')
        items = [
            track for track in store.get('catalog', [])
            if normalize(q) in normalize(track['title'] + ' ' + track['artist'])
        ]
        return {'items': items[:100], 'total': len(items)}

    @app.post('/api/overrides')
    async def override(req: Request):
        data = await body(req)
        ensure_idle()
        with engine.exclusive():
            key = str(data.get('query_key', ''))
            track_id = str(data.get('id', ''))
            note = str(data.get('note', '')).strip()
            if not re.fullmatch('[a-f0-9]{64}', key) or not note or len(note) > 200:
                raise ValueError('需有效请求标识和简短人工核对说明')
            if track_id != 'skip' and not any(
                track['id'] == track_id and track.get('available', True)
                for track in store.get('catalog', [])
            ):
                raise ValueError('请选择Plex中真实存在的可用曲目ID')
            rules = store.get('overrides')
            rules[key] = {'id': track_id, 'note': note}
            store.set('overrides', rules)
            store.set('plan', None)
        return {'message': '修正已保存；请重新预览，不会自动立即写入'}

    @app.get('/api/report')
    def report():
        plan = store.get('plan') or {}
        export = {
            key: value
            for key, value in plan.items()
            if key not in ('signature', 'track_fingerprints')
        }
        return Response(
            json.dumps(export, ensure_ascii=False, indent=2),
            media_type='application/json',
            headers={'Content-Disposition': 'attachment; filename="classification-report.json"'},
        )
