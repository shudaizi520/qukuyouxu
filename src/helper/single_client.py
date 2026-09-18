"""Independent HTTPS metadata-only session; never inherits the Plex session."""
import json
import time
from urllib.parse import urlsplit
import requests
from .single import MID,parse_detail,parse_search,SingleSourceError,SinglePaused

DETAIL_URL='https://u.y.qq.com/cgi-bin/musicu.fcg'
SEARCH_URL='https://c.y.qq.com/splcloud/fcgi-bin/smartbox_new.fcg'

class SingleQQClient:
    def __init__(self,session=None,delay=2.0,cancelled=None,budget=10000):
        self.session=session or requests.Session();self.session.trust_env=False;self.session.auth=None
        self.session.headers.clear()
        self.session.headers.update({'Referer':'https://y.qq.com/','Accept':'application/json',
                                     'User-Agent':'PlexPlaylistHelper-MetadataProbe/1.0'})
        self.delay=max(0,float(delay));self.cancelled=cancelled or (lambda:False)
        self.last=0;self.count=0;self.budget=budget

    def _json(self,url,params=None):
        if url not in (DETAIL_URL,SEARCH_URL):raise ValueError('只允许固定的QQ HTTPS元数据路径')
        if self.count>=self.budget:raise SinglePaused('达到本轮请求预算；已完成结果已保存，可稍后继续')
        while time.monotonic()-self.last<self.delay:
            if self.cancelled():raise SinglePaused('已暂停，已完成结果已保存')
            time.sleep(max(0,min(.2,self.delay-(time.monotonic()-self.last))))
        if self.cancelled():raise SinglePaused('已暂停，已完成结果已保存')
        self.last=time.monotonic();self.count+=1
        self.session.cookies.clear()
        try:
            with self.session.get(url,params=params,timeout=(5,15),allow_redirects=False,stream=True) as r:
                if r.status_code!=200:
                    wait=3600 if r.status_code in (401,403,429) else 900
                    try:wait=max(wait,min(86400,int(r.headers.get('Retry-After',0))))
                    except (ValueError,TypeError):pass
                    kind='rate_limit' if r.status_code==429 else 'rejected' if r.status_code in (401,403) else 'network'
                    raise SingleSourceError(f'QQ返回HTTP {r.status_code}，已停止本轮并保留缓存；不会绕过限制',kind,wait)
                raw=bytearray();started=time.monotonic()
                for part in r.iter_content(65536):
                    if self.cancelled():raise SinglePaused('已暂停，未完成的响应不会存为成功')
                    if time.monotonic()-started>45:raise SingleSourceError('QQ单次响应耗时过长，停止本轮','network',900)
                    raw.extend(part)
                    if len(raw)>2*1024*1024:raise SingleSourceError('QQ单曲响应超过2MB，已停止读取')
                data=json.loads(raw)
                if not isinstance(data,dict):raise SingleSourceError('QQ返回结构异常，不能当作空分类成功')
                return data
        except requests.RequestException:
            raise SingleSourceError('NAS到QQ的连接失败，请检查NAS网络；已保存进度，未修改歌单','network',900) from None
        except (ValueError,UnicodeError):
            raise SingleSourceError('QQ返回非JSON或结构变化；未登录、未绕过限制，已停止本轮') from None
        finally:self.session.cookies.clear()

    def detail(self,mid):
        if not MID.fullmatch(str(mid)):raise ValueError('QQ MID格式无效')
        body={'comm':{'ct':24,'cv':4747474,'uin':0},'req':{'module':'music.pf_song_detail_svr',
              'method':'get_song_detail_yqq','param':{'song_mid':mid}}}
        raw=self._json(DETAIL_URL,{'format':'json','data':json.dumps(body,ensure_ascii=False,separators=(',',':'))})
        return parse_detail(raw,mid)

    def search(self,query):
        if not isinstance(query,str) or not query.strip() or len(query)>120:raise ValueError('搜索文字需为1至120字')
        return parse_search(self._json(SEARCH_URL,{'format':'json','key':query.strip()}))
