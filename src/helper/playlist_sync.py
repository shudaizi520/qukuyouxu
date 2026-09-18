"""Guarded membership replacement for the dedicated daily recommendation playlist.

Keep the playlist ID, title and ownership marker, but converge membership to today's
exact desired set. The transition is deliberately bounded: when old and new sets are
disjoint, add one anchor first (max old+1), remove stale members, then add the rest.
This prevents a failed update from leaving old+new (for example 60 tracks for a
30-track daily list).
"""
import time


READ_AFTER_WRITE_ATTEMPTS = 10
READ_AFTER_WRITE_DELAY = 1.0


def sync_owned_items(plex,before,desired):
    from .engine import SafetyError, fingerprint, state_ids
    desired=list(map(str,desired))
    if not desired or len(desired)>100 or len(set(desired))!=len(desired) or any(not k.isdigit() for k in desired):
        raise SafetyError('每日推荐需1～100首不同的有效本地曲目，禁止清空歌单')
    current=plex.playlist_state(before['id'])
    if fingerprint(current)!=fingerprint(before):raise SafetyError('每日歌单在写入前被修改，停止')
    if len(set(state_ids(current)))!=len(current['items']):raise SafetyError('每日歌单已有重复曲目，先核对，不自动清理')
    def verify(expected):
        # Plex can briefly serve the pre-write playlist after a successful
        # append/remove/move.  Retry only the read; never repeat the mutation.
        for attempt in range(READ_AFTER_WRITE_ATTEMPTS):
            fresh=plex.playlist_state(before['id'])
            if (fresh['id']==before['id'] and fresh['title']==before['title'] and
                    fresh.get('summary','')==before.get('summary','') and state_ids(fresh)==expected):
                return fresh
            if attempt+1<READ_AFTER_WRITE_ATTEMPTS:
                time.sleep(READ_AFTER_WRITE_DELAY*(attempt+1))
        raise SafetyError('每日歌单变更后回读不一致，停止后续修改')

    desired_set=set(desired)
    existing=set(state_ids(current))
    # If old/new are fully disjoint, keep the playlist non-empty without ever
    # appending the whole new day on top of the old day. One anchor caps the
    # transient size at old_count + 1 instead of old_count + desired_count.
    if not (existing & desired_set):
        anchor=desired[0]
        plex.append(before['id'],[anchor])
        current=verify(state_ids(current)+[anchor])

    # Remove stale entries before bulk-adding missing desired members. Any failure
    # here leaves at most the old list plus one anchor, never a doubled daily list.
    for item in [x for x in list(current['items']) if x['id'] not in desired_set]:
        expected=[x['id'] for x in current['items'] if x['item_id']!=item['item_id']]
        plex.remove_items(before['id'],[item['item_id']]);current=verify(expected)

    missing=[k for k in desired if k not in set(state_ids(current))]
    if missing:
        expected=state_ids(current)+missing
        plex.append(before['id'],missing)
        current=verify(expected)

    if len(current['items'])!=len(desired) or set(state_ids(current))!=desired_set:
        raise SafetyError('每日歌单成员替换后数量或集合不一致，停止更新')

    # Membership is the product contract. Some Plex servers acknowledge MOVE but
    # never apply it to audio playlists, so exact ordering must not turn a correct
    # 50-song playlist into an unsafe/uncertain result.
    return current
