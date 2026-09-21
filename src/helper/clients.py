"""Bounded HTTP integrations. QQ metadata only. Plex token never in URLs or logs."""
import hashlib
import html
import json
import math
import re
import time
import uuid
from urllib.parse import urlsplit, parse_qs
from xml.etree import ElementTree as ET
import requests
from .plex_identity import plex_headers

class SourceError(RuntimeError): pass
class PlexError(RuntimeError): pass
class PlexNotFound(PlexError): pass

QQ_HOSTS={'y.qq.com','i.y.qq.com','c.y.qq.com','u.y.qq.com'}

def validate_base(value):
    p=urlsplit(str(value).strip())
    if p.scheme not in ('http','https') or not p.hostname or p.username or p.password or p.query or p.fragment or p.path not in ('','/'):
        raise ValueError('Plex地址只填写 http(s)://IP或域名:端口，不带 /web、密码或参数')
    try: _=p.port
    except ValueError: raise ValueError('端口不正确') from None
    return str(value).strip().rstrip('/')

def parse_playlist_id(value):
    value=str(value).strip()
    if re.fullmatch(r'[1-9][0-9]{0,19}',value):return value
    p=urlsplit(value)
    if p.scheme not in ('http','https') or p.hostname not in QQ_HOSTS or p.username or p.password or p.port not in (None,80,443):
        raise ValueError('请填写QQ歌单数字ID或y.qq.com官方歌单链接')
    m=re.search(r'/(?:playlist|playsquare)/(\d+)',p.path)
    if m:return m[1]
    q=parse_qs(p.query)
    for k in ('id','disstid','dissid'):
        v=q.get(k,[''])[0]
        if re.fullmatch(r'[1-9][0-9]{0,19}',v):return v
    raise ValueError('链接中没有歌单ID；短分享链接请在浏览器打开后复制完整歌单地址')

def parse_qq_tracks(raw,total):
    if not isinstance(raw,list) or not isinstance(total,int) or not 0<total<=10000 or len(raw)!=total:
        raise SourceError('歌单为空、超出上限或分页不完整；保留旧数据，不覆盖')
    out=[]
    for x in raw:
        artist='、'.join(html.unescape(str(s.get('name',''))) for s in x.get('singer',[]) if isinstance(s,dict))
        name=html.unescape(str(x.get('title') or x.get('songname') or '')).strip()
        mid=str(x.get('mid') or x.get('songmid') or x.get('id') or '')
        album=x.get('album') or {}
        album=album.get('name','') if isinstance(album,dict) else str(album)
        if not name or not artist or not mid:raise SourceError('歌单曲目字段缺失，停止更新该来源')
        try: duration=float(x.get('interval') or 0)
        except (ValueError,TypeError):raise SourceError('歌单时长字段异常') from None
        if not math.isfinite(duration) or not 0<=duration<=86400:raise SourceError('歌单时长数值异常')
        if max(len(name),len(artist),len(album),len(mid))>512:raise SourceError('歌单字段过长')
        out.append({'id':mid,'title':name,'artist':artist,'album':html.unescape(album),'duration':duration})
    # Dedup repeated occurrences; completeness was checked before dedup.
    return list({t['id']:t for t in out}.values())

def parse_qq_tags(groups):
    out=[]
    try:
        for group in groups:
            for tag in group['v_item']:
                out.append({'id':str(int(tag['id'])),'name':str(tag['name']),'group':str(group['group_name'])})
    except (KeyError,TypeError,ValueError):raise SourceError('QQ分类接口结构变化') from None
    if not out:raise SourceError('QQ未返回可用分类，不使用虚构分类')
    return out

