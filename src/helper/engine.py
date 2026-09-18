"""Read-preview, explicit approval, append-only updates, protected rollback."""
from contextlib import contextmanager
import hashlib
import json
import math
import threading
import time
import uuid
from .clients import PlexClient, QQClient, PlexError, SourceError
from .names import short_title, TITLE_POLICY
from .rename import RenamingMixin
from .daily import DailyMixin
from .metadata import prepare_catalog
from .match import Catalog, match, normalize, title_key, artist_key, flags

class SafetyError(ValueError): pass

def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()

def state_ids(state):return [str(x['id']) for x in state['items']]
def fingerprint(state):return digest({'title':state['title'],'summary':state.get('summary',''),'ids':state_ids(state)})
def track_fingerprint(t):return digest({k:t.get(k) for k in ('id','title','artist','album','duration','available','guid','paths')})
def query_key(t):return digest({k:t.get(k) for k in ('title','artist','album','duration')})
def safe_error(exc):
    return str(exc)[:300] if isinstance(exc,(PlexError,SourceError,SafetyError)) else f'{type(exc).__name__}：操作失败；未自动重复写入，请检查环境或联系维护者'

SNAPSHOT_UNSAFE_STATUSES = frozenset(('prepared','uncertain','restoring'))

def _snapshot_created_at(row):
    try:
        value=float(row.get('created_at'))
        return value if math.isfinite(value) and value >= 0 else None
    except (TypeError,ValueError,OverflowError):
        return None

def _snapshot_stream(row):
    return str(row.get('category_id') or row.get('kind') or 'unknown')

def retain_snapshots(rows,referenced_ids,now,days=365,per_stream=50):
    """Bound completed history while preserving rollback and uncertain state."""
    referenced_ids={str(value) for value in referenced_ids or set() if value}
    cutoff=float(now)-max(0,int(days))*86400
    limit=max(0,int(per_stream))
    keep=set()
    completed={}
    for index,row in enumerate(rows or []):
        identifier=str(row.get('id') or '')
        created_at=_snapshot_created_at(row)
        if (identifier in referenced_ids or row.get('status') in SNAPSHOT_UNSAFE_STATUSES
                or created_at is None or created_at >= cutoff):
            keep.add(index)
        else:
            completed.setdefault(_snapshot_stream(row),[]).append((created_at,index))
    for stream_rows in completed.values():
        for _,index in sorted(stream_rows,key=lambda item:(-item[0],item[1]))[:limit]:
            keep.add(index)
    return [row for index,row in enumerate(rows or []) if index in keep]

