"""Multi-label playlist associations, never Genre-to-mood inference.

Pure helpers: verified recording IDs bridge metadata spellings; category membership
is a playlist curator's association, not an authoritative acoustic judgement.
"""
from collections import defaultdict
from copy import deepcopy
import re
import time
import uuid
from .match import Catalog,match,normalize,artist_key
from .single import (SINGLE_POLICY,MID,compare_identity,match_fingerprint,
                     recording_flags,recording_title_key,seconds,LANG_FLAG)

THEME_POLICY='v0.1.9-theme-membership-1'
TOPICS=[
 {'key':'internet','name':'网络热歌','aliases':['网络歌曲','网络热歌','抖音热门'],'dimension':'主题'},
 {'key':'ktv','name':'KTV金曲','aliases':['KTV金曲','KTV'],'dimension':'主题'},
 {'key':'show','name':'综艺现场','aliases':['综艺'],'dimension':'主题','derived':True},
 {'key':'film','name':'影视金曲','aliases':['影视','影视原声'],'dimension':'主题'},
 {'key':'duet','name':'合唱精选','aliases':['合唱','对唱'],'dimension':'主题','derived':True},
 {'key':'drive','name':'开车精选','aliases':['开车','驾车','车载'],'dimension':'场景'},
 {'key':'sport','name':'运动精选','aliases':['运动'],'dimension':'场景'},
 {'key':'work','name':'工作陪伴','aliases':['学习工作','工作','学习'],'dimension':'场景'},
 {'key':'sleep','name':'睡前舒缓','aliases':['睡前'],'dimension':'场景'},
 {'key':'sad','name':'伤感情歌','aliases':['伤感'],'dimension':'心情'},
 {'key':'heal','name':'治愈陪伴','aliases':['治愈'],'dimension':'心情'},
 {'key':'sweet','name':'甜蜜情歌','aliases':['甜蜜','甜蜜情歌'],'dimension':'心情'},
]
BY_KEY={x['key']:x for x in TOPICS}
DEFAULT_THEME={'enabled':True,'selected':[x['key'] for x in TOPICS],
               'max_pages':3,'page_size':20,'min_pages':2,'plateau_pages':2,'source_days':7}
ALIASES={'drive':['开车提神'],'work':['工作专注'],'heal':['治愈精选'],
         'internet':['网络歌曲精选'],'sport':['运动节奏']}

def theme_key(source,tags=()):
    key=source.get('theme_key')
    if key in BY_KEY:return key
    name=normalize(source.get('name'))
    tag=next((t for t in tags if str(t.get('id'))==str(source.get('value'))),{}) if source.get('kind')=='qq_category' else {}
    tag_name=normalize(tag.get('name'))
    for item in TOPICS:
        names=[item['name']]+item['aliases']+ALIASES.get(item['key'],[])
        if name in {normalize(x) for x in names} or tag_name and tag_name in {normalize(x) for x in item['aliases']}:
            return item['key']
    return None


def provision_sources(current,tags,selected):
    """Exact live taxonomy IDs only; retain existing sources and managed IDs."""
    out=deepcopy(current);missing=[]
    for key in selected:
        if key not in BY_KEY:continue
        item=BY_KEY[key]
        existing=next((s for s in out if theme_key(s,tags)==key),None)
        resolved=next((t for alias in item['aliases'] for t in tags if normalize(t.get('name'))==normalize(alias)),None)
        if existing:
            # Keep explicit seed playlists and their owned ID; enrich from a real topic directory.
            # An explicitly disabled existing source stays disabled, not resurrected.
            if existing.get('theme_key') is None:existing['theme_key']=key
            if existing.get('kind')=='qq_playlist':
                if resolved:existing['theme_category_id']=str(resolved['id'])
                else:
                    existing.pop('theme_category_id',None)
                    if not item.get('derived'):missing.append(key)
            continue
        if resolved:
            src={'id':uuid.uuid4().hex,'kind':'qq_category','value':str(resolved['id']),
                 'name':item['name'],'enabled':True,'approved':False,'limit':20,
                 'theme_key':key,'theme_generated':True}
        elif item.get('derived'):
            src={'id':uuid.uuid4().hex,'kind':'local_theme','value':key,'name':item['name'],
                 'enabled':True,'approved':False,'theme_key':key,'theme_generated':True}
        else:missing.append(key);continue
        if len(out)>=40:missing.append(key);continue
        if any(s.get('name')==src['name'] for s in out):missing.append(key);continue
        out.append(src)
    return out,missing