class QQClient:
    def __init__(self, session=None, delay=1.0):
        self.session=session or requests.Session()
        self.delay=delay
        self.last=0
        self.session.headers.update({'User-Agent':'PlexPlaylistHelper/0.1.0','Referer':'https://y.qq.com/','Accept':'application/json'})

    def _json(self,url,params=None):
        if urlsplit(url).hostname not in QQ_HOSTS:raise SourceError('拒绝非QQ地址')
        time.sleep(max(0,self.delay-(time.monotonic()-self.last)))
        self.last=time.monotonic()
        try:
            with self.session.get(url,params=params,timeout=(5,20),allow_redirects=False,stream=True) as r:
                if r.status_code!=200:raise SourceError(f'QQ返回HTTP {r.status_code}；不绕过限制')
                chunks=[];size=0
                for b in r.iter_content(65536):
                    size+=len(b)
                    if size>8*1024*1024:raise SourceError('QQ响应超过8MB限制')
                    chunks.append(b)
                data=json.loads(b''.join(chunks))
            if not isinstance(data,dict):raise ValueError()
            return data
        except (requests.RequestException,ValueError):raise SourceError('QQ连接失败或返回非JSON数据，请检查NAS联网与来源接口') from None

    def _rpc(self,module,method,param,key='req'):
        cookies=self.session.cookies
        uin=str(cookies.get('qqmusic_uin') or cookies.get('uin') or '0')
        musickey=str(cookies.get('qqmusic_key') or cookies.get('qm_keyst') or '')
        comm={'ct':24,'cv':4747474,'uin':int(uin) if uin.isdigit() else 0}
        if musickey:comm.update({'qq':uin,'authst':musickey,'tmeLoginType':2})
        d=self._json('https://u.y.qq.com/cgi-bin/musicu.fcg',{'format':'json','data':json.dumps({'comm':comm,key:{'module':module,'method':method,'param':param}},ensure_ascii=False,separators=(',',':'))})
        r=d.get(key,{})
        if d.get('code')!=0 or r.get('code')!=0 or not isinstance(r.get('data'),dict):
            raise SourceError('QQ接口拒绝或结构改变；不使用空结果覆盖旧数据')
        return r['data']

    def tags(self):
        d=self._rpc('playlist.PlaylistAllCategoriesServer','get_all_categories',{'qq':''},key='tags')
        return parse_qq_tags(d.get('v_group',[]))

    def category_playlists(self,tagid,limit=3):
        if isinstance(limit,bool) or not isinstance(limit,int) or not 1<=limit<=20:raise SourceError('每类参考歌单数量须为1到20')
        d=self._rpc('playlist.PlayListCategoryServer','get_category_content',{'titleid':int(tagid),'caller':'0','category_id':int(tagid),'size':limit,'page':0,'use_page':1},key='playlist')
        try:
            arr=d['content']['v_item']
            results=[{'id':str(x['basic']['tid']),'title':html.unescape(x['basic']['title'])} for x in arr]
            if not results:raise ValueError()
            total=d['content'].get('total_cnt')
            if total is not None and len(results)<min(int(total),limit):raise SourceError('QQ分类分页数量不足，保留旧缓存')
            return results[:limit]
        except (KeyError,TypeError,ValueError):raise SourceError('该QQ分类没有返回可用精选歌单') from None

    def playlist(self, playlist_id):
        pid=parse_playlist_id(playlist_id)
        # Official public site protocol; do not login/sign/challenge-bypass.
        d=self._json('https://c.y.qq.com/qzone/fcg-bin/fcg_ucc_getcdinfo_byids_cp.fcg',{'type':1,'json':1,'utf8':1,'onlysong':0,'new_format':1,'disstid':pid,'loginUin':0,'hostUin':0,'format':'json','platform':'yqq.json'})
        if d.get('code')==0 and d.get('subcode',0)==0 and d.get('cdlist'):
            cd=d['cdlist'][0]; raw=cd.get('songlist',[])
            total=cd.get('total_song_num',cd.get('total_songnum',cd.get('songnum')))
            # No claimed total => use the paginated protocol, don't assume completeness.
            if total is not None and len(raw)==int(total):
                return {'title':html.unescape(str(cd.get('dissname','QQ歌单'))),'tracks':parse_qq_tracks(raw,int(total)),'url':f'https://y.qq.com/n/ryqq/playlist/{pid}'}
        rows=[]; expected=None; title='QQ歌单';seen=set()
        for start in range(0,10000,100):
            page=self._rpc('music.srfDissInfo.aiDissInfo','uniform_get_Dissinfo',{'disstid':int(pid),'userinfo':1,'tag':1,'orderlist':1,'song_begin':start,'song_num':100,'onlysonglist':0,'enc_host_uin':''},key='req_1')
            try:total=int(page['total_song_num']); raw=page['songlist']
            except (KeyError,TypeError,ValueError):raise SourceError('QQ歌单分页字段缺失') from None
            if not 0<total<=10000 or expected is not None and total!=expected:raise SourceError('QQ歌单数量异常或拉取期间发生变化')
            expected=total
            if not isinstance(raw,list) or not raw:raise SourceError('QQ歌单分页中途为空')
            digest=hashlib.sha256(json.dumps(raw,sort_keys=True).encode()).hexdigest()
            if digest in seen:raise SourceError('QQ重复返回同一分页')
            seen.add(digest);rows.extend(raw)
            title=html.unescape(str(page.get('dirinfo',{}).get('title') or title))
            if len(rows)>=total:break
        return {'title':title,'tracks':parse_qq_tracks(rows,expected),'url':f'https://y.qq.com/n/ryqq/playlist/{pid}'}

    def prepare_run(self,store,ttl=86400,force=False,progress=None):
        """Read-only metadata cache; downloads once per unique playlist per run."""
        self._cache_store=store;self._cache_ttl=ttl;self._cache_force=force
        self._run_cache={};self._progress=progress

    def cached_playlist(self,pid):
        pid=parse_playlist_id(pid);run=getattr(self,'_run_cache',{})
        if pid in run:return run[pid]
        store=getattr(self,'_cache_store',None);now=time.time()
        old=store.get('qq_playlist:'+pid) if store else None
        if old and not getattr(self,'_cache_force',False) and now-old.get('fetched_at',0)<self._cache_ttl:
            data=old['data']
        else:
            data=self.playlist(pid)  # Exceptions do not erase the previous good cache.
            if store:
                index=store.get('qq_playlist_index',{})
                index[pid]={'time':now,'tracks':len(data['tracks'])}
                changes={'qq_playlist:'+pid:{'data':data,'fetched_at':now}}
                while len(index)>128 or sum(v['tracks'] for v in index.values())>100000:
                    oldest=min(index,key=lambda k:index[k]['time'])
                    if oldest==pid and len(index)==1:break
                    del index[oldest];changes['qq_playlist:'+oldest]=None
                changes['qq_playlist_index']=index;store.set_many(changes)
        run[pid]=data;self._run_cache=run
        return data

    def fetch(self,source):
        if source['kind']=='qq_playlist':
            data=self.cached_playlist(source['value'])
            pid=parse_playlist_id(source['value'])
            return {**data,'memberships':{pid:[t['id'] for t in data['tracks']]},'reference_count':1}
        if source['kind']=='qq_category':
            lists=self.category_playlists(source['value'],source.get('limit',3)); tracks={}; origins=[];memberships={}
            for i,p in enumerate(lists,1):
                if getattr(self,'_progress',None):self._progress('分类 '+source['name']+'：读取参考歌单 '+str(i)+'/'+str(len(lists)))
                d=self.cached_playlist(p['id'])
                tracks.update({t['id']:t for t in d['tracks']})
                origins.append({'id':p['id'],'title':d['title'],'url':d['url']})
                memberships[p['id']]=[t['id'] for t in d['tracks']]
            if not tracks:raise SourceError('分类下没有完整曲目，保留旧结果')
            return {'title':source['name'],'tracks':list(tracks.values()),'origins':origins,
                    'memberships':memberships,'reference_count':len(lists)}
        if source['kind']=='csv':return {'title':source['name'],'tracks':source['csv_tracks'],'origins':[{'title':'用户导入CSV元数据','url':''}]}
        raise SourceError('未知来源类型')


