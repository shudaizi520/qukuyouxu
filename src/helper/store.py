"""Application-owned SQLite only. Never opens Plex's database or music files."""
import json
from contextlib import contextmanager
import os
from pathlib import Path
import sqlite3
import threading
import uuid

DEFAULT_SETTINGS={'plex_url':'','plex_token':'','section':'','account_label':'','interval_minutes':10,'source_hours':24,'min_tracks':5,'auto_enabled':False}

class Store:
    def __init__(self,root):
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True)
        self.path=self.root/'helper.sqlite3';self.lock=threading.RLock()
        with self._db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS state (k TEXT PRIMARY KEY, v TEXT NOT NULL)')
            from .behavior_store import ensure_behavior_schema
            ensure_behavior_schema(db)
        os.chmod(self.path,0o600)
        if not self.get('installation_id'):self.set('installation_id',uuid.uuid4().hex)
        if self.get('settings') is None:self.set('settings',dict(DEFAULT_SETTINGS))
        for k in ('sources','snapshots','events'):
            if self.get(k) is None:self.set(k,[])
        for k in ('cache','managed','overrides','metadata_overrides'):
            if self.get(k) is None:self.set(k,{})
        from .recommend import DEFAULT_DAILY
        from .base_mixin import DEFAULT_BASE
        if self.get('daily_settings') is None:self.set('daily_settings',dict(DEFAULT_DAILY))
        if self.get('base_settings') is None:self.set('base_settings',dict(DEFAULT_BASE))
        if self.get('feedback') is None:self.set('feedback',{'tracks':{},'artists':{}})
        if self.get('daily_history') is None:self.set('daily_history',[])
        if self.get('behavior_events') is None:self.set('behavior_events',[])
        if self.get('behavior_sessions') is None:self.set('behavior_sessions',{})
        if self.get('product_settings') is None:self.set('product_settings',{'behavior_enabled':True})
        if self.get('auth_sessions') is None:self.set('auth_sessions',[])
        self._upgrade_daily_policy()
        self._upgrade_base_policy()

    def _upgrade_daily_policy(self):
        from .recommend import DAILY_POLICY,DEFAULT_DAILY
        if self.get('daily_policy')==DAILY_POLICY:return
        old=self.get('daily_settings') or {};cfg={**DEFAULT_DAILY,**old}
        # Migrate only the old built-in diversity defaults; preserve explicit size/hour/favorite selections.
        if old.get('artist_cap',3)==3:cfg['artist_cap']=2
        if old.get('album_cap',2)==2:cfg['album_cap']=1
        cfg.setdefault('favorite_cap',4);cfg.setdefault('repeat_days',21)
        changes={'daily_policy':DAILY_POLICY,'daily_settings':cfg,'daily_plan':None,
                 'daily_notice':'每日推荐策略已更新：星标/收藏仍最多少量出现，并会与其它歌曲稳定打散；手动生成只生成预览，确认后才发布。'}
        old_plan=self.get('daily_plan')
        if old_plan and not old_plan.get('applied'):changes['daily_previous_plan']=old_plan
        self.set_many(changes)

    def _upgrade_base_policy(self):
        # Application state only. Never deletes playlists, files, user feedback,
        # or the theme/daily plans. Reconfirm only the corrected base subsystem.
        from .base import BASE_POLICY
        if self.get('base_policy')==BASE_POLICY:return
        cfg=self.get('base_settings',{})
        old=self.get('base_plan')
        had_base=bool(old or cfg.get('enabled') or cfg.get('approved') or
                      any(k.startswith('base:') for k in self.get('managed',{})))
        changes={'base_policy':BASE_POLICY}
        if had_base:
            notice='QQ单曲资料与语种证据规则已更新：旧基础预览已失效。请重新生成基础分类预览并确认；主题歌单和每日推荐保持不变。'
            changes.update(base_settings={**cfg,'enabled':False,'approved':False,'approved_policy':None},base_notice=notice)
            if old:
                changes['base_previous_policy_plan']=old
                changes['base_plan']={**old,'invalidated_reason':notice}
        self.set_many(changes)
    @contextmanager
    def _db(self):
        db=sqlite3.connect(self.path,timeout=20)
        try:
            db.execute('PRAGMA journal_mode=WAL');db.execute('PRAGMA synchronous=FULL')
            with db:
                yield db
        finally:
            db.close()
    def get(self,key,default=None):
        with self.lock, self._db() as db:
            r=db.execute('SELECT v FROM state WHERE k=?',(key,)).fetchone()
            return json.loads(r[0]) if r else default
    def get_prefix(self,prefix):
        if not isinstance(prefix,str) or not prefix:raise ValueError('需要非空状态前缀')
        with self.lock,self._db() as db:
            return {k:json.loads(v) for k,v in db.execute('SELECT k,v FROM state WHERE substr(k,1,?)=?',(len(prefix),prefix))}
    def set(self,key,value):
        with self.lock, self._db() as db:
            db.execute('INSERT INTO state VALUES (?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v',(key,json.dumps(value,ensure_ascii=False)))
    def set_many(self, values):
        # Encode before beginning the transaction: any failure changes no key.
        encoded=[(key,json.dumps(value,ensure_ascii=False)) for key,value in values.items()]
        with self.lock, self._db() as db:
            db.executemany('INSERT INTO state VALUES (?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v',encoded)
    def log(self,message,level='info'):
        import time
        with self.lock:
            events=self.get('events',[]);events.append({'time':time.time(),'level':level,'message':str(message)[:500]});self.set('events',events[-200:])
