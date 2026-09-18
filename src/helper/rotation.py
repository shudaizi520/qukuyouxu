"""v0.3.5 preview rotation; Plex publication safeguards remain authoritative."""
import time
from .recommend import recommend
from .daily_mix_v035 import POLICY_VERSION, recommend_rotating_v035, save_v035_plan
from . import daily as _daily

# The v0.3.4 preview signature did not identify its algorithm.  Keep the old
# method for rollback/debugging, then wrap it so a pending v0.3.4 preview can
# never be published after this upgrade.  The selected history user also
# invalidates a preview when changed.
if not hasattr(_daily.DailyMixin, '_v034_daily_signature'):
    _daily.DailyMixin._v034_daily_signature = _daily.DailyMixin.daily_signature

    def _daily_signature_v035(self):
        from .engine import digest
        product = self.store.get('product_settings', {}) or {}
        return digest({
            'base': self._v034_daily_signature(),
            'policy': POLICY_VERSION,
            'history_user': str(product.get('behavior_user') or '').strip().casefold(),
            'behavior': digest(self.store.get('behavior_events',[]) or []),
        })

    _daily.DailyMixin.daily_signature = _daily_signature_v035

def recommend_rotating(engine,*args,**kwargs):
    return recommend_rotating_v035(engine,recommend,*args,**kwargs)

def save_rotating_plan(engine,values):
    return save_v035_plan(engine,values)

def reconciliation_state(engine):
    from .engine import SafetyError, fingerprint, state_ids
    store=engine.store
    managed=store.get('daily_managed')
    if not managed:raise SafetyError('没有已托管的每日歌单；不会按名称接管其他歌单。')
    if any(s.get('category_id')=='daily' and s.get('status') in ('prepared','uncertain','restoring')
           for s in store.get('snapshots',[])):
        raise SafetyError('存在未完成写入，请先使用原有安全修复入口。')
    p=engine.plex_factory(store.get('settings'));identity=p.identity()
    if managed.get('scope')!=engine.daily_scope() or managed.get('machine')!=identity['machine']:
        raise SafetyError('账户、服务器或资料库不一致，不能重新确认托管。')
    current=p.playlist_state(managed['id'])
    if str(current['id'])!=str(managed['id']):raise SafetyError('返回的歌单标识不一致。')
    marker_ok=engine.marker('daily') in current.get('summary','')
    fp=fingerprint(current)
    return managed,current,{'playlist_id':str(managed['id']),'title':current.get('title',''),
        'count':len(state_ids(current)),'marker_ok':marker_ok,'changed':fp!=managed.get('fingerprint'),
        'review_fingerprint':fp,'can_accept':marker_ok}

def accept_reconciliation(engine,data):
    from .engine import SafetyError
    if data.get('confirm') is not True:raise SafetyError('需要明确确认重新托管，不能自动接受变化。')
    managed,current,report=reconciliation_state(engine)
    if not report['can_accept']:raise SafetyError('本安装管理标记缺失或不符，不接管、不覆盖。')
    if data.get('playlist_id')!=report['playlist_id'] or data.get('review_fingerprint')!=report['review_fingerprint']:
        raise SafetyError('歌单在确认期间发生变化，请重新核对。')
    audit=engine.store.get('daily_reconciliation_audit',[]) or []
    audit.append({'created_at':time.time(),'before_managed':managed,'accepted_state':current})
    revised={**managed,'fingerprint':report['review_fingerprint']}
    cfg=dict(engine.store.get('daily_settings',{}) or {});cfg['enabled']=False
    engine.store.set_many({'daily_managed':revised,'daily_plan':None,'daily_settings':cfg,
                          'daily_reconciliation_audit':audit[-200:]})
    return {'message':'已保留当前 Plex 歌单并重新确认托管，自动更新已暂停。请重新生成预览，检查后再发布；本次没有修改 Plex。'}

def attach_reconciliation_routes(app,store,engine,body,ensure_idle):
    from fastapi import Request
    @app.get('/api/daily/reconciliation')
    def inspect_reconciliation():
        ensure_idle()
        with engine.exclusive():
            return reconciliation_state(engine)[2]
    @app.post('/api/daily/reconciliation')
    async def reconcile(req:Request):
        data=await body(req);ensure_idle()
        with engine.exclusive():return accept_reconciliation(engine,data)
