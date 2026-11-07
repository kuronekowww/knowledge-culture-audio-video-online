# 工作文件、时码与精简调度

本页图片镜头与数量规则适用于纯图片路线；新任务先完成[制作方式选择](presentation-modes.md)，网页动画分镜/插图按[网页路线](web-animation.md)执行。

## 整批选段

selection顶层使用segments；内部为兼容固定身份仍使用highlight_id。下面仅为结构示意：专辑、声音、文稿、用户选择、域名和文件路径均需替换为真实内容，不得当作已经发生的确认或证据。

```json
{
  "album_id": "123456",
  "config": {
    "shared_root": "/data/knowledge-shared",
    "csv": "/data/input/tracks.csv",
    "asset_allowed_hosts": [
      "your-real-media-host.example"
    ],
    "creative_options": {
      "branding_enabled": true,
      "album_tone": "根据真实专辑填写",
      "style_candidates": [
        {
          "id": "USER",
          "name": "用户指定画风",
          "reason": "实际用户要求",
          "prompt": "二维编辑插画，低饱和蓝灰与暖橙点色，柔和散射光，细纸纹理"
        }
      ],
      "style_id": "USER",
      "style_confirmation": {
        "confirmed_by": "user",
        "evidence": "实际用户选择记录，不得照抄示例"
      }
    },
    "album_visuals": {
      "reference_image": "/data/input/album-cover.png",
      "reference_summary": "填写实际用户封面的视觉分析",
      "background_prompt": "本专辑风格的无字竖版底图，边角纹理向内自然延伸，中央干净",
      "background_revision": 1,
      "background_size": "1024x1536"
    }
  },
  "segments": [
    {
      "highlight_id": "K01",
      "track_id": "10001",
      "summary": "一个可独立理解的完整价值单元",
      "clips": [
        {
          "start": 120,
          "end": 480
        }
      ],
      "content": {
        "problem": "本条解决的具体问题",
        "demand_reason": "目标人群在何种处境需要这个答案",
        "delivered_value": "观众听完能理解或做到什么",
        "conclusion": "原声在本段实际给出的阶段结论",
        "complete_reason": "说明背景、论述、案例和结论为何足以独立成立",
        "opening_hook": "原声开头的问题或有意义的现象",
        "evidence": [
          {
            "start": 200,
            "end": 260,
            "text": "替换为所选范围内真实文稿及结论证据"
          }
        ],
        "return_hook": {
          "question": "本条之外真实存在的进阶问题",
          "track_id": "10001",
          "start": 500,
          "end": 580,
          "evidence": "替换为已核对的后续文稿，先检查声音目录和时长"
        }
      }
    }
  ]
}
```

clips和content.evidence/return_hook用源音频秒，可用HH:MM:SS小数格式；每条一份track_id，clips按源顺序且不重叠。跨声音分成独立任务。每条clips合计至少300秒；大于600秒需content.extended_reason，多段需content.splice_reason。不存在speed配置时保持1.0，显式非1会拒绝。结构验证不能确认语义正确或来源真实，执行者必须核对。

selection.config不写album_id，放顶层。config的相对csv路径以各job为基准，推荐绝对路径；shared_root必须稳定且持久。首次提供creative_options真实确认；同专辑后续可省略，让脚本从专辑级档案恢复。密钥只用环境变量。

```bash
python scripts/flow.py select --input /batch/selection.json --manifest /batch/batch.json
python scripts/flow.py batch --manifest /batch/batch.json
```

脚本创建jobs/<album>-<highlight_id>，固定ID与声音/摘要，不按排序换号。改成不同内容须新ID；修改切点保留该条身份并重跑受影响阶段。没有固定8条要求。

## 待办

