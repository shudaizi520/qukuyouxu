"""Theme evidence joins and home profile. No new Plex writer is introduced."""
from copy import deepcopy
import time
from .theme import DEFAULT_THEME,TOPICS,BY_KEY,THEME_POLICY,ThemeMatcher,theme_key,provision_sources,recording_topics
from .theme_client import ThemeFetchError,merge_reference_track,ReferenceIdentityConflict
from .single import match_fingerprint
from .metadata import prepare_catalog
from .clients import SourceError
from .theme_errors import PlaylistUnavailableError,playlist_privacy_details
from .theme_unavailable import remember,active,skip_row,invalid_cached_sources


class ThemeMixin:
    def _init_theme(self):
        self._init_playlist_isolation()
        self._init_theme_recovery()
        if self.store.get('theme_settings') is None:self.store.set('theme_settings',deepcopy(DEFAULT_THEME))
        if self.store.get('theme_exclusions') is None:self.store.set('theme_exclusions',{})
        if self.store.get('theme_policy')==THEME_POLICY:return
        plan=self.store.get('plan');base=self.store.get('base_plan');wf=self.workflow_settings()
        changes={'theme_policy':THEME_POLICY,'theme_notice':'主题关联已升级：原缓存、歌单和每日推荐保留。请点一次整理，检查新增歌曲及来源后确认。',
                 'workflow_settings':{**wf,'enabled':False,'approved_scope':''},'workflow_pending':None}
        if plan:
            changes['theme_previous_plan']=plan
            changes['plan']={**plan,'invalidated_reason':'主题证据规则已更新，请重新整理预览。'}
        if base:changes['base_plan']={**base,'invalidated_reason':'主题关联规则已更新，请重新整理；现有歌单不变。'}
        if self.store.get('workflow_state'):
            changes['workflow_state']={'phase':'idle','message':'主题扩充已准备好，单曲缓存保留。点击整理即可读取更多参考歌单。'}
        self.store.set_many(changes)

    def _init_playlist_isolation(self):
        """Move only the known playlist-scoped failure out of the global lock."""
        err=self.store.get('theme_last_error') or {}
        hold=self.store.get('theme_cooldown') or {}
        migrated=playlist_privacy_details(err) and err.get('kind') in ('api_error','playlist_unavailable') and err.get('scope')!='upstream'
        snapshot_message='QQ歌单分页不完整或读取期间数量变化，保留旧资料'
        snapshot_migrated=err.get('kind')=='protocol_error' and err.get('message')==snapshot_message
        first=self.store.get('theme_reference_policy')!='v0.1.13'
        if migrated:
            remember(self.store,{**err,'kind':'playlist_unavailable'})
            self.store.set_many({'theme_previous_resource_error':err,'theme_last_error':None})
            hd=hold.get('details') or {}
            if (playlist_privacy_details(hd) and hd['context']['disstid']==err['context']['disstid']):
                self.store.set('theme_cooldown',None)
        if snapshot_migrated:
            # v0.1.12 treated a single changing/incomplete playlist snapshot as a
            # global theme failure.  The old error has no playlist/count context,
            # so preserve it for audit but remove only the obsolete global lock.
            self.store.set_many({'theme_previous_resource_error':err,'theme_last_error':None,'theme_cooldown':None})
        if first or migrated or snapshot_migrated:
            cfg=self.workflow_settings();cfg.update(enabled=False,approved_scope='')
            changes={'theme_reference_policy':'v0.1.13','workflow_settings':cfg,'workflow_pending':None,
                     'workflow_last_index':None,
                     'theme_notice':'单份参考歌单若隐私拒绝或分页快照不完整，只隔离该来源；有旧完整缓存时继续复用，其他来源继续整理。原单曲缓存与歌单保留。'}
            for key in ('plan','base_plan'):
                plan=self.store.get(key)
                if plan:changes[key]={**plan,'invalidated_reason':'参考歌单错误隔离规则已更新，请重新生成预览。'}
            state=self.store.get('workflow_state') or {}
            if snapshot_migrated or (migrated and not self.store.get('theme_cooldown')) or first and state.get('phase') in ('review','ready','idle','theme_error'):
                changes['workflow_state']={'phase':'idle','message':'原资料已保留。点击整理新增歌曲；单份来源异常会隔离或复用旧完整缓存，不再拖停整轮。'}
            self.store.set_many(changes)

    def _theme_invalid_reference_cache(self,src,entry):
        return invalid_cached_sources(self.store,src,entry.get('data') or {})

    def _init_theme_recovery(self):
        hold=self.store.get('theme_cooldown') or {}
        if hold and not hold.get('details') and not self.store.get('theme_last_error'):
            # The old build discarded upstream codes. Retain the actual deadline,
            # but never relabel the opaque old failure as confirmed rate limiting.
            from .theme_errors import safe_text
            details={'kind':'legacy_unknown','recorded_at':time.time(),
                'message':'旧版只保存了通用失败提示，没有HTTP状态或QQ错误码，无法还原具体原因。',
                'previous_message':safe_text(hold.get('reason','')),'wait_basis':'legacy_local_backoff',
                'retryable':False,'retry_after':hold.get('until',0)}
            # Only this exact v0.1.9 message was emitted for a JSON business error.
            # Correct that LOCAL blanket one-hour policy; never shorten HTTP429,
            # server Retry-After or an unknown legacy wait. No request is sent here.
            legacy='QQ接口拒绝请求，停止本轮；不更换协议或身份绕过'
            if hold.get('reason')==legacy:
                previous=hold.get('until',0);details['legacy_until']=previous
                hold={**hold,'until':min(previous,previous-3600+60)}
                details.update(wait_basis='legacy_blanket_corrected',retry_after=hold['until'],
                    message='旧版将一个未记录错误码的QQ业务错误统一锁定一小时；已改为仅防重复点击，不代表接口已恢复。')
                state=self.store.get('workflow_state') or {}
                if state.get('phase')=='cooldown':self.store.set('workflow_state',{**state,'retry_after':hold['until']})
                cfg=self.workflow_settings();cfg['enabled']=False;self.store.set('workflow_settings',cfg)
            self.store.set_many({'theme_last_error':details,'theme_cooldown':{**hold,'details':details}})
        state=self.store.get('workflow_state') or {}
        if state.get('phase')=='cooldown' and hold:
            self.store.set('workflow_state',{**state,'message':'主题来源读取未成功。可以先用已有资料生成预览，或查看下方失败详情。'})

    def _record_theme_error(self,exc):
        hold=exc.hold();details={**hold['details'],'retry_after':hold['until']}
        hold['details']=details
        self.store.set_many({'theme_cooldown':hold,'theme_last_error':details})
        self.store.log('主题读取失败：'+str(exc))
        return hold

    def theme_settings(self):return {**deepcopy(DEFAULT_THEME),**(self.store.get('theme_settings') or {})}

    def set_theme_settings(self,value):
        from .engine import SafetyError
        with self.exclusive():
            if not isinstance(value,dict):raise SafetyError('主题设置需要对象')
            cfg=self.theme_settings();selected=value.get('selected',cfg['selected']);enabled=value.get('enabled',True)
            if not isinstance(enabled,bool) or not isinstance(selected,list) or any(k not in BY_KEY for k in selected) or len(set(selected))!=len(selected):
                raise SafetyError('请从页面选择有效且不重复的主题')
            pages=value.get('max_pages',cfg['max_pages'])
            if isinstance(pages,bool) or not isinstance(pages,int) or not 1<=pages<=6:raise SafetyError('主题读取深度必须1至6批')
            cfg.update(enabled=enabled,selected=selected,max_pages=pages)
            if cfg==self.theme_settings():return {'message':'主题设置未变化，已有预览仍保留。'}
            sources=self.store.get('sources')
            for s in sources:
                key=theme_key(s,self.store.get('qq_tags',[]))
                if key:
                    s['enabled']=key in selected;s['approved']=False
            wf=self.workflow_settings();wf.update(enabled=False,approved_scope='')
            self.store.set_many({'theme_settings':cfg,'sources':sources,'plan':None,'base_plan':None,
                 'workflow_pending':None,'workflow_settings':wf,'workflow_last_index':None,
                 'workflow_state':{'phase':'idle','message':'主题选择已保存。点击整理后，会在原歌单中预览新增歌曲。'}})
            return {'message':'已保存主题选择；已有歌单不会删除。点击整理后查看结果。'}

    def _theme_prepare_sources(self):
        cfg=self.theme_settings()
        if not cfg['enabled'] or not hasattr(self.qq,'fetch_category'):return
        now=time.time();hold=self.store.get('theme_cooldown') or {}
        if hold.get('until',0)>now:raise ThemeFetchError(hold.get('reason','QQ主题读取冷却中'),max(1,hold['until']-now),hold.get('details'))
        tags=self.store.get('qq_tags',[])
        if not tags:
            self.progress('读取 QQ 实际主题目录；不会创建歌单或重新查询单曲。')
            if hasattr(self.qq,'cancelled'):self.qq.cancelled=lambda:self.stop.is_set() or self.workflow_pause.is_set()
            try:tags=self.qq.tags()
            except ThemeFetchError:raise
            except SourceError as exc:raise ThemeFetchError(str(exc),60) from None
            self.store.set('qq_tags',tags)
        src,missing=provision_sources(self.store.get('sources'),tags,cfg['selected'])
        self.store.set_many({'sources':src,'theme_unavailable':[{'key':k,'name':BY_KEY[k]['name'],'reason':'当前QQ分类目录未返回精确对应入口，未伪造来源'} for k in missing]})

    def _theme_matcher(self,effective,machine):
        prefix='single_result:'+self._single_scope(machine)+':'
        return ThemeMatcher(effective,self.store.get_prefix(prefix).values())

    def _theme_match_signature(self,effective,machine):
        from .engine import digest
        m=self._theme_matcher(effective,machine)
        return digest(sorted((str(t['id']),mid,r.get('fingerprint'),r.get('checked_at'),r.get('expires_at'),d)
                             for mid,rows in m.by_mid.items() for t,d,r in rows))

    def _theme_source_signature(self,src):
        from .engine import digest
        cfg=self.theme_settings()
        value={k:v for k,v in src.items() if k not in ('approved','enabled')}
        # No cache revision here: automatic new-song classification should not
        # ask for whole-scope approval every time a single is enriched.
        if src.get('kind') in ('qq_category','local_theme') or src.get('theme_category_id'):value['theme_policy']=[THEME_POLICY,cfg]
        return digest(value)

    def _theme_ttl(self,src):
        return self.theme_settings()['source_days']*86400 if (src['kind']=='qq_category' or src.get('theme_category_id')) and self.theme_settings()['enabled'] else self.store.get('settings').get('source_hours',24)*3600

    def _theme_fetch(self,src,matcher,existing=()):
        key=theme_key(src,self.store.get('qq_tags',[]));cfg=self.theme_settings()
        if src['kind']=='local_theme':data={'title':src['name'],'tracks':[],'origins':[],'memberships':{},'reference_count':0}
        elif src['kind']=='qq_playlist' and src.get('theme_category_id') and cfg['enabled'] and hasattr(self.qq,'fetch_category'):
            try:
                seed_skips=[]
                try:seed=self.qq.fetch(src)
                except PlaylistUnavailableError as exc:
                    remember(self.store,exc.details);seed=None;seed_skips=[skip_row(exc,src['name'])]
                try:extra=self.qq.fetch_category({**src,'kind':'qq_category','value':src['theme_category_id'],'theme_key':key},matcher,cfg,existing)
                except PlaylistUnavailableError as exc:
                    if seed is None:raise
                    extra={'tracks':[],'origins':[],'memberships':{},'theme_stats':{
                        'skipped':getattr(exc,'source_skips',[skip_row(exc)]),'coverage_limited':True}}
                missing_seed=seed is None
                seed=seed or {'tracks':[],'memberships':{},'title':src['name']}
                rows={str(q['id']):q for q in seed['tracks']}
                conflicted=set();conflict_rows=list((extra.get('theme_stats') or {}).get('identity_conflict_rows') or [])
                for q in extra['tracks']:
                    mid=str(q['id'])
                    if mid in conflicted:continue
                    try:rows[mid]=merge_reference_track(rows.get(mid),q)
                    except ReferenceIdentityConflict as exc:
                        conflicted.add(mid);rows.pop(mid,None);conflict_rows.append({**exc.details,'source_id':'seed+category','source_title':src['name']})
                data={**extra,'tracks':list(rows.values())}
                origins={str(o['id']):o for o in extra['origins']}
                pid=str(src['value'])
                if not missing_seed:origins.setdefault(pid,{'id':pid,'title':seed['title'],'url':seed.get('url',''),
                   'basis':'用户已配置的原QQ参考歌单','fetched_at':seed.get('fetched_at')})
                memberships={**seed.get('memberships',{}),**extra['memberships']}
                if conflicted:
                    memberships={k:[str(x) for x in v if str(x) not in conflicted] for k,v in memberships.items()}
                data['origins']=list(origins.values());data['memberships']=memberships
                data['reference_count']=len(origins)
                matches={str(r['id']) for q in data['tracks'] if (r:=matcher.match(q)).get('id')}
                data['theme_stats']={**extra['theme_stats'],'references':len(origins),'matched':len(matches),
                                     'new_local':len(matches-set(existing)),'retained_seed_playlist':None if missing_seed else pid,
                                     'skipped':seed_skips+extra['theme_stats'].get('skipped',[]),
                                     'identity_conflicts':len({str(r.get('mid')) for r in conflict_rows if r.get('mid')}),
                                     'identity_conflict_rows':conflict_rows[-100:],
                                     'coverage_limited':missing_seed or extra['theme_stats'].get('coverage_limited',False)}
            except SourceError as exc:
                if isinstance(exc,ThemeFetchError):raise
                raise ThemeFetchError(str(exc),60) from None
        elif src['kind']=='qq_category' and cfg['enabled'] and hasattr(self.qq,'fetch_category'):
            try:data=self.qq.fetch_category({**src,'theme_key':key},matcher,cfg,existing)
            except SourceError as exc:
                if isinstance(exc,ThemeFetchError):raise
                raise ThemeFetchError(str(exc),60) from None
        else:data=self.qq.fetch(src)
        data=deepcopy(data)
        # Derivation is narrow recording metadata, never generic artist/Genre.
        if cfg['enabled'] and key in ('duet','show'):
            byid={str(t['id']):t for t in data.get('tracks',[])}
            for mid,rows in matcher.by_mid.items():
                eligible=[(t,d,r) for t,d,r in rows if key in recording_topics(t,d)]
                if not eligible:continue
                t,d,r=eligible[0];q={**d,'id':mid};byid[mid]=q;oid='detail:'+mid
                basis='已核对QQ单曲的多演唱者记录' if key=='duet' else '已核对QQ单曲的Live和综艺节目记录'
                data.setdefault('origins',[]).append({'id':oid,'title':d.get('album') or d['title'],
                    'url':d.get('source_url',''),'basis':basis,'fetched_at':d.get('fetched_at') or r.get('checked_at')})
                data.setdefault('memberships',{})[oid]=[mid]
            data['tracks']=list(byid.values())
        return data

    def _theme_origin_index(self,data):
        origins={str(o['id']):o for o in data.get('origins',[]) if o.get('id')};index={}
        for pid,mids in (data.get('memberships') or {}).items():
            o=origins.get(str(pid),{'id':str(pid),'title':data.get('title','参考歌单'),'url':data.get('url','')})
            for mid in set(str(x) for x in mids):index.setdefault(mid,[]).append(o)
        return index

    def _theme_row_origins(self,data,index,mid):
        if str(mid) in index:return index[str(mid)]
        if not data.get('memberships') and len(data.get('origins') or [])==1:
            return data['origins']  # a directly specified single playlist
        return [{'title':data.get('title','参考范围'),
                 'basis':'此旧汇总未保存逐曲来源关系；请更新主题来源后查看具体歌单'}]

    def _theme_needs_local_refresh(self,src):
        return src['kind']=='local_theme' or (self.theme_settings()['enabled'] and theme_key(src,self.store.get('qq_tags',[])) in ('show','duet'))

    def _theme_excluded(self,cid,t,exclusions=None):
        r=(exclusions if exclusions is not None else self.store.get('theme_exclusions') or {}).get(cid,{}).get(str(t['id']))
        return bool(r and r.get('fingerprint')==match_fingerprint(t))

    def exclude_theme(self,cid,tid,excluded):
        from .engine import SafetyError
        with self.exclusive():
            if not isinstance(excluded,bool):raise SafetyError('排除设置必须为开或关')
            if not any(s['id']==cid for s in self.store.get('sources')):raise SafetyError('主题不存在')
            effective,_=prepare_catalog(self.store.get('catalog',[]),self.store.get('metadata_overrides',{}))
            t=next((t for t in effective if str(t['id'])==str(tid)),None)
            if t is None:raise SafetyError('曲目不在当前音乐库记录中')
            allrows=self.store.get('theme_exclusions',{});rows=allrows.setdefault(cid,{})
            if excluded:rows[str(tid)]={'fingerprint':match_fingerprint(t),'time':time.time(),'title':t['title'],'artist':t['artist']}
            else:rows.pop(str(tid),None)
            wf=self.workflow_settings();wf.update(enabled=False,approved_scope='')
            self.store.set_many({'theme_exclusions':allrows,'plan':None,'base_plan':None,'workflow_pending':None,
                   'workflow_settings':wf,'workflow_last_index':None,'workflow_state':{'phase':'idle','message':'主题排除已保存，请重新整理预览；其他主题不受影响。'}})
            return {'message':'以后不再向这个主题补入这首歌；不会从已存在的歌单自动删歌，其他主题和每日推荐不受影响。' if excluded else '已取消本主题排除，请重新整理预览。'}

    def theme_evidence(self,cid,offset=0,limit=40,added_only=True):
        from .engine import SafetyError
        p=self.store.get('plan') or {};g=next((g for g in p.get('groups',[]) if g['id']==cid),None)
        if g is None:raise SafetyError('没有该主题预览，请先整理')
        added=set(g['add']);items={}
        for row in g.get('matched_rows',[]):
            t=row['local'];tid=str(t['id'])
            if added_only and tid not in added:continue
            dst=items.setdefault(tid,{'id':tid,'title':t['title'],'artist':t['artist'],'added':tid in added,'methods':[],'origins':[]})
            method=row.get('match_method','metadata')
            if method not in dst['methods']:dst['methods'].append(method)
            seen={str(o.get('id') or o.get('url') or o.get('title')) for o in dst['origins']}
            for o in row.get('origins') or []:
                oid=str(o.get('id') or o.get('url') or o.get('title'))
                if oid not in seen:dst['origins'].append(deepcopy(o));seen.add(oid)
        arr=list(items.values());limit=min(100,max(1,int(limit)));offset=max(0,int(offset))
        return {'id':cid,'title':g['title'],'total':len(arr),'offset':offset,'next':offset+limit if offset+limit<len(arr) else None,
                'items':arr[offset:offset+limit],'stats':g.get('theme_stats') or {},
                'notice':'参考歌单的选歌关联，不是QQ对单曲的权威心情/场景鉴定。'}

    def theme_status(self):
        return {'settings':self.theme_settings(),'topics':TOPICS,'unavailable':self.store.get('theme_unavailable',[]),
                'progress':self.store.get('theme_progress'), 'cooldown':self.store.get('theme_cooldown') or {},
                'error':self.store.get('theme_last_error') or {},
                'skipped_references':[{'id':pid,'reason':'隐私校验未通过，24小时内不重复访问此来源','until':r['until'],
                  'details':r['details']} for pid,r in sorted(active(self.store).items())],
                'notice':self.store.get('theme_notice','')}
