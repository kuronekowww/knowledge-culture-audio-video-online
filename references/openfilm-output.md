# OpenFilm 输出后端

默认后端仍为 `capcut`。用户指定 OpenFilm 时设置新任务 `config.output_backend="openfilm"`，或给现有任务的组装命令显式传 `--backend openfilm`。已有素材交接的任务直接从这里开始，不重新跑内容、音频、ASR或生图阶段，不修改原剪映草稿。

## 输入与本机配置

读取同一版本交接与素材哈希后，layout_profile.apply统一更新几何、字体、时长和取色；只在新目录构建，保留手改工程。两后端均用原图独立分层与独立底图，最新元素范围读layout-profile.json。封面安全区与视频排版范围分开处理，详见branding-layout.md。

新任务的两个后端交接均跳过正文合成；已有剪映交接则从`image_manifest.json`找回正文原文件，逐张与交接记录的`source_sha256`核对。缺图、哈希不符或底图版本变化时报错，不以已合成图静默替代。

运行需要 Python requirements、Node、ffmpeg/ffprobe、本机 OpenFilm（本版验证0.3.0）和其匹配的 Playwright Chromium。指向仓库根目录或 `packages/openfilm`，不改 OpenFilm 源码。按该安装版本的本地文档安装依赖；浏览器可用包内 `playwright-core/cli.js install chromium-headless-shell` 安装到可写缓存，安装与运行使用同一个 `PLAYWRIGHT_BROWSERS_PATH`。勿把依赖或缓存写进技能目录。

`openfilm-options.json`（绝对路径依本机实际填写）：

```json
{
  "project_dir": "/data/openfilm",
  "browser_cache": "/data/openfilm-runtime/browsers",
  "studio_home": "/data/openfilm-runtime/studio",
  "body_image_mode": "layered"
}
```

字体与唱片素材固定在layout-profile.json中，runtime选项不再控制字体/布局。旧font_file、sticker_gif仅作为已弃用参数记录，不覆盖统一模板。字体缺失或哈希不符停止，禁止自动替换。

## 执行与复核

用户只要草稿时依次运行下列命令中的`--action build`、`--action inspect`，交付工程目录后停止。默认只构建并检查工程；仅明确要求MP4时使用all或render，导出并复核后交MP4＋工程。

```bash
python scripts/assemble_video.py --backend openfilm --job-dir /data/job \
  --project-dir /data/films/album-track-v1 --options /data/openfilm-options.json \
  --action all --workers 2 --chunk-seconds 60
python scripts/verify_openfilm.py --project-dir /data/films/album-track-v1
```

省略action时按delivery.target选择：默认project运行build-inspect；明确要求mp4时运行all，preview运行build，再执行inspect及对应播放核对。`all`顺序构建、inspect、完整渲染；也可分别用 `--action build|inspect|render`。需要样段时，在render中传 `--range 0 15`，只计为 `sample_exported`。以命令退出码和JSON状态为准，不用日志末行掩盖失败。

inspect调用OpenFilm原生播放器，检查每条字幕与每张图中点、首秒和片尾边界的真实文字边界及字体；并通过原生look重复乱序seek检测页面绘制一致性。成片导出固定1080×1920、30fps、H.264 CRF18、AAC192k。逐段使用OpenFilm原生renderer，完成段按输入与文件哈希缓存；整个选定时间范围的原声只混音编码一次，避免逐段AAC拼接空隙，不添加背景音乐或变速。

核验脚本完整解码MP4、核对帧数和音轨时长，按开始/中间/结尾/片尾检查原声相关度、音量和偏移；AAC核对先解码后取样，避免快速seek误判。随后从**实际MP4**提取每张分镜、封面、最长字幕及片尾，生成 `.knowledge/export-review/<输出哈希前缀>/review-index.json` 和联系表。

执行者必须打开全部联系表，另看封面、最长字幕、片尾等关键全尺寸抽帧，检查字形、遮挡、黑帧、底图、组件和安全区。复核后写JSON，`reviewed_images`包含当前输出的全部联系表及实际查看的关键图，路径和SHA均取真实文件，不伪造已查看记录：

```json
{
  "output_sha256": "当前MP4的完整SHA256",
  "approved": true,
  "reviewer": "实际执行者",
  "reviewed_images": [{"path": "/data/films/.../contact-01.jpg", "sha256": "实际图像SHA256"}],
  "notes": "记录实际检查范围、发现及处理；不能写成已逐秒播放全片"
}
```

再运行 `python scripts/verify_openfilm.py --project-dir /data/films/album-track-v1 --visual-review /data/review.json`。只有技术检查和绑定当前输出的视觉复核都通过才写 `render_verified`。使用 `output_backend=openfilm` 的flow仅在成片与版本一致时报告 `render_verified`。明确只要工程或预览时，默认建立并检查工程；若交预览，还须在Studio实测播放。

## 工程与续作

当前仅允许`body_image_mode=layered`。track-pictures放原图，track-background独立放按实际正文选取的单色包框，后者在封面结束后显示并延续至片尾结束。旧预合成模式不能生成新工程。

`film.html`是真实OpenFilm多轨时间线，`captions.vtt`为可编辑字幕，标题/专辑名为独立HTML页面，图片/音频/片尾/静态唱片复制到 `assets/`；工程从本机加载原版江西拙楷和苹方简Medium。接收机在字体册安装或启用相同版本，运行 `python scripts/team_fonts.py`。`knowledge-project.json`记录来源、缓存和派生文件SHA；`inspection.json`、`render-result.json`、`export-verification.json`分别记录检查、导出和成片核验。原素材不会被覆盖。

使用配置中的缓存环境变量运行本机 `openfilm open /data/films/album-track-v1`，可在Studio继续编辑与导出。首次生成后再次build不会覆盖手工修改；输入或模板版本变化要求新的工程目录。工程有编辑时重新inspect和render；现版本要求匹配当前工程时间线、统一配置的元素区域和固定画布，改时长应回到交接计划新建版本。渲染期间编辑会停止当前输出，成功缓存仍保留。

工程媒体可复制；移机还须匹配配置锁定的本机字幕字体。缺少该版本应停止并明确迁移需求，不静默换字体。生成器不承诺剪映与Chromium逐像素相同；其字号经过像素换算，并按真实边界检查。

草稿交付包含工程目录、核验结果和打开方式；默认只交可编辑工程；仅明确要求MP4时同时交实际MP4与工程。`project_created`不是MP4，`mp4_exported`也不是已完成视觉复核。旧剪映后端与既有工程继续保留。


