"""Bounded public category pagination; no login, audio URLs or evasion.

A completed *sample* is not all QQ playlists. Each reference playlist must still
pass the original completeness parser. Partial/interrupted runs keep caches but
are not silently installed as new theme evidence.
"""
from copy import deepcopy
import hashlib
import html
import json
import re
import time
from urllib.parse import urlsplit
import requests
from .clients import QQClient,SourceError,QQ_HOSTS,parse_playlist_id
from .single import SinglePaused
from .theme import theme_key,playlist_relevance,THEME_POLICY

from .theme_errors import ThemeFetchError,PlaylistUnavailableError,request_context,upstream_error
from .theme_unavailable import cached_error,remember,skip_row


def playlist_tags(raw):
    """Read only explicit tag names in known response containers, never titles."""
    rows=[]
    for key in ('tags','taglist','tag','tag_list'):
        value=raw.get(key) if isinstance(raw,dict) else None
        if isinstance(value,list):rows.extend(value)
    for key in ('dirinfo','basic'):
        item=raw.get(key) if isinstance(raw,dict) else None
        if isinstance(item,dict):rows.extend(playlist_tags(item))
    out=[]
    for row in rows[:100]:
        name=row.get('name') or row.get('tagname') if isinstance(row,dict) else row if isinstance(row,str) else None
        if isinstance(name,str) and name.strip() and len(name)<100:out.append(html.unescape(name.strip()))
    return list(dict.fromkeys(out))



class ReferenceIdentityConflict(SourceError):
    """One QQ MID was described as incompatible recordings by two references.

    This is a row-level evidence conflict, not an upstream/theme failure.  Only
    safe recording fields are retained for the review report.
    """
    def __init__(self,mid,prior,current,reasons):
        self.mid=str(mid or prior.get('id') or current.get('id') or '')
        def public(row):
            return {k:row.get(k) for k in ('title','artist','duration','album') if row.get(k) not in (None,'')}
        self.details={'mid':self.mid,'prior':public(prior),'current':public(current),'reasons':list(reasons)}
        super().__init__('同一QQ曲目编号的参考资料互相矛盾；已仅隔离这首歌，不影响其他歌曲整理')

def merge_reference_track(prior,q):
    if prior and (prior.get('title'),prior.get('artist'),prior.get('duration'))!=(q.get('title'),q.get('artist'),q.get('duration')):
        # Album metadata may differ, but contradictory identity for one MID is unsafe.
        from .theme import recording_title_key
        from .match import artist_key
        from .single import recording_flags,seconds
        pf,qf=recording_flags(prior),recording_flags(q)
        pd,qd=seconds(prior.get('duration')),seconds(q.get('duration'))
        conflicting_flags=bool(pf and qf and not (pf<=qf or qf<=pf))
        conflicting_duration=bool(pd is not None and qd is not None and abs(pd-qd)>max(3,min(6,pd*.01)))
        reasons=[]
        if recording_title_key(prior['title'])!=recording_title_key(q['title']):reasons.append('title')
        if artist_key(prior['artist'])!=artist_key(q['artist']):reasons.append('artist')
        if conflicting_flags:reasons.append('edition')
        if conflicting_duration:reasons.append('duration')
        if reasons:
            raise ReferenceIdentityConflict(prior.get('id') or q.get('id'),prior,q,reasons)
        if pf and not qf:q=prior  # retain an explicit edition, not a less detailed listing
    return q

