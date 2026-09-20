"""Lazy incremental maintenance built from the proven resumable scanner."""
from __future__ import annotations

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
            return self._preview(bool(force_sources))

    def refresh_new_tracks(self):
        """Find only missing/changed tracks, then append to already-approved playlists."""
        with self.exclusive():
            self.progress('检查 Plex 里有没有新增歌曲')
            single = self._enrich_singles(new_only=True, auto_connect=True)
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
