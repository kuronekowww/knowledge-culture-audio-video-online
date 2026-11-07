# 专辑封面、声音竖封面与全专辑共用底图

## 确认风格与参考语义

用户可提供专辑封面；没有封面但已有确认的文字画风时也可继续。先视觉读取，提炼表现方式、配色、材质、装饰和时代氛围；复用已有确认档案。用户明确要求按该封面调性制作，已构成本次风格授权，在creative_options.style_confirmation记录实际指令即可，不再机械三选一。没有明确方向才提供三种候选。不同专辑根据各自参考，不把本次蓝金红商史风格强套到所有专辑。

参考封面的书名、作者名、人物和物件不是本集内容事实。只借调性，声音封面的主体依据本集原声；例如明代人物不照搬参考封面的清代官帽。当前既有接口是文字生图，不能宣称原图已作为参考附件上传；先读图再把视觉特征写入提示词。未来接口有明确参考图契约再接入，不猜字段。

## 配置

首次在selection.config.album_visuals写以下对象；相对reference_image以job为基准，推荐绝对路径。共享目录持久化，同用户同专辑必须一致。首次会把参考原图按哈希复制到专辑共享目录；原聊天临时附件后来消失时，仍根据已绑定的原路径和哈希复用这份参考及底图，不重新要求上传或收费。

```json
{
 "album_visuals":{
  "reference_image":"/data/input/album-cover.png",
  "reference_summary":"根据用户实际封面提炼配色、材质、时代及装饰，不抄示例",
  "background_prompt":"按本专辑风格写完整无字氛围图提示词，边缘纹理向中间自然延伸，中央保持安静留白",
  "background_revision":1,
  "background_size":"1024x1536"
 }
}
```

没有封面但用户已确认文字画风时，使用`reference_mode: "style_only"`，不填reference_image，background_prompt基于该文字风格；来源记录明确为文字画风，不声称图片传输损坏，不编造占位图。真实封面路径已配置但文件丢失时需恢复或明确移除路径切换模式，不静默忽略。

style_only下专辑底图仍一次生成并跨声音共用，reference_path/reference_sha256为null；品牌PNG、专辑名、来源、上方标题、字幕、播放贴纸和片尾仍按品牌开关保留，仅略过缺少真实素材的专辑小封面。已有同专辑真实封面缓存时可直接复用。没有封面也没有已确认风格才询问风格，不把模型生成图或底图当作用户封面。

reference_image仅用于真实参考的视觉分析/来源记录，不由当前/generate接口自动上传。

## 共用底图只生成一次

album_visuals.ensure_background在<shared_root>/albums/<album_id>/visual_assets/.background.lock内执行，锁覆盖实际生成与下载。不同声音/批次/并发任务共享background.json和同一原图；缓存键只含album_id、reference_sha256（style_only为null）、style_hash、background_revision，绝不包含track_id或episode_title。

同一版本首次完整提示词写入versions/<key>/image_manifest.json；后续Agent改写同版本background_prompt也复用首份，不无故收费重生。缺文件时按同一manifest与缓存/下载回执恢复，不设置retry-generation；未知提交保留原状态，不能自动重发。

底图竖向low，无任何文字、汉字、数字、书名、作者、题字、logo、水印、人物、单集情节、播放键、UI、边框或视频占位框。画风跟专辑参考一致，装饰不要仅局限于最外窄边：左右约四分之一宽度、顶部和底部可有稀疏低对比纹理，向内柔和消散；中央约半宽与中段横视频区域保持安静干净，标题及专辑信息区域便于后期排字。不要密集重复花纹或强亮金粉。

只在用户明确要求修改背景时递增background_revision，并填写regeneration_confirmation={"confirmed_by":"user","evidence":"实际修改指令"}。旧版本原图与history记录保留，旧job明确固定原reference/画风/revision仍可复用。换本集标题、人物或品牌开关不新建背景版本。

## 每个声音一张带字竖封面

### 专辑封面与标题设计档案

`flow select`创建本集job时先查`<shared_root>/albums/<album_id>/visual_assets/cover_design.json`，有记录即复制为`job/cover_design_reference.json`。执行者写标题、封面提示词前先读该档案，同时复用`visual_style.json`和已核验的`background.json`。档案保存**可复用的逻辑**：主角/核心书名的呈现、配色材质、构图层级、标题语气、当前画布及封面安全区（数值只读全局layout-profile.json）；每集的人物关系、拜帖等道具和标题内容仍依据当集原声更换。参考专辑封面只借视觉线索，不复制原图的人脸和构图。

无档案的首集，执行者写`job/cover_design.json`，字段`design.core_label`、`core_subject`、`visual_tone`、`composition`、`title_logic`、`safe_crop`均须填写本专辑实际内容。封面逐字核对通过后，流程将它连同首张封面哈希和共用底图版本保存到共享`cover_design.json`。同专辑后续先读并沿用；用户明确要求改版时保留旧版本和修改依据，不静默覆盖。专辑名相同但album_id不同不共用档案。

