# 同事上手

把分发包解压，保留名为 knowledge-culture-audio-video-online 的完整目录，用 WorkBuddy 或 Codex 的本地技能入口加载。任务和素材放在可写工作区。同事首次开工可直接说：

> 用这个工具处理我给的声音 ID，沿用喜马拉雅竖版布局。先把每条的封面标题、片内两行标题、可用倍速和对应成片正文时长列在一起给我确认。做完交可编辑剪映草稿和素材。

## 第一次配置

在可写工作区执行 `python3 /技能实际路径/scripts/runtime_doctor.py`。如在技能目录中运行，请先将 KNOWLEDGE_SHARED_DIR 设置为固定的可写缓存目录。已有合格环境直接复用。若提示当前环境缺少 Python 包，可执行 `python3 scripts/runtime_doctor.py --install-missing`。纯图片草稿使用 Python 3.12 或以上、requests、Pillow、ffmpeg 和 ffprobe；剪映组装另需本机 CapCut Mate 服务或已安装的项目。网页才检查共享的 Hyperframes、Node 和浏览器；MP4 音频核验才需 numpy。具体环境、凭据和机器目录设置见[运行环境](server-runtime.md)。收到的工程使用[接收机导入指令](draft-assembly.md)，不要从制作机复制字体路径或改字号。

两台 Mac 的系统版本也可能带有不同的苹方文件。上一步会检查江西拙楷 Regular 与苹方简体 Medium 的实际版本。若有缺项，按[本机字体配置](server-runtime.md)一次指向匹配字体；新机只做草稿迁移也需要这两种原字形。

首次配置后，后续命令里的 python 指上一步选定的解释器，scripts 指技能下的脚本目录。运行工作区可与技能目录分开。各集沿用同一个共享缓存目录。每人单独配置凭据，发布包只含空模板。已经在制作的旧任务保留原版及其工作目录，新的团队任务采用当前版。

## 一批标题和倍速，一次答复

默认正文 1 倍。整批可用 selection.config.speed=1.2（或 1.4），逐条用 segments[].speed 覆盖。原声选段至少 5、6、7 分钟，才分别可用 1、1.2、1.4 倍；预检会列出可用档位，最终实测的正文仍须至少 300 秒。片尾原速，字幕与分镜跟随最终音频。

每条 selection.segments[] 可直接填 titles，含 episode_title、cover_title_lines（封面一行或两行）、title_line1、title_line2；也可给出简称、左/右/上方位置和一行重点色（见[封面规范](cover-generation.md)），封面标题排一至两行，整体居于中下部，1080×1920 成品的标题组中心约 y=1203px。专辑简称可留空，或用 cover_label_placement 选 left/right/top；cover_accent_line 可选要突出的一行。另有原声依据 content.evidence。本批同专辑多声音从总表取材时，顶层 catalog 指向导入好的标准总表，每条使用自己的 track_id、source_audio 和可选 user_transcript_path。跨专辑按专辑建立 selection。整集/选段必填项见[工作文件](workflow.md)。

```sh
python scripts/flow.py select --input selection.json --manifest batch.json
```

脚本先核对候选和标题；缺标题为 awaiting_agent_title，执行者在相应 job 的 copy.json 填写。awaiting_user_title 返回真实的封面分行、片内两行、原声依据和 title_version。给用户看文字即可。用户可以答“第 1、3 条通过，第 2 条第二行改为……”。只对真实通过的版本执行下面的登记，VERSION1、VERSION3 用刚才返回的值替换。已逐字批准的同一组文字可直接登记。

```sh
python scripts/title_review.py --manifest batch.json --accept VERSION1 VERSION3 --evidence "用户的实际答复"
python scripts/flow.py batch --manifest batch.json --only K01 K03
```

修改一条时更新该条 copy.json，后续补写专辑信息时保留已定的四项标题；若变动了选段需同步 selection 并重新 select，避免用过期的 selection 覆盖最新标题。用 `python scripts/title_review.py --manifest batch.json` 取当前待办。用户若给出完整的最终改字，可按其原话直接登记该版本；仍由执行者新拟的文字须先展示。选段、原声证据、完整标题或分行变动会使旧批准失效。未改的条目可继续。请勿用示例答复或自动脚本代签。

## 字幕从哪里来

平常只给声音 ID 即可。工具先比对这条声音的 AI 文稿和剪辑后的原声：能对上的短句沿用时码，局部长段或开头缺口按原声补齐。整集以长段为主时，只做一次整轨识别并复用。常规结果由脚本推进，执行者一次核对，确实听不清且会影响含义的地方集中向用户确认。已有带时间的原始 JSON 时，每条声音可在 selection.segments[] 填自己的 ai_doc_path。文稿工具导出的纯文本可作校对依据；复用时间轴需要含 startTime、endTime、text 的原始数据。字幕核对通过后，任务目录内的 subtitles.srt 可直接导入剪映。[字幕来源与核对](subtitle-review.md)

## 执行者只跟待办

- 每次只处理摘要点名的任务、文件与问题。状态查询用 `python scripts/flow.py status --manifest batch.json`。等待答复时结束本轮。更新字幕、分镜或审图结果后，再推进对应 ID。
- 纯图片分镜仅写一份，每镜的描述、信息点和来源由人或内容模型完成，编译器生成正式图片清单，统一清晰线条、平滑画面和干净暗部；封面和已有的三镜抽看覆盖颗粒问题。[字段与示例](team-visuals.md)
- 在一个批次内处理 5～10 条，默认 2 个声音同时处理，共享 3 个图像提交和 3 个下载名额。先沿用默认配额，观察实际请求时长与失败情况。[批量命令与分段耗时](performance.md)
- 所有就绪的默认剪映项目用 `python scripts/flow.py assemble-ready --manifest batch.json` 依次组装。草稿发给同事时，发送整个草稿目录含 assets，不单发 JSON。对方把草稿导入最终位置，首次用同一时间点看标题、字幕、专辑行。[跨 Mac 导入](draft-assembly.md)
