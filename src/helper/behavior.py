"""Local playback-behavior learning from read-only Plex session samples.

No Plex ratings/tags are changed. Events are deliberately weak signals and decay.
"""
import math

MAX_EVENT_AGE=180*86400
MAX_EVENTS=5000


def _number(v,default=0.0):
    try:
        n=float(v)
        return n if math.isfinite(n) and n>=0 else default
    except (TypeError,ValueError,OverflowError):
        return default


def _classify(state):
    duration=max(1.0,_number(state.get('duration_seconds'),1.0))
    observed=_number(state.get('observed_seconds'))
    offset=_number(state.get('offset_seconds'))
    first=_number(state.get('first_offset'))
    ratio=offset/duration
    # Completion is credible only when the tracker saw the track near its beginning,
    # or actually observed at least 75% progression. This avoids treating a seek as listening.
    credible_completion=(first<=max(30.0,duration*0.15) and ratio>=0.75) or observed/duration>=0.75
    if credible_completion:return 1.0,'completed'
    if observed<=15 and offset<=15 and ratio<0.20:return -1.0,'fast_skip'
    if observed<=45 and offset<=45 and ratio<0.35:return -0.5,'early_exit'
    return 0.0,'neutral'


def recent_behavior_events(events, now):
    """Return only behavior evidence still eligible for learning and display."""
    return [
        row for row in (events or [])
        if isinstance(row, dict) and 0 <= now - _number(row.get('at'), now + 1) <= MAX_EVENT_AGE
    ]


