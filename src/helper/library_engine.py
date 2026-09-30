"""Lazy incremental maintenance built from the proven resumable scanner."""
from __future__ import annotations

import copy
import time
import uuid

from .base_mixin import BaseMixin
from .engine import Engine, WorkflowPaused
from .library_discovery import new_discovery_groups
from .single_mixin import SingleMixin


def _pending_candidate_plan(plan, candidates):
    """Expose only new candidates while preserving the immutable full evidence plan."""
    candidate_ids = {str(row.get('id') or '') for row in candidates}
    pending = copy.deepcopy(plan)
    pending.update(id=uuid.uuid4().hex, created_at=time.time(), applied=False)
    pending.pop('result', None)
    pending['review_group_ids'] = sorted(candidate_ids)
    return pending


class LibraryEngine(SingleMixin, BaseMixin, Engine):
    def __init__(self, store, plex_factory=None, qq=None, single_factory=None):
        Engine.__init__(self, store, plex_factory=plex_factory, qq=qq)
        from .external_service import ExternalPlaylistService
        from .external_sources import ExternalProviderRegistry, SafeSourceHttp
        from .connection_scope import migrate_managed_scopes
        migrate_managed_scopes(store)
        self._init_single(single_factory=single_factory)
        self.external = ExternalPlaylistService(
            store, self.plex_factory,
            ExternalProviderRegistry(self.qq, SafeSourceHttp()),
            progress=self.workflow_progress,
        )

    def analyze_library(self, force_sources=True):
        """Resume full song enrichment, then derive a read-only category preview."""
        with self.exclusive():
            single = self._enrich_singles(new_only=False, auto_connect=True)
            if single.get("status") != "completed":
                message = single.get("message") or "整理已暂停，已完成的资料已保存"
                self._record_workflow_pause("preview", message)
                raise WorkflowPaused(message)
            if self.single_pause.is_set() or self.workflow_pause.is_set():
                message = "整理已暂停，已完成的资料已保存"
                self._record_workflow_pause("preview", message)
                raise WorkflowPaused(message)
            theme = self._preview(bool(force_sources))
            base = self._preview_base()
            return {'theme': theme, 'base': base}

    def apply_workflow(self, theme_plan_id, base_plan_id, selected_ids):
        """Apply one user confirmation across theme and QQ-field categories."""
        with self.exclusive():
            selected = {str(value) for value in (selected_ids or set())}
            theme_plan = copy.deepcopy(self.store.get('plan') or {})
            base_plan = self.store.get('base_plan') or {}
            if not theme_plan_id and not base_plan_id:
                raise ValueError('分类预览已经变化，请重新整理')
            if theme_plan_id and str(theme_plan.get('id') or '') != str(theme_plan_id):
                raise ValueError('主题分类预览已经变化，请重新整理')
            if base_plan_id and str(base_plan.get('id') or '') != str(base_plan_id):
                raise ValueError('基础分类预览已经变化，请重新整理')
            if theme_plan_id:
                for group in theme_plan.get('groups') or []:
                    if str(group.get('id') or '') not in selected:
                        group['blocked'] = list(group.get('blocked') or []) + ['本次未选择']
                self.store.set('plan', theme_plan)
            base_ids = {
                str(group.get('id') or '') for group in base_plan.get('groups') or []
                if str(group.get('id') or '') in selected
            }
            empty = {'written': 0, 'unchanged': 0, 'skipped': 0, 'errors': []}
            base_result = self._apply_base(base_plan_id, False, allowed_ids=base_ids) if base_plan_id else dict(empty)
            theme_result = Engine._apply(self, theme_plan_id, False) if theme_plan_id else dict(empty)
            result = {
                'written': int(base_result.get('written') or 0) + int(theme_result.get('written') or 0),
                'unchanged': int(base_result.get('unchanged') or 0) + int(theme_result.get('unchanged') or 0),
                'skipped': int(base_result.get('skipped') or 0) + int(theme_result.get('skipped') or 0),
                'errors': list(base_result.get('errors') or []) + list(theme_result.get('errors') or []),
                'base': base_result, 'theme': theme_result,
            }
            self.store.set('workflow_apply_result', result)
            return result

    def refresh_new_tracks(self):
        """Refresh evidence and converge every enabled, approved managed playlist."""
        with self.exclusive():
            self.progress('检查 Plex 里有没有新增歌曲')
            try:
                single = self._enrich_singles(new_only=True, auto_connect=True)
            except Exception as exc:
                from .clients import PlexError, SourceError
                if not isinstance(exc, (PlexError, SourceError)):
                    raise
                from .scheduler_retry import TransientScheduleError
                raise TransientScheduleError('新增歌曲只读检查暂时不可用') from exc
            result = {
                'status': single.get('status'), 'new_count': int(single.get('new_count') or 0),
                'processed': int(single.get('processed') or 0), 'base': None, 'theme': None,
                'external': None, 'auto_added_count': 0, 'candidate_count': 0,
                'updated_at': time.time(), 'message': single.get('message') or '',
            }
            base_candidates = []
            theme_candidates = []
            base_candidate_plan = None
            theme_candidate_plan = None
            auto_added_ids = set()

            def retryable_read(message, operation):
                try:
                    return operation()
                except Exception as exc:
                    from .clients import PlexError
                    if not isinstance(exc,PlexError):raise
                    from .scheduler_retry import TransientScheduleError
                    raise TransientScheduleError(message) from exc

            def checkpoint(message=None, status=None):
                errors = [
                    value
                    for key in ('base', 'theme')
                    for value in ((result.get(key) or {}).get('errors') or [])
                ]
                result.update(
                    auto_added_count=len(auto_added_ids),
                    candidate_count=len(base_candidates) + len(theme_candidates),
                    updated_at=time.time(),
                )
                if message is not None:
                    result['message'] = message
                if status is not None:
                    result['status'] = status
                elif errors:
                    result['status'] = 'attention'
                self.store.set('incremental_status', result)
                if errors:
                    parts = [result.get(key) or {} for key in ('base', 'theme')]
                    self.store.set('library_maintenance_attention', {
                        'status': 'attention', 'message': result['message'],
                        'written': sum(int(row.get('written') or 0) for row in parts),
                        'unchanged': sum(int(row.get('unchanged') or 0) for row in parts),
                        'skipped': sum(int(row.get('skipped') or 0) for row in parts),
                        'errors': list(errors), 'updated_at': result['updated_at'],
                    })
                # Candidate plans are written after the status timestamp so a
                # restart still sees them as newer review work.
                if theme_candidates:
                    self.store.set('plan', _pending_candidate_plan(
                        theme_candidate_plan, theme_candidates,
                    ))
                if base_candidates:
                    self.store.set('base_plan', _pending_candidate_plan(
                        base_candidate_plan, base_candidates,
                    ))
                return errors

            def finish_if_paused():
                stopped = self.single_pause.is_set() or self.workflow_pause.is_set()
                held = single.get('status') in ('paused', 'blocked')
                if not (stopped or held):
                    return False
                if stopped:
                    result.update(status='paused', message='新增歌曲检查已暂停，进度已经保存')
                checkpoint(result['message'], result['status'])
                self._record_workflow_pause('incremental', result['message'])
                self.progress(result['message'])
                return True

            if finish_if_paused():
                return result
            if single.get('status') != 'completed':
                checkpoint(result['message'], result['status'])
                self.progress(result['message'] or '新增歌曲检查已暂停，进度已经保存')
                return result
            result['external'] = {
                'rematch': self.external.rematch_missing(),
                'refresh': self.external.auto_refresh(),
            }
            managed = self.store.get('managed', {}) or {}
            sources = self.store.get('sources', []) or []
            approved = {str(row.get('id')) for row in sources if row.get('approved') and row.get('enabled', True)}
            if approved or (result['new_count'] and sources):
                self.progress('正在补入已经确认过的主题歌单')
                plan=retryable_read('主题分类读取暂时不可用',lambda:self._preview(False))
                automatically_handled = {**managed, **{key: {} for key in approved}}
                theme_candidates = new_discovery_groups(plan, automatically_handled)
                theme_candidate_plan = plan
                if finish_if_paused():
                    return result
                if approved:
                    result['theme']=retryable_read(
                        '主题分类读取暂时不可用',
                        lambda:self._apply(plan['id'],automatic=True),
                    )
                    auto_added_ids.update(map(str, result['theme'].get('added_ids') or []))
                    checkpoint('新增歌曲检查进行中；主题歌单维护结果已经保存。')
                    if finish_if_paused():
                        return result
                if theme_candidates:
                    # Base classification consumes theme evidence. Keep the
                    # complete immutable theme plan available before building it;
                    # review_group_ids only controls what the UI may confirm.
                    self.store.set('plan', _pending_candidate_plan(
                        theme_candidate_plan, theme_candidates,
                    ))

            managed = self.store.get('managed', {}) or {}
            base_ids = {str(key) for key in managed if str(key).startswith('base:')}
            if base_ids or result['new_count']:
                self.progress('新增歌曲资料已保存，正在补入已有基础分类歌单')
                plan=retryable_read('基础分类读取暂时不可用',self._preview_base)
                base_candidates = new_discovery_groups(plan, managed)
                base_candidate_plan = plan
                if finish_if_paused():
                    return result
                if base_ids:
                    result['base']=retryable_read(
                        '基础分类读取暂时不可用',
                        lambda:self._apply_base(plan['id'],automatic=True),
                    )
                    auto_added_ids.update(map(str, result['base'].get('added_ids') or []))
                    checkpoint('新增歌曲检查进行中；基础分类维护结果已经保存。')
                    if finish_if_paused():
                        return result

            if finish_if_paused():
                return result
            errors = []
            for part in ('base', 'theme'):
                errors.extend((result.get(part) or {}).get('errors') or [])
            result['auto_added_count'] = len(auto_added_ids)
            result['candidate_count'] = len(base_candidates) + len(theme_candidates)
            if errors:
                result['status'] = 'attention'
            if result['new_count']:
                parts = [f"新增歌曲检查完成：处理 {result['new_count']} 首"]
                if result['auto_added_count']:
                    parts.append(f"{result['auto_added_count']} 首已自动加入已有歌单")
                if result['candidate_count']:
                    parts.append(f"发现 {result['candidate_count']} 个新歌单等待确认")
                if errors:
                    parts.append('部分已有歌单触发保护并跳过，请查看运行记录')
                elif len(parts) == 1:
                    parts.append('没有符合现有歌单或新分类门槛的歌曲')
                result['message'] = '；'.join(parts) + '。'
            elif result['candidate_count']:
                result['message'] = f"检查完成，没有新增歌曲；发现 {result['candidate_count']} 个新歌单等待确认。"
                if errors:
                    result['message'] += ' 部分已有歌单触发保护并跳过，请查看运行记录。'
            elif errors:
                result['message'] = '新增歌曲已经检查；部分已有歌单触发保护并跳过，请查看运行记录。'
            else:
                result['message'] = '检查完成，没有新增歌曲；已检查并恢复程序管理的歌单。'
            checkpoint(result['message'], 'attention' if errors else result['status'])
            if not errors:
                self.store.set('library_maintenance_attention', None)
            self.progress(result['message'])
            retryable=[]
            for part in ('base','theme'):
                retryable.extend((result.get(part) or {}).get('retryable_errors') or [])
            if retryable:
                from .scheduler_retry import TransientScheduleError
                raise TransientScheduleError('Plex 托管歌单暂时无法完成同步')
            conflicts=[]
            for part in ('base','theme'):
                conflicts.extend((result.get(part) or {}).get('conflicts') or [])
            if conflicts:
                from .engine import SafetyError
                raise SafetyError('程序管理歌单存在身份、范围或证据冲突，请查看运行记录')
            return result
