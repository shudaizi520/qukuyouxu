"""Display-only naming. Never changes classification, song tags or file names."""
import re
import unicodedata

TITLE_POLICY = 'short-v1'
ALIASES = {
    '国语': '国语精选', '华语': '华语精选', '粤语': '粤语经典',
    '民谣': '民谣精选', '摇滚': '摇滚精选', '古风': '古风精选',
    '睡前': '睡前舒缓', '开车': '开车提神', '工作': '工作专注',
    '运动': '运动精选', '治愈': '治愈精选', '伤感': '伤感情歌',
    '影视': '影视金曲', '怀旧': '怀旧金曲', '经典': '经典老歌',
}


def short_title(value):
    """Remove the UI-hostile legacy prefix; shorten only known, meaningful names.

    Unknown custom labels keep their complete wording. Arbitrarily slicing six
    characters would merely replace Plexamp's ellipsis with permanent data loss.
    """
    name = unicodedata.normalize('NFKC', str(value)).strip()
    name = re.sub(r'^(?:自动分类\s*[·•:：\-]\s*)+', '', name).strip()
    if not name or len(name) > 80 or any(ord(c) < 32 for c in name):
        raise ValueError('歌单名称为空、过长或包含控制字符')
    decade = re.fullmatch(r'(\d{2})年代(?:青春回忆|怀旧经典|经典回忆|经典金曲|青春经典|回忆|怀旧)', name)
    if decade:
        return decade.group(1) + '年代经典'
    return ALIASES.get(name, name)


def name_key(value):
    return unicodedata.normalize('NFKC', str(value)).strip().casefold()