def _optional_number(value,maximum,integer=False):
    if value is None or value=='':return None
    try:
        n=float(value)
        if not math.isfinite(n) or not 0<=n<=maximum or integer and n!=int(n):return None
        return int(n) if integer else n
    except (ValueError,TypeError,OverflowError):return None

def parse_plex_track(e):
    a=e.attrib
    parts=e.findall('./Media/Part')
    available=bool(parts) and any(p.get('exists','1')!='0' and p.get('accessible','1')!='0' for p in parts)
    return {'id':str(a['ratingKey']),'title':a.get('title',''),'artist':a.get('originalTitle') or a.get('grandparentTitle',''),
        'album':a.get('parentTitle',''),'duration':float(a.get('duration') or 0)/1000,'guid':a.get('guid',''),
        'thumb':a.get('thumb',''),
        'available':available,'paths':[p.get('file','') for p in parts],
        'user_rating':_optional_number(a.get('userRating'),10),
        'view_count':_optional_number(a.get('viewCount'),10000000,True),
        'last_viewed_at':_optional_number(a.get('lastViewedAt'),4102444800,True),
        'skip_count':_optional_number(a.get('skipCount'),10000000,True),
        'added_at':_optional_number(a.get('addedAt'),4102444800,True),
        'year':_optional_number(a.get('year') or str(a.get('originallyAvailableAt') or '')[:4],3000,True),
        'genres':[x.get('tag') for x in e.findall('Genre') if x.get('tag')][:20],
        'styles':[x.get('tag') for x in e.findall('Style') if x.get('tag')][:20],
        'moods':[x.get('tag') for x in e.findall('Mood') if x.get('tag')][:20],
        'bitrate':max([int(m.get('bitrate') or 0) for m in e.findall('Media')]+[0])}


