"""Explicit, verified in-place renaming for application-owned Plex playlists.

All remote writes are title-only. Each write has a prepared snapshot before the
request; ambiguous responses block future maintenance instead of blind retries.
"""
from collections import Counter
from copy import deepcopy
import time
import uuid
from .names import short_title, name_key

UNRESOLVED = ('prepared', 'uncertain', 'restoring')


class RenamingMixin:
    def preview_names(self):
        from .engine import SafetyError, digest, fingerprint, safe_error
        with self.exclusive():
            cfg = self.store.get('settings')
            if not cfg.get('plex_url') or not cfg.get('plex_token'):
                raise SafetyError('请先保存 Plex 地址和 Token')
            p = self.plex_factory(cfg)
            identity = p.identity()
            lists = p.playlists()
            managed = self.store.get('managed')
            source_map = {s['id']: s for s in self.store.get('sources')}
            snapshots = self.store.get('snapshots')
            groups = []
            for cid, record in managed.items():
                self.progress('检查歌单名称：' + record.get('title', cid))
                row = {'category_id': cid, 'playlist_id': record['id'],
                       'old_title': record.get('title', ''), 'new_title': '',
                       'before': None, 'blocked': [], 'action': 'rename'}
                try:
                    if cid not in source_map:
                        raise SafetyError('来源管理记录缺失；不改名')
                    if record.get('machine') and record['machine'] != identity['machine']:
                        raise SafetyError('Plex 服务器身份与托管记录不符')
                    row['new_title'] = short_title(source_map[cid]['name'])
                    before = p.playlist_state(record['id'])
                    row['before'] = before
                    row['old_title'] = before['title']
                    if (before['id'] != record['id'] or self.marker(cid) not in before.get('summary', '')
                            or fingerprint(before) != record['fingerprint']):
                        raise SafetyError('歌单有手工修改或管理标记变化；跳过，不覆盖')
                    if any(s['category_id'] == cid and s['status'] in UNRESOLVED for s in snapshots):
                        raise SafetyError('有未完成或结果不明的写入快照；请先核对')
                    if before['title'] == row['new_title']:
                        row['action'] = 'unchanged'
                    elif any(name_key(x.get('title', '')) == name_key(row['new_title'])
                             and str(x.get('ratingKey', x.get('id', ''))) != record['id'] for x in lists):
                        raise SafetyError('目标短名已被另一个歌单使用；不合并或覆盖')
                except Exception as exc:
                    row['blocked'].append(safe_error(exc))
                groups.append(row)
            targets = Counter(name_key(g['new_title']) for g in groups if g['new_title'])
            for g in groups:
                if g['new_title'] and targets[name_key(g['new_title'])] > 1:
                    g['blocked'].append('多个分类会得到同一短名；跳过，避免混淆')
            plan = {'id': uuid.uuid4().hex, 'created_at': time.time(),
                    'signature': self.signature(), 'managed_signature': digest(managed),
                    'machine': identity['machine'], 'groups': groups, 'applied': False}
            self.store.set('name_plan', plan)
            self.store.log(f'短名预览完成：{sum(not g["blocked"] and g["action"]=="rename" for g in groups)} 个待改名；没有改动 Plex')
            return plan

    def _commit_rename_snapshot(self, snap, managed, extra=None):
        """Commit the journal and managed fingerprint together in our own DB."""
        rows = self.store.get('snapshots')
        rows = [snap if s['id'] == snap['id'] else s for s in rows]
        self.store.set_many({'snapshots': rows, 'managed': managed, **(extra or {})})

    def apply_names(self, plan_id):
        from .engine import SafetyError, digest, fingerprint, safe_error
        with self.exclusive():
            plan = self.store.get('name_plan')
            managed = self.store.get('managed')
            if (not plan or plan['id'] != plan_id or plan.get('applied')
                    or plan['signature'] != self.signature()
                    or plan['managed_signature'] != digest(managed)):
                raise SafetyError('短名预览或托管状态已变化，请重新预览短名')
            if time.time() - plan['created_at'] > 1800:
                raise SafetyError('短名预览超过30分钟，请重新预览')
            p = self.plex_factory(self.store.get('settings'))
            if p.identity()['machine'] != plan['machine']:
                raise SafetyError('Plex 服务器身份变化，停止改名')
            result = {'renamed': 0, 'unchanged': 0, 'skipped': 0, 'errors': [], 'details': []}
            for g in plan['groups']:
                cid = g['category_id']
                item = {'category_id': cid, 'old_title': g['old_title'], 'new_title': g['new_title']}
                if g['blocked']:
                    result['skipped'] += 1
                    result['details'].append({**item, 'status': '已跳过', 'reason': '；'.join(g['blocked'])})
                    continue
                try:
                    before = p.playlist_state(g['playlist_id'])
                    if before != g['before'] or self.marker(cid) not in before.get('summary', ''):
                        raise SafetyError('预览后歌单内容或名称变化；拒绝覆盖')
                    if any(s['category_id'] == cid and s['status'] in UNRESOLVED for s in self.store.get('snapshots')):
                        raise SafetyError('发现结果未核对的写入记录；停止改名')
                    if g['action'] == 'unchanged':
                        result['unchanged'] += 1
                        result['details'].append({**item, 'status': '已是短名', 'reason': ''})
                        continue
                    if any(name_key(x.get('title', '')) == name_key(g['new_title'])
                           and str(x.get('ratingKey', x.get('id', ''))) != before['id'] for x in p.playlists()):
                        raise SafetyError('预览后出现同名歌单；停止该项改名')
                except Exception as exc:
                    reason = safe_error(exc)
                    result['errors'].append(g['old_title'] + '：' + reason)
                    result['details'].append({**item, 'status': '未修改', 'reason': reason})
                    continue
                self.progress('原地改名：' + g['old_title'] + ' → ' + g['new_title'])
                snap = {'id': uuid.uuid4().hex, 'kind': 'rename', 'category_id': cid,
                        'title': g['new_title'], 'created_at': time.time(), 'status': 'prepared',
                        'before': before, 'after': None, 'add': [], 'marker': self.marker(cid),
                        'plan_id': plan_id, 'machine': plan['machine'],
                        'managed_before': deepcopy(managed[cid])}
                self._save_snapshot(snap)
                try:
                    p.rename(before['id'], g['new_title'])
                    after = p.playlist_state(before['id'])
                    expected = {**before, 'title': g['new_title']}
                    if after != expected:
                        raise SafetyError('改名后回读不一致；不会重试或删除重建')
                    snap.update(status='applied', after=after)
                    new_managed = deepcopy(managed)
                    new_managed[cid] = {**managed[cid], 'title': after['title'],
                                       'fingerprint': fingerprint(after), 'snapshot_id': snap['id'],
                                       'machine': plan['machine']}
                    self._commit_rename_snapshot(snap, new_managed)
                    managed = new_managed
                    result['renamed'] += 1
                    result['details'].append({**item, 'status': '已改名', 'reason': ''})
                except Exception as exc:
                    reason = safe_error(exc)
                    snap.update(status='uncertain', error=reason)
                    self._save_snapshot(snap)
                    result['errors'].append(g['old_title'] + '：' + reason)
                    result['details'].append({**item, 'status': '结果待核对', 'reason': reason})
            # Keep coverage visible, but an older write preview must never be applied
            # after playlist titles/fingerprints have changed.
            old_plan = self.store.get('plan')
            if old_plan:
                old_plan['signature'] = ''
                old_plan['invalidated_reason'] = '歌单名称检查已完成；如需补充歌曲，请重新生成分类预览。'
            plan.update(applied=True, result=result)
            self.store.set_many({'name_plan': plan, 'plan': old_plan})
            self.store.log(f'改名完成：成功{result["renamed"]}，不变{result["unchanged"]}，跳过{result["skipped"]}，失败{len(result["errors"])}；曲目不作增删')
            return result

    def _restore_name_snapshot(self, snap, managed):
        from .engine import SafetyError, fingerprint, safe_error
        cid = snap['category_id']
        p = self.plex_factory(self.store.get('settings'))
        if p.identity()['machine'] != snap['machine']:
            raise SafetyError('Plex 服务器身份变化，拒绝恢复改名')
        current = p.playlist_state(managed[cid]['id'])
        if current != snap['after'] or self.marker(cid) not in current.get('summary', ''):
            raise SafetyError('改名后又有手工修改，拒绝覆盖')
        if any(name_key(x.get('title', '')) == name_key(snap['before']['title'])
               and str(x.get('ratingKey', x.get('id', ''))) != current['id'] for x in p.playlists()):
            raise SafetyError('旧名称已被其他歌单使用，拒绝恢复')
        snap['status'] = 'restoring'
        self._save_snapshot(snap)
        try:
            p.rename(current['id'], snap['before']['title'])
            restored = p.playlist_state(current['id'])
            if restored != snap['before']:
                raise SafetyError('恢复名称后回读不一致')
            managed[cid] = {**snap['managed_before'], 'fingerprint': fingerprint(restored), 'title': restored['title']}
            sources = self.store.get('sources')
            for src in sources:
                if src['id'] == cid:
                    src['approved'] = False
            snap['status'] = 'restored'
            self._commit_rename_snapshot(snap, managed, {'sources': sources, 'plan': None, 'name_plan': None})
            self.store.log('已恢复歌单旧名称，未增删曲目：' + restored['title'])
            return {'message': '旧名称已恢复；曲目未改动，该分类需重新确认自动维护'}
        except Exception as exc:
            snap.update(status='uncertain', error=safe_error(exc))
            self._save_snapshot(snap)
            raise SafetyError('恢复名称的结果待核对；不会自动继续写入') from None