def behavior_profile(events,now,excluded_ids=None):
    excluded_ids={str(value) for value in (excluded_ids or ())}
    grouped={}
    for e in recent_behavior_events(events, now):
        age=now-_number(e.get('at'))
        if age<0 or age>MAX_EVENT_AGE:continue
        tid=str(e.get('track_id',''))
        if not tid.isdigit() or tid in excluded_ids:continue
        grouped.setdefault(tid,[]).append(e)
    out={}
    skip_kinds={'fast_skip','early_exit','observed_skip'}
    for tid,rows in grouped.items():
        rows=sorted(rows,key=lambda e:_number(e.get('at')))
        positives=[];explicit_negatives=[];skips=[]
        for e in rows:
            at=_number(e.get('at'));value=float(e.get('value') or 0);kind=str(e.get('kind') or '')
            if value>0:positives.append((at,value))
            elif value<0 and kind in skip_kinds:skips.append((at,value))
            elif value<0:explicit_negatives.append((at,value))
        positive_score=sum(value*math.exp(-(now-at)/(150*86400)) for at,value in positives)
        explicit_score=sum(value*math.exp(-(now-at)/(180*86400)) for at,value in explicit_negatives)
        skip_days={int(at//86400) for at,_ in skips}
        # One skip is ambiguous. Only repeated skips on different days slowly
        # affect long-term taste; recent skips primarily become temporary fatigue.
        repeated_skip_score=-0.35*max(0,len(skip_days)-1)
        long_term=positive_score+explicit_score+repeated_skip_score
        if not positives and not explicit_negatives and len(skip_days)<=1:long_term=0.0
        short_term=sum(value*math.exp(-(now-at)/(14*86400)) for at,value in positives+explicit_negatives+skips)
        latest_positive=max((at for at,_ in positives),default=0)
        recent_skips=[(at,value) for at,value in skips if 0<=now-at<30*86400 and at>latest_positive]
        recent_skip_days=len({int(at//86400) for at,_ in recent_skips})
        latest_skip=max((at for at,_ in recent_skips),default=0)
        fatigue_days=0;cooldown_until=0
        if positives and latest_skip:
            fatigue_days=7 if recent_skip_days==1 else 14 if recent_skip_days==2 else 30
            cooldown_until=latest_skip+fatigue_days*86400
        elif not positives and recent_skip_days>=2:
            fatigue_days=7 if recent_skip_days==2 else 14 if recent_skip_days==3 else 30
            cooldown_until=latest_skip+fatigue_days*86400
        recent_positive=sum(0<=now-at<7*86400 for at,_ in positives)
        out[tid]={
            'score':round(long_term+short_term*0.35,6),
            'long_term_score':round(long_term,6),
            'short_term_score':round(short_term,6),
            'positive':len(positives),'negative':len(explicit_negatives)+len(skips),
            'recent_positive':recent_positive,'recent_skip_days':recent_skip_days,
            'fatigue_days':fatigue_days,'cooldown_until':cooldown_until,
            'last_event':max((_number(e.get('at')) for e in rows),default=0.0),
        }
    return out


class BehaviorTracker:
    def __init__(self,store):self.store=store

    def _finalize(self,state,now,events):
        value,kind=_classify(state)
        if not value:return 0
        event={'track_id':str(state['track_id']),'value':value,'kind':kind,'at':now,
               'observed_seconds':round(_number(state.get('observed_seconds')),3),
               'offset_seconds':round(_number(state.get('offset_seconds')),3),
               'duration_seconds':round(_number(state.get('duration_seconds')),3),
               'user':str(state.get('user',''))[:120]}
        # One finalization per active state; state disappears or changes immediately after this.
        events.append(event);return 1

    def sample(self,plex,now):
        cfg=self.store.get('product_settings') or {'behavior_enabled':True,'behavior_user':''}
        if not cfg.get('behavior_enabled',True):
            return {'sampled':0,'active':0,'new_events':0,'status':'disabled'}
        rows=plex.sessions()
        wanted=str(cfg.get('behavior_user') or '').strip()
        if wanted:rows=[r for r in rows if str(r.get('user',''))==wanted]
        previous=self.store.get('behavior_sessions') or {};current={};events=self.store.get('behavior_events') or []
        new_events=0
        seen=set()
        for raw in rows[:20]:
            sid=str(raw.get('session_id',''))
            tid=str(raw.get('track_id',''))
            if not sid or not tid.isdigit() or sid in seen:continue
            seen.add(sid);prev=previous.get(sid)
            if prev and str(prev.get('track_id'))!=tid:
                new_events+=self._finalize(prev,now,events);prev=None
            offset=_number(raw.get('offset_seconds'));duration=_number(raw.get('duration_seconds'))
            if duration<=0:continue
            if prev:
                elapsed=max(0.0,now-_number(prev.get('last_sample_at'),now))
                delta=offset-_number(prev.get('offset_seconds'))
                observed=_number(prev.get('observed_seconds'))
                # Only count plausible forward playback; large seeks do not inflate listened time.
                if 0<=delta<=max(5.0,elapsed*2.5):observed+=delta
                started=_number(prev.get('started_at'),now);first=_number(prev.get('first_offset'))
            else:
                observed=0.0;started=now;first=offset
            current[sid]={'session_id':sid,'track_id':tid,'title':str(raw.get('title',''))[:300],
                          'artist':str(raw.get('artist',''))[:300],'user':str(raw.get('user',''))[:120],
                          'player_id':str(raw.get('player_id',''))[:200],'state':str(raw.get('state',''))[:40],
                          'duration_seconds':duration,'offset_seconds':offset,'first_offset':first,
                          'observed_seconds':observed,'started_at':started,'last_sample_at':now}
        for sid,prev in previous.items():
            if sid not in current and sid not in seen:new_events+=self._finalize(prev,now,events)
        cutoff=now-MAX_EVENT_AGE
        events=[e for e in events if _number(e.get('at'))>=cutoff][-MAX_EVENTS:]
        self.store.set_many({'behavior_sessions':current,'behavior_events':events,
                             'behavior_status':{'updated_at':now,'active':len(current),'event_count':len(events),'new_events':new_events,'user_filter':wanted}})
        return {'sampled':len(rows),'active':len(current),'new_events':new_events,'status':'ok'}
