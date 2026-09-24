"""Safe preview/publish/auto-maintain pipeline for full-library base categories."""
import time
import uuid
from .base import build_base_groups, BASE_POLICY, ERA_CATEGORIES, ALBUM_ERA_CATEGORIES
from .match import normalize
from .single import LANGUAGE_CATEGORIES
from .metadata import prepare_catalog
from .album_metadata import attach_albums,base_track_fingerprint,coverage_dimensions,album_genre_eligibility

DEFAULT_BASE={'enabled':False,'approved':False,'interval_hours':24}

class BaseMixin:
    def _read_base_catalog(self,p,section):
        tracks=p.tracks(section)
        ids=sorted({str(t['album_id']) for t in tracks if t.get('album_id')},key=int)
        albums=p.album_metadata(ids,section,self.progress) if ids else {}
        enriched,diagnostics=attach_albums(tracks,albums)
        return tracks,enriched,diagnostics

    def _theme_plan_for_base(self,machine,track_ids):
        plan=self.store.get('plan') or {}
        if plan.get('machine')!=machine or plan.get('invalidated_reason'):return None
        # Ignore stale rows that no longer exist locally; base builder validates IDs too.
        if not plan.get('groups'):return None
        return plan

    def base_signature(self):
        from .engine import digest
        cfg=self.store.get('settings');theme=self.store.get('plan') or {}
        evidence=[]
        if not theme.get('invalidated_reason'):
            for g in theme.get('groups') or []:
                evidence.append((g.get('id'),tuple(sorted(str((r.get('local') or {}).get('id','')) for r in g.get('matched_rows') or []))))
        return digest({'connection':[cfg.get(k) for k in ('plex_url','plex_token','section','account_label')],
                       'metadata_overrides':self.store.get('metadata_overrides',{}),'theme_evidence':evidence,
                       'manual_edits':self.store.get('playlist_manual_edits',{}),
                       'base_policy':BASE_POLICY,'single_revision':self.store.get('single_revision',0),'min_tracks':cfg.get('min_tracks',5)})

    def preview_base(self):
        with self.exclusive():return self._preview_base()

    def _preview_base(self):
        from .engine import SafetyError, safe_error, fingerprint, state_ids, track_fingerprint
        cfg=self.store.get('settings')
        if not cfg.get('plex_url') or not cfg.get('plex_token') or not cfg.get('section'):
            raise SafetyError('先配置Plex地址、Token和音乐资料库')
        self.progress('读取 Plex 全曲库基础分类证据（不写入）')
        p=self.plex_factory(cfg);identity=p.identity();tracks,enriched,diagnostics=self._read_base_catalog(p,cfg['section'])
        if not tracks:raise SafetyError('Plex资料库没有曲目；不会改已有分类')
        effective,audit=prepare_catalog(enriched,self.store.get('metadata_overrides',{}))
        # Helper-side identity correction is not permission to inherit metadata
        # from a different album; validate album identity after correction too.
        for t in effective:
            a=t.get('_base_album')
            if a and normalize(t.get('album'))!=normalize(a.get('title')):t.pop('_base_album',None)
        from .base import _genre_categories
        _,diagnostics['genre_supplement_skipped']=album_genre_eligibility(
            [t for t in effective if t.get('available',True) and not t.get('_metadata_blocked')],_genre_categories)
        self.store.set_many({'catalog':tracks,'metadata_audit':audit})
        theme=self._theme_plan_for_base(identity['machine'],{t['id'] for t in tracks})
        effective=self.single_attach(effective,identity['machine'])
        from .engine import digest
        single_digest=digest([(t['id'],t.get('_qq_single')) for t in effective])
        raw=build_base_groups(effective,theme,min_tracks=1,diagnostics=diagnostics)
        playlists=p.playlists();managed=self.store.get('managed');snapshots=self.store.get('snapshots')
        # Minimum size applies to new lists, not to judging members of an
        # already-managed era: a valid small list is not a wrong-year list.
        raw=[g for g in raw if g['matched']>=int(cfg.get('min_tracks',5)) or
             (g['id'] in managed and g['title'] in ERA_CATEGORIES+ALBUM_ERA_CATEGORIES+LANGUAGE_CATEGORIES)]
        # Keep legacy era playlists visible even if no current track qualifies.
        # Never silently leave a formerly published wrong-era list unreported.
        known={g['id'] for g in raw}
        for title in ERA_CATEGORIES+ALBUM_ERA_CATEGORIES+LANGUAGE_CATEGORIES:
            cid='base:'+normalize(title)
            if cid in managed and cid not in known:
                raw.append({'id':cid,'title':title,'kind':'base','desired':[],'matched':0,
                            'evidence':{},'inferred_count':0,'blocked':[]})
        groups=[];base_ids=set()
        disabled={str(value) for value in (self.store.get('managed_disabled_categories',[]) or [])}
        era_ids={'base:'+normalize(title) for title in ERA_CATEGORIES+ALBUM_ERA_CATEGORIES}
        language_ids={'base:'+normalize(title) for title in LANGUAGE_CATEGORIES}
        for g in raw:
            cid=g['id'];title=managed.get(cid,{}).get('title') or g['title']
            from .playlist_hub import apply_manual_edits
            desired=apply_manual_edits(self.store,'category',cid,list(g['desired']));blocked=[];current=None;add=desired[:];action='create'
            if cid in disabled:blocked.append('已停止维护：保留 Plex 中现有歌单，不再自动写入')
            if cid in managed:
                try:
                    current=p.playlist_state(managed[cid]['id'])
                    if self.marker(cid) not in current.get('summary','') or fingerprint(current)!=managed[cid].get('fingerprint'):
                        blocked.append('歌单被手工修改或所有权标记变化：暂停，不覆盖')
                    exists=set(state_ids(current));add=[k for k in desired if k not in exists];action='append' if add else 'unchanged'
                    if cid in language_ids and exists-set(desired):
                        blocked.append(f'已有语种歌单包含{len(exists-set(desired))}首尚未获得当前单曲语种证据的曲目；不会自动删除，请先核对旧快照')
                    if cid in era_ids and exists-set(desired):
                        blocked.append(f'已有年代歌单包含{len(exists-set(desired))}首不符合当前明确年份规则的曲目；不会自动删除，请先核对或恢复旧快照')
                except Exception as exc:blocked.append('读取程序管理歌单失败：'+safe_error(exc))
            elif any(x.get('title')==title for x in playlists):blocked.append('已存在同名未托管歌单：不会接管或覆盖')
            if any(s.get('category_id')==cid and s.get('status') in ('prepared','uncertain','restoring') for s in snapshots):
                blocked.append('上次写入结果待核对：请先核对快照和Plex')
            base_ids.update(desired)
            groups.append({**g,'title':title,'desired':desired,'matched':len(desired),'add':add,'action':action,'before':current,'blocked':blocked})
        if any(g['id'] in era_ids and any('不符合当前' in b for b in g['blocked']) for g in groups):
            # Append-only maintenance must not leave one track in an obsolete
            # decade and also publish it into a new one. No silent deletion.
            for g in groups:
                if g['id'] in era_ids and not g['blocked']:
                    g['blocked'].append('已有年代歌单成员待核对，为避免同曲双重年代，暂停年代写入；不自动删除旧歌')
        if any(g['id'] in language_ids and any('尚未获得当前单曲' in b for b in g['blocked']) for g in groups):
            for g in groups:
                if g['id'] in language_ids and not g['blocked']:
                    g['blocked'].append('已有语种歌单成员待核对，为避免旧错误语种并存，暂停语种写入；不自动删歌')
        current_ids={str(t['id']) for t in tracks};theme_ids=set()
        if theme:
            for g in theme.get('groups') or []:theme_ids.update(str(x) for x in g.get('desired') or [] if str(x) in current_ids)
        union=base_ids|theme_ids
        plan={'id':uuid.uuid4().hex,'policy':BASE_POLICY,'created_at':time.time(),'signature':self.base_signature(),'machine':identity['machine'],
              'library_count':len(tracks),'base_covered':len(base_ids),'base_coverage':round(100*len(base_ids)/len(tracks),2),
              'theme_covered':len(theme_ids),'theme_coverage':round(100*len(theme_ids)/len(tracks),2),
              'union_covered':len(union),'union_coverage':round(100*len(union)/len(tracks),2),
              'unclassified_count':len(tracks)-len(union),'groups':groups,
              'unclassified':[{**{k:t.get(k) for k in ('id','title','artist','album','year','album_id','album_guid','parent_year','genres','styles','_metadata_status')},
                               'album_metadata':t.get('_base_album')} for t in effective if str(t['id']) not in union],
              'track_fingerprints':{t['id']:track_fingerprint(t) for t in tracks},'applied':False,
              'metadata_review_count':sum(t.get('_metadata_blocked',False) for t in effective),
              'single_evidence_digest':single_digest,'metadata_diagnostics':diagnostics,'dimensions':coverage_dimensions(groups,len(tracks)),
              'album_evidence_fingerprints':{t['id']:base_track_fingerprint(t) for t in enriched}}
        self.store.set('base_plan',plan)
        self.store.log(f"基础分类预览：全库{len(tracks)}；基础覆盖{len(base_ids)}；与主题合并覆盖{len(union)}；尚未写入")
        self.progress('基础分类预览完成；先看基础/主题/合并覆盖，再确认写入')
        return plan

    def apply_base(self,plan_id,automatic=False):
        with self.exclusive():return self._apply_base(plan_id,automatic)

    def _apply_base(self,plan_id,automatic=False,allowed_ids=None):
        from .engine import SafetyError, safe_error, fingerprint, state_ids, track_fingerprint
        plan=self.store.get('base_plan');cfg=self.store.get('settings')
        if not plan or plan.get('policy')!=BASE_POLICY or plan.get('invalidated_reason'):
            raise SafetyError('基础分类年代规则已更新或预览已失效，请重新预览')
        if plan.get('id')!=plan_id or plan.get('signature')!=self.base_signature():raise SafetyError('基础分类配置或证据已变化，请重新预览')
        if plan.get('applied'):raise SafetyError('该基础分类预览已确认过，请重新预览')
        if time.time()-plan['created_at']>1800:raise SafetyError('基础分类预览超过30分钟，请重新预览')
        p=self.plex_factory(cfg)
        if p.identity()['machine']!=plan['machine']:raise SafetyError('Plex服务器身份变化，停止写入')
        fresh_tracks,fresh_enriched,_=self._read_base_catalog(p,cfg['section'])
        fresh={t['id']:track_fingerprint(t) for t in fresh_tracks}
        fresh_evidence={t['id']:base_track_fingerprint(t) for t in fresh_enriched}
        managed=self.store.get('managed');existing_titles={x.get('title') for x in p.playlists()};result={'written':0,'unchanged':0,'skipped':0,'errors':[]}
        disabled={str(value) for value in (self.store.get('managed_disabled_categories',[]) or [])}
        fresh_effective,_=prepare_catalog(fresh_enriched,self.store.get('metadata_overrides',{}))
        from .engine import digest
        fresh_single=digest([(t['id'],t.get('_qq_single')) for t in self.single_attach(fresh_effective,plan['machine'])])
        if fresh_single!=plan.get('single_evidence_digest'):
            result['errors'].append('单曲补全证据过期或变化，请重新预览；本轮未写入')
            plan['result']=result;self.store.set('base_plan',plan)
            return result
        if fresh_evidence!=plan.get('album_evidence_fingerprints',{}):
            # Album supplementation depends on peers as well as the selected
            # track. A newly added or changed peer can reveal a mixed album.
            result['errors'].append('Plex曲库或所属专辑资料在预览后变化，请重新预览；本轮未写入')
            plan['result']=result;self.store.set('base_plan',plan)
            return result
        for g in plan['groups']:
            cid=g['id']
            if (allowed_ids is not None and cid not in allowed_ids) or cid in disabled or g['blocked'] or automatic and cid not in managed:
                result['skipped']+=1;continue
            if any(fresh.get(k)!=plan['track_fingerprints'].get(k) or
                   fresh_evidence.get(k)!=plan.get('album_evidence_fingerprints',{}).get(k) for k in g['desired']):
                result['errors'].append(g['title']+'：Plex曲目或所属专辑资料在预览后变化，请重新预览');continue
            before=None
            try:
                if g['before']:
                    before=p.playlist_state(g['before']['id'])
                    if fingerprint(before)!=fingerprint(g['before']) or self.marker(cid) not in before.get('summary',''):raise SafetyError('预览后歌单被修改，停止覆盖')
                elif g['title'] in existing_titles:raise SafetyError('预览后出现同名歌单，不接管')
                if not g['add'] and before:
                    result['unchanged']+=1;continue
            except Exception as exc:
                result['errors'].append(g['title']+'：'+safe_error(exc));continue
            snap={'id':uuid.uuid4().hex,'kind':'base','category_id':cid,'title':g['title'],'created_at':time.time(),'status':'prepared',
                  'before':before,'after':None,'add':g['add'],'marker':self.marker(cid),'plan_id':plan_id}
            self._save_snapshot(snap)
            try:
                if before:
                    p.append(before['id'],g['add']);after=p.playlist_state(before['id'])
                else:after=p.create(g['title'],g['desired'],self.marker(cid))
                expected=(state_ids(before) if before else [])+g['add']
                if state_ids(after)!=expected or after['title']!=g['title'] or self.marker(cid) not in after.get('summary',''):
                    raise SafetyError('写入后回读与预期不一致，不标记成功，也不自动重试')
                snap.update(status='applied',after=after);self._save_snapshot(snap)
                managed[cid]={'id':after['id'],'fingerprint':fingerprint(after),'snapshot_id':snap['id'],'title':after['title']}
                self.store.set('managed',managed);existing_titles.add(after['title']);result['written']+=1
                runtime=getattr(self,'profile_runtime',None)
                if runtime is not None and runtime.registry.get(self.store.profile_id).get('kind')=='owner':
                    from .library_sharing import queue_owner_revision
                    queue_owner_revision(runtime,self.store.profile_id,cid,'publish',managed[cid])
            except Exception as exc:
                snap.update(status='uncertain',error=safe_error(exc));self._save_snapshot(snap)
                result['errors'].append(g['title']+'：'+safe_error(exc))
        settings=self.store.get('base_settings',dict(DEFAULT_BASE))
        if not automatic and not result['errors']:
            settings['approved']=True;settings['approved_policy']=BASE_POLICY;self.store.set('base_settings',settings)
        plan['applied']=True;plan['result']=result;self.store.set('base_plan',plan)
        self.store.log(f"基础分类写入：成功{result['written']}，不变{result['unchanged']}，跳过{result['skipped']}，失败{len(result['errors'])}")
        return result

    def set_base_schedule(self,enabled):
        from .engine import SafetyError
        cfg={**DEFAULT_BASE,**(self.store.get('base_settings') or {})};enabled=bool(enabled)
        if enabled:
            if not cfg.get('approved') or cfg.get('approved_policy')!=BASE_POLICY or not any(k.startswith('base:') for k in self.store.get('managed')):
                raise SafetyError('先预览并确认写入一次基础分类，再开启自动维护')
            if any(s.get('kind')=='base' and s.get('status') in ('prepared','uncertain','restoring') for s in self.store.get('snapshots')):
                raise SafetyError('基础分类有待核对写入，不能开启自动维护')
        cfg['enabled']=enabled;self.store.set('base_settings',cfg);self.store.set('base_last_run',time.time())
        return {'message':'基础分类自动维护已开启' if enabled else '基础分类自动维护已暂停'}

    def base_auto(self):
        with self.exclusive():
            cfg={**DEFAULT_BASE,**(self.store.get('base_settings') or {})}
            if not cfg.get('enabled') or not cfg.get('approved') or cfg.get('approved_policy')!=BASE_POLICY:return {'written':0,'unchanged':0,'skipped':0,'errors':[]}
            plan=self._preview_base();return self._apply_base(plan['id'],True)

    def base_due(self,now=None):
        now=now or time.time();cfg={**DEFAULT_BASE,**(self.store.get('base_settings') or {})}
        return bool(cfg.get('enabled') and cfg.get('approved') and cfg.get('approved_policy')==BASE_POLICY and now-self.store.get('base_last_run',0)>=max(1,int(cfg.get('interval_hours',24)))*3600)
