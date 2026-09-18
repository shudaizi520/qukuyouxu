"""QQ single-track field parser and conservative recording-identity checks.

No network or file writes. Protocol selection is grounded in the user's
Windows probe. A successful HTTP response is not itself a local match.
"""
from copy import deepcopy
import hashlib
import html
import json
import math
import re
import unicodedata
from .clients import SourceError
from .match import artist_key, title_key, flags, normalize

SINGLE_POLICY='v0.1.7-single-fields'
MID=re.compile(r'^[A-Za-z0-9]{14}$')
LANGUAGES={
    '国语':'国语','國語':'国语','普通话':'国语','普通話':'国语','mandarin':'国语',
    '粤语':'粤语','粵語':'粤语','cantonese':'粤语',
    '英语':'英语','英語':'英语','英文':'英语','english':'英语',
    '日语':'日语','日語':'日语','japanese':'日语',
    '韩语':'韩语','韓語':'韩语','korean':'韩语',
    '闽南语':'闽南语','閩南語':'闽南语','hokkien':'闽南语','客家语':'客家话','客家话':'客家话',
    '法语':'法语','french':'法语','德语':'德语','german':'德语','西班牙语':'西班牙语','spanish':'西班牙语',
    '俄语':'俄语','russian':'俄语','泰语':'泰语','thai':'泰语','葡萄牙语':'葡萄牙语','portuguese':'葡萄牙语',
    '意大利语':'意大利语','italian':'意大利语','印地语':'印地语','hindi':'印地语',
}
LANGUAGE_CATEGORIES=tuple(dict.fromkeys(LANGUAGES.values()))
GENRES={
    'pop':['流行全库'],'流行':['流行全库'],'c pop':['流行全库'],'mandopop':['流行全库'],
    'cantopop':['流行全库'],'j pop':['流行全库'],'k pop':['流行全库'],
    'folk':['民谣全库'],'民谣':['民谣全库'],'singer songwriter':['民谣全库'],
    'rock':['摇滚全库'],'摇滚':['摇滚全库'],'alternative rock':['摇滚全库'],'indie rock':['摇滚全库'],
    'pop rock':['流行全库','摇滚全库'],'folk rock':['民谣全库','摇滚全库'],'metal':['摇滚全库'],'punk':['摇滚全库'],
    'electronic':['电子全库'],'electronica':['电子全库'],'edm':['电子全库'],'dance':['电子全库'],'电子':['电子全库'],
    'hip hop':['嘻哈全库'],'rap':['嘻哈全库'],'嘻哈':['嘻哈全库'],'说唱':['嘻哈全库'],
    'r&b':['R&B全库'],'rnb':['R&B全库'],'rhythm and blues':['R&B全库'],'soul':['R&B全库'],
    'jazz':['爵士全库'],'爵士':['爵士全库'],'classical':['古典全库'],'古典':['古典全库'],
    'country':['乡村全库'],'乡村':['乡村全库'],'blues':['蓝调全库'],'reggae':['雷鬼全库'],
    'soundtrack':['影视原声'],'ost':['影视原声'],'原声':['影视原声'],'影视原声':['影视原声'],
    'new age':['轻音乐全库'],'easy listening':['轻音乐全库'],'轻音乐':['轻音乐全库'],
    'world music':['世界音乐'],'古风':['古风全库'],
}
LANG_FLAG={'cantonese':'粤语','mandarin':'国语','english':'英语'}

class SingleSourceError(SourceError):
    """Safe message only, never the response body, request URL, or credentials."""
    def __init__(self,message,kind='protocol',cooldown=900):
        super().__init__(message);self.kind=kind;self.cooldown=int(cooldown)

class SinglePaused(Exception):pass

def _zero(v):return type(v) in (int,float) and v==0

def text(v,limit=512):
    if not isinstance(v,str):return ''
    return html.unescape(v).strip()[:limit]

def values(info,key):
    block=info.get(key) if isinstance(info,dict) else None
    arr=block.get('content') if isinstance(block,dict) else None
    if not isinstance(arr,list):return []
    return list(dict.fromkeys(text(x.get('value'),128) for x in arr[:20] if isinstance(x,dict) and text(x.get('value'),128)))

def seconds(v):
    if isinstance(v,bool) or not isinstance(v,(int,float)):return None
    return float(v) if math.isfinite(v) and 0<v<=86400 else None

def _word(s):return re.sub(r'[\s_-]+',' ',text(s).casefold()).strip()

def normalized_fields(detail):
    langs=[];genres=[];ul=[];ug=[]
    for raw in detail.get('language_values') or []:
        # Keep unknown/dialect labels visible; never interpret numeric enums.
        parts=re.split(r'\s*[/、,，;；]\s*',text(raw))
        for p in parts:
            name=LANGUAGES.get(p) or LANGUAGES.get(p.casefold())
            if name:langs.append(name)
            elif p:ul.append(p)
    for raw in detail.get('genre_values') or []:
        mapped=GENRES.get(_word(raw))
        if mapped:genres.extend(mapped)
        elif text(raw):ug.append(text(raw))
    return {k:list(dict.fromkeys(v)) for k,v in [('languages',langs),('genres',genres),('unknown_languages',ul),('unknown_genres',ug)]}

