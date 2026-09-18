"""Explainable local daily recommender.

No Sonic Analysis, LLM, audio upload, or invented history. Favorites and ratings are
weak preference seeds; actual playback behavior can teach affinities without making
favorite tracks dominate the daily list.
"""
from collections import Counter, defaultdict
import hashlib
import math
from .match import artist_key, title_key, normalize
from .metadata import PLACEHOLDER_ARTISTS
from .audience import is_childrens_track

DAILY_POLICY='v0.2.3-childrens-isolation'
DEFAULT_DAILY={'size':30,'artist_cap':2,'album_cap':1,'favorite_cap':4,'favorite_percent':20,'repeat_days':21,
               'cooldown_hours':24,'hour':6,'enabled':False,'seed_playlist_ids':[]}


def number(value,default=0):
    try:
        n=float(value)
        return n if math.isfinite(n) and n>=0 else default
    except (TypeError,ValueError,OverflowError):return default


def diagnostic_display(stats):
    """Map recommendation diagnostics to the labels shown by the status page."""
    stats = stats or {}
    buckets = stats.get('bucket_counts') or {}
    return {
        'summary': {
            'candidate': int(number(stats.get('candidate_count'))),
            'selected': int(number(stats.get('selected'))),
        },
        'buckets': {
            'stable': int(number(buckets.get('稳定喜好'))),
            'recent': int(number(buckets.get('近期口味'))),
            'rediscovery': int(number(buckets.get('久未重听'))),
            'exploration': int(number(buckets.get('曲库探索'))),
        },
        'exclusions': {
            'manual': int(number(stats.get('excluded_never_recommend'))),
            'recent_plays': int(number(stats.get('excluded_recent_plays'))),
            'fatigue': int(number(stats.get('excluded_by_fatigue'))),
            'recent_daily': int(number(stats.get('excluded_recent_daily'))),
        },
    }


def song_key(t):
    return title_key(t.get('title'))+'|'+','.join(sorted(artist_key(t.get('artist'))))


def _jitter(seed,tid):
    return int(hashlib.sha256((str(seed)+'|'+str(tid)).encode()).hexdigest()[:12],16)/float(0xffffffffffff)


def _valid(t,features=()):
    return (str(t.get('id','')).isdigit() and t.get('available',True) and
            title_key(t.get('title')) and normalize(t.get('artist')) not in PLACEHOLDER_ARTISTS and
            not t.get('_metadata_blocked') and not is_childrens_track(t,features) and
            45<=number(t.get('duration'))<=1800)


def _compress(profile):
    values={k:math.sqrt(max(0,v)) for k,v in profile.items() if v>0}
    top=max(values.values(),default=1)
    return {k:v/top for k,v in values.items()}