def validate_audio_range(value):
    value = str(value or '').strip()
    if not value:
        return ''
    match = re.fullmatch(r'bytes=(\d*)-(\d*)', value)
    if not match or not any(match.groups()):
        raise ValueError('音频 Range 请求无效')
    start, end = match.groups()
    if start and end and int(start) > int(end):
        raise ValueError('音频 Range 请求无效')
    return value

def validate_audio_offset(value):
    try:
        number = float(value or 0)
    except (TypeError, ValueError, OverflowError):
        raise ValueError('音频播放位置无效') from None
    if not math.isfinite(number) or not 0 <= number <= 86400:
        raise ValueError('音频播放位置无效')
    return number

class PlexClient:
    def __init__(self,base,token,session=None,store=None):
        self.base=validate_base(base)
        if not isinstance(token,str) or not 8<=len(token)<=512 or not token.isascii() or any(ord(c)<33 for c in token):
            raise ValueError('请填写有效的Plex Token，不是密码或claim码')
        self.session=session or requests.Session()
        self.session.trust_env=False
        if store is None:
            raise ValueError('Plex客户端需要安装身份存储')
        self.session.headers.update(plex_headers(store,token,accept='application/xml'))
        self.machine=None

    def _xml(self,path,method='GET',params=None):
        if not path.startswith('/') or path.startswith('//'):raise PlexError('拒绝异常Plex路径')
        try:
            with self.session.request(method,self.base+path,params=params,timeout=(5,25),allow_redirects=False,stream=True) as r:
                if r.status_code==404:raise PlexNotFound('Plex 中没有这个项目')
                if r.status_code not in (200,201,204):raise PlexError(f'Plex返回HTTP {r.status_code}，请核对地址、Token和权限')
                chunks=[];size=0
                for b in r.iter_content(65536):
                    size+=len(b)
                    if size>16*1024*1024:raise PlexError('Plex响应过大，拒绝继续')
                    chunks.append(b)
                body=b''.join(chunks)
                if b'<!DOCTYPE' in body or b'<!ENTITY' in body:raise PlexError('拒绝XML实体定义')
                return ET.fromstring(body) if body.strip() else ET.Element('MediaContainer')
        except (requests.RequestException,ET.ParseError):raise PlexError('Plex连接或XML解析失败；写入若超时不会自动重试，请检查记录') from None

    def identity(self):
        e=self._xml('/')
        self.machine=e.get('machineIdentifier')
        if not self.machine or not re.fullmatch(r'[A-Za-z0-9_-]+',self.machine):raise PlexError('地址不是可识别的Plex服务器')
        return {'server':e.get('friendlyName','Plex'),'machine':self.machine,'version':e.get('version','')}

    def sections(self):
        return [{'id':e.get('key'),'title':e.get('title'),'paths':[p.get('path') for p in e.findall('Location')]} for e in self._xml('/library/sections').findall('Directory') if e.get('type')=='artist']

    def _page(self,path,tag,params=None):
        rows=[];expected=None; seen=set()
        for start in range(0,100000,300):
            p=dict(params or {},**{'X-Plex-Container-Start':start,'X-Plex-Container-Size':300})
            root=self._xml(path,params=p); items=root.findall(tag)
            total=int(root.get('totalSize',root.get('size',len(items))))
            if expected is None:expected=total
            if total!=expected:raise PlexError('Plex分页期间曲库发生变化，请重试预览')
            if not items and len(rows)<total:raise PlexError('Plex分页不完整')
            for e in items:
                k=e.get('playlistItemID') if tag=='Track' and '/playlists/' in path else e.get('ratingKey')
                if k in seen:raise PlexError('Plex分页返回重复条目')
                seen.add(k);rows.append(e)
            if len(rows)>=expected:
                if len(rows)!=expected:raise PlexError('Plex分页数量不一致')
                return rows
        raise PlexError('Plex结果超过10万条上限')

    def tracks(self,section):
        if not str(section).isdigit():raise ValueError('音乐资料库ID无效')
        return [parse_plex_track(e) for e in self._page(f'/library/sections/{section}/all','Track',{'type':10})]

    def _audio_source(self, track_id):
        track_id = str(track_id or '')
        if not track_id.isdigit():raise PlexError('音频曲目ID无效')
        root = self._xml(f'/library/metadata/{track_id}')
        tracks = [row for row in root.findall('Track') if str(row.get('ratingKey') or '') == track_id]
        if len(tracks) != 1:raise PlexError('Plex音频曲目不存在或不唯一')
        parts = [
            (media_index, part_index, media, part)
            for media_index, media in enumerate(tracks[0].findall('Media'))
            for part_index, part in enumerate(media.findall('Part'))
            if part.get('exists', '1') != '0' and part.get('accessible', '1') != '0'
            and str(part.get('key') or '').startswith('/library/parts/')
            and not str(part.get('key') or '').startswith('//')
        ]
        if not parts:raise PlexError('Plex音频文件不存在或不安全')
        def selected(value):
            return str(value or '').lower() in {'1', 'true', 'yes'}
        parts.sort(key=lambda row: (
            not (selected(row[2].get('selected')) or selected(row[3].get('selected'))),
            row[0], row[1],
        ))
        return track_id, *parts[0]

    def open_audio_part(self, track_id, range_header=''):
        track_id, _media_index, _part_index, _media, part = self._audio_source(track_id)
        range_header = validate_audio_range(range_header)
        headers = {'Range': range_header} if range_header else {}
        try:
            response = self.session.request(
                'GET', self.base + part.get('key'), headers=headers,
                timeout=(5, 25), allow_redirects=False, stream=True,
            )
        except requests.RequestException:
            raise PlexError('Plex音频连接失败，请稍后重试') from None
        if response.status_code not in (200, 206):
            response.close()
            raise PlexError(f'Plex音频返回HTTP {response.status_code}')
        return response

    def open_browser_audio(self, track_id, range_header='', offset_seconds=0):
        """Return a browser-safe audio stream without modifying the source file."""
        track_id, media_index, part_index, media, part = self._audio_source(track_id)
        range_header = validate_audio_range(range_header)
        offset_seconds = validate_audio_offset(offset_seconds)
        container = str(media.get('container') or part.get('container') or '').lower()
        codec = str(media.get('audioCodec') or '').lower()
        browser_safe = (
            (container == 'flac' and codec in {'', 'flac'})
            or (container == 'mp3' and codec in {'', 'mp3'})
            or (container in {'m4a', 'mp4'} and codec in {'', 'aac', 'mp3'})
        )
        if browser_safe:
            headers = {'Range': range_header} if range_header else {}
            url = self.base + part.get('key')
            params = None
            timeout = (5, 25)
        else:
            url = self.base + '/music/:/transcode/universal/start.mp3'
            params = {
                'path': f'/library/metadata/{track_id}', 'mediaIndex': str(media_index),
                'partIndex': str(part_index), 'protocol': 'http', 'directPlay': '0',
                'directStream': '0', 'directStreamAudio': '0',
                'musicBitrate': '320',
                'offset': format(offset_seconds, '.3f').rstrip('0').rstrip('.') or '0',
                'location': 'lan',
            }
            headers = {
                'Accept': 'audio/mpeg', 'X-Plex-Client-Profile-Name': 'generic',
                'X-Plex-Platform': 'Chrome', 'X-Plex-Device': 'Browser',
                'X-Plex-Session-Identifier': uuid.uuid4().hex,
                'X-Plex-Client-Profile-Extra': (
                    'add-transcode-target(type=musicProfile&context=streaming&'
                    'protocol=http&container=mp3&audioCodec=mp3)'
                ),
            }
            timeout = (5, 45)
        try:
            response = self.session.request(
                'GET', url, params=params, headers=headers, timeout=timeout,
                allow_redirects=False, stream=True,
            )
        except requests.RequestException:
            raise PlexError('Plex音频连接失败，请稍后重试') from None
        if response.status_code not in (200, 206):
            response.close()
            raise PlexError(f'Plex音频返回HTTP {response.status_code}')
        return response

    def open_artwork(self, artwork_path):
        artwork_path = str(artwork_path or '')
        if (not re.fullmatch(r'/library/metadata/\d+/(?:thumb|art)/\d+', artwork_path)
                or artwork_path.startswith('//')):
            raise PlexError('Plex封面路径无效')
        try:
            response = self.session.request(
                'GET', self.base + artwork_path, timeout=(5, 20),
                allow_redirects=False, stream=True,
            )
        except requests.RequestException:
            raise PlexError('Plex封面连接失败，请稍后重试') from None
        if response.status_code != 200:
            response.close()
            raise PlexError(f'Plex封面返回HTTP {response.status_code}')
        return response

    def playlists(self):
        return [dict(e.attrib) for e in self._page('/playlists','Playlist',{'playlistType':'audio'})]

    def playlist_view(self,pid):
        if not str(pid).isdigit():raise PlexError('歌单ID无效')
        roots=self._xml(f'/playlists/{pid}').findall('Playlist')
        if len(roots)!=1:raise PlexError('程序管理的歌单不存在；不会擅自重建')
        e=roots[0]
        if e.get('playlistType')!='audio':raise PlexError('不是音乐歌单')
        arr=self._page(f'/playlists/{pid}/items','Track')
        def item(x):
            try:
                duration = max(0, int(x.get('duration') or 0) // 1000)
            except (TypeError, ValueError):
                duration = 0
            return {
                'id': str(x.get('ratingKey')), 'item_id': str(x.get('playlistItemID')),
                'title': x.get('title', ''), 'artist': x.get('grandparentTitle', ''),
                'album': x.get('parentTitle', ''), 'duration': duration,
                'thumb': x.get('thumb', ''),
            }
        return {'id':str(pid),'title':e.get('title',''),'summary':e.get('summary',''),
                'smart':e.get('smart')=='1','items':[item(x) for x in arr]}

    def playlist_state(self,pid):
        state=self.playlist_view(pid)
        if state.get('smart'):raise PlexError('不是普通音乐歌单')
        return state

    def read_playlist_until(self,pid,predicate,attempts=8,delay=0.25):
        """Retry only Plex reads after a write; never repeats the mutation."""
        if isinstance(attempts,bool) or not isinstance(attempts,int) or not 1<=attempts<=20:
            raise ValueError('回读次数无效')
        last=None
        for attempt in range(attempts):
            last=self.playlist_state(pid)
            if predicate(last):return last
            if attempt+1<attempts:time.sleep(delay*(attempt+1))
        return last

    def read_playlist_view_until(self,pid,predicate,attempts=8,delay=0.25):
        """Retry normal or smart playlist reads after one metadata mutation."""
        if isinstance(attempts,bool) or not isinstance(attempts,int) or not 1<=attempts<=20:
            raise ValueError('回读次数无效')
        last=None
        for attempt in range(attempts):
            last=self.playlist_view(pid)
            if predicate(last):return last
            if attempt+1<attempts:time.sleep(delay*(attempt+1))
        return last

    def read_playlists_until(self,predicate,attempts=8,delay=0.25):
        """Retry account playlist listing after one delete mutation."""
        if isinstance(attempts,bool) or not isinstance(attempts,int) or not 1<=attempts<=20:
            raise ValueError('回读次数无效')
        last=None
        for attempt in range(attempts):
            last=self.playlists()
            if predicate(last):return last
            if attempt+1<attempts:time.sleep(delay*(attempt+1))
        return last

    def _uri(self,ids):
        if not ids or any(not str(x).isdigit() for x in ids):raise PlexError('空曲目或非法曲目ID')
        if not self.machine:self.identity()
        return f'server://{self.machine}/com.plexapp.plugins.library/library/metadata/'+','.join(ids)

    def create(self,title,ids,marker,description=None):
        first=ids[:100]
        root=self._xml('/playlists','POST',{'title':title,'type':'audio','smart':0,'uri':self._uri(first)})
        el=root.find('Playlist')
        if el is None:raise PlexError('创建歌单未返回ID，请核对Plex；不会自动重试创建')
        pid=str(el.get('ratingKey'))
        self._xml(f'/playlists/{pid}','PUT',{'summary':marker+'\n'+(description or '仅匹配本地音乐；本助手自动补充，不自动删除旧曲目。')})
        self.append(pid,ids[100:])
        return self.playlist_state(pid)

    def append(self,pid,ids):
        for i in range(0,len(ids),100):self._xml(f'/playlists/{pid}/items','PUT',{'uri':self._uri(ids[i:i+100])})

    def remove_items(self,pid,item_ids):
        for item in item_ids:
            if not str(item).isdigit():raise PlexError('歌单条目ID异常')
            self._xml(f'/playlists/{pid}/items/{item}','DELETE')

    def rename(self,pid,title):
        """Plex title-only PUT. No create/delete/item/cover operation is performed."""
        if not str(pid).isdigit():raise PlexError('歌单ID异常')
        if not isinstance(title,str) or not title.strip() or len(title)>80 or any(ord(c)<32 for c in title):
            raise PlexError('歌单标题无效')
        self._xml(f'/playlists/{pid}','PUT',{'title':title})

    def update_playlist_summary(self,pid,summary):
        """Update only playlist metadata; membership and artwork are untouched."""
        if not str(pid).isdigit():raise PlexError('歌单ID异常')
        if not isinstance(summary,str) or not summary.strip() or len(summary)>2000:
            raise PlexError('歌单摘要无效')
        self._xml(f'/playlists/{pid}','PUT',{'summary':summary})

    def move_item(self,pid,item_id,after=None):
        if any(not str(x).isdigit() for x in (pid,item_id)) or after is not None and not str(after).isdigit():
            raise PlexError('歌单条目ID异常')
        self._xml(f'/playlists/{pid}/items/{item_id}/move','PUT',{'after':str(after)} if after is not None else {})

    def playlist_track_ids(self,pid):
        """Read selected favorite playlist (including smart playlists); never edits it."""
        if not str(pid).isdigit():raise PlexError('收藏歌单ID无效')
        roots=self._xml(f'/playlists/{pid}').findall('Playlist')
        if len(roots)!=1 or roots[0].get('playlistType')!='audio':raise PlexError('收藏来源不是可读取的音乐歌单')
        return [str(x.get('ratingKey')) for x in self._page(f'/playlists/{pid}/items','Track')]

    def delete_playlist(self,pid):
        if not str(pid).isdigit():raise PlexError('歌单ID异常')
        self._xml(f'/playlists/{pid}','DELETE')