def playlist_relevance(playlist,key,tags):
    """A category result is usable unless obviously a multi-scene grab bag.

    Explicit returned playlist tags supporting this exact topic take precedence.
    Never fan out to other moods based solely on a title containing keywords.
    """
    item=BY_KEY.get(key)
    if not item:return True,'来自已配置QQ分类'
    actual={normalize(t.get('name') if isinstance(t,dict) else t) for t in tags or []}
    target={normalize(x) for x in item['aliases']}
    if actual & target:return True,'QQ歌单实际标签支持本主题'
    text=normalize(playlist.get('title',''))
    moods=[x for x in TOPICS if x['dimension'] in ('场景','心情')]
    hits={x['key'] for x in moods if any(normalize(a) in text for a in x['aliases'])}
    if len(hits)>=3:return False,'标题混合多个场景/心情且无明确目标标签，暂不作为主题依据'
    return True,'QQ分类目录中的参考歌单（选歌关联，不是单曲权威标签）'


def recording_topics(local,detail):
    if compare_identity(local,detail)!='matched':return []
    f=recording_flags(detail);out=[]
    if 'instrumental' not in f and 2<=len(artist_key(detail.get('singers') or detail.get('artist')))<=8:
        out.append('duet')
    # Live alone does not imply a television program, let alone a mood.
    program=re.search(r'王牌对王牌|声生不息|天赐的声音|梦想的声音|中国好声音|我是歌手|歌手20\d{2}|蒙面唱将|我们的歌|乘风破浪|披荆斩棘',str(detail.get('album','')))
    if 'live' in f and program:out.append('show')
    return out


def origins_for(mid,data):
    byid={str(o.get('id')):o for o in data.get('origins',[]) if o.get('id')}
    result=[]
    for pid,ids in (data.get('memberships') or {}).items():
        if str(mid) in {str(x) for x in ids}:
            origin=byid.get(str(pid),{'id':str(pid),'title':'QQ参考歌单','url':'https://y.qq.com/n/ryqq/playlist/'+str(pid)})
            result.append(deepcopy(origin))
    if not result and data.get('url'):
        result.append({'title':data.get('title','用户指定来源'),'url':data['url']})
    return result


class ThemeMatcher:
    def __init__(self,tracks,records=(),now=None):
        self.catalog=Catalog(tracks);self.by_mid=defaultdict(list);now=time.time() if now is None else now
        for r in records:
            if not isinstance(r,dict):continue
            local=self.catalog.by_id.get(str(r.get('id')));d=r.get('detail') or {};mid=str(d.get('mid',''))
            if (local and r.get('status') in ('matched','matched_no_fields') and r.get('policy')==SINGLE_POLICY
                and r.get('expires_at',0)>now and r.get('fingerprint')==match_fingerprint(local)
                and MID.fullmatch(mid) and compare_identity(local,d)=='matched'):
                self.by_mid[mid].append((local,d,r))

    def match(self,q):
        mid=str(q.get('id') or q.get('mid') or '')
        pool=self.by_mid.get(mid,[])
        if not pool:return match(q,self.catalog)
        good=[]
        for local,d,r in pool:
            if recording_title_key(q.get('title'))!=recording_title_key(d.get('title')):continue
            if artist_key(q.get('artist') or q.get('singers'))!=artist_key(d.get('artist') or d.get('singers')):continue
            # Listing may omit Live/album; explicit flags cannot contradict detail.
            qf=recording_flags(q);df=recording_flags(d)
            if not (qf-set(LANG_FLAG)) <= (df-set(LANG_FLAG)):continue
            qlang={LANG_FLAG[k] for k in qf if k in LANG_FLAG}
            from .single import normalized_fields
            langs=set(normalized_fields(d)['languages'])
            if qlang and not qlang<=langs:continue
            a=seconds(q.get('duration'));b=seconds(d.get('duration'))
            if a is not None and (b is None or abs(a-b)>max(3,min(6,b*.01))):continue
            good.append((local,d,r))
        ids=[str(x[0]['id']) for x in pool]
        if not good:return {'status':'verified_mid_conflict','id':None,'candidates':ids[:8]}
        if len(good)>1:
            albums={normalize(x[0].get('album')) for x in good};secs=[seconds(x[0].get('duration')) for x in good]
            if len(albums)!=1 or '' in albums or None in secs or max(secs)-min(secs)>1:
                return {'status':'ambiguous','id':None,'candidates':[str(x[0]['id']) for x in good[:8]]}
        good.sort(key=lambda x:(-int(x[0].get('bitrate') or 0),str(x[0]['id'])))
        local,d,r=good[0]
        return {'status':'verified_mid','id':str(local['id']),'candidates':[str(x[0]['id']) for x in good[:8]],
                'mid':mid,'checked_at':r.get('checked_at'),'expires_at':r['expires_at']}