def recommend(tracks,features,feedback,settings,history,seed_ids,now,seed,behavior=None):
    cfg={**DEFAULT_DAILY,**(settings or {})};behavior=behavior or {}
    size=int(cfg['size']);cap=int(cfg['artist_cap']);acap=int(cfg['album_cap']);favcap=int(cfg['favorite_cap']);repeat=int(cfg['repeat_days'])
    if not 1<=size<=100 or not 1<=cap<=10 or not 1<=acap<=10 or not 0<=favcap<=10 or not 1<=repeat<=90:
        raise ValueError('推荐数量或多样性设置无效')
    track_feedback=(feedback or {}).get('tracks',{});artist_feedback=(feedback or {}).get('artists',{})
    seed_ids=set(map(str,seed_ids or []));all_valid=[t for t in tracks if _valid(t,features.get(str(t.get('id')),()))];byid={str(t['id']):t for t in all_valid}
    childrens_excluded=sum(is_childrens_track(t,features.get(str(t.get('id')),())) for t in tracks)
    avoided_artists={key for name,r in artist_feedback.items() if r.get('value')=='avoid' for key in artist_key(name)}
    liked_artists={key for name,r in artist_feedback.items() if r.get('value')=='like' for key in artist_key(name)}
    avoided=set()
    for t in all_valid:
        tid=str(t['id']);rating=t.get('user_rating')
        if (track_feedback.get(tid,{}).get('value')=='avoid' or artist_key(t['artist']) & avoided_artists or
                rating is not None and 0<number(rating)<=2):avoided.add(tid)
    avoided_songs={song_key(byid[k]) for k in avoided if k in byid}
    usable=[t for t in all_valid if str(t['id']) not in avoided and song_key(t) not in avoided_songs]

    aprofile=defaultdict(float);album_profile=defaultdict(float);fprofile=defaultdict(float)
    positives={};direct={};known_played=0;rated=0
    for t in usable:
        tid=str(t['id']);rating=number(t.get('user_rating'));count=number(t.get('view_count'))
        b=behavior.get(tid,{}) or {};bscore=float(b.get('score') or 0)
        islike=track_feedback.get(tid,{}).get('value')=='like';favorite=tid in seed_ids or rating>=8
        if rating>0:rated+=1
        if count>0:known_played+=1
        # Seeds teach taste. Favorites are intentionally strong here because direct
        # favorite tracks are later hard-capped and cannot leak into other buckets.
        weight=(5 if islike else 0)+(2.5 if rating>=8 else 1 if rating>=6 else 0)+(4 if favorite else 0)+max(0,bscore)*3
        if count>0:
            days=max(0,(now-number(t.get('last_viewed_at'),now))/86400)
            weight+=min(1.5,math.log1p(count))*(0.3+0.7*math.exp(-days/180))
        if weight<=0:continue
        positives[tid]=weight;direct[tid]={'like':islike,'rating':rating,'favorite':favorite,'seeded':tid in seed_ids,'count':count,'behavior':bscore,
                                         'recent_positive':int(b.get('recent_positive') or 0)}
        artists=artist_key(t['artist'])
        for a in artists:aprofile[a]+=weight/max(1,len(artists))
        if normalize(t.get('album')):album_profile[(tuple(sorted(artists)),normalize(t['album']))]+=weight
        for f in set(features.get(tid,[])):fprofile[f]+=weight
    for a in liked_artists:aprofile[a]+=5
    aprofile=_compress(aprofile);album_profile=_compress(album_profile);fprofile=_compress(fprofile)
    personalized=bool(positives or liked_artists)

    prior_ids=set();prior_songs=set()
    for h in history or []:
        if 0<=now-number(h.get('created_at'))<repeat*86400:
            prior_ids.update(map(str,h.get('ids',[])));prior_songs.update(h.get('song_keys',[]))

    candidates=[];cooled=0
    for t in usable:
        tid=str(t['id']);last=number(t.get('last_viewed_at'));artists=artist_key(t['artist']);sk=song_key(t)
        if (last and now-last<cfg['cooldown_hours']*3600) or tid in prior_ids or sk in prior_songs:
            cooled+=1;continue
        album=(tuple(sorted(artists)),normalize(t.get('album')));d=direct.get(tid,{});b=behavior.get(tid,{}) or {}
        reasons=[];score=1.0
        if d.get('like'):score+=4;reasons.append('你主动标记过喜欢')
        direct_limited=tid in seed_ids or number(t.get('user_rating'))>0
        if d.get('favorite'):score+=2
        if direct_limited:
            if tid in seed_ids:reasons.append('来自你的收藏，收藏/星标歌曲每天只少量出现')
            else:reasons.append('你在Plex点过星，所有星标歌曲合计只少量出现')
        if d.get('rating',0)>=8:score+=2
        direct_behavior=float(b.get('score') or 0)
        if direct_behavior>0:
            score+=min(4,direct_behavior*1.5);reasons.append('过去的实际播放表现较好')
        elif direct_behavior<0:
            score+=max(-3,direct_behavior*2);reasons.append('近期有过较早跳过，已降低权重')
        artist_score=max((aprofile.get(a,0) for a in artists),default=0)
        album_score=album_profile.get(album,0)
        related=sorted(((fprofile.get(f,0),f) for f in set(features.get(tid,[])) if fprofile.get(f,0)),reverse=True)
        feature_score=related[0][0] if related else 0
        affinity=6*artist_score+3.5*feature_score+1.5*album_score
        recent_positive=int(b.get('recent_positive') or 0)
        # A track that was itself completed repeatedly this week can still teach
        # taste, but should not receive the full self-affinity echo.
        candidate_affinity=affinity*(0.35 if recent_positive else 1.0)
        score+=candidate_affinity
        if artist_score:reasons.append('和你经常听完的歌手/歌曲有关联')
        if related:
            readable=next((f.split(':',1)[1] for _,f in related if f.startswith('分类:')),None)
            reasons.append('与你偏好的“'+readable+'”主题有关联' if readable else '与你偏好的参考歌单有关联')
        elif album_score:reasons.append('来自你有正向播放记录的专辑')
        if d.get('count',0):score+=min(0.6,math.log1p(d['count'])/5)
        if recent_positive:score-=min(3,recent_positive*1.1)  # do not echo what was just replayed repeatedly
        play_count=t.get('view_count');novelty=1. if play_count in (None,0) else 1/(1+math.log1p(number(play_count)))
        score+=novelty*0.8
        added=number(t.get('added_at'));added_days=(now-added)/86400 if added and now>=added else 99999
        recent_added=0<=added_days<=90
        if recent_added:score+=max(0,1.2-added_days/90)
        last_days=(now-last)/86400 if last and now>=last else 99999
        rediscover=bool((direct_behavior>0 or d.get('count',0)>0 or d.get('rating',0)>=6) and last_days>=30)
        if not reasons:reasons.append('从整个本地曲库探索，帮助发现不常听的歌曲')
        candidates.append({'id':tid,'title':t['title'],'artist':t['artist'],'album':t.get('album',''),
            'duration':t.get('duration'),'score':round(score,6),'reasons':reasons[:3],'song_key':sk,
            '_artists':artists,'_album':album if album[1] else None,'_novelty':novelty,'_tie':_jitter(seed,tid),
            '_favorite':bool(d.get('favorite')),'_direct_limited':direct_limited,'_affinity':candidate_affinity,'_rediscover':rediscover,'_recent_added':recent_added,
            '_last_days':last_days})

    selected=[];artists_used=Counter();albums_used=Counter();used_ids=set();used_songs=set()
    def take_n(rows,count,kind,predicate=lambda row:True):
        taken=0
        for row in rows:
            if taken>=count or len(selected)>=size:break
            if not predicate(row) or row['id'] in used_ids or row['song_key'] in used_songs:continue
            if any(artists_used[a]>=cap for a in row['_artists']):continue
            if row['_album'] and albums_used[row['_album']]>=acap:continue
            item={k:v for k,v in row.items() if not k.startswith('_')};item['bucket']=kind
            selected.append(item);used_ids.add(row['id']);used_songs.add(row['song_key']);taken+=1
            artists_used.update(row['_artists'])
            if row['_album']:albums_used[row['_album']]+=1
        return taken

    ranked=sorted(candidates,key=lambda r:(-r['score'],-r['_tie'],r['id']))
    affinity_rows=sorted(candidates,key=lambda r:(-r['_affinity'],-r['score'],-r['_tie'],r['id']))
    rediscover_rows=sorted(candidates,key=lambda r:(-r['_last_days'],-r['score'],-r['_tie'],r['id']))
    recent_rows=sorted(candidates,key=lambda r:(-number(byid[r['id']].get('added_at')),-r['score'],-r['_tie'],r['id']))
    explore=sorted(candidates,key=lambda r:(-(r['_novelty']+r['_tie']*.7),-r['_affinity'],r['id']))

    favorite_quota=min(favcap,max(0,round(size*0.13)))
    take_n(ranked,favorite_quota,'收藏/星标点缀',lambda r:r['_direct_limited'])
    take_n(affinity_rows,round(size*.33),'行为相似',lambda r:not r['_direct_limited'] and r['_affinity']>0)
    take_n(rediscover_rows,round(size*.23),'久未重听',lambda r:not r['_direct_limited'] and r['_rediscover'])
    take_n(recent_rows,round(size*.10),'最近新增',lambda r:not r['_direct_limited'] and r['_recent_added'])
    take_n(explore,size-len(selected),'探索',lambda r:not r['_direct_limited'])
    if len(selected)<size:take_n(ranked,size-len(selected),'补充',lambda r:not r['_direct_limited'])

    # Selection buckets are quota mechanisms, not playback order. Mix the final
    # list with a deterministic per-day shuffle so favorite/starred tracks do
    # not occupy the first slots and refreshes keep the same order.
    selected.sort(key=lambda row:(_jitter(seed+'|order',str(row['id'])),str(row['id'])))

    warnings=[]
    if not personalized:warnings.append('暂无足够播放偏好：今天以曲库探索和最近新增为主，播放行为会逐步参与下一次推荐。')
    elif len(positives)<5:warnings.append('播放偏好样本还较少；推荐会随实际听完/快速跳过记录逐步调整。')
    if len(selected)<size:warnings.append(f'符合冷却与多样性限制的歌曲仅{len(selected)}首，没有用重复歌曲凑数。')
    stats={'library_count':len(tracks),'eligible_count':len(all_valid),'positive_seed_count':len(positives),
           'favorite_playlist_seed_count':len(seed_ids & set(byid)),'favorite_selected_count':sum(x['bucket']=='收藏/星标点缀' for x in selected),
           'rated_count':rated,'played_count':known_played,'behavior_positive_tracks':sum((x.get('score') or 0)>0 for x in behavior.values()),
           'behavior_negative_tracks':sum((x.get('score') or 0)<0 for x in behavior.values()),
           'excluded_by_feedback':len(all_valid)-len(usable),'cooled_count':cooled,'candidate_count':len(candidates),
           'missing_rating_fields':sum(t.get('user_rating') is None for t in tracks),
           'missing_playcount_fields':sum(t.get('view_count') is None for t in tracks),'childrens_excluded_count':childrens_excluded,
           'requested':size,'selected':len(selected),
           'bucket_counts':dict(Counter(x['bucket'] for x in selected))}
    return {'mode':'personalized' if personalized else 'exploration','items':selected,'stats':stats,'warnings':warnings,'policy':DAILY_POLICY}
