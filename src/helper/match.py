"""Conservative matching, not AI classification. No file writes or network access."""
from collections import defaultdict
from functools import lru_cache
import html
import re
import unicodedata

try:
    from opencc import OpenCC
    _converter = OpenCC('t2s')
except ImportError:
    _converter = None

CONVERSION_AVAILABLE = _converter is not None

@lru_cache(maxsize=50000)
def normalize(text):
    text = unicodedata.normalize('NFKC', html.unescape(str(text or ''))).casefold()
    if _converter:
        text = _converter.convert(text)
    return ''.join(ch for ch in text if unicodedata.category(ch)[0] in ('L','N'))

FLAGS = {
    'live': r'\blive\b|现场|現場|演唱会|演唱會',
    'instrumental': r'伴奏|纯音乐|純音樂|instrumental|off\s*vocal|karaoke',
    'cover': r'\bcover\b|翻唱|翻自',
    'remix': r'\bremix\b|混音|\bdj\b',
    'acoustic': r'\bacoustic\b|不插电|不插電|\bunplugged\b',
    'cantonese': r'粤语版|粵語版|cantonese\s*version|(?:^|[\s(（])粤(?:语|語)?(?:版)?(?=[\s)）]|$)',
    'mandarin': r'国语版|國語版|mandarin\s*version|(?:^|[\s(（])国(?:语|語)?(?:版)?(?=[\s)）]|$)',
    'english': r'英文版|english\s*version|(?:^|[\s(（])英(?:文)?(?:版)?(?=[\s)）]|$)',
}

def version(text):
    text=unicodedata.normalize('NFKC',str(text or '')).lower()
    return frozenset(k for k,v in FLAGS.items() if re.search(v,text))

def title_key(text):
    # Strip ONLY explicit version annotation, not arbitrary bracket content.
    t=unicodedata.normalize('NFKC',str(text or ''))
    t=re.sub(r'\(([^()]*)\)',lambda m:'' if version(m[1]) else m[0],t)
    return normalize(t)

def artist_key(text):
    """Normalize collaboration delimiters, protecting the literal band AC/DC.

    Names inside a QQ artist list and raw Plex strings use the same rules.
    Do not infer unknown aliases or discard collaborators.
    """
    if isinstance(text,list):
        return frozenset(k for value in text for k in artist_key(value))
    text=unicodedata.normalize('NFKC',html.unescape(str(text or '')))
    text=re.sub(r'(?i)\bac\s*/\s*dc\b','ACDC',text)
    parts=re.split(r'、|;|；|/|\s*&\s*|\s+(?:feat\.?|featuring|with)\s+',text,flags=re.I)
    return frozenset(normalize(x) for x in parts if normalize(x))

def flags(track):
    known=version(track.get('title')) | (version(track.get('album')) & {'live','instrumental','remix','acoustic'})
    # Filename is never authoritative identity, but explicit version warnings
    # must not disappear just because a Plex label omits them.
    for path in track.get('paths') or []:
        basename=str(path).replace('\\','/').rsplit('/',1)[-1]
        stem=re.sub(r'^\d{10,}\s*[-_.]\s*','',basename)
        parts=re.split(r'\s+[-–—]\s+',stem,maxsplit=1)
        known |= version(parts[-1])
    return known | frozenset(track.get('_original_version') or [])

class Catalog:
    def __init__(self, tracks):
        self.tracks=list(tracks)
        self.by_title=defaultdict(list)
        self.by_id={}
        self.by_hint=defaultdict(list)
        for t in self.tracks:
            self.by_title[title_key(t.get('title'))].append(t)
            self.by_id[str(t['id'])]=t
            hint=t.get('_metadata_suggestion')
            if hint and t.get('_metadata_blocked'):self.by_hint[title_key(hint['title'])].append(t)


def match(query, catalog):
    def result(status, candidates=(), chosen=None):
        return {'status':status,'id':chosen,'candidates':[str(t['id']) for t in candidates[:8]]}
    if not title_key(query.get('title')) or not artist_key(query.get('artist')):
        return result('missing_tags')
    pool=catalog.by_title.get(title_key(query['title']),[])
    if not pool:
        hints=catalog.by_hint.get(title_key(query['title']),[])
        if hints:return result('metadata_conflict',hints)
        return result('missing')
    selected=[t for t in pool if artist_key(t.get('artist'))==artist_key(query.get('artist'))]
    if not selected:return result('artist_mismatch',pool)
    pool=selected
    selected=[t for t in pool if flags(t)==flags(query)]
    if not selected:return result('version_mismatch',pool)
    pool=selected
    qsec=float(query.get('duration') or 0)
    if qsec:
        # Unknown duration is not proof of equality, keep for album+artist matching.
        selected=[t for t in pool if not t.get('duration') or abs(float(t['duration'])-qsec)<=max(3,qsec*.015)]
        if not selected:return result('duration_mismatch',pool)
        pool=selected
    selected=[t for t in pool if t.get('available',True)]
    if not selected:return result('unavailable',pool)
    pool=selected
    selected=[t for t in pool if not t.get('_metadata_blocked')]
    if not selected:return result('metadata_conflict',pool)
    pool=selected
    qa=normalize(query.get('album'))
    same_album=[t for t in pool if qa and normalize(t.get('album'))==qa]
    if same_album:pool=same_album
    if len(pool)>1:
        # Duplicate files may be collapsed only with the same album AND close known durations.
        albums={normalize(t.get('album')) for t in pool}
        secs=[float(t.get('duration') or 0) for t in pool]
        if len(albums)!=1 or '' in albums or min(secs)<=0 or max(secs)-min(secs)>1:
            return result('ambiguous',pool)
    pool=sorted(pool,key=lambda t:(-int(t.get('bitrate') or 0),str(t['id'])))
    return result('matched',pool,str(pool[0]['id']))
