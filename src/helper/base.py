"""Explainable full-library classification from local metadata plus conservative QQ evidence.

This module is deliberately pure: no network, Plex writes, file writes, or AI.
Unknown songs remain unclassified instead of being forced into a catch-all bucket.
"""
from collections import Counter, defaultdict
import re
from .match import normalize, artist_key, flags, title_key
from .single import LANGUAGE_CATEGORIES

# Stable genres may be inferred to album/artist siblings. Scene, mood, era and
# language labels are intentionally excluded from artist-wide propagation.
STABLE_QQ_GENRES={
    '民谣':'民谣全库','摇滚':'摇滚全库','古风':'古风全库','轻音乐':'轻音乐全库',
}
DIRECT_QQ_LABELS={
    '影视':'影视原声',
}
BASE_POLICY='v0.1.7-single-provenance'
ERA_CATEGORIES=('70年代及更早','80年代','90年代','00年代','10年代','20年代')
ALBUM_ERA_CATEGORIES=('70前专辑','80年代专辑','90年代专辑','00年代专辑','10年代专辑','20年代专辑')
ALBUM_YEAR_REASON='Plex所属专辑版本发行年（不是单曲首发年）'
# QQ nostalgia/era themes stay in the independent theme playlists. They are
# never release-date evidence for this track, album siblings, or the artist.
YEAR_REASON='Plex发行年份'
GENRE_RULES=[
    ('摇滚全库',('rock','alternative rock','indie rock','punk','metal','摇滚','朋克','金属')),
    ('民谣全库',('folk','singer songwriter','singer-songwriter','民谣','民歌')),
    ('电子全库',('electronic','electronica','edm','dance','house','trance','techno','dubstep','电子','舞曲')),
    ('R&B全库',('r&b','rnb','rhythm and blues','soul','节奏布鲁斯','灵魂乐')),
    ('嘻哈全库',('hip hop','hip-hop','rap','trap','嘻哈','说唱')),
    ('古典全库',('classical','classical crossover','古典')),
    ('爵士全库',('jazz','爵士')),
    ('乡村全库',('country','乡村')),
    ('轻音乐全库',('new age','easy listening','ambient','instrumental','轻音乐','新世纪')),
    ('影视原声',('soundtrack','film score','score','original score','ost','影视原声','原声')),
    ('蓝调全库',('blues','蓝调')),
    ('雷鬼全库',('reggae','雷鬼')),
    ('世界音乐',('world','world music','世界音乐')),
    # Put generic pop last so a specific genre may coexist rather than replace it.
    ('流行全库',('pop','c-pop','mandopop','cantopop','j-pop','k-pop','pop rock','流行')),
]
ORDER=['70年代及更早','80年代','90年代','00年代','10年代','20年代',
       '粤语','国语','流行全库','摇滚全库','民谣全库','古风全库','电子全库','R&B全库','嘻哈全库',
       '古典全库','爵士全库','乡村全库','轻音乐全库','影视原声','蓝调全库','雷鬼全库','世界音乐',
       '现场Live','DJ混音','伴奏纯音乐']


def _token(text):
    return re.sub(r'[^a-z0-9&]+',' ',str(text or '').casefold()).strip()


def _genre_categories(track):
    values=list(track.get('genres') or [])+list(track.get('styles') or [])
    raw=' | '.join(str(v) for v in values if v)
    folded=_token(raw)
    chinese=str(raw).casefold()
    out=[]
    for title,needles in GENRE_RULES:
        for needle in needles:
            n=str(needle).casefold()
            if any('\u4e00'<=c<='\u9fff' for c in n):
                hit=n in chinese
            else:
                # Word-ish containment prevents "rap" from matching "crap".
                hit=bool(re.search(r'(?<![a-z0-9])'+re.escape(n)+r'(?![a-z0-9])',folded))
            if hit:
                out.append(title);break
    return out


def _era(year):
    # No truncation of fractional years, and no 2030/2100 -> '20年代' fallback.
    if isinstance(year,bool) or not isinstance(year,(int,str)):return None
    value=str(year).strip()
    if not re.fullmatch(r'[0-9]{4}',value):return None
    y=int(value)
    if not 1877<=y<=2029:return None
    if y<1980:return '70年代及更早'
    if y<1990:return '80年代'
    if y<2000:return '90年代'
    if y<2010:return '00年代'
    if y<2020:return '10年代'
    return '20年代'


def _album_era(track):
    # A track with an explicit date never receives a second album-fallback decade.
    if track.get('year') is not None:return None
    e=_era((track.get('_base_album') or {}).get('year'))
    return ALBUM_ERA_CATEGORIES[ERA_CATEGORIES.index(e)] if e else None


