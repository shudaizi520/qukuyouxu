"""Authenticated metadata-only controls. No unauthenticated import or proxy endpoints."""
import json
from fastapi import Request
from fastapi.responses import Response

def attach_single_routes(app,store,engine,body,ensure_idle):
    @app.post('/api/single/pause')
    async def pause(req:Request):
        await body(req)
        return engine.request_single_pause()

    @app.post('/api/single/schedule')
    async def schedule(req:Request):
        d=await body(req);ensure_idle()
        from .engine import SafetyError
        if d.get('enabled') is True:raise SafetyError('独立定时开关已合并，请在首页开启“自动整理新歌”。')
        with engine.exclusive():return engine.set_single_schedule(False)

    @app.get('/api/single/results')
    def results(offset:int=0,limit:int=50):
        if not 0<=offset<=100000 or not 1<=limit<=100:raise ValueError('结果分页范围无效')
        return engine.single_report(offset,limit)

    @app.get('/api/single/report')
    def report():
        return Response(json.dumps(engine.single_report(),ensure_ascii=False,indent=2),media_type='application/json',
            headers={'Content-Disposition':'attachment; filename="qq-single-enrichment-report.json"'})
