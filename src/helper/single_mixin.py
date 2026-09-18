"""Resumable, bounded QQ single metadata enrichment. No Plex mutation here."""
from collections import Counter
from copy import deepcopy
import json
import re
import threading
import time
from .single import (SINGLE_POLICY,MID,SingleSourceError,SinglePaused,compare_identity,
                     select_detail,normalized_fields,match_fingerprint,recording_title_key)
from .single_client import SingleQQClient
from .metadata import prepare_catalog
from .match import artist_key

DETAIL_TTL=30*86400
NEGATIVE_TTL=7*86400
DEFAULT_SINGLE={'enabled':False,'interval_hours':24}
STATUS_LABELS={
    'matched':'已核对并取得分类','matched_no_fields':'已核对，来源没有可识别分类',
    'no_candidate':'搜索建议未给出候选','no_verified_candidate':'候选未通过核对',
    'ambiguous':'多个版本无法可靠区分','candidate_limit':'候选过多，保留待核对',
    'metadata_conflict':'本地信息冲突，未自动认定版本','missing_local_identity':'歌名或歌手缺失',
    'missing_duration':'缺少可靠时长','unavailable':'Plex曲目不可用','title_mismatch':'歌名不一致',
    'artist_mismatch':'完整歌手不一致','duration_mismatch':'时长不一致','version_mismatch':'现场/DJ等版本不一致',
    'language_conflict':'语种证据冲突','language_unverified':'未确认本地标注的语种版本',
}

def _digest(v):
    from .engine import digest
    return digest(v)

def _valid_record(r,t,now):
    return bool(_same_track_record(r,t) and r.get('expires_at',0)>now)

def _same_track_record(r,t):
    """Age may trigger a full refresh, but it never turns an unchanged song into a new song."""
    if not isinstance(r,dict) or r.get('policy')!=SINGLE_POLICY:return False
    fingerprint=r.get('fingerprint')
    if fingerprint==match_fingerprint(t):return True
    # Early cache entries predated helper-only metadata audit fields. Reuse only
    # when every Plex identity field is still identical.
    legacy={k:v for k,v in t.items() if k not in ('_metadata_blocked','_metadata_status')}
    return fingerprint==match_fingerprint(legacy)

def _incremental_record_reusable(r,t,now):
    """Keep durable matches, but retry expired misses during incremental scans."""
    if not _same_track_record(r,t):return False
    if r.get('status') in ('matched','matched_no_fields'):return True
    return r.get('expires_at',0)>now

def adopt_matching_records(store,current_prefix,tracks,current):
    """Copy exact per-track evidence across connection-token scopes without querying QQ."""
    byid={str(t['id']):t for t in tracks};best={}
    for tid,record in (current or {}).items():
        if tid in byid and _same_track_record(record,byid[tid]):
            best[tid]=(float(record.get('checked_at') or 0),True,record)
    for key,record in store.get_prefix('single_result:').items():
        if not isinstance(record,dict) or 'id' not in record:continue
        tid=str(record['id']);track=byid.get(tid)
        if track is None or not _same_track_record(record,track):continue
        candidate=(float(record.get('checked_at') or 0),key.startswith(current_prefix),record)
        if tid not in best or candidate[:2]>best[tid][:2]:best[tid]=candidate
    adopted={};out={}
    for tid,(_,is_current,record) in best.items():
        if is_current:out[tid]=record;continue
        copy={**record,'adopted_from_prior_connection':True,'adopted_at':time.time()}
        adopted[current_prefix+tid]=copy;out[tid]=copy
    if adopted:store.set_many(adopted)
    return out

