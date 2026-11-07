"""Compatibility import: this skill always uses source-grounded full review."""

def active(cfg):
    mode=cfg.get('subtitle_review_mode','full')
    if mode!='full':
        raise ValueError('知识文化内容必须用full核对术语、数字与语义，不支持keywords_only')
    return False