class Engine(RenamingMixin, DailyMixin):
    def __init__(self,store,plex_factory=None,qq=None):
        self.store=store;self.gate=threading.Lock();self.stop=threading.Event()
        self.plex_factory=plex_factory or (lambda cfg:PlexClient(cfg['plex_url'],cfg['plex_token'],store=self.store))
        self.qq=qq or QQClient();self.status_lock=threading.Lock();self.job={'running':False,'message':'尚未运行','error':''}

    @contextmanager
    def exclusive(self):
        if not self.gate.acquire(blocking=False):raise SafetyError('已有任务执行中，请等待完成')
        try:yield
        finally:self.gate.release()

    def signature(self):
        cfg=self.store.get('settings')
        return digest({'connection':[cfg.get(k) for k in ('plex_url','plex_token','section','account_label')],
                       'sources':[{k:v for k,v in s.items() if k!='approved'} for s in self.store.get('sources')],
                       'min_tracks':cfg.get('min_tracks',5),'overrides':self.store.get('overrides'),'title_policy':TITLE_POLICY,
                       'metadata_overrides':self.store.get('metadata_overrides',{}),'match_policy':'v0.1.3'})
    def marker(self,cid):return f"[PCH:{self.store.get('installation_id')}:{cid}]"
    def progress(self,message):
        with self.status_lock:self.job['message']=message
    def preview(self,force_sources=False):
        with self.exclusive():return self._preview(force_sources)

    def _preview(self,force_sources=False):
        cfg=self.store.get('settings')
        if not cfg.get('plex_url') or not cfg.get('plex_token') or not cfg.get('section'):
            raise SafetyError('先配置Plex地址、Token和音乐资料库')
        self.progress('读取 Plex 音乐资料库（不写入）')
        p=self.plex_factory(cfg);identity=p.identity()
        tracks=p.tracks(cfg['section'])
        if not tracks:raise SafetyError('Plex资料库没有曲目；不会清空已有分类')
        effective,audit=prepare_catalog(tracks,self.store.get('metadata_overrides',{}))
        self.store.set('metadata_audit',audit)
        catalog=Catalog(effective)
        if hasattr(self.qq,'prepare_run'):self.qq.prepare_run(self.store,cfg.get('source_hours',24)*3600,force_sources,self.progress)
        from .theme import DEFAULT_THEME, BY_KEY, provision_sources, theme_key
        theme_cfg={**DEFAULT_THEME,**(self.store.get('theme_settings') or {})}
        if theme_cfg.get('enabled'):
            tags=self.store.get('qq_tags',[]) or []
            if not tags:
                self.progress('读取 QQ 主题目录')
                tags=self.qq.tags()
            sources,missing=provision_sources(self.store.get('sources'),tags,theme_cfg.get('selected',[]))
            usable=[]
            for source in sources:
                if source.get('kind')=='local_theme' and source.get('theme_generated'):
                    key=theme_key(source,tags)
                    if key:missing.append(key)
                    continue
                usable.append(source)
            self.store.set_many({
                'qq_tags':tags,
                'sources':usable,
                'theme_unavailable':[
                    {'key':key,'name':BY_KEY[key]['name'],'reason':'当前版本没有可靠来源，暂不生成该歌单'}
                    for key in dict.fromkeys(missing) if key in BY_KEY
                ],
            })
        playlists=p.playlists();cache=self.store.get('cache');managed=self.store.get('managed');overrides=self.store.get('overrides')
        snapshots=self.store.get('snapshots');groups=[];covered=set();now=time.time()
        for src in self.store.get('sources'):
            if not src.get('enabled',True):continue
            cid=src['id']; entry=cache.get(cid,{});error=''
            self.progress('检查分类来源：'+src['name'])
            source_sig=digest({k:v for k,v in src.items() if k not in ('approved','enabled')})
            changed_source=entry.get('source_signature') is not None and entry['source_signature']!=source_sig
            due=changed_source or not entry.get('data') or now-entry.get('fetched_at',0)>=cfg.get('source_hours',24)*3600
            # Failure backoff avoids hitting an unavailable upstream every page view.
            due=due and now-entry.get('last_attempt',0)>=600
            if force_sources or due:
                try:
                    data=self.qq.fetch(src)
                    if not data.get('tracks'):raise SourceError('来源没有曲目，保留旧数据')
                    entry={'data':data,'fetched_at':now,'last_attempt':now,'error':'','source_signature':source_sig}
                except Exception as exc:
                    entry={**entry,'last_attempt':now,'error':safe_error(exc)}
                cache[cid]=entry;self.store.set('cache',cache)
            error=entry.get('error','')
            title=managed.get(cid,{}).get('title') or short_title(src['name']); desired=[];unmatched=[];matched_rows=[];matches=set(); blocked=[]
            if error:blocked.append('来源刷新失败（保留缓存但暂停写入）：'+error)
            if not entry.get('data'):blocked.append('尚未取得完整来源')
            if changed_source and entry.get('source_signature')!=source_sig:blocked.append('分类来源范围已变化，尚未成功读取新版范围')
            for q in entry.get('data',{}).get('tracks',[]):
                key=query_key(q);ov=overrides.get(key);r=match(q,catalog)
                if ov:
                    local=catalog.by_id.get(str(ov.get('id')))
                    if ov.get('id')=='skip':r={'status':'manual_skip','id':None,'candidates':[]}
                    elif local and local.get('available',True):r={'status':'manual_match','id':local['id'],'candidates':[]}
                    else:r={'status':'override_missing','id':None,'candidates':[]}
                row={**q,**r,'query_key':key}
                if r['id']:
                    if r['id'] not in matches:desired.append(r['id']);matches.add(r['id'])
                    matched_rows.append({'source':q,'local':catalog.by_id[r['id']],'manual':bool(ov)})
                else:
                    row['candidate_details']=[catalog.by_id[k] for k in r['candidates'] if k in catalog.by_id]
                    unmatched.append(row)
            current=None;action='create';add=desired[:]
            if cid in managed:
                try:
                    current=p.playlist_state(managed[cid]['id'])
                    if self.marker(cid) not in current.get('summary','') or fingerprint(current)!=managed[cid]['fingerprint']:
                        blocked.append('歌单被手动修改或所有权标记变化：暂停，不覆盖')
                    exists=set(state_ids(current));add=[k for k in desired if k not in exists];action='append' if add else 'unchanged'
                except Exception as exc:blocked.append('读取程序管理歌单失败：'+safe_error(exc))
            elif any(x.get('title')==title for x in playlists):
                blocked.append('已存在同名未托管歌单：不会接管或覆盖')
            if any(s['category_id']==cid and s['status'] in ('prepared','uncertain','restoring') for s in snapshots):
                blocked.append('上次写入结果待核对：请先交给维护者核对快照和Plex')
            if len(desired)<cfg.get('min_tracks',5):blocked.append('本地可靠匹配不足最低歌曲数；不创建稀疏或空歌单')
            covered.update(matches)
            groups.append({'id':cid,'title':title,'kind':src['kind'],'source_title':entry.get('data',{}).get('title',''),
                           'fetched_at':entry.get('fetched_at'), 'origins':entry.get('data',{}).get('origins',[]) or [{'title':entry.get('data',{}).get('title',''),'url':entry.get('data',{}).get('url','')}],
                           'total':len(entry.get('data',{}).get('tracks',[])),'matched':len(desired),'desired':desired,'add':add,
                           'unmatched':unmatched,'matched_rows':matched_rows,'action':action,'blocked':blocked,'before':current,
                           'approved':src.get('approved',False),'reference_count':entry.get('data',{}).get('reference_count',len(entry.get('data',{}).get('origins',[])))})
        plan={'id':uuid.uuid4().hex,'created_at':time.time(),'signature':self.signature(),'machine':identity['machine'],
              'library_count':len(tracks),'covered':len(covered),'coverage':round(100*len(covered)/len(tracks),2),
              'unclassified':[t for t in tracks if t['id'] not in covered],'groups':groups,
              'track_fingerprints':{t['id']:track_fingerprint(t) for t in tracks},'applied':False,
              'metadata_review_count':sum(t['_metadata_blocked'] for t in effective)}
        self.store.set('catalog',tracks);self.store.set('plan',plan)
        self.store.log(f"预览完成：Plex {len(tracks)} 首；{len(groups)} 个分类；覆盖 {len(covered)} 首（可能有暂停项）；尚未写入")
        self.progress('预览已完成；点击查看匹配详情，确认后才写入Plex')
        return plan

    def apply(self,plan_id,automatic=False):
        with self.exclusive():return self._apply(plan_id,automatic)

    def _apply(self,plan_id,automatic=False):
        plan=self.store.get('plan');cfg=self.store.get('settings')
        if not plan or plan.get('id')!=plan_id or plan.get('signature')!=self.signature():raise SafetyError('配置或预览已变化，请重新生成预览')
        if plan.get('applied'):raise SafetyError('该预览已经确认过，请生成新预览')
        if time.time()-plan['created_at']>1800:raise SafetyError('预览超过30分钟，请重新预览')
        p=self.plex_factory(cfg)
        if p.identity()['machine']!=plan['machine']:raise SafetyError('Plex服务器身份变化，停止写入')
        # Refresh real metadata before any mutation. Do not trust old ratingKeys blindly.
        fresh={t['id']:track_fingerprint(t) for t in p.tracks(cfg['section'])}
        sources=self.store.get('sources');source_map={s['id']:s for s in sources}
        managed=self.store.get('managed');result={'written':0,'unchanged':0,'skipped':0,'errors':[]}
        for g in plan['groups']:
            src=source_map.get(g['id']);cid=g['id']
            if not src or not src.get('enabled',True) or g['blocked'] or automatic and not src.get('approved'):
                result['skipped']+=1;continue
            if any(fresh.get(k)!=plan['track_fingerprints'].get(k) for k in g['desired']):
                result['errors'].append(g['title']+'：Plex曲目在预览后变化，请重新预览');continue
            before=None
            try:
                if g['before']:
                    before=p.playlist_state(g['before']['id'])
                    if fingerprint(before)!=fingerprint(g['before']) or self.marker(cid) not in before.get('summary',''):raise SafetyError('预览后歌单被修改，停止覆盖')
                elif any(x.get('title')==g['title'] for x in p.playlists()):raise SafetyError('预览后出现同名歌单，不接管')
                if not g['add'] and before:
                    result['unchanged']+=1;src['approved']=True;continue
            except Exception as exc:
                result['errors'].append(g['title']+'：'+safe_error(exc));continue
            snap={'id':uuid.uuid4().hex,'category_id':cid,'title':g['title'],'created_at':time.time(),'status':'prepared','before':before,'after':None,'add':g['add'],'marker':self.marker(cid),'plan_id':plan_id}
            self._save_snapshot(snap)
            try:
                if before:
                    p.append(before['id'],g['add']);after=p.playlist_state(before['id'])
                else:after=p.create(g['title'],g['desired'],self.marker(cid))
                expected=(state_ids(before) if before else [])+g['add']
                if state_ids(after)!=expected or after['title']!=g['title'] or self.marker(cid) not in after.get('summary',''):
                    raise SafetyError('写入后回读与预期不一致，不标记成功，也不自动重试')
                snap.update(status='applied',after=after)
                self._save_snapshot(snap)
                managed[cid]={'id':after['id'],'fingerprint':fingerprint(after),'snapshot_id':snap['id'],'title':after['title']}
                self.store.set('managed',managed);src['approved']=True;result['written']+=1
            except Exception as exc:
                snap.update(status='uncertain',error=safe_error(exc));self._save_snapshot(snap)
                src['approved']=False;result['errors'].append(g['title']+'：'+safe_error(exc))
        self.store.set('sources',sources);plan['applied']=True;plan['result']=result;self.store.set('plan',plan)
        self.store.log(f"写入结果：成功{result['written']}项，不变{result['unchanged']}项，跳过{result['skipped']}项，失败{len(result['errors'])}项")
        return result

    def _save_snapshot(self,snapshot):
        rows=self.store.get('snapshots');found=False
        for i,r in enumerate(rows):
            if r['id']==snapshot['id']:rows[i]=snapshot;found=True;break
        if not found:rows.append(snapshot)
        referenced=set()
        for key in ('managed','daily_managed','retired_managed','smart_mix_managed'):
            values=self.store.get(key,{}) or {}
            if isinstance(values,dict) and 'snapshot_id' in values:
                records=(values,)
            elif isinstance(values,dict):
                records=values.values()
            elif isinstance(values,list):
                records=values
            else:
                records=()
            for record in records:
                if isinstance(record,dict) and record.get('snapshot_id'):
                    referenced.add(str(record['snapshot_id']))
        rows=retain_snapshots(rows,referenced,time.time())
        self.store.set('snapshots',rows)

    def restore(self,snapshot_id):
        with self.exclusive():
            rows=self.store.get('snapshots');snap=next((r for r in rows if r['id']==snapshot_id),None)
            if not snap or snap['status']!='applied':raise SafetyError('只能恢复已成功完成的写入快照')
            if snap.get('kind')=='daily':return self._restore_daily_snapshot(snap)
            if snap.get('kind')=='smart_mix':
                from .smart_mix_web import restore_smart_mix
                return restore_smart_mix(self,snap)
            cid=snap['category_id'];managed=self.store.get('managed')
            if cid not in managed or managed[cid]['snapshot_id']!=snapshot_id:raise SafetyError('不是该分类最近一次写入，请先恢复更新的快照')
            if snap.get('kind')=='rename':return self._restore_name_snapshot(snap,managed)
            p=self.plex_factory(self.store.get('settings'));after=p.playlist_state(managed[cid]['id'])
            if fingerprint(after)!=fingerprint(snap['after']) or self.marker(cid) not in after.get('summary',''):
                raise SafetyError('当前歌单与快照不同，可能有手工修改；拒绝覆盖')
            snap['status']='restoring';self._save_snapshot(snap)
            try:
                if snap['before'] is None:
                    p.delete_playlist(after['id']);del managed[cid]
                else:
                    before=snap['before'];n=len(before['items'])
                    if state_ids(after)[:n]!=state_ids(before):raise SafetyError('不是安全的追加记录，拒绝恢复')
                    p.remove_items(after['id'],[x['item_id'] for x in after['items'][n:]])
                    current=p.playlist_state(after['id'])
                    if fingerprint(current)!=fingerprint(before):raise SafetyError('恢复回读不一致')
                    prior=next((r for r in reversed(rows) if r['id']!=snapshot_id and r['category_id']==cid and r['status']=='applied'),None)
                    managed[cid]={'id':current['id'],'fingerprint':fingerprint(current),'snapshot_id':prior['id'] if prior else '', 'title':current['title']}
                self.store.set('managed',managed);snap['status']='restored';self._save_snapshot(snap)
                src=self.store.get('sources')
                for s in src:
                    if s['id']==cid:s['approved']=False
                self.store.set('sources',src);self.store.set('plan',None)
                self.store.log('已恢复并暂停该分类自动写入：'+snap['title']);return {'message':'已恢复；该分类需重新预览确认才会再次自动写入'}
            except Exception as exc:
                snap.update(status='uncertain',error=safe_error(exc));self._save_snapshot(snap)
                raise SafetyError('恢复结果待人工核对；不会继续自动写入') from None

    def auto(self):
        with self.exclusive():
            plan=self._preview(False);return self._apply(plan['id'],True)

    def start_job(self,kind,**kwargs):
        with self.status_lock:
            if self.job['running'] or self.gate.locked():raise SafetyError('已有任务在执行，请等完成')
            self.job={'running':True,'kind':kind,'message':'任务开始','error':'','started_at':time.time()}
        def work():
            try:
                if kind=='preview':self.preview(kwargs.get('force_sources',False))
                elif kind=='apply':self.apply(kwargs['plan_id'])
                elif kind=='restore':self.restore(kwargs['snapshot_id'])
                elif kind=='auto':self.auto()
                elif kind=='name_preview':self.preview_names()
                elif kind=='name_apply':self.apply_names(kwargs['plan_id'])
                elif kind=='daily_preview':self.preview_daily(force_full=bool(kwargs.get('force_full')))
                elif kind=='daily_apply':self.publish_daily(kwargs['plan_id'])
                elif kind=='daily_auto':self.daily_auto()
                elif kind=='incremental':self.refresh_new_tracks()
                elif kind=='daily_repair':self.repair_daily(kwargs['snapshot_id'])
                elif kind=='base_preview':self.preview_base()
                elif kind=='base_apply':self.apply_base(kwargs['plan_id'])
                elif kind=='single_check':self.check_single_connection()
                elif kind=='single_enrich':self.enrich_singles()
                else:raise SafetyError('未知任务')
                if kind!='incremental':self.progress('任务完成，请查看预览或写入结果')
            except Exception as exc:
                with self.status_lock:self.job['error']=safe_error(exc)
                self.store.log(safe_error(exc),'error')
            finally:
                finished_at=time.time()
                try:self.store.set('last_run',finished_at)
                finally:
                    with self.status_lock:self.job['running']=False;self.job['finished_at']=finished_at
        threading.Thread(target=work,daemon=True,name='playlist-job').start()
        return {'message':'已开始，在页面查看进度'}

    def scheduler(self):
        while not self.stop.wait(60):
            cfg=self.store.get('settings')
            if self.daily_due():
                try:self.start_job('daily_auto')
                except SafetyError:pass
                continue
            if cfg.get('auto_enabled') and time.time()-self.store.get('last_run',0)>=cfg.get('interval_minutes',10)*60:
                try:self.start_job('auto')
                except SafetyError:pass
