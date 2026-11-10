"""Apply the house rendering finish to new images while keeping scene content and literal titles."""
import re


VERSION = 'smooth-clear-v1'
RULE = ('【画面表现】线条清晰连贯，轮廓和五官边缘干净准确，色面平滑、明暗自然过渡；'
        '用色彩、形体和光影表现材质，保留衣饰与器物的必要结构。背景与暗部保持洁净，'
        '不叠加细碎噪点、脏污颗粒、胶片粗粒、纸面颗粒或斑驳做旧效果。')

# Limit replacements to rendering effects. Objects such as sand grains, a dirty coat or an
# antique book can be part of the story. The approved words in the title are never restyled.
REVIEW = '同次查看脸部、平坦背景和衣饰/器物轮廓，检查线条清晰、色面平滑；可见细碎噪点、脏污颗粒或成片斑点时，标出具体区域并定点返工。'

SURFACES = (
    (re.compile(r'(?:粗糙|斑驳|污损|做旧|陈旧)(?:的)?(?:纸本|纸页|纸面|羊皮纸|宣纸|纸张)(?:肌理|质感|纹理|底纹)?'
                r'|(?:粗糙|细腻|细碎|轻微|仿旧|复古)?(?:的)?(?:旧纸|纸面|纸张|纸质|羊皮纸|宣纸|纸本)(?:纤维颗粒|纤维纹理|颗粒感?|肌理|纹理|底纹|纸纹)'
                r'|(?:粗糙|细腻|细碎|细|做旧|复古|陈旧)纸纹(?:理)?|旧纸纹(?:理)?'), '平滑纸面'),
    (re.compile(r'(?:细腻|细微|粗糙|细小|浅淡|复古|细碎|轻微|脏污|斑驳|密集|粗粝|细密)?(?:的)?'
                r'(?:胶片(?:感)?(?:颗粒|噪点)|画面噪点|脏污颗粒|脏颗粒|做旧颗粒|颗粒噪点)(?:感|质感|纹理|效果|层)?'
                r'|脏污的颗粒|(?:细碎|密集|细密|粗粝|细小)(?:的)?(?:颗粒(?:感|纹理)|噪点)'
                r'|(?:斑驳|脏污)(?:的)?(?:做旧|纹理|底纹|肌理|质感)(?:效果)?'), '平滑色面'),
    (re.compile(r'\b(?:(?:subtle|fine|rough|coarse|heavy|visible|vintage|dirty|aged)\s+)*'
                r'(?:(?:film|paper|digital)[\s-]+grain(?:\s+(?:texture|overlay|effect))?|film\s+noise|'
                r'(?:grainy|grungy|grunge|distressed|weathered|rough|gritty)\s+(?:paper(?:\s+texture)?|parchment|textures?|overlays?|finish)|'
                r'(?:grit|dirt|noise|speckle)\s+overlay|dirty\s+(?:speckl(?:es|ing)|mottling)|fine\s+noise|grainy\s+finish)\b', re.I), 'smooth tonal shading'),
)
NEGATED = re.compile(r'(?:不(?:要|添加|使用|带|叠加|含|画|采用|出现|铺设|加)|禁止|避免|无|去除|消除|去掉|没有'
                     r'|\b(?:no|without|avoid|remove|exclude)\s+)[^，。；,;.\n]{0,14}$', re.I)
LITERAL = re.compile(r'((?:完整(?:本集|视频)?标题(?:为)?|专辑简称逐字为|第[一二三123]行逐字[：:]?)\s*“[^\n]*”)')


def clean_text(text):
    parts = LITERAL.split(text)
    for i in range(0, len(parts), 2):
        segment = parts[i]
        for pattern, replacement in SURFACES:
            def replace(match):
                if NEGATED.search(match.string[:match.start()]):
                    return match.group()
                return replacement
            segment = pattern.sub(replace, segment)
        parts[i] = re.sub(r'(平滑纸面|平滑色面|smooth tonal shading)(?:[、，,]\s*\1)+', r'\1', segment)
    return ''.join(parts)


def finish_prompt(prompt):
    content = prompt.removesuffix('\n'+RULE)
    return clean_text(content).rstrip()+'\n'+RULE


def new_recipe(previous):
    """Reading or resuming a previously compiled recipe retains its paid request keys."""
    return previous.get('image_finish') == VERSION or not (
        previous.get('style_prefix') or previous.get('compiled_by') or previous.get('cover_policy'))


def finish_manifest(manifest, previous):
    if not new_recipe(previous):
        return
    for row in manifest['images']:
        row['prompt'] = finish_prompt(row['prompt'])
    manifest['image_finish'] = VERSION
