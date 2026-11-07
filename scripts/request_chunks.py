"""Split requests by real serialized UTF-8 body size and item count."""
import json
import config


def partition(items, field, base=None, limits=None):
    limits = limits or config.load_spec()['api_limits']
    count, cap = limits['max_items'], limits['max_payload_bytes']
    if type(count) is not int or count < 1 or type(cap) is not int or cap < 1024:
        raise ValueError('无效请求分组上限')
    base = base or {}
    def body(rows):
        return {**base, field:json.dumps(rows, ensure_ascii=False, separators=(',',':'))}
    def fits(rows):
        # Reserve room for the latest real draft_url added by the downstream caller.
        return len(rows) <= count and len(json.dumps(body(rows),ensure_ascii=False).encode('utf-8')) + 4096 <= cap
    group = []
    for item in items:
        if not fits([item]): raise ValueError('单项超过请求体限制，需调整素材URL或平台限制')
        if group and not fits(group+[item]): yield body(group); group = []
        group.append(item)
    if group: yield body(group)