| stage | Agent/平台做什么 | 脚本继续执行 |
|---|---|---|
| analyze_highlights | 按完整价值写cut_plan与content | 注册稳定身份 |
| cut_speed | 根据cut_review_request核对句首尾和语义边界，写真实证据 | 原速剪切拼接；保留源到成片的映射 |
| subtitles | 通读真实unit与上下文，核对术语、人名、数字、引文、分句 | 校验全部unit覆盖、依据和真实时码，输出字幕 |
| storyboard | 一次写visual_bible、storyboard、copy、image_manifest与album_visuals配置 | 绑定时码与专辑画风，专辑一次共用底图＋每集竖封面/横正文low生图、下载、技术校验与标题检查 |
| publish_handoff | 按清单上传并回填；已配置上传适配器则自动 | 字节核验、仅竖版交接与完整请求序列 |

awaiting_agent不是向用户提问；awaiting_user用于缺失画风等真实选择；awaiting_platform用于上传。用户要求的额外审核按其要求执行，不伪造审批。具体失败只读对应日志/文件，不重复全量检查。

## 分镜与文案

storyboard.shots的start/end使用**最终音频秒**，从0连续覆盖全音频，镜头ID唯一并与image_manifest对应。每镜补充source_unit_ids、beat、画面描述、人物/器物/场景和依据。按内容节点调整数量，无7～9张硬上限。

```json
{"shots":[{"id":"SH001","start":0,"end":24,"source_unit_ids":["真实unit ID"],"beat":"解释本镜的一个信息点"}]}
```

上例只是单镜结构，不是完整360秒分镜。必须补齐所有真实镜头到最终音频末尾。

copy.json包含album_name、album_core_label、episode_title、cover_title_lines、title_line1、title_line2。先读本job的cover_design_reference.json（若有），沿用同专辑标题语气与封面构图逻辑；首次封面另写cover_design.json以便核对后入共享档案。完整标题episode_title与cover_title_lines拼接必须逐字相同；封面可两行或三行，不因上方核心人物字样而省略完整标题。正文标题按layout-profile.json指定字体实测宽度，允许自然问号，过长精简文案而不减字号；详见branding-layout.md。旧任务已有cta/notice字段允许保留但完全忽略，不要求补写、不审核这些未显示字段，也不生成末尾钩子或AI提示文字。

0～1秒仅封面及品牌PNG，没有独立文字。所有正文文字、小专辑封面及贴纸从1秒开始；音频从0播放。字幕保留原始CSV/词级时码，只在交接展示层隐藏首秒：首秒内结束的cue不显示，跨1秒的cue从1秒显示到原end，后续cue原样，不加全局1秒偏移。

image_manifest含quality=low、独立cover_portrait（1080×1920满幅）及所有SH镜头（横向1536×1024等）。封面9:16，示例正文图3:2。共享album_background由album_visuals独立生成并缓存，不能列进逐集image_manifest；正式素材以固定专辑背景适配1080×1920，不裁掉知识要点。

```json
{"output_dir":"images/generated","quality":"low","images":[
 {"id":"cover_portrait","filename":"cover_portrait.png","size":"1080x1920","prompt":"1080×1920满幅；专辑核心主体在中间，较小专辑简称在顶部或右侧，完整本集标题在下方按语义排两行或三行、至少双色，允许叠在人物衣服区域但不挡脸，全部关键内容在x54–1026/y250–1440内"},
 {"id":"SH001","filename":"SH001.png","size":"1536x1024","prompt":"已定稿的本镜完整描述"}
]}
```

所有真实镜头都要列入；独立封面占正文首1秒，SH001对应画面从1秒继续，其余时码不偏移。不为封面另外插入1秒静音。恢复由脚本绑定当前版本；音频改变时重新核对受影响分镜，不仅改哈希冒充语义复核。

共用底图、每集标题检查与示例字段详见[专辑视觉资产](album-visuals.md)。cover_title_lines示例：["武则天的父亲","请袁天罡看相，","他为啥不敢去？"]，episode_title为同一内容的完整单行；这是布局示例，不是当前专辑默认标题。

总表优先：用户提供总表后先持久导入，再按声音ID点选，不重新查询全专辑清单。selection.config保留master_catalog行快照与已下载的album_visuals参考，不能拿另一声音的地址套用。10分钟按80～100张正文图、约7秒一镜规划，其他时长同比例；草稿名为专辑名_声音ID_视频标题。完整流程见[master-catalog.md](master-catalog.md)。
