"""Quarantine one inaccessible reference, never bypass upstream privacy checks."""
from copy import deepcopy
import time
from .theme_errors import PlaylistUnavailableError,playlist_privacy_details

TTL=24*3600
PREFIX='qq_playlist_unavailable:'

def remember(store,details):
    if not store or not playlist_privacy_details(details):return
    pid=str(details['context']['disstid']);now=time.time()
    old=store.get(PREFIX+pid) or {}
    # Cache hits must not extend the quarantine indefinitely.
    if old.get('until',0)>now:return
    changes={PREFIX+pid:{'id':pid,'until':now+TTL,'recorded_at':now,'details':deepcopy(details)}}
    for key in ('plan','base_plan'):
        plan=store.get(key)
        if plan:changes[key]={**plan,'invalidated_reason':'检测到参考歌单访问条件变化，请重新生成预览；已有歌单不变。'}
    store.set_many(changes)
    records={k:v for k,v in store.get_prefix(PREFIX).items() if isinstance(v,dict)}
    for key,row in sorted(records.items(),key=lambda kv:kv[1].get('recorded_at',0))[:max(0,len(records)-1500)]:
        store.set(key,None)

def active(store):
    now=time.time()
    return {str(r['id']):r for r in store.get_prefix(PREFIX).values()
            if isinstance(r,dict) and r.get('until',0)>now and playlist_privacy_details(r.get('details'))}

def cached_error(store,pid):
    row=store.get(PREFIX+str(pid)) if store else None
    if not isinstance(row,dict) or row.get('until',0)<=time.time() or not playlist_privacy_details(row.get('details')):return None
    details={**deepcopy(row['details']),'cached_skip':True,'skip_until':row['until']}
    return PlaylistUnavailableError(details)

def skip_row(exc,title=''):
    return {'id':str(exc.details['context']['disstid']),'title':str(title)[:200],
            'reason':'此参考歌单隐私校验未通过，未读取其内容；只跳过此来源',
            'error':deepcopy(exc.details)}

def invalid_cached_sources(store,src,data):
    """A remembered refusal cannot silently reappear as trusted old evidence."""
    denied=set(active(store))
    if not denied:return []
    observed={str(o.get('id')) for o in data.get('origins',[]) if o.get('id')}
    observed.update(str(x) for x in data.get('memberships',{}))
    skipped={str(r.get('id')) for r in data.get('theme_stats',{}).get('skipped',[])
             if r.get('error',{}).get('kind')=='playlist_unavailable'}
    if src.get('kind')=='qq_playlist' and str(src.get('value')) not in skipped:
        observed.add(str(src.get('value')))
    return sorted(observed & denied)
