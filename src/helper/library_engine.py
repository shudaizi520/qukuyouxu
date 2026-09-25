"""Lazy incremental maintenance built from the proven resumable scanner."""
from __future__ import annotations

import copy
import time

from .base_mixin import BaseMixin
from .engine import Engine, WorkflowPaused
from .single_mixin import SingleMixin


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
        """Find only missing/changed tracks, then append to already-approved playlists."""
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
                'external': None, 'updated_at': time.time(), 'message': single.get('message') or '',
            }
            def finish_if_paused():
                stopped = self.single_pause.is_set() or self.workflow_pause.is_set()
                held = single.get('status') in ('paused', 'blocked')
                if not (stopped or held):
                    return False
                if stopped:
                    result.update(status='paused', message='新增歌曲检查已暂停，进度已经保存')
                result['updated_at'] = time.time()
                self.store.set('incremental_status', result)
                self._record_workflow_pause('incremental', result['message'])
                self.progress(result['message'])
                return True

            if finish_if_paused():
                return result
            if single.get('status') != 'completed':
                self.store.set('incremental_status', result)
                self.progress(result['message'] or '新增歌曲检查已暂停，进度已经保存')
                return result
            result['external'] = {
                'rematch': self.external.rematch_missing(),
                'refresh': self.external.auto_refresh(),
            }
            if not result['new_count']:
                result['message'] = '检查完成，没有发现需要查询的新增或有变化歌曲。'
                self.store.set('incremental_status', result)
                self.progress(result['message'])
                return result

            managed = self.store.get('managed', {}) or {}
            if any(str(key).startswith('base:') for key in managed):
                self.progress('新增歌曲资料已保存，正在补入已有基础分类歌单')
                plan = self._preview_base()
                if finish_if_paused():
                    return result
                result['base'] = self._apply_base(plan['id'], automatic=True)
                if finish_if_paused():
                    return result

            sources = self.store.get('sources', []) or []
            approved = {str(row.get('id')) for row in sources if row.get('approved') and row.get('enabled', True)}
            if approved:
                self.progress('正在补入已经确认过的主题歌单')
                plan = self._preview(False)
                if finish_if_paused():
                    return result
                result['theme'] = self._apply(plan['id'], automatic=True)
                if finish_if_paused():
                    return result

            if finish_if_paused():
                return result
            errors = []
            for part in ('base', 'theme'):
                errors.extend((result.get(part) or {}).get('errors') or [])
            if errors:
                result['status'] = 'attention'
                result['message'] = '新增歌曲已经检查；部分歌单触发保护并跳过，请查看运行记录。'
            else:
                result['message'] = f"新增歌曲检查完成：处理 {result['new_count']} 首，并更新已有分类。"
            result['updated_at'] = time.time()
            self.store.set('incremental_status', result)
            self.progress(result['message'])
            return result
