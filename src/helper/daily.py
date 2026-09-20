"""Journaled daily recommendation lifecycle; independent of category append jobs."""
from datetime import datetime, timezone, timedelta
import time
import uuid
from .metadata import prepare_catalog
from .recommend import recommend, DEFAULT_DAILY, DAILY_POLICY, song_key
from .behavior import behavior_profile
from .behavior_store import BehaviorRepository
from .plex_webhook import load_behavior_snapshot
from .playlist_sync import has_exact_members, sync_owned_items
from .audience import filter_childrens_context
DAILY_CID = 'daily'
CST = timezone(timedelta(hours=8))
OBSOLETE_SAME_NAME_BLOCK = '存在同名非本助手托管的“每日推荐”，不接管'

def day_at(now):
    return datetime.fromtimestamp(now, CST).strftime('%Y-%m-%d')

def number_time(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def active_daily_blocks(plan):
    return [reason for reason in (plan.get('blocked') or []) if reason != OBSOLETE_SAME_NAME_BLOCK]


def rolling_preserve_ids(before, tracks, behavior_events, published_at, history=None,
                         max_consecutive=2):
    """Keep current playlist members that have no play/skip signal after publish."""
    if not before:
        return []
    published_at = number_time(published_at)
    listened = {
        str(row.get('id')) for row in tracks or []
        if number_time(row.get('last_viewed_at')) > published_at
    }
    for event in behavior_events or []:
        event_at = number_time(event.get('at') or event.get('viewed_at'))
        track_id = str(event.get('track_id') or event.get('id') or '')
        if track_id and event_at > published_at:
            listened.add(track_id)
    result = []
    for item in before.get('items', []):
        track_id = str(item.get('id') or '')
        consecutive = 0
        for row in reversed(history or []):
            if track_id in {str(value) for value in row.get('ids', []) or []}:
                consecutive += 1
            else:
                break
        if (track_id and track_id not in listened and track_id not in result
                and consecutive < max(1, int(max_consecutive))):
            result.append(track_id)
    return result


def published_daily_view(plan, playlist, published_at):
    """Keep a token-free, durable projection of the last verified publish."""
    items = []
    for row in plan.get('items', []) or []:
        public = {}
        for key in ('id', 'title', 'artist', 'album', 'bucket'):
            if row.get(key) is not None:
                public[key] = str(row.get(key))
        if row.get('reasons') is not None:
            public['reasons'] = [str(value)[:240] for value in row.get('reasons', []) if value]
        items.append(public)
    return {
        'plan_id': str(plan.get('id') or ''),
        'playlist_id': str(playlist.get('id') or ''),
        'title': str(playlist.get('title') or ''),
        'date': str(plan.get('date') or ''),
        'published_at': published_at,
        'count': len(items),
        'items': items,
    }

class DailyMixin:

    def daily_signature(self):
        from .engine import digest
        cfg = self.store.get('settings')
        daily = {**DEFAULT_DAILY, **self.store.get('daily_settings', {})}
        return digest({'connection': [cfg.get(k) for k in ('plex_url', 'plex_token', 'section', 'account_label')], 'rules': {k: v for k, v in daily.items() if k not in ('enabled', 'hour')}, 'feedback': self.store.get('feedback', {}), 'metadata': self.store.get('metadata_overrides', {}), 'policy': DAILY_POLICY, 'daily_playlist_target': self.store.get('daily_playlist_target')})

    def daily_scope(self):
        from .connection_scope import stable_library_scope
        return stable_library_scope(self.store)

    def _daily_features(self, tracks):
        from .engine import track_fingerprint
        plan = self.store.get('plan') or {}
        features = {}
        active = {s['id'] for s in self.store.get('sources') if s.get('enabled', True)}
        fresh = {t['id']: track_fingerprint(t) for t in tracks}
        cache = self.store.get('cache')
        if plan.get('signature') != self.signature() or plan.get('invalidated_reason'):
            return features
        for g in plan.get('groups', []):
            if g['id'] not in active:
                continue
            mapping = {}
            for r in g.get('matched_rows', []):
                tid = str(r['local']['id'])
                if fresh.get(tid) != plan.get('track_fingerprints', {}).get(tid):
                    continue
                features.setdefault(tid, []).append('分类:' + g['title'])
                sid = str(r['source'].get('id', ''))
                if sid:
                    mapping[sid] = tid
            for pid, sids in cache.get(g['id'], {}).get('data', {}).get('memberships', {}).items():
                for sid in sids:
                    if str(sid) in mapping:
                        features.setdefault(mapping[str(sid)], []).append('歌单:' + str(pid))
        return features

    def preview_daily(self, now=None, force_full=False):
        with self.exclusive():
            return self._preview_daily(time.time() if now is None else now, origin='manual', force_full=force_full)

    def _preview_daily(self, now, origin='manual', force_full=False):
        from .engine import SafetyError, fingerprint, track_fingerprint, safe_error
        cfg = self.store.get('settings')
        daily = {**DEFAULT_DAILY, **self.store.get('daily_settings', {})}
        if not cfg.get('plex_url') or not cfg.get('plex_token') or (not cfg.get('section')):
            raise SafetyError('先连接Plex并选择音乐资料库')
        self.progress('每日推荐：读取本地曲库及当前账户的评分、播放记录（不分析音频）')
        p = self.plex_factory(cfg)
        identity = p.identity()
        validate_daily_target(self, identity)
        raw = p.tracks(cfg['section'])
        if not raw:
            raise SafetyError('Plex未返回曲目，不覆盖已有每日推荐')
        effective, audit = prepare_catalog(raw, self.store.get('metadata_overrides', {}))
        managed = self.store.get('daily_managed')
        blocked = []
        before = None
        if any((x.get('category_id') == DAILY_CID and x.get('status') in ('prepared', 'uncertain', 'restoring') for x in self.store.get('snapshots'))):
            blocked.append('上一次每日歌单写入结果待核对，禁止自动重试')
        target_title = daily_target_title(self)
        if managed:
            try:
                if managed.get('scope') != self.daily_scope() or managed.get('machine') != identity['machine']:
                    raise SafetyError('每日歌单所属账户或服务器已变化')
                before = p.playlist_state(managed['id'])
                if before.get('title') != target_title:
                    raise SafetyError('每日歌单名称已变化，请改回“每日推荐”后重试')
            except Exception as exc:
                blocked.append(safe_error(exc))
        else:
            same_name = next((row for row in p.playlists() if row.get('title') == target_title), None)
            same_name_id = str((same_name or {}).get('id') or (same_name or {}).get('ratingKey') or '')
            if same_name_id:
                before = p.playlist_state(same_name_id)
        seed_ids = []
        generated = {x['id'] for x in self.store.get('managed').values()}
        if managed:
            generated.add(managed['id'])
        for pid in daily.get('seed_playlist_ids', []):
            if pid in generated:
                raise SafetyError('不能把助手自动生成的歌单当作个人收藏，会造成自我强化')
            self.progress('每日推荐：读取你明确选择的收藏歌单')
            seed_ids.extend(p.playlist_track_ids(pid))
        features = self._daily_features(raw)
        base_store = getattr(self.store, 'base', self.store)
        profile_id = str(getattr(self.store, 'profile_id', '') or '')
        if profile_id and hasattr(base_store, '_db'):
            behavior = load_behavior_snapshot(self.store, now)
            behavior_events = BehaviorRepository(base_store).list_events(profile_id, now)
        else:
            behavior_events = self.store.get('behavior_events', [])
            behavior = behavior_profile(behavior_events, now)
        audience = filter_childrens_context(effective, features, behavior_events, seed_ids)
        behavior = {
            track_id: state for track_id, state in behavior.items()
            if track_id not in audience['excluded_ids']
        }
        published_at = number_time((managed or {}).get('published_at'))
        if not published_at:
            published_at = max(
                [number_time(row.get('created_at')) for row in self.store.get('daily_history', [])] or [0]
            )
        preserve_ids = [] if force_full else rolling_preserve_ids(
            before, audience['tracks'], audience['events'], published_at,
            history=self.store.get('daily_history', []),
        )
        result = recommend_rotating(
            self, audience['tracks'], audience['features'], self.store.get('feedback', {}),
            daily, self.store.get('daily_history', []), audience['seed_ids'], now,
            self.store.get('installation_id') + '|' + day_at(now), behavior=behavior,
            preserve_ids=preserve_ids,
        )
        before_ids = [str(row.get('id')) for row in (before or {}).get('items', [])]
        selected_ids = [str(row.get('id')) for row in result.get('items', [])]
        result['rolling'] = {
            'mode': 'full' if force_full else 'rolling',
            'retained': len(preserve_ids),
            'consumed': max(0, len((before or {}).get('items', [])) - len(preserve_ids)),
            'unchanged': bool(before and selected_ids == before_ids
                              and len(selected_ids) == int(daily.get('size', 30))),
        }
        result['stats']['childrens_excluded_count'] = len(audience['excluded_ids'])
        if not result['items']:
            blocked.append('没有符合限制的可用候选，保留现有每日歌单')
        plan = {**result, 'id': uuid.uuid4().hex, 'created_at': now, 'date': day_at(now), 'signature': self.daily_signature(), 'machine': identity['machine'], 'scope': self.daily_scope(), 'before': before, 'blocked': blocked, 'applied': False, 'origin': origin, 'track_fingerprints': {t['id']: track_fingerprint(t) for t in raw}, 'metadata_review_count': sum((t['_metadata_blocked'] for t in effective))}
        save_rotating_plan(self, {'catalog': raw, 'metadata_audit': audit, 'daily_plan': plan})
        self.store.log(f"每日推荐预览：{len(plan['items'])}首；偏好依据{plan['stats']['positive_seed_count']}首；尚未写入")
        return plan

    def publish_daily(self, plan_id, now=None):
        with self.exclusive():
            return self._publish_daily(plan_id, time.time() if now is None else now)

    def _publish_daily(self, plan_id, now):
        from .engine import SafetyError, fingerprint, track_fingerprint, state_ids, safe_error
        plan = self.store.get('daily_plan') or {}
        cfg = self.store.get('settings')
        if plan.get('id') != plan_id or plan.get('signature') != self.daily_signature():
            raise SafetyError('每日预览或偏好已变化，请重新生成')
        if plan.get('applied'):
            raise SafetyError('这份每日推荐已经发布，不重复写入')
        blocked = active_daily_blocks(plan)
        if blocked:
            raise SafetyError('每日推荐暂不能发布：' + '；'.join(blocked))
        if plan.get('blocked') != blocked:
            plan['blocked'] = blocked
            self.store.set('daily_plan', plan)
        if not 0 <= now - plan['created_at'] <= 1800 or plan['date'] != day_at(now):
            raise SafetyError('每日预览已过期，请重新生成')
        if any((x.get('category_id') == DAILY_CID and x.get('status') in ('prepared', 'uncertain', 'restoring') for x in self.store.get('snapshots'))):
            raise SafetyError('存在结果待核对的每日歌单变更，不重试')
        p = self.plex_factory(cfg)
        if p.identity()['machine'] != plan['machine'] or plan['scope'] != self.daily_scope():
            raise SafetyError('Plex身份已变化')
        fresh = {t['id']: track_fingerprint(t) for t in p.tracks(cfg['section'])}
        ids = [x['id'] for x in plan['items']]
        planned_ids = list(ids)
        from .playlist_hub import apply_manual_edits
        ids = apply_manual_edits(self.store, 'daily', 'daily', ids)
        if (not ids or any(k not in fresh for k in ids)
                or any((fresh.get(k) != plan['track_fingerprints'].get(k) for k in planned_ids))):
            raise SafetyError('候选歌曲在预览后变化/不可用，未写入；请重新预览')
        before = plan['before']
        managed = self.store.get('daily_managed')
        if before:
            current = p.playlist_state(before['id'])
            if fingerprint(current) != fingerprint(before):
                raise SafetyError('每日歌单在预览后被修改，不覆盖')
        elif managed:
            raise SafetyError('每日歌单状态变化，请重新生成')
        else:
            same_name = next((row for row in p.playlists() if row.get('title') == daily_target_title(self)), None)
            same_name_id = str((same_name or {}).get('id') or (same_name or {}).get('ratingKey') or '')
            if same_name_id:
                before = p.playlist_state(same_name_id)
        snap = {'id': uuid.uuid4().hex, 'kind': 'daily', 'category_id': DAILY_CID, 'title': daily_target_title(self), 'created_at': now, 'status': 'prepared', 'before': before, 'after': None, 'add': ids, 'marker': self.marker(DAILY_CID), 'plan_id': plan_id, 'machine': plan['machine'], 'scope': plan['scope'], 'before_daily_record': managed, 'before_published_view': self.store.get('daily_published_view')}
        self._save_snapshot(snap)
        try:
            if before:
                after = sync_owned_items(p, before, ids)
            else:
                after = p.create(daily_target_title(self), ids, self.marker(DAILY_CID), description='仅播放本地音乐；每日推荐会按已确认设置更新成员，其他歌单不受影响。')
            actual_ids = state_ids(after)
            if (after['title'] != daily_target_title(self) or len(actual_ids) != len(ids)
                    or set(actual_ids) != set(ids)):
                raise SafetyError('每日歌单写入回读不符，停止自动维护')
            snap.update(status='applied', after=after)
            self._save_snapshot(snap)
            record = {'id': after['id'], 'title': after['title'], 'fingerprint': fingerprint(after), 'snapshot_id': snap['id'], 'machine': plan['machine'], 'scope': plan['scope'], 'date': plan['date'], 'published_at': now, 'count': len(after.get('items', []))}
            history = self.store.get('daily_history', [])
            history.append({'date': plan['date'], 'created_at': now, 'ids': ids, 'song_keys': [x.get('song_key') for x in plan['items'] if x.get('song_key')], 'plan_id': plan_id})
            plan.update(applied=True, result={'written': len(ids), 'playlist_id': after['id'], 'date': plan['date']})
            if ids != planned_ids:
                by_id = {str(row.get('id')): row for row in self.store.get('catalog', []) or []}
                original = {str(row.get('id')): row for row in plan.get('items', []) or []}
                plan['items'] = [dict(original.get(track_id) or by_id.get(track_id) or {'id': track_id}) for track_id in ids]
            published = published_daily_view(plan, after, now)
            self.store.set_many({'daily_managed': record, 'daily_history': history[-90:], 'daily_plan': plan, 'daily_published_view': published, 'daily_auto_suspension': None})
            self.store.log('每日推荐已发布：' + str(len(ids)) + '首；歌单ID保留用于后续更新')
            return plan['result']
        except Exception as exc:
            snap.update(status='uncertain', error=safe_error(exc))
            self._save_snapshot(snap)
            daily = {**DEFAULT_DAILY, **self.store.get('daily_settings', {})}
            daily['enabled'] = False
            self.store.set_many({'daily_settings': daily, 'daily_auto_suspension': {
                'reason': '每日歌单发布结果待核对', 'at': time.time(),
            }})
            raise SafetyError('每日歌单变更结果待核对，已暂停自动更新：' + safe_error(exc)) from None

    def repair_daily(self, snapshot_id, now=None):
        with self.exclusive():
            return self._repair_daily(snapshot_id, time.time() if now is None else now)

    def _repair_daily(self, snapshot_id, now):
        from .engine import SafetyError, fingerprint, state_ids, track_fingerprint
        rows = self.store.get('snapshots')
        snap = next((r for r in rows if r.get('id') == snapshot_id), None)
        if not snap or snap.get('kind') != 'daily' or snap.get('status') not in ('uncertain', 'prepared'):
            raise SafetyError('没有可安全修复的每日推荐记录')
        before = snap.get('before')
        desired = list(map(str, snap.get('add') or []))
        if not before or not desired or len(desired) > 100 or (len(set(desired)) != len(desired)):
            raise SafetyError('上次每日推荐记录不完整，拒绝自动修复')
        if snap.get('scope') != self.daily_scope():
            raise SafetyError('账户/服务器/资料库已变化，不能修复旧记录')
        cfg = self.store.get('settings')
        p = self.plex_factory(cfg)
        if p.identity()['machine'] != snap.get('machine'):
            raise SafetyError('Plex服务器身份已变化，不能修复')
        current = p.playlist_state(before['id'])
        if current['title'] != daily_target_title(self):
            raise SafetyError('当前歌单名称不是“每日推荐”，拒绝修改')
        if len(set(state_ids(current))) != len(current['items']):
            raise SafetyError('当前每日推荐含重复条目，需要人工核对')
        allowed = set(state_ids(before)) | set(desired)
        if any((k not in allowed for k in state_ids(current))):
            raise SafetyError('每日推荐出现不属于上次更新的新曲目，可能有手工修改，拒绝自动修复')
        fresh = {t['id']: track_fingerprint(t) for t in p.tracks(cfg['section']) if t.get('available', True)}
        if any((k not in fresh for k in desired)):
            raise SafetyError('上次推荐中的歌曲已从资料库移除或不可用，请重新生成')
        after = sync_owned_items(p, current, desired)
        actual_ids = state_ids(after)
        if (len(actual_ids) != len(desired) or set(actual_ids) != set(desired)
                or after['title'] != daily_target_title(self)):
            raise SafetyError('修复后回读不一致，停止自动维护')
        snap.update(status='applied', after=after, repaired_at=now, error='')
        self._save_snapshot(snap)
        previous_plan = self.store.get('daily_plan') or self.store.get('daily_previous_plan') or {}
        date = (previous_plan.get('date') if previous_plan.get('id') == snap.get('plan_id') else None) or day_at(snap.get('created_at') or now)
        record = {'id': after['id'], 'title': after['title'], 'fingerprint': fingerprint(after), 'snapshot_id': snap['id'], 'machine': snap['machine'], 'scope': snap['scope'], 'date': date, 'published_at': now}
        history = self.store.get('daily_history', [])
        if not any((h.get('plan_id') == snap.get('plan_id') for h in history)):
            history.append({'date': date, 'created_at': now, 'ids': desired, 'song_keys': [], 'plan_id': snap.get('plan_id')})
        repaired_view = (
            published_daily_view(previous_plan, after, now)
            if previous_plan.get('id') == snap.get('plan_id') and previous_plan.get('items')
            else None
        )
        changes = {'daily_managed': record, 'daily_history': history[-90:], 'daily_published_view': repaired_view, 'daily_notice': f'上次每日推荐发布已安全收敛为 {len(desired)} 首；旧曲目未继续累加。自动更新仍保持关闭，请重新生成下一批。'}
        if previous_plan.get('id') == snap.get('plan_id'):
            previous_plan = {**previous_plan, 'applied': True, 'result': {'written': len(desired), 'playlist_id': after['id'], 'date': date, 'repaired': True}}
            if self.store.get('daily_plan', {}).get('id') == previous_plan.get('id'):
                changes['daily_plan'] = previous_plan
            else:
                changes['daily_previous_plan'] = previous_plan
        daily = {**DEFAULT_DAILY, **self.store.get('daily_settings', {})}
        daily['enabled'] = False
        changes['daily_settings'] = daily
        changes['daily_auto_suspension'] = {'reason': '每日歌单修复后等待手动确认', 'at': now}
        self.store.set_many(changes)
        self.store.log(f'每日推荐修复完成：{len(desired)}首；已清理上次未收尾的旧成员')
        return {'written': len(desired), 'playlist_id': after['id'], 'date': date, 'repaired': True}

    def _restore_daily_snapshot(self, snap):
        from .engine import SafetyError, fingerprint, safe_error
        record = self.store.get('daily_managed')
        if not record or record.get('snapshot_id') != snap['id']:
            raise SafetyError('只能恢复最近一次每日推荐变更')
        if self.daily_scope() != snap['scope']:
            raise SafetyError('账户/服务器/资料库已变化，不能恢复')
        p = self.plex_factory(self.store.get('settings'))
        if p.identity()['machine'] != snap['machine']:
            raise SafetyError('服务器身份已变化')
        current = p.playlist_state(record['id'])
        if fingerprint(current) != fingerprint(snap['after']):
            raise SafetyError('每日歌单被手动改动，停止自动恢复')
        if snap['before']:
            raw = p.tracks(self.store.get('settings')['section'])
            usable = {str(t['id']) for t in raw if t.get('available', True)}
            if any((str(x['id']) not in usable for x in snap['before']['items'])):
                raise SafetyError('原每日歌单含已移除或不可用曲目，未修改当前歌单；请先核对')
        snap['status'] = 'restoring'
        self._save_snapshot(snap)
        daily = {**DEFAULT_DAILY, **self.store.get('daily_settings', {})}
        daily['enabled'] = False
        self.store.set_many({'daily_settings': daily, 'daily_auto_suspension': {
            'reason': '每日歌单恢复后等待手动确认', 'at': time.time(),
        }})
        try:
            if snap['before']:
                after = sync_owned_items(p, current, [x['id'] for x in snap['before']['items']])
                expected_ids = [str(x['id']) for x in snap['before']['items']]
                if (after.get('title') != snap['before'].get('title')
                        or after.get('summary', '') != snap['before'].get('summary', '')
                        or not has_exact_members(after, expected_ids)):
                    raise SafetyError('恢复回读不一致')
                restored = {**snap['before_daily_record'], 'fingerprint': fingerprint(after)}
            else:
                p.delete_playlist(record['id'])
                restored = None
            snap['status'] = 'restored'
            self._save_snapshot(snap)
            self.store.set_many({'daily_managed': restored, 'daily_plan': None, 'daily_history': [h for h in self.store.get('daily_history', []) if h.get('plan_id') != snap['plan_id']], 'daily_published_view': snap.get('before_published_view')})
            self.store.log('已恢复每日推荐变更；自动每日更新已暂停')
            return {'message': '每日推荐已恢复；自动更新暂停，分类歌单未动'}
        except Exception as exc:
            snap.update(status='uncertain', error=safe_error(exc))
            self._save_snapshot(snap)
            raise SafetyError('恢复每日歌单的结果待核对，自动更新已暂停') from None

    def daily_due(self, now=None, schedule=None):
        now = time.time() if now is None else now
        daily = {**DEFAULT_DAILY, **self.store.get('daily_settings', {})}
        if schedule:
            daily.update({key: schedule[key] for key in ('enabled', 'hour') if key in schedule})
        record = self.store.get('daily_managed')
        pending = self.store.get('daily_plan') or {}
        manual_waiting = bool(pending.get('origin') == 'manual' and (not pending.get('applied')) and (pending.get('date') == day_at(now)) and (0 <= now - number_time(pending.get('created_at')) <= 1800))
        return bool(daily['enabled'] and record and (not self.store.get('daily_auto_suspension')) and (not manual_waiting)
                    and self.store.get('daily_auto_checked_date') != day_at(now)
                    and (datetime.fromtimestamp(now, CST).hour >= daily['hour'])
                    and (now - self.store.get('daily_last_attempt', 0) >= 1800))

    def daily_auto(self, schedule=None, scheduled=False, now=None):
        with self.exclusive():
            now = time.time() if now is None else float(now)
            daily = {**DEFAULT_DAILY, **self.store.get('daily_settings', {})}
            if schedule:
                daily.update({key: schedule[key] for key in ('enabled', 'hour') if key in schedule})
            if self.store.get('daily_auto_suspension'):
                return {'status': 'suspended', 'message': '该用户的自动更新已暂停，请手动发布一次确认恢复'}
            pending = self.store.get('daily_plan') or {}
            manual_waiting = bool(
                pending.get('origin') == 'manual' and not pending.get('applied')
                and pending.get('date') == day_at(now)
                and 0 <= now - number_time(pending.get('created_at')) <= 1800
            )
            if scheduled and manual_waiting:
                return {
                    'status': 'deferred', 'message': '等待手动预览确认或过期',
                    'retry_at': number_time(pending.get('created_at')) + 1801,
                }
            if scheduled and now - number_time(self.store.get('daily_last_attempt')) < 1800:
                return {
                    'status': 'deferred', 'message': '等待上次尝试的安全间隔',
                    'retry_at': number_time(self.store.get('daily_last_attempt')) + 1800,
                }
            scheduled_ready = bool(
                scheduled and daily['enabled'] and self.store.get('daily_managed')
                and self.store.get('daily_auto_checked_date') != day_at(now)
            )
            if not scheduled_ready and not self.daily_due(now, schedule=schedule):
                return {'message': '今天已发布、尚未到时间或自动更新暂停'}
            self.store.set('daily_last_attempt', now)
            plan = self._preview_daily(now, origin='auto')
            self.store.set('daily_auto_checked_date', day_at(now))
            if plan['blocked']:
                daily = self.store.get('daily_settings')
                daily['enabled'] = False
                self.store.set_many({'daily_settings': daily, 'daily_auto_suspension': {
                    'reason': '每日推荐存在阻止项', 'at': now,
                }})
                from .engine import SafetyError
                raise SafetyError('每日推荐存在阻止项，已暂停自动更新：' + '；'.join(plan['blocked']))
            if plan.get('rolling', {}).get('unchanged'):
                self.store.set('daily_plan', None)
                return {'message': '没有新的播放进度，今日推荐保持不变', 'unchanged': True}
            return self._publish_daily(plan['id'], time.time())
from .restart import daily_target_title, validate_daily_target
from .rotation import recommend_rotating, save_rotating_plan
