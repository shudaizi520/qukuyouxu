"""Guarded replacement used ONLY for the dedicated daily recommendation playlist.

Keep the playlist ID, title and ownership marker, but converge membership to today's
exact desired set. The transition is deliberately bounded: when old and new sets are
disjoint, add one anchor first (max old+1), remove stale members, then add the rest.
This prevents a failed update from leaving old+new (for example 60 tracks for a
30-track daily list).
"""
from copy import deepcopy


def sync_owned_items(plex,before,desired):
    from .engine import SafetyError, fingerprint, state_ids
    desired=list(map(str,desired))
    if not desired or len(desired)>100 or len(set(desired))!=len(desired) or any(not k.isdigit() for k in desired):
        raise SafetyError('每日推荐需1～100首不同的有效本地曲目，禁止清空歌单')
    current=plex.playlist_state(before['id'])
    if fingerprint(current)!=fingerprint(before):raise SafetyError('每日歌单在写入前被修改，停止')
    if len(set(state_ids(current)))!=len(current['items']):raise SafetyError('每日歌单已有重复曲目，先核对，不自动清理')
    def verify(expected):
        fresh=plex.playlist_state(before['id'])
        if (fresh['id']!=before['id'] or fresh['title']!=before['title'] or
                fresh.get('summary','')!=before.get('summary','') or state_ids(fresh)!=expected):
            raise SafetyError('每日歌单变更后回读不一致，停止后续修改')
        return fresh

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
        raise SafetyError('每日歌单成员替换后数量或集合不一致，停止重排')

    # Reorder only after membership is exact. A MOVE failure leaves the correct
    # members and count, merely not the preferred ordering.
    for position,tid in enumerate(desired):
        if state_ids(current)[position]==tid:continue
        moving=next(x for x in current['items'] if x['id']==tid)
        previous=current['items'][position-1]['item_id'] if position else None
        expected_items=deepcopy(current['items']);expected_items.remove(moving);expected_items.insert(position,moving)
        plex.move_item(before['id'],moving['item_id'],previous)
        current=verify([x['id'] for x in expected_items])
    return current
