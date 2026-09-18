"""Keep verified album provenance separate; no I/O, filename guessing, or AI."""
from collections import Counter,defaultdict
from copy import deepcopy
import hashlib
import json
import re
from .match import normalize,artist_key


def attach_albums(tracks,albums):
    """Join solely on the Track's parentRatingKey; never merge by title/artist.

    Returns isolated copies plus diagnostic counts. The original Track.year,
    genres and styles are never overwritten by parent metadata.
    """
    requested={str(t['album_id']) for t in tracks if t.get('album_id')}
    result=[];diagnostics={'tracks':len(tracks),'album_ids':len(requested),
        'albums_read':len(set(albums)&requested),'missing_albums':len(requested-set(albums)),
        'tracks_without_album_id':0,'mismatched_tracks':0,'attached_tracks':0,
        'track_year_count':0,'parent_year_count':0,'album_year_count':0,
        'track_tag_count':0,'album_tag_count':0,'parent_year_conflicts':0}
    for original in tracks:
        t=deepcopy(original);t.pop('_base_album',None)
        diagnostics['track_year_count']+=bool(t.get('year'))
        diagnostics['parent_year_count']+=bool(t.get('parent_year'))
        diagnostics['track_tag_count']+=bool(t.get('genres') or t.get('styles'))
        aid=str(t.get('album_id') or '');a=albums.get(aid)
        if not aid:
            diagnostics['tracks_without_album_id']+=1
        elif a:
            same_id=str(a.get('id') or '')==aid
            title_ok=bool(normalize(t.get('album'))) and normalize(t.get('album'))==normalize(a.get('title'))
            tg,ag=t.get('album_guid'),a.get('guid')
            guid_ok=not (tg and ag and tg!=ag)
            if same_id and title_ok and guid_ok:
                t['_base_album']=deepcopy(a);diagnostics['attached_tracks']+=1
                diagnostics['album_year_count']+=bool(a.get('year'))
                diagnostics['album_tag_count']+=bool(a.get('genres') or a.get('styles'))
                if t.get('parent_year') and a.get('year') and t['parent_year']!=a['year']:
                    diagnostics['parent_year_conflicts']+=1
                    # Exact album object takes precedence for the album-version bucket,
                    # but the discrepancy stays visible in per-track evidence/report.
                    t['_base_album']['parent_year_conflict']=t['parent_year']
            else:diagnostics['mismatched_tracks']+=1
        result.append(t)
    return result,diagnostics


def base_track_fingerprint(track):
    fields=('id','title','artist','album','duration','available','guid','paths','year','genres','styles','moods',
            'album_id','album_guid','parent_year','released_at','_base_album')
    return hashlib.sha256(json.dumps({k:track.get(k) for k in fields},sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def album_genre_eligibility(tracks,categorize):
    """Conservative album-style supplementation. Not a single-track ground truth.

    No propagation from mixed-artist compilations, title-marked anthologies,
    multiple unrelated genres, or a peer whose explicit tags disagree.
    """
    peers=defaultdict(list)
    for t in tracks:
        if t.get('_base_album'):peers[str(t['album_id'])].append(t)
    allowed={};reasons=Counter()
    for aid,items in peers.items():
        a=items[0]['_base_album'];categories=categorize(a);specific=set(categories)-{'流行全库'}
        artist=artist_key(a.get('artist'));name=normalize(a.get('artist'))
        anthology=bool(re.search(r'(?i)精选|精選|合辑|合輯|选集|選集|合集|群星|greatest\s*hits|best\s*of|anthology|compilation|collection',a.get('title','')))
        reason=None
        if not categories:reason='专辑没有可识别风格标签'
        elif a.get('compilation') or anthology or name in ('','various','variousartists','群星','unknown'):
            reason='合辑或精选不向单曲传播风格'
        elif len(specific)>1:reason='专辑包含多个不同风格，待核对'
        elif not artist or any(artist_key(t.get('artist'))!=artist for t in items):reason='专辑歌手与单曲歌手不一致'
        else:
            known=set()
            for t in items:known.update(set(categorize(t))-{'流行全库'})
            if len(known)>1 or known and specific and not known<=specific:
                reason='同专辑已有单曲风格冲突'
        if reason:reasons[reason]+=len(items);continue
        allowed[aid]=categories
    return allowed,dict(reasons)


def coverage_dimensions(groups,total):
    sets=defaultdict(set)
    for group in groups:sets[group.get('dimension','genre')].update(map(str,group.get('desired',[])))
    sets['language_genre']=sets['language']|sets['genre']
    dates=sets['track_year']|sets['album_year']
    non_dates=sets['language_genre']|sets['version']
    sets['date_only']=dates-non_dates
    sets['non_date']=non_dates
    return {k:{'covered':len(sets[k]),'coverage':round(100*len(sets[k])/total,2) if total else 0}
            for k in ('track_year','album_year','language','genre','version','language_genre','non_date','date_only')}
