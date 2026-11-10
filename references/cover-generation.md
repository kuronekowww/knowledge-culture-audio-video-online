# 视频号封面

把本集的主要人物、事件、物品、概念示意或书籍置于中央，中下部是读者第一眼要读到的完整标题。图像生成前先把一至两行封面标题和片内两行标题给用户看；等当前文字、分行与选段获准，再提交本集封面请求。多个声音一次展示。标题改动后重新登记该条批准。

## 参照与构图

[9:16 案例缩览](../assets/cover-case-reference.jpg) 中的人物关系占据画面中心，小简称置右，下方两行标题用红、浅金区分。[3:4 PSD 合成图](../assets/cover-layout-reference.png) 中人物居中，左侧放小字，下方同色的艺术字压在衣服上。新封面按当集素材选择视觉主体和字形，两种参考都允许标题压在图上，关键的脸、书封图案、物品结构与概念关系应清晰。默认生图服务以文字说明接收这套构图。

- 最终交付满幅 1080×1920。中央 3:4 是 x=0～1080、y=240～1680。完整标题（含花饰）、主要对象的识别区域、关键动作及道具和可选简称都收入此区；包括冠饰、头发、耳坠和前景衣着轮廓都不得借出画规避检查；必要时缩小主体，只有无识别要点的背景延展，沿用模板的左右留白。
- 原生生图请求 1024×1536。保留原图全宽，等比放大到 1080×1620，放在最终画布 y=150～1770。上下各 150px 由原图边缘背景延展，两侧简称和标题末字保留。原图顶部、底部都留纯背景。中央 3:4 在原生图中向内取整为 x=0～1024、y=86～1450；加上左右留白，主体和完整字形收在 x=52～972 内。编译器自动向内换算每个区域，审图使用最终图坐标。
- 封面主标题按整张竖图高度上移约 15%，在 1080×1920 成品中约 288px。参照前次样例，整组字形中心以 y=1203px 为目标（原生 1024×1536 图约 y=998px），边界留在 y=772～1362。先给标题组留出服饰、桌面或背景的位置，核对中心焦点与文字分离。
- 封面标题可排一行或两行，按语义换行，拼接与用户批准的文字逐字相同。可用易辨的花体、艺术字或普通标题字。允许同色，也可用一行重点色。片内两行标题继续使用团队校准字体。
- 短名仅在 copy.album_core_label 有值时显示；可选左、右或上方。该项缺省、为空或明确设置位置 none 时省略。正文品牌署名与专辑全名独立使用。

可在 selection.segments[].titles 里随标题填写以下可选键，也可在本条 copy.json 填写。

| 字段 | 用法 |
|---|---|
| album_core_label | 实际要显示的专辑简称 |
| cover_label_placement | auto、left、right、top 或 none |
| cover_accent_line | 1 或 2 选择已存在的一行；0 表示同色；缺省随画风选择 |

## 纯图片与网页

纯图片路线由分镜阶段自动生成请求。网页路线在用户批准标题后填写 visual_bible.json 的 cover_scene 和 style_prompt，在准备网页工程前生成封面。

```sh
python scripts/team_visuals.py --job-dir JOB --web-cover
python scripts/generate_images.py --manifest JOB/web-cover-manifest.json
python scripts/team_visuals.py --job-dir JOB --review-web-cover
```

网页生成结果是 JOB/images/generated/cover_portrait.png。在 config.web_assets.cover 登记该图（相对于 JOB 或绝对路径）。已有合适的最终封面可直接运行最后一条命令，并用 --image 指向该文件。对照 web-cover-review-request.json 在同目录写 web-cover-review.json。已有旧封面可单独据实复用，新的标题、分行或图片字节须重新核对。

## 一次目视核对

打开原图与同一请求中的 crop_preview（从最终封面裁出的中央 3:4）。逐字读到标题末字，查看完整主体、道具和两侧简称；标题可以叠图，人脸和主物件的识别细节须清楚。将观察写进请求指定的回执。

- 回执包含请求的 basis、title_exact、reviewer、evidence 和 layout。纯图片仍须包含其同轮抽查的 body_sample。
- layout.cover_policy 取自请求。subject_bounds_px 记录主要人物或物件足以辨认的完整区域、关键动作与道具的外框；冠饰尖端、耳坠及前景轮廓不得略去，仅无识别要点的背景纹理可忽略，focus_point_px 写可定位的中心焦点，例如主人公眉心或物件核心。title_bounds_px 从上至下记录每一行完整字形边界。dominant_core_subject 和 face_or_core_detail_clear 由执行者实际观察。纯图片还需记录请求的 profile、canvas=[1080,1920]、full_bleed。
- 显示简称时记录 core_label、core_label_exact、core_label_placement、core_label_bounds_px 和 core_label_max_glyph_height_px。省略简称时对应字段可留空。
- 两行且明确指定颜色处理时记录 title_line_colors；指定重点色则补实际 accent_line。单行或自由选色无需填两种颜色。首次封面通过后保存专辑主体与视觉调性，后续新集采用当前构图。

## 参考分工与精致构图

用户给头像、案例与PSD时分别登记用途：头像锁定年龄、脸型、视线、姿态和装饰；案例借氛围、配色与字形层级；PSD先读合成图，借主体比例、留白和文字与人物的衔接。保持已批准标题，不抄参考中的人物或文案。参考图的标志若已有独立品牌层，不重复画入。

先缩小主体，给完整冠饰、耳坠和两行标题留空间；可在中央3:4内部再留一圈余量。优先克制的金红、自然肤色、清楚的衣纹和柔和空间层次，避免过大头像、塑料皮肤、刺眼金色、厚重噪点和拥挤装饰。审图必须同时看满幅和中央3:4裁图。

封面默认high，正文仍low；参考图必须实际传给支持图像参考的工具，不得只在提示词声称已参考。已有内置图像工具时可用它完成带图参考生成，再通过 `image_provenance.py import` 登记真实provider、model（未知填unknown）、来源哈希和证据。工具未返回quality档位时只记录请求意图，actual quality保持unknown，不冒称服务执行了high。代理拒绝high时保留失败回执，不默降low、不换后端重发。