def parse_detail(raw,requested_mid):
    if not MID.fullmatch(str(requested_mid)):raise ValueError('QQ歌曲MID应为14位英文字母或数字')
    if not isinstance(raw,dict) or not _zero(raw.get('code')):
        raise SingleSourceError('QQ单曲详情拒绝请求或结构变化，已停止本轮；不会绕过限制','rejected',3600)
    req=raw.get('req')
    if not isinstance(req,dict) or not _zero(req.get('code')):
        raise SingleSourceError('QQ单曲详情返回错误，已停止本轮；保留已有缓存','rejected',3600)
    data=req.get('data');t=data.get('track_info') if isinstance(data,dict) else None
    if not isinstance(t,dict):raise SingleSourceError('QQ单曲详情缺少 track_info；不当作无标签成功')
    mid=text(t.get('mid'))
    if mid!=requested_mid:raise SingleSourceError('QQ返回的MID与请求不一致，停止采纳资料')
    singers=t.get('singer');singers=[text(s.get('name')) for s in singers[:30] if isinstance(s,dict) and text(s.get('name'))] if isinstance(singers,list) else []
    title=text(t.get('title')) or text(t.get('name'))
    if not title or not singers:raise SingleSourceError('QQ详情缺失歌名或完整歌手字段')
    album=t.get('album');album=text(album.get('name')) or text(album.get('title')) if isinstance(album,dict) else ''
    info=data.get('info')
    return {'mid':mid,'title':title,'artist':'、'.join(singers),'singers':singers,'album':album,
            'subtitle':text(t.get('subtitle')),'duration':seconds(t.get('interval')),
            'language_values':values(info,'lan'),'genre_values':values(info,'genre'),
            'publication_values':values(info,'pub_time'),'source_url':'https://y.qq.com/n/ryqq/songDetail/'+mid}

def parse_search(raw):
    if not isinstance(raw,dict) or not _zero(raw.get('code')) or not _zero(raw.get('subcode',0)):
        raise SingleSourceError('QQ搜索建议拒绝请求，已停止；不会绕过限制','rejected',3600)
    data=raw.get('data');songs=data.get('song') if isinstance(data,dict) else None
    arr=songs.get('itemlist') if isinstance(songs,dict) else None
    if not isinstance(arr,list):raise SingleSourceError('QQ搜索建议字段变化，不能把异常响应当作没有歌曲')
    result=[];seen=set()
    for x in arr[:10]:
        if not isinstance(x,dict):continue
        mid=text(x.get('mid'))
        if not MID.fullmatch(mid) or mid in seen:continue
        seen.add(mid);result.append({'mid':mid,'title':text(x.get('name')),'artist':text(x.get('singer'))})
    return result

_DJ=re.compile(r'(?<![a-z0-9])dj(?![a-z0-9])',re.I)
def recording_flags(track):
    result=set(flags(track));texts=[track.get('title',''),track.get('album','')]
    for p in track.get('paths') or []:
        name=str(p).replace('\\','/').rsplit('/',1)[-1]
        texts.append(re.split(r'\s+[-–—]\s+',name,maxsplit=1)[-1])
    if any(_DJ.search(unicodedata.normalize('NFKC',str(v))) for v in texts):result.add('remix')
    return frozenset(result)

def recording_title_key(value):
    t=unicodedata.normalize('NFKC',str(value or ''))
    t=re.sub(r'\(([^()]*)\)',lambda m:'' if _DJ.search(m[1]) else m[0],t)
    return title_key(t)

def compare_identity(local,detail):
    if not local.get('available',True):return 'unavailable'
    if local.get('_metadata_blocked'):return 'metadata_conflict'
    if not title_key(local.get('title')) or not artist_key(local.get('artist')):return 'missing_local_identity'
    if recording_title_key(local.get('title'))!=recording_title_key(detail.get('title')):return 'title_mismatch'
    if artist_key(local.get('artist'))!=artist_key(detail.get('singers') or detail.get('artist')):return 'artist_mismatch'
    lf=recording_flags(local);df=recording_flags({**detail,'title':detail.get('title','')+' '+detail.get('subtitle','')})
    local_lang={LANG_FLAG[k] for k in lf if k in LANG_FLAG}
    title_lang={LANG_FLAG[k] for k in df if k in LANG_FLAG}
    dl=set(normalized_fields(detail)['languages'])
    if title_lang and dl and not title_lang<=dl:return 'language_conflict'
    if local_lang and (dl or title_lang) and not local_lang<=(dl or title_lang):return 'language_conflict'
    # Language text in source metadata can replace a title suffix; recording versions cannot.
    if (lf-set(LANG_FLAG))!=(df-set(LANG_FLAG)):return 'version_mismatch'
    if local_lang and not (dl or title_lang):return 'language_unverified'
    a=seconds(local.get('duration'));b=seconds(detail.get('duration'))
    if a is None or b is None:return 'missing_duration'
    if abs(a-b)>max(3,min(6,a*.01)):return 'duration_mismatch'
    return 'matched'

def select_detail(local,details):
    decisions=[{'mid':d['mid'],'status':compare_identity(local,d),'title':d['title'],'artist':d['artist'],
                'duration':d['duration'],'album':d['album']} for d in details]
    good=[d for d,r in zip(details,decisions) if r['status']=='matched']
    if len(good)>1 and normalize(local.get('album')):
        same=[d for d in good if normalize(d['album'])==normalize(local['album'])]
        if same:good=same
    if not good:return {'status':decisions[0]['status'] if len(decisions)==1 else 'no_verified_candidate','checks':decisions}
    if len(good)>1:return {'status':'ambiguous','checks':decisions}
    chosen=good[0];fields=normalized_fields(chosen)
    return {'status':'matched' if fields['languages'] or fields['genres'] else 'matched_no_fields',
            'detail':deepcopy(chosen),'fields':fields,'checks':decisions,
            'basis':'歌名、完整歌手、已知时长及版本一致；不是音频指纹验证'}

def match_fingerprint(track):
    keys=('id','title','artist','album','duration','available','guid','paths','_metadata_blocked','_metadata_status')
    v={k:track.get(k) for k in keys};v['version_flags']=sorted(recording_flags(track));v['policy']=SINGLE_POLICY
    return hashlib.sha256(json.dumps(v,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
