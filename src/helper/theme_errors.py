"""Structured, bounded upstream diagnostics. No raw bodies, cookies or query URLs."""
from copy import deepcopy
from datetime import timezone
from email.utils import parsedate_to_datetime
import math
import re
import time
from urllib.parse import urlsplit
from .clients import SourceError


def safe_text(value, limit=240):
    if not isinstance(value,(str,int,float)) or isinstance(value,bool):return ''
    text=str(value)[:2048]
    text=re.sub(r'(?i)(?:https?://|/mnt/|/media/|[a-z]:[\\/])[^\s<>]+','[地址已隐藏]',text)
    text=re.sub(r'(?i)\b(?:authorization|bearer|cookie|(?:[a-z_]*token)|password|passwd|secret|uin|qq|enc_host_uin|p_skey|skey)\b\s*[:=]?\s*[^\s,;]+','[凭据已隐藏]',text)
    text=re.sub(r'\b[A-Za-z0-9_-]{24,}\b','[长标识已隐藏]',text)
    text=re.sub(r'[\x00-\x1f\x7f]',' ',text)
    return text[:limit]


def request_context(url,params=None):
    """Only predefined numeric identifiers; never serialize the full request."""
    parsed=urlsplit(url);out={'endpoint':parsed.hostname+parsed.path,'operation':'公开歌单读取','context':{}}
    params=params or {};p=params
    try:
        req=json_load_request(params.get('data'))
        for value in req.values():
            if isinstance(value,dict) and value.get('module') and value.get('method'):
                out['operation']=safe_text(value['module'],90)+'/'+safe_text(value['method'],90)
                p=value.get('param') or {};break
    except (TypeError,ValueError):pass
    for key in ('disstid','category_id','titleid','page','size','song_begin','song_num'):
        v=p.get(key)
        if isinstance(v,(str,int)) and not isinstance(v,bool) and re.fullmatch(r'\d{1,20}',str(v)):
            out['context'][key]=v
    return out


def json_load_request(text):
    import json
    return json.loads(text) if isinstance(text,str) else {}


def retry_delay(value,now=None):
    now=time.time() if now is None else now
    if value is None:return None
    text=str(value).strip()
    try:
        if re.fullmatch(r'\d{1,10}',text):return float(text)
        dt=parsedate_to_datetime(text)
        if dt.tzinfo is None:dt=dt.replace(tzinfo=timezone.utc)
        return max(0,dt.timestamp()-now)
    except (ValueError,TypeError,OverflowError):return None


def small_code(value):
    if isinstance(value,bool):return safe_text(str(value),32)
    if isinstance(value,(str,int,float)):
        return value if isinstance(value,int) and abs(value)<10**15 else safe_text(value,48)
    return None


class ThemeFetchError(SourceError):
    def __init__(self,message,cooldown=60,details=None):
        self.cooldown=max(0,float(cooldown))
        self.details=deepcopy(details or {'kind':'protocol_error','message':safe_text(message),
             'wait_basis':'local_backoff','retryable':False,'recorded_at':time.time()})
        self.details.setdefault('message',safe_text(message))
        self.details['cooldown_seconds']=self.cooldown
        super().__init__(message)

    def hold(self):
        return {'until':time.time()+self.cooldown,'reason':str(self),'details':deepcopy(self.details)}



PLAYLIST_ENDPOINT='c.y.qq.com/qzone/fcg-bin/fcg_ucc_getcdinfo_byids_cp.fcg'

def playlist_privacy_details(d):
    """Narrow response seen on the user's screenshot; not a public/private verdict."""
    if not isinstance(d,dict):return False
    return (d.get('endpoint')==PLAYLIST_ENDPOINT and d.get('http_status')==200
        and type(d.get('http_status')) is int and d.get('code') in (0,'0')
        and not isinstance(d.get('code'),bool) and str(d.get('subcode'))=='4000'
        and bool(re.fullmatch(r'[0-9]{1,20}',str((d.get('context') or {}).get('disstid',''))))
        and bool(re.fullmatch(r'check\s+privacy\s+error[!.]?',str(d.get('server_message','')).strip(),re.I))
        and d.get('wait_basis')!='server_retry_after')

class PlaylistUnavailableError(ThemeFetchError):
    """Only a specific reference failed. Never retry it via another endpoint."""
    def __init__(self,details):
        d=deepcopy(details)
        d.update(kind='playlist_unavailable',scope='playlist',retryable=False,wait_basis='reference_quarantine')
        pid=str((d.get('context') or {}).get('disstid',''))
        message=f'参考歌单 {pid} 隐私校验未通过（check privacy error）；跳过此来源，不绕过权限'
        d['message']=message
        super().__init__(message,0,d)


def upstream_error(context,kind,http_status=None,raw=None,rpc=None,headers=None,message=''):
    d={**deepcopy(context),'kind':kind,'recorded_at':time.time(),'http_status':http_status}
    raw=raw if isinstance(raw,dict) else {};rpc=rpc if isinstance(rpc,dict) else {}
    for key in ('code','subcode'):
        if key in raw:d[key]=small_code(raw[key])
    if 'code' in rpc:d['rpc_code']=small_code(rpc['code'])
    texts=[]
    for obj in (raw,rpc):
        for key in ('message','msg','error','errMsg','err_msg'):
            value=safe_text(obj.get(key))
            if value and value not in texts:texts.append(value)
    d['server_message']='；'.join(texts)[:300]
    ra=(headers or {}).get('Retry-After') or (headers or {}).get('retry-after')
    delay=retry_delay(ra)
    # Only HTTP429 is classified as rate limiting without guessing QQ business codes.
    fallback=300 if kind=='rate_limit' else 60
    wait=max(2,delay) if delay is not None else fallback
    d.update(wait_basis='server_retry_after' if delay is not None else 'local_backoff',
             retryable=kind in ('network_error','server_error','rate_limit'))
    labels={'api_error':'QQ返回非成功业务代码（原因见返回信息，未判定为限流）',
            'protocol_error':'QQ返回结构与程序预期不一致', 'access_denied':'QQ拒绝访问（不能仅据此断定限流）',
            'auth_error':'QQ要求验证访问权限', 'rate_limit':'QQ返回HTTP429（请求频率受限）',
            'network_error':'QQ连接失败', 'server_error':'QQ服务返回异常','http_error':'QQ请求未成功'}
    pieces=[message or labels.get(kind,'主题读取未成功')]
    if http_status is not None:pieces.append('HTTP '+str(http_status))
    for field in ('code','subcode','rpc_code'):
        if field in d:pieces.append(field+'='+str(d[field]))
    if d['server_message']:pieces.append(d['server_message'])
    summary='；'.join(pieces)[:600];d['message']=summary
    if kind=='api_error' and playlist_privacy_details(d):return PlaylistUnavailableError(d)
    return ThemeFetchError(summary,wait,d)