标题面向视频号30–50岁观众：开头点出他们认识的人物或事件，接着用口语化的具体动作和疑问制造悬念，并由本集选段确实回答。不要用“看朝局”“看人也看局势”等抽象词代替问题。封面主角占主要画面，顶部/右侧放较小专辑简称，下方用两行或三行、至少双色把**完整**本集标题写清；即便上方已写人名，也不从下方标题省略该人名。视频内两行可以精简，但必须指向同一个故事问题。例：武则天专辑上方“武则天”，下方“武则天的父亲请袁天罡看相，他为啥不敢去？”；此例只适用于该专辑该声音。

封面以1080×1920满幅生成和使用。安全区上13%（250px）、下25%（480px）、左右各5%（54px），关键内容完整放在x=54～1026、y=250～1440内；背景可满屏。构图是在画中调整主体和文字，不把整张图片缩成卡片。专辑核心主体与两至三行标题的比例参考[最新用户比例图](../assets/cover-proportion-reference.png)；专辑简称改为顶部或右侧的次级识别，不再用巨大字体压过主像。标题可叠在衣服区域，避开人脸，至少双色突出重点。[用户PSD合成图](../assets/cover-layout-reference.png)仅作辅助。完整生成与复核契约见[封面规范](cover-generation.md)，当前layout-profile.json始终覆盖旧档案坐标、字体层级与同色标题习惯。

job的image_manifest包含cover_portrait与各SH镜头，**不含album_background**。cover_portrait用竖向尺寸，SH镜头用横向尺寸，quality均low。封面独立设计，不拿第一张剧情图顶替；上方可见主体，中下方标题，布局按内容调整。无播放键、左下角小字、额外文案或平台UI。

copy.episode_title是完整原文；copy.cover_title_lines为两行或三行，拼接后必须逐字等于episode_title（含问号等标点）。creative_options.bind_prompts把所选画风与核心标识、缓存设计及完整标题/分行注入封面提示词，重复resume不会重复添加；正文提示词不注入标题。写封面prompt时不能同时要求“无文字”，允许专辑核心标识和完整视频标题，除此之外不加其他文字。

生成后执行者查看封面并逐字核对标题。标题错漏仅修该封面，不能因此重生共用底图。文字已嵌入图，首秒封面和组件显示策略读取layout-profile.json，所有独立文字、小封面与贴纸从1秒后开始；上方标题按统一配置测宽并保持字号，原声与后续字幕时码不偏移。

## 一次底图检查与每集标题检查

脚本技术校验解码、方向、哈希与来源，不能代替识字/审图。review_images待办由当前执行者处理，**不是再次请求用户批准**；不要求逐张正文图复审。运行`python scripts/album_visuals.py review-request --job-dir /job`生成visual_review_request.json，包含真实文件路径、expected_title、cover_basis和背景review目标。实际看图后才填写：

- job/cover_review.json：basis原样复制cover_basis，title_exact=true，reviewer与具体evidence；layout记录当前安全区profile、canvas=[1080,1920]、full_bleed=true、core_label/core_label_exact/core_subject_present及subject_bounds_px/title_bounds_px/core_label_bounds_px，完整边界均在安全区；另按[封面规范](cover-generation.md)记录当前cover_policy绑定、逐行标题边界/主色、简称位置/单字高度和主体无遮挡检查。
- 共享visual_assets/reviews/<background.key>.json：sha256原样复制背景哈希，no_text=true、center_clear=true，reviewer与具体evidence。

图片、标题或背景版本改变旧记录失效；只变单集标题不使背景检查失效。同一背景真实哈希未变不重复检查。不能伪造OCR或把技术检查称为用户批准。

## 竖版装配

正式画布1080×1920。封面已经是满幅成品，组装直接复制，不缩小、不套框、不用共用底图填边。旧2:3封面需单独修正构图，不能仅通过缩小整图过关。正文横图按画布同宽等比缩放并居中，背后独立使用已缓存的专辑底图。

封面请求明确size=1080x1920、quality=low，不能将1024×1536当作9:16。若具体接口不支持该尺寸，应明确其返回/错误，使用支持的图片工作流完成1080×1920满幅成品并如实记录来源；不能虚构成功或偷偷改回旧尺寸。模型输出偏差需在组装前技术适配与目视检查，不能冒称原生返回尺寸。背景的历史1024×1536缓存可复用适配到1080×1920，不因封面改版重生成底图。

档案除design核心字段外，建议保存typography、PSD参考资产与哈希、palette、source_label_theme、示例封面路径/哈希、background_key、适用安全区profile。首次生成并核对后记录；同专辑后续生成前查阅，复用风格与底图，仅按本集改标题/辅助场景。用户明确改版时先将旧档案写入history，再递增version并记录修改依据，不清空既有素材。

## 呈现资产与全局模板

用户在工程中更换真实专辑小封面时，将原文件按哈希保存到visual_assets/presentation，并在presentation-assets.json记录album_id、base_reference_sha256和album_cover.path/sha256。此记录只覆盖该专辑的呈现封面，不更改原有底图/生图参考，不保存坐标、字号或字体。job/presentation-assets.json为明确的任务资产绑定；无任务绑定时读取共享记录。源封面已换版而绑定不符时停止，不擅自沿用旧封面。所有几何始终读取全局layout-profile.json。

历史cover_design.json中的typography、safe_crop、palette仅作历史设计参考，不能覆盖统一视频模板。新专辑标题色在每次组装时按当前呈现封面与固定算法重算，记录在timeline.album_color_analysis。
