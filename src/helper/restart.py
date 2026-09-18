"""Opt-in detachment of an unrecognized daily list; never writes to Plex."""
import time
from .rotation import reconciliation_state

def daily_target_title(engine):
    from .engine import SafetyError
    target=engine.store.get('daily_playlist_target')
    if not target:return '每日推荐'
    if target.get('scope')!=engine.daily_scope():
        raise SafetyError('新每日歌单的账户/资料库范围已变化，请先恢复对应的 Plex 设置。')
    return target['title']

def validate_daily_target(engine,identity):
    from .engine import SafetyError
    target=engine.store.get('daily_playlist_target')
    if target and (target.get('scope')!=engine.daily_scope() or target.get('machine')!=identity['machine']):
        raise SafetyError('新每日歌单的服务器或账户范围已变化，未生成或发布；请核对 Plex 设置。')

def restart_proposal(engine):
    from .engine import SafetyError
    managed,current,report=reconciliation_state(engine)
    if report['marker_ok']:raise SafetyError('当前歌单管理标记有效，请使用重新确认托管；不需要新建。')
    plex=engine.plex_factory(engine.store.get('settings'))
    titles={x.get('title','') for x in plex.playlists()}
    title='每日推荐·自动'
    for suffix in range(2,102):
        if title not in titles:break
        title='每日推荐·自动（'+str(suffix)+'）'
    else:raise SafetyError('同名歌单过多，未自动选择名称。')
    return {**report,'can_restart':True,'new_title':title}

def prepare_restart(engine,data):
    from .engine import SafetyError
    if data.get('confirm') is not True:raise SafetyError('请明确确认保留旧歌单、另建每日推荐。')
    report=restart_proposal(engine)
    if any(data.get(k)!=report[k] for k in ('playlist_id','review_fingerprint','new_title')):
        raise SafetyError('旧歌单或新名称在核对后发生变化，请重新核对；未修改 Plex。')
    # One atomic local write; old list stays untouched. The existing journaled
    # preview/publish path creates the new list only after another explicit publish.
    managed=engine.store.get('daily_managed')
    archive=engine.store.get('daily_detached_playlists',[]) or []
    archive.append({'created_at':time.time(),'before_managed':managed,'review':report,
                    'previous_target':engine.store.get('daily_playlist_target')})
    cfg=dict(engine.store.get('daily_settings',{}) or {});cfg['enabled']=False
    engine.store.set_many({'daily_detached_playlists':archive,'daily_managed':None,'daily_plan':None,
        'daily_settings':cfg,'daily_playlist_target':{'title':report['new_title'],
        'scope':managed['scope'],'machine':managed['machine']}})
    return {'message':'旧歌单已保留、不再由助手维护。现在请生成预览并发布到“'+report['new_title']+'”；之后更新同一张新歌单。本次尚未写入 Plex。',
            'new_title':report['new_title']}

def attach_restart_routes(app,store,engine,body,ensure_idle):
    from fastapi import Request
    @app.get('/api/daily/restart')
    def inspect_restart():
        ensure_idle()
        with engine.exclusive():return restart_proposal(engine)
    @app.post('/api/daily/restart')
    async def restart(req:Request):
        data=await body(req);ensure_idle()
        with engine.exclusive():return prepare_restart(engine,data)
