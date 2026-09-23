"""Helper-only identity evidence. Reads metadata strings, never opens an audio file."""
from copy import deepcopy
import hashlib
import json
import re
from pathlib import PurePosixPath
from .match import artist_key, title_key, normalize, flags, version

_FIELDS=('id','title','artist','album','duration','available','guid','paths')
PLACEHOLDER_ARTISTS={'', 'various', 'variousartists', 'unknown', 'unknownartist', '群星', '未知', '未知歌手'}

def identity_fingerprint(track):
    return hashlib.sha256(json.dumps({k:track.get(k) for k in _FIELDS},sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()

def filename_suggestion(track):
    """Only the unambiguous Artist - Title file layout. Never infer from folders."""
    candidates=[]
    for value in track.get('paths') or []:
        name=PurePosixPath(str(value).replace('\\','/')).name
        stem=re.sub(r'\.(?:flac|mp3|ogg|opus|m4a|aac|wav|aiff?|ape|wma)$','',name,flags=re.I)
        stem=re.sub(r'^\d{10,}\s*[-_.]\s*','',stem)
        pair=re.split(r'\s+[-–—]\s+',stem,maxsplit=1)
        if len(pair)!=2:continue
        artist,title=pair
        artist=re.sub(r'\s+_\s+','、',artist).strip()
        title=title.strip()
        if not title_key(title) or not artist_key(artist) or normalize(artist).isdigit():continue
        candidates.append({'title':title,'artist':artist,'filename':name})
    if not candidates:return None
    signatures={(title_key(t['title']),artist_key(t['artist'])) for t in candidates}
    return candidates[0] if len(signatures)==1 else None

def inspect_track(track):
    suggestion=filename_suggestion(track);issues=[]
    incomplete=not title_key(track.get('title')) or normalize(track.get('artist')) in PLACEHOLDER_ARTISTS
    if incomplete:issues.append('Plex歌名或歌手缺失/为占位值')
    if suggestion:
        if not incomplete and title_key(track.get('title'))!=title_key(suggestion['title']):
            issues.append('文件名歌名与Plex歌名不同，不能凭文件名覆盖')
        if not incomplete and artist_key(track.get('artist'))!=artist_key(suggestion['artist']):
            issues.append('文件名歌手与Plex歌手不同，需确认版本或标签')
        filename_flags=version(suggestion['title'])
        plex_flags=version(track.get('title')) | (version(track.get('album')) & {'live','instrumental','remix','acoustic'})
        if filename_flags-plex_flags:issues.append('文件名包含Plex未标明的版本信息')
    # A well-formed Plex identity remains usable even when an untrusted filename
    # spells the recording differently.  Keep the discrepancy visible as an
    # advisory, but do not prevent strict QQ title/artist/duration verification.
    status='incomplete' if incomplete else 'filename_difference' if issues else 'ok'
    return {'id':str(track['id']),'status':status,'issues':issues,'original':{k:track.get(k) for k in _FIELDS},
            'suggestion':suggestion,'fingerprint':identity_fingerprint(track)}

def prepare_catalog(tracks,corrections):
    result=[];audit=[]
    for original in tracks:
        t=deepcopy(original);row=inspect_track(original);rule=corrections.get(str(t['id']))
        if rule:
            if rule.get('fingerprint')==row['fingerprint']:
                # Keep all explicit version warnings; identity editing is not permission to
                # silently turn an instrumental/live/remix into a studio recording.
                t['_original_version']=sorted(flags(original))
                for k in ('title','artist','album'):
                    if k in rule:t[k]=rule[k]
                row.update(status='confirmed',issues=['助手内部已确认；不修改Plex标签或原文件'],correction={k:rule.get(k) for k in ('title','artist','album','note')})
            else:
                row.update(status='stale_correction',issues=['原曲目信息已变化，之前的内部纠正需重新核对']+row['issues'])
        t['_metadata_blocked']=row['status'] in ('incomplete','conflict','stale_correction')
        t['_metadata_status']=row['status']
        if row['suggestion']:t['_metadata_suggestion']=row['suggestion']
        result.append(t)
        if row['status']!='ok':audit.append(row)
    return result,audit

def metadata_audit_rows(tracks,corrections,query='',review_only=False):
    """Return the real helper audit, optionally limited to rows that block classification."""
    _,rows=prepare_catalog(tracks,corrections)
    if review_only:
        rows=[row for row in rows if row.get('status') in ('incomplete','conflict','stale_correction')]
    key=normalize(query)
    if key:
        rows=[row for row in rows if key in normalize(json.dumps(row,ensure_ascii=False))]
    return rows