class ThemeQQClient(QQClient):
    def __init__(self,session=None,delay=2.0,auth=None):
        super().__init__(session,delay);self.cancelled=lambda:False;self.count=0;self.budget=2400;self._seen_tags={};self.auth=auth

    def _check(self):
        if self.cancelled():raise SinglePaused('主题扩充已暂停；已读取歌单保留，继续时复用有效缓存。')

    def _json(self,url,params=None):
        self._check()
        if urlsplit(url).hostname not in QQ_HOSTS:raise ThemeFetchError('拒绝非QQ地址')
        context=request_context(url,params)
        while True:
            left=self.delay-(time.monotonic()-self.last)
            if left<=0:break
            time.sleep(min(.2,left));self._check()
        if self.count>=self.budget:raise ThemeFetchError('达到本轮主题请求预算，已暂停；保留缓存，稍后继续',600)
        self.count+=1;self.last=time.monotonic();started=self.last
        headers={}
        try:
            with self.session.get(url,params=params,timeout=(5,20),allow_redirects=False,stream=True) as r:
                headers=getattr(r,'headers',{})
                if r.status_code!=200:
                    kind={401:'auth_error',403:'access_denied',429:'rate_limit'}.get(r.status_code,'server_error' if r.status_code>=500 else 'http_error')
                    raise upstream_error(context,kind,r.status_code,headers=headers)
                chunks=[];size=0
                for chunk in r.iter_content(65536):
                    self._check();size+=len(chunk)
                    if size>8*1024*1024 or time.monotonic()-started>35:
                        raise upstream_error(context,'protocol_error',200,message='QQ响应超过大小/时间上限')
                    chunks.append(chunk)
                raw=json.loads(b''.join(chunks))
            if not isinstance(raw,dict):raise ValueError()
            self._response_context=context;self._response_headers=headers
            if raw.get('code') not in (0,'0',None) or raw.get('subcode',0) not in (0,'0'):
                raise upstream_error(context,'api_error',200,raw=raw,headers=headers)
            pid=str((params or {}).get('disstid',''))
            if pid and isinstance(raw.get('cdlist'),list) and raw['cdlist']:
                self._seen_tags[pid]=playlist_tags(raw['cdlist'][0])
            reqdata=(params or {}).get('data')
            if isinstance(reqdata,str):
                request=json.loads(reqdata)
                for key,value in request.items():
                    if not isinstance(value,dict):continue
                    pid=str((value.get('param') or {}).get('disstid',''))
                    data=(raw.get(key) or {}).get('data') if isinstance(raw.get(key),dict) else None
                    if pid and isinstance(data,dict):self._seen_tags[pid]=playlist_tags(data)
            return raw
        except requests.RequestException:
            raise upstream_error(context,'network_error') from None
        except (ValueError,TypeError):
            raise upstream_error(context,'protocol_error',200,message='QQ返回非JSON或数据结构异常') from None

    def _rpc(self,module,method,param,key='req'):
        params={'format':'json','data':json.dumps({'comm':{'ct':24,'cv':4747474,'uin':0},
                    key:{'module':module,'method':method,'param':param}},ensure_ascii=False,separators=(',',':'))}
        url='https://u.y.qq.com/cgi-bin/musicu.fcg'
        d=self._json(url,params);r=d.get(key)
        context=request_context(url,params);headers=getattr(self,'_response_headers',{})
        if not isinstance(r,dict):raise upstream_error(context,'protocol_error',200,raw=d,headers=headers)
        if d.get('code') not in (0,'0') or r.get('code') not in (0,'0'):
            raise upstream_error(context,'api_error',200,raw=d,rpc=r,headers=headers)
        if not isinstance(r.get('data'),dict):
            raise upstream_error(context,'protocol_error',200,raw=d,rpc=r,headers=headers)
        return r['data']

    def playlist(self,pid):
        # Production uses the app-owned QR credential and the current songlist
        # detail API.  Legacy transport remains only for existing synthetic
        # fixtures/custom clients that deliberately construct us without auth.
        data=self.auth.playlist(pid) if self.auth is not None else super().playlist(pid)
        tags=list(dict.fromkeys((data.get('tags') or [])+deepcopy(self._seen_tags.get(str(pid),[]))))
        rows={};conflicted=set();conflicts=[]
        for q in data.get('tracks',[]):
            mid=str(q.get('id',''))
            if not mid or mid in conflicted:continue
            try:rows[mid]=merge_reference_track(rows.get(mid),q)
            except ReferenceIdentityConflict as exc:
                conflicted.add(mid);rows.pop(mid,None);conflicts.append({**exc.details,'source_id':str(pid),'source_title':data.get('title','QQ歌单')})
        return {**data,'tracks':list(rows.values()),'tags':tags,'identity_conflict_rows':conflicts}

    def prepare_run(self,store,ttl=86400,force=False,progress=None):
        super().prepare_run(store,ttl,force,progress);self.count=0;self._privacy_streak=0

    def category_page(self,tagid,page=0,size=20):
        if isinstance(page,bool) or not isinstance(page,int) or not 0<=page<6:raise SourceError('分类页码超出安全范围')
        if isinstance(size,bool) or not isinstance(size,int) or not 1<=size<=20:raise SourceError('分类每页须1到20份')
        if not re.fullmatch(r'[0-9]{1,12}',str(tagid)):raise SourceError('无效QQ分类ID')
        d=self._rpc('playlist.PlayListCategoryServer','get_category_content',
             {'titleid':int(tagid),'caller':'0','category_id':int(tagid),'size':size,'page':page,'use_page':1},key='playlist')
        try:
            content=d['content'];arr=content['v_item']
            if not isinstance(arr,list) or len(arr)>size:raise ValueError()
            total=content.get('total_cnt')
            if total is not None:
                if isinstance(total,bool) or not re.fullmatch(r'\d+',str(total)):raise ValueError()
                total=int(total)
                if total>10000000:raise ValueError()
                expected=max(0,min(size,total-page*size))
                if len(arr)!=expected:raise SourceError('QQ分类分页数量不足或变化，保留旧缓存')
            rows=[];seen=set()
            for x in arr:
                b=x['basic'];pid=parse_playlist_id(str(b['tid']));title=html.unescape(str(b['title'])).strip()
                if not title or len(title)>1000 or pid in seen:raise SourceError('QQ分类分页重复ID或标题异常')
                seen.add(pid);rows.append({'id':pid,'title':title,'tags':playlist_tags(b)})
            return {'items':rows,'total':total,'has_more':page*size+len(rows)<total if total is not None else len(rows)==size}
        except (KeyError,TypeError,ValueError):raise SourceError('QQ分类分页结构变化，未把异常当成读取完成') from None

    def cached_category_page(self,tagid,page,size):
        store=self._cache_store;key=f'qq_theme_page:{tagid}:{page}:{size}';old=store.get(key);now=time.time()
        if old and not self._cache_force and now-old.get('fetched_at',0)<self._cache_ttl:return deepcopy(old['data'])
        data=self.category_page(tagid,page,size)
        store.set(key,{'data':data,'fetched_at':now});return deepcopy(data)

    def cached_playlist(self,pid):
        """Larger bounded theme cache, separate from individual track results."""
        self._check();pid=parse_playlist_id(pid);run=getattr(self,'_run_cache',{})
        denied=cached_error(getattr(self,'_cache_store',None),pid)
        if denied:raise denied
        if pid in run:return run[pid]
        store=getattr(self,'_cache_store',None);now=time.time();old=store.get('qq_playlist:'+pid) if store else None
        if old and not self._cache_force and now-old.get('fetched_at',0)<self._cache_ttl:
            data=deepcopy(old['data']);fetched=old['fetched_at']
        else:
            try:
                data=self.playlist(pid);fetched=time.time();self._privacy_streak=0
            except PlaylistUnavailableError as exc:
                remember(store,exc.details)
                # A refusal is scoped to this one reference.  Even several in a row
                # must not convert into a global theme failure.
                raise
            except SourceError as exc:
                details=deepcopy(getattr(exc,'details',{}) or {})
                if details.get('kind')!='playlist_snapshot_incomplete':raise
                # Keep the last *complete* positive cache if it exists.  Never store
                # the partial response, and never refresh its fetched_at timestamp.
                if old and isinstance(old.get('data'),dict) and isinstance(old['data'].get('tracks'),list):
                    data=deepcopy(old['data']);fetched=old.get('fetched_at',0)
                    data['reference_warning']=details
                    data['using_previous_complete_cache']=True
                else:
                    raise
            if store:
                index=store.get('qq_playlist_index',{});index[pid]={'time':fetched,'tracks':len(data['tracks'])}
                changes={'qq_playlist:'+pid:{'data':data,'fetched_at':fetched}}
                while len(index)>1500 or sum(v.get('tracks',0) for v in index.values())>750000:
                    oldest=min(index,key=lambda k:index[k]['time'])
                    if oldest==pid and len(index)==1:break
                    del index[oldest];changes['qq_playlist:'+oldest]=None
                changes['qq_playlist_index']=index;store.set_many(changes)
        data={**data,'fetched_at':fetched};run[pid]=data;self._run_cache=run;return data

    def fetch_category(self,src,matcher,settings,existing=()):
        max_pages=int(settings['max_pages']);size=int(settings.get('page_size',20))
        if not 1<=max_pages<=6 or not 1<=size<=20:raise SourceError('主题扩充范围超出安全预算')
        tracks={};origins=[];memberships={};seen=set();page_signatures=set();total=None
        covered=set(existing);initial=set(existing);allmatches=set();streak=0;pages=[];skipped=[];why='budget'
        conflicted_mids=set();conflict_rows=[]
        for page in range(max_pages):
            self._check();d=self.cached_category_page(src['value'],page,size)
            if d['total'] is not None:
                if total is not None and total!=d['total']:raise SourceError('QQ分类总量在分页期间变化，请稍后重试；旧结果保留')
                total=d['total']
            ids=tuple(p['id'] for p in d['items'])
            if ids and ids in page_signatures:raise SourceError('QQ重复返回同一分页，停止；不会当成新来源')
            page_signatures.add(ids);gain=set();read=0
            for pos,p in enumerate(d['items'],1):
                self._check()
                if p['id'] in seen:continue
                seen.add(p['id'])
                if self._progress:self._progress(f"主题 {src['name']}：第{page+1}/{max_pages}批，歌单{pos}/{len(d['items'])}；已核对本地{len(allmatches)}首")
                try:v=self.cached_playlist(p['id'])
                except PlaylistUnavailableError as exc:
                    skipped.append(skip_row(exc,p['title']))
                    if self._progress:self._progress('已跳过无法访问的参考歌单 '+p['id']+'，正在继续其他公开来源。')
                    continue
                except SourceError as exc:
                    details=deepcopy(getattr(exc,'details',{}) or {})
                    if details.get('kind')!='playlist_snapshot_incomplete':raise
                    skipped.append({'id':str(p['id']),'title':str(p['title'])[:200],
                        'reason':'本次分页不完整或读取期间数量变化；没有可用旧完整缓存，已跳过此来源',
                        'error':details})
                    if self._progress:self._progress('参考歌单 '+p['id']+' 本次分页不完整，已跳过并继续其他来源。')
                    continue
                warning=deepcopy(v.get('reference_warning') or {})
                if warning.get('kind')=='playlist_snapshot_incomplete':
                    skipped.append({'id':str(p['id']),'title':str(v.get('title') or p['title'])[:200],
                        'reason':'本次分页不完整或读取期间数量变化；继续使用上次完整缓存',
                        'error':warning,'used_previous_complete_cache':True})
                read+=1
                for cr in v.get('identity_conflict_rows',[]):
                    mid=str(cr.get('mid',''))
                    if mid:
                        conflicted_mids.add(mid);tracks.pop(mid,None);conflict_rows.append(deepcopy(cr))
                        for mids in memberships.values():
                            while mid in mids:mids.remove(mid)
                allowed,reason=playlist_relevance(v,theme_key(src),v.get('tags') or p.get('tags') or [])
                if not allowed:
                    skipped.append({'id':p['id'],'title':v['title'],'reason':reason});continue
                o={'id':p['id'],'title':v['title'],'url':v.get('url',''), 'tags':v.get('tags') or p.get('tags') or [],
                   'category_id':str(src['value']),'category_name':src['name'],'page':page+1,'fetched_at':v.get('fetched_at'), 'basis':reason}
                origins.append(o);memberships[p['id']]=[]
                for q in v['tracks']:
                    qid=str(q['id'])
                    if qid in conflicted_mids:continue
                    prior=tracks.get(qid)
                    try:q=merge_reference_track(prior,q)
                    except ReferenceIdentityConflict as exc:
                        conflicted_mids.add(qid);tracks.pop(qid,None)
                        conflict_rows.append({**exc.details,'source_id':p['id'],'source_title':v['title']})
                        # Remove this MID from every already-recorded membership.
                        for mids in memberships.values():
                            while qid in mids:mids.remove(qid)
                        continue
                    tracks[qid]=q;memberships[p['id']].append(qid)
                    r=matcher.match(q)
                    if r.get('id'):
                        tid=str(r['id']);allmatches.add(tid)
                        if tid not in covered:gain.add(tid)
                if len(tracks)>100000:raise SourceError('单主题超过10万来源曲目安全预算，停止并保留旧结果')
            covered.update(gain);pages.append({'page':page+1,'references':read,'new_local':len(gain),'sampled':len(d['items'])})
            if self._cache_store:self._cache_store.set('theme_progress',{'source_id':src['id'],'title':src['name'],'page':page+1,'max_pages':max_pages,'matched':len(allmatches),'new_local':len(covered-initial),'requests':self.count,'updated_at':time.time()})
            streak=streak+1 if not gain else 0
            if not d['has_more']:why='end';break
            if page+1>=settings.get('min_pages',2) and streak>=settings.get('plateau_pages',2):why='plateau';break
        if not origins:
            privacy=[r for r in skipped if r.get('error',{}).get('kind')=='playlist_unavailable']
            if privacy:
                exc=PlaylistUnavailableError(privacy[-1]['error'])
                exc.source_skips=skipped
                raise exc
            raise SourceError('本主题没有取得可用参考歌单；没有以空结果替换旧缓存')
        # Recompute matches from the final non-conflicted evidence so an earlier
        # sighting of a later-conflicted MID cannot inflate coverage.
        allmatches={str(r['id']) for q in tracks.values() if (r:=matcher.match(q)).get('id')}
        memberships={pid:[mid for mid in mids if mid not in conflicted_mids] for pid,mids in memberships.items()}
        return {'title':src['name'],'tracks':list(tracks.values()),'origins':origins,'memberships':memberships,
                'reference_count':len(origins),'theme_policy':THEME_POLICY,
                'theme_stats':{'pages':pages,'references':len(origins),'new_local':len(allmatches-initial),
                    'matched':len(allmatches),'available_references':total,'stop':why,'skipped':skipped,
                    'identity_conflicts':len(conflicted_mids),'identity_conflict_rows':conflict_rows[-100:],
                    'coverage_limited':any(r.get('error',{}).get('kind') in ('playlist_unavailable','playlist_snapshot_incomplete') for r in skipped),'is_full_qq':False}}