class SingleMixin:
    def _init_single(self,single_factory=None):
        self.single_pause=threading.Event()
        self.single_factory=single_factory or (lambda **kw:SingleQQClient(**kw))
        old=self.store.get('single_state')
        if old and old.get('status')=='running':
            self.store.set('single_state',{**old,'status':'paused','message':'上次运行中断；已完成缓存保留，可继续未完成曲目'})

    def single_connection_scope(self):
        from .connection_scope import stable_library_scope
        return stable_library_scope(self.store)

    def _single_scope(self,machine):return _digest([self.single_connection_scope(),machine])

    def _single_guard(self):
        from .engine import SafetyError
        cfg=self.store.get('settings')
        if not all(cfg.get(k) for k in ('plex_url','plex_token','section')):raise SafetyError('先保存有效的 Plex 连接和音乐资料库')
        hold=self.store.get('single_cooldown') or {}
        if hold.get('connection_scope')==self.single_connection_scope() and hold.get('until',0)>time.time():
            raise SafetyError('QQ访问保护冷却中，请到页面提示的时间后再继续；不要重复点击或绕过限制')

    def _single_hold(self,exc):
        until=time.time()+exc.cooldown
        self.store.set('single_cooldown',{'connection_scope':self.single_connection_scope(),'until':until,'reason':str(exc)})
        return until

    def _single_connection_valid(self):
        c=self.store.get('single_connection') or {}
        return bool(c.get('ok') and c.get('scope')==self.single_connection_scope() and time.time()-c.get('checked_at',0)<86400)

    def check_single_connection(self):
        with self.exclusive():return self._check_single_connection()

    def _check_single_connection(self):
        self._single_guard();self.single_pause.clear()
        client=self.single_factory(cancelled=lambda:self.stop.is_set() or self.single_pause.is_set(),budget=2)
        now=time.time();out={'ok':False,'scope':self.single_connection_scope(),'checked_at':now,
                            'network_location':'NAS助手容器','requests':0}
        try:
            self.progress('从 NAS 检测 QQ 单曲详情与搜索建议（仅2次元数据请求，不读取音频）')
            # Connectivity probe only. It is never linked to a local track automatically.
            detail=client.detail('004Z8Ihr0JIu5s')
            control={'title':'七里香','artist':'周杰伦','duration':299,'available':True,'album':''}
            if compare_identity(control,detail)!='matched' or not normalized_fields(detail)['languages']:
                raise SingleSourceError('QQ详情可达但探测曲目/字段不符合预期，停止整库补全')
            candidates=client.search('七里香 周杰伦')
            out.update(ok=True,requests=client.count,detail_fields=normalized_fields(detail),search_candidates=len(candidates),
                       message='NAS可读取QQ单曲详情和搜索建议。还未给本地歌曲匹配，也未修改Plex歌单。')
            self.store.set_many({'single_connection':out,'single_cooldown':None,
                'single_detail:'+detail['mid']:{'detail':detail,'fetched_at':time.time()}})
            return {k:v for k,v in out.items() if k!='scope'}
        except SinglePaused as exc:
            out.update(message=str(exc),requests=client.count)
            self.store.set('single_connection',out)
            return {k:v for k,v in out.items() if k!='scope'}
        except SingleSourceError as exc:
            out.update(message=str(exc),error_kind=exc.kind,requests=client.count,retry_after=self._single_hold(exc))
            self.store.set('single_connection',out)
            return {k:v for k,v in out.items() if k!='scope'}

    def _single_detail(self,client,mid):
        old=self.store.get('single_detail:'+mid);now=time.time()
        if old and now-old.get('fetched_at',0)<DETAIL_TTL:return {**deepcopy(old['detail']),'fetched_at':old['fetched_at']}
        d=client.detail(mid);self.store.set('single_detail:'+mid,{'detail':d,'fetched_at':now});return {**d,'fetched_at':now}

    def _single_search(self,client,query):
        key='single_search:'+_digest(query);now=time.time();old=self.store.get(key)
        if old and now-old.get('fetched_at',0)<86400:return deepcopy(old['items'])
        items=client.search(query);self.store.set(key,{'items':items,'fetched_at':now});return items

    def _single_hints(self,tracks,machine):
        plan=self.store.get('plan') or {};out={};byid={str(t['id']):t for t in tracks}
        if plan.get('machine')!=machine or plan.get('invalidated_reason'):return out
        for g in plan.get('groups') or []:
            for row in g.get('matched_rows') or []:
                tid=str((row.get('local') or {}).get('id',''));q=row.get('source') or {};mid=str(q.get('id',''));local=byid.get(tid)
                if local is None or not MID.fullmatch(mid):continue
                # A prior/manual playlist match is just a candidate source; recheck now.
                candidate={**q,'mid':mid,'singers':[],'language_values':[],'genre_values':[]}
                if compare_identity(local,candidate)=='matched':
                    out.setdefault(tid,[])
                    if mid not in out[tid]:out[tid].append(mid)
        return out

    def _single_lookup(self,t,hints,client):
        if not t.get('available',True):return {'status':'unavailable'}
        if t.get('_metadata_blocked'):return {'status':'metadata_conflict'}
        if not recording_title_key(t.get('title')) or not artist_key(t.get('artist')):return {'status':'missing_local_identity'}
        from .single import seconds
        if seconds(t.get('duration')) is None:return {'status':'missing_duration'}
        details=[];seen=set()
        def inspect(mid):
            if mid in seen:return
            if self.single_pause.is_set() or self.stop.is_set():raise SinglePaused('已暂停，已完成查询保留')
            seen.add(mid);details.append(self._single_detail(client,mid))
        if len(hints)>5:return {'status':'candidate_limit','message':'既有QQ匹配版本超过5个，未按排名猜选'}
        for mid in hints:inspect(mid)
        if details:
            result=select_detail(t,details)
            if result['status'] in ('matched','matched_no_fields','ambiguous'):return result
        name=str(t.get('title','')).strip()
        performers=re.sub(r'[、/;；]+',' ',str(t.get('artist',''))).strip()
        queries=[(name[:65]+' '+performers[:53]).strip()]
        # Second narrower suggestion query is a search aid, never an identity relaxation.
        first=re.split(r'[、/;；]',str(t.get('artist','')))[0].strip()
        short=re.sub(r'[（(][^()（）]*[)）]','',name).strip()
        alt=(short[:65]+' '+first[:53]).strip()
        if alt and alt not in queries:queries.append(alt)
        any_candidates=False
        for query in queries:
            candidates=self._single_search(client,query);any_candidates|=bool(candidates)
            suitable=[x for x in candidates if recording_title_key(x.get('title'))==recording_title_key(t.get('title'))
                      and artist_key(x.get('artist'))==artist_key(t.get('artist'))]
            if len(set(x['mid'] for x in suitable)|seen)>5:
                return {'status':'candidate_limit','message':'同名同歌手候选较多，未只取第一个冒充匹配'}
            for c in suitable:inspect(c['mid'])
            if details:
                r=select_detail(t,details)
                if r['status'] in ('matched','matched_no_fields','ambiguous'):return r
        return select_detail(t,details) if details else {'status':'no_verified_candidate' if any_candidates else 'no_candidate'}

    def request_single_pause(self):
        self.single_pause.set()
        return {'message':'已请求暂停；当前请求结束后停下，已完成结果保留，继续时不从头重查。'}

    def enrich_singles(self):
        with self.exclusive():return self._enrich_singles()

    def _enrich_singles(self,new_only=False,auto_connect=False):
        from .engine import SafetyError
        self.single_pause.clear();cfg=self.store.get('settings');self.progress('读取本地单曲清单（不要求完整专辑）')
        if not all(cfg.get(k) for k in ('plex_url','plex_token','section')):raise SafetyError('先保存有效的 Plex 连接和音乐资料库')
        p=self.plex_factory(cfg);machine=p.identity()['machine'];tracks=p.tracks(cfg['section'])
        if not tracks:raise SafetyError('Plex曲库为空，停止补全；不清空已保存记录')
        effective,audit=prepare_catalog(tracks,self.store.get('metadata_overrides',{}));scope=self._single_scope(machine)
        prefix='single_result:'+scope+':';saved=self.store.get_prefix(prefix)
        saved={str(v['id']):v for v in saved.values() if isinstance(v,dict) and 'id' in v}
        saved=adopt_matching_records(self.store,prefix,effective,saved)
        hints=self._single_hints(effective,machine);now=time.time()
        reusable=(lambda r,t:_incremental_record_reusable(r,t,now)) if new_only else lambda r,t:_valid_record(r,t,now)
        valid={str(t['id']):saved[str(t['id'])] for t in effective if reusable(saved.get(str(t['id'])),t)}
        queue=[t for t in effective if str(t['id']) not in valid]
        queue.sort(key=lambda t:(0 if hints.get(str(t['id'])) else 1,str(t['id'])))
        client=None
        state={'scope':scope,'connection_scope':self.single_connection_scope(),'machine':machine,
               'status':'running','started_at':now,'library_count':len(tracks),'processed':len(valid),'cached':len(valid),
               'new_count':len(queue),'remaining':len(queue),'requests':0,
               'message':'开始逐曲补全；仅存助手缓存，不修改歌曲或歌单','retry_after':None}
        self.store.set_many({'catalog':tracks,'metadata_audit':audit,'single_scope':scope,'single_state':state})
        def update():
            counts=Counter(r['status'] for r in valid.values())
            state.update(statuses=dict(counts),matched=counts.get('matched',0),
                         language_count=sum(bool((r.get('fields') or {}).get('languages')) for r in valid.values() if r['status']=='matched'),
                         genre_count=sum(bool((r.get('fields') or {}).get('genres')) for r in valid.values() if r['status']=='matched'),
                         processed=len(valid),remaining=len(tracks)-len(valid),requests=client.count if client else 0)
            self.store.set('single_state',state)
        update();invalidated=False
        try:
            if not queue:
                state.update(status='completed',message='没有发现需要查询的新增或有变化歌曲；旧资料继续复用。')
                return {k:v for k,v in state.items() if k not in ('scope','machine','connection_scope')}
            self._single_guard()
            if not self._single_connection_valid():
                if not auto_connect:raise SafetyError('先点击“检测 NAS 的 QQ 连接”；电脑检测结果不能代替容器检测。检测超过24小时也需重检。')
                check=self._check_single_connection()
                if not check.get('ok'):
                    state.update(status='blocked',message=check.get('message') or 'QQ连接检测未通过，已保留进度',
                                 retry_after=check.get('retry_after'))
                    return {k:v for k,v in state.items() if k not in ('scope','machine','connection_scope')}
            client=self.single_factory(cancelled=lambda:self.stop.is_set() or self.single_pause.is_set(),budget=10000)
            for t in queue:
                if self.single_pause.is_set() or self.stop.is_set():raise SinglePaused('已暂停；点击继续会复用缓存，不从头查询')
                tid=str(t['id']);state['message']=f"逐曲补全 {len(valid)+1}/{len(tracks)}：{t.get('title','')} / {t.get('artist','')}"
                self.progress(state['message']);self.store.set('single_state',state)
                result=self._single_lookup(t,hints.get(tid,[]),client);stamp=time.time()
                record={**result,'id':tid,'title':t.get('title'),'artist':t.get('artist'),'fingerprint':match_fingerprint(t),
                        'policy':SINGLE_POLICY,'checked_at':stamp,'expires_at':stamp+(DETAIL_TTL if result['status']=='matched' else NEGATIVE_TTL)}
                if result['status']=='matched':record['expires_at']=min(record['expires_at'],result['detail'].get('fetched_at',stamp)+DETAIL_TTL)
                valid[tid]=record
                # Independent per-track checkpoints, not repeatedly serializing the whole catalog.
                self.store.set(prefix+tid,record)
                self.store.set('single_revision',int(self.store.get('single_revision',0))+1)
                if not invalidated:
                    old=self.store.get('base_plan')
                    if old and not old.get('invalidated_reason'):
                        self.store.set('base_plan',{**old,'invalidated_reason':'QQ单曲资料已更新，请在补全结束后重新生成基础分类预览。'})
                    invalidated=True
                update()
            state.update(status='completed',message='本轮逐曲补全已完成；未匹配/缺字段保留待核对。')
        except SinglePaused as exc:state.update(status='paused',message=str(exc))
        except SingleSourceError as exc:
            state.update(status='blocked',message=str(exc),error_kind=exc.kind,retry_after=self._single_hold(exc))
        except Exception:
            state.update(status='error',message='本轮遇到异常并停止；已完成检查点保留，未修改歌单。')
            raise
        finally:
            state['finished_at']=time.time();update();self.store.set('single_last_run',time.time())
            self.store.log(f"单曲资料补全：{state['status']}；已处理{state['processed']}，取得可用分类{state.get('matched',0)}，请求{client.count if client else 0}；未写入Plex")
        return {k:v for k,v in state.items() if k not in ('scope','machine','connection_scope')}

    def single_attach(self,tracks,machine):
        # Pure cache adoption into copies; must use freshly prepared/effective identities.
        scope=self._single_scope(machine);saved=self.store.get_prefix('single_result:'+scope+':');now=time.time();out=[]
        for t in tracks:
            t=deepcopy(t);r=saved.get('single_result:'+scope+':'+str(t['id']))
            if _same_track_record(r,t) and r.get('status')=='matched':
                t['_qq_single']={'detail':deepcopy(r['detail']),'fields':deepcopy(r['fields']),
                                 'checked_at':r['checked_at'],'basis':r.get('basis','')}
            out.append(t)
        return out

    def single_status(self):
        cfg={**DEFAULT_SINGLE,**(self.store.get('single_settings') or {})};scope=self.single_connection_scope()
        c=self.store.get('single_connection') or {};state=self.store.get('single_state') or {}
        hold=self.store.get('single_cooldown') or {}
        if c.get('scope')!=scope:c={}
        if state.get('connection_scope')!=scope:state={}
        return {'settings':cfg,'connection':{k:v for k,v in c.items() if k!='scope'},
                'state':{k:v for k,v in state.items() if k not in ('scope','machine','connection_scope')},
                'cooldown':{k:v for k,v in hold.items() if k!='connection_scope'} if hold.get('connection_scope')==scope else {},
                'labels':STATUS_LABELS,'policy':SINGLE_POLICY}

    def single_report(self,offset=None,limit=50):
        public=self.single_status();state=self.store.get('single_state') or {};scope=state.get('scope')
        items=[]
        if state.get('connection_scope')==self.single_connection_scope() and scope:
            saved=self.store.get_prefix('single_result:'+scope+':')
            effective,_=prepare_catalog(self.store.get('catalog',[]),self.store.get('metadata_overrides',{}));byid={str(t['id']):t for t in effective}
            for r in saved.values():
                t=byid.get(str(r.get('id')))
                if t is None:continue
                row={k:v for k,v in r.items() if k not in ('fingerprint',)}
                row['status_label']=STATUS_LABELS.get(r.get('status'),r.get('status'))
                row['stale']=not _valid_record(r,t,time.time());items.append(row)
        items.sort(key=lambda x:(x.get('status')=='matched',str(x['id'])))
        total=len(items)
        if offset is not None:items=items[offset:offset+limit]
        return {**public,'items':items,'total':total,'offset':offset or 0,
                'next':offset+limit if offset is not None and offset+limit<total else None,
                'notice':'仅缓存的来源资料和元数据身份核对；不是音频指纹验证，不包含Plex密钥，也未改音乐文件。'}

    def set_single_schedule(self,enabled):
        from .engine import SafetyError
        cfg={**DEFAULT_SINGLE,**(self.store.get('single_settings') or {})}
        if enabled:
            self._single_guard()
            state=self.single_status()['state']
            if not self._single_connection_valid() or state.get('matched',0)<1:
                raise SafetyError('先在NAS完成连接检测并取得至少一首已核对的单曲资料，再开启增量补全')
        cfg['enabled']=bool(enabled);self.store.set('single_settings',cfg);self.store.set('single_last_run',time.time())
        return {'message':'已开启每日增量补全（只更新助手缓存；歌单仍需基础分类确认/维护）' if enabled else '单曲资料增量补全已暂停'}

    def single_due(self,now=None):
        now=now or time.time();cfg={**DEFAULT_SINGLE,**(self.store.get('single_settings') or {})}
        hold=self.store.get('single_cooldown') or {}
        return bool(cfg['enabled'] and now>=hold.get('until',0) and now-self.store.get('single_last_run',0)>=86400)

    def single_auto(self):
        with self.exclusive():
            if not (self.store.get('single_settings') or {}).get('enabled'):return {'status':'disabled'}
            if not self._single_connection_valid():
                check=self._check_single_connection()
                if not check['ok']:return check
            return self._enrich_singles()