def _source_label(group):
    text=' '.join(str(group.get(k) or '') for k in ('source_title','title'))
    for needle,title in STABLE_QQ_GENRES.items():
        if needle in text:return ('stable',title)
    for needle,title in DIRECT_QQ_LABELS.items():
        if needle in text:return ('direct',title)
    return (None,None)


def _akey(track):return tuple(sorted(artist_key(track.get('artist'))))
def _album_key(track):return (_akey(track),normalize(track.get('album')))


def build_base_groups(tracks,theme_plan=None,min_tracks=5,diagnostics=None):
    """Return deterministic local category groups.

    `theme_plan` is optional high-confidence evidence from the existing QQ
    intersection workflow. Only genre-like categories are propagated, and only
    under strict album/artist thresholds. Language/mood/scene is never artist-
    propagated. Track eras only use explicit track dates. Verified album-version dates are
    shown in separate playlists, never inferred from QQ or sibling songs.
    """
    if diagnostics is None:diagnostics={}
    diagnostics['single_language_conflicts']=[]
    usable=[t for t in tracks if t.get('available',True) and not t.get('_metadata_blocked')]
    by_id={str(t['id']):t for t in usable}
    assigned=defaultdict(set);reasons=defaultdict(Counter);inferred=defaultdict(set);evidence_rows=defaultdict(list)
    from .album_metadata import album_genre_eligibility
    album_genres,_=album_genre_eligibility(usable,_genre_categories)
    def add(cat,tid,reason,is_inferred=False,field=None):
        tid=str(tid)
        if tid not in by_id:return
        # Enforce the year-only contract at the common assignment boundary,
        # even if a future source adapter accidentally emits an era category.
        if cat in ERA_CATEGORIES:
            if is_inferred or reason!=YEAR_REASON or cat!=_era(by_id[tid].get('year')):return
        if cat in ALBUM_ERA_CATEGORIES:
            if is_inferred or reason!=ALBUM_YEAR_REASON or cat!=_album_era(by_id[tid]):return
        confirmed=((by_id[tid].get('_qq_single') or {}).get('fields') or {}).get('genres',[])
        if confirmed and cat not in confirmed and (is_inferred or reason.startswith('QQ主题')) and cat not in LANGUAGE_CATEGORIES:return False
        assigned[cat].add(tid);reasons[cat][reason]+=1
        t=by_id[tid];a=t.get('_base_album') or {}
        evidence_rows[cat].append({'track_id':tid,'title':t.get('title'),'artist':t.get('artist'),
            'reason':reason,'inferred':is_inferred,'field':field,
            'track_year':t.get('year'),'album_id':a.get('id'),'album_title':a.get('title'),
            'album_year':a.get('year'),'parent_year_conflict':a.get('parent_year_conflict')})
        if field and field.startswith('qq.'):
            q=t.get('_qq_single') or {};d=q.get('detail') or {}
            evidence_rows[cat][-1].update(qq_mid=d.get('mid'),source_url=d.get('source_url'),
                source_values=d.get('language_values' if cat in LANGUAGE_CATEGORIES else 'genre_values',[]),checked_at=q.get('checked_at'))
        if is_inferred:inferred[cat].add(tid)
        return True

    for t in usable:
        tid=str(t['id']);era=_era(t.get('year'))
        if era:add(era,tid,YEAR_REASON,field='track.year/originallyAvailableAt')
        album_era=_album_era(t)
        if album_era:add(album_era,tid,ALBUM_YEAR_REASON,field='album.year/originallyAvailableAt')
        q=t.get('_qq_single') or {};qfields=q.get('fields') or {}
        direct_genres=qfields.get('genres') or _genre_categories(t)
        for cat in direct_genres:
            add(cat,tid,'QQ单曲详情流派（来源标签）' if qfields.get('genres') else 'Plex Genre/Style',
                field='qq.info.genre' if qfields.get('genres') else 'track.Genre/Style')
        # Use album style only to supplement tracks without explicit usable genre.
        # No sung-language propagation, and mixed/compilation albums are excluded.
        if not direct_genres:
            for cat in album_genres.get(str(t.get('album_id')),[]):
                add(cat,tid,'Plex所属专辑风格补充（非单曲直接标签）',True,'album.Genre/Style')
        raw_tags=' | '.join(str(v) for v in list(t.get('genres') or [])+list(t.get('styles') or [])).casefold()
        local_lang=set()
        if 'cantopop' in raw_tags:local_lang.add('粤语')
        if 'mandopop' in raw_tags:local_lang.add('国语')
        if re.search(r'(?i)(?:电视剧|電影|电影|影视|原声|原聲|\bost\b|soundtrack)',str(t.get('album') or '')):
            add('影视原声',tid,'专辑名称明确标注影视/原声')
        vf=flags(t)
        if 'live' in vf:add('现场Live',tid,'标题/专辑明确标注Live或现场')
        if 'remix' in vf:add('DJ混音',tid,'标题/专辑明确标注DJ/Remix')
        if 'instrumental' in vf:add('伴奏纯音乐',tid,'标题/专辑明确标注伴奏/纯音乐')
        # Explicit version labels only. Never infer a sung language from artist origin.
        for flag,lang in [('cantonese','粤语'),('mandarin','国语'),('english','英语')]:
            if flag in vf:local_lang.add(lang)
        qq_lang=set(qfields.get('languages') or [])
        if qq_lang and local_lang and not local_lang<=qq_lang:
            diagnostics['single_language_conflicts'].append({'track_id':tid,'title':t.get('title'),
                'local_labels':sorted(local_lang),'qq_labels':sorted(qq_lang),'reason':'单曲语种来源互相矛盾，暂不采用任何一边，不影响独立流派证据'})
        elif qq_lang:
            for lang in qq_lang:add(lang,tid,'QQ单曲详情语种（不是歌单主题）',field='qq.info.lan')
        elif len(local_lang)==1:
            for lang in local_lang:add(lang,tid,'曲名或Plex直接标签明确标注语种',field='track.language_marker')
        elif len(local_lang)>1:
            diagnostics['single_language_conflicts'].append({'track_id':tid,'title':t.get('title'),
                'local_labels':sorted(local_lang),'qq_labels':[],'reason':'本地多个语种线索，未取得明确双语单曲资料'})

    stable_seeds=defaultdict(set);album_seeds=defaultdict(set)
    theme_plan=theme_plan or {}
    for group in theme_plan.get('groups') or []:
        mode,cat=_source_label(group)
        if not cat:continue
        for row in group.get('matched_rows') or []:
            local=row.get('local') or {};tid=str(local.get('id') or '')
            current=by_id.get(tid)
            if not current:continue
            # Never seed from a stale ratingKey whose current identity differs.
            if title_key(local.get('title'))!=title_key(current.get('title')) or artist_key(local.get('artist'))!=artist_key(current.get('artist')):
                continue
            oldsec=float(local.get('duration') or 0);newsec=float(current.get('duration') or 0)
            if oldsec and newsec and abs(oldsec-newsec)>max(3,oldsec*.015):continue
            accepted=add(cat,tid,'QQ主题/分类命中（辅助依据，非单曲字段）')
            if mode=='stable' and accepted:stable_seeds[cat].add(tid);album_seeds[cat].add(tid)

    # Stable GENRE propagation only: >=2 seeds in the exact same artist+album.
    album_members=defaultdict(list);artist_members=defaultdict(list)
    for t in usable:
        if _album_key(t)[1]:album_members[_album_key(t)].append(t)
        if _akey(t):artist_members[_akey(t)].append(t)
    for cat,seeds in album_seeds.items():
        album_counts=Counter(_album_key(by_id[tid]) for tid in seeds if _album_key(by_id[tid])[1])
        for key,count in album_counts.items():
            if count<2:continue
            for t in album_members.get(key,[]):
                add(cat,t['id'],f'同艺人同专辑已有{count}首QQ高置信度样本',True)

    # Artist propagation: genre only, >=3 seeds and >=80% purity among all stable
    # genre seed evidence for that exact collaborator set.
    artist_cat=defaultdict(Counter)
    for cat,seeds in stable_seeds.items():
        for tid in seeds:
            key=_akey(by_id[tid])
            if key:artist_cat[key][cat]+=1
    for key,counts in artist_cat.items():
        total=sum(counts.values())
        for cat,count in counts.items():
            if count>=3 and total and count/total>=.80:
                for t in artist_members.get(key,[]):
                    add(cat,t['id'],f'同一艺人已有{count}首稳定QQ风格样本（纯度{count/total:.0%}）',True)

    order={name:i for i,name in enumerate(list(ERA_CATEGORIES)+list(ALBUM_ERA_CATEGORIES)+ORDER[len(ERA_CATEGORIES):])}
    result=[]
    for title,ids in assigned.items():
        if len(ids)<int(min_tracks):continue
        desired=sorted(ids,key=lambda x:(int(x) if x.isdigit() else x))
        result.append({'id':'base:'+normalize(title),'title':title,'kind':'base','desired':desired,'matched':len(desired),
                       'evidence':dict(reasons[title]),'inferred_count':len(inferred[title]),'blocked':[],
                       'dimension':'track_year' if title in ERA_CATEGORIES else 'album_year' if title in ALBUM_ERA_CATEGORIES else
                           'language' if title in LANGUAGE_CATEGORIES else 'version' if title in ('现场Live','DJ混音','伴奏纯音乐') else 'genre',
                       'evidence_rows':evidence_rows[title]})
    result.sort(key=lambda g:(order.get(g['title'],999),g['title']))
    return result
