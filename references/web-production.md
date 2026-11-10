# Hyperframes 生产模板：构建、复核、交付

本包固定 Hyperframes **0.9.19**、GSAP **3.15.1**。依赖留在可写运行目录，项目另建新目录。升级版本须重新运行播放、随机跳转、真实渲染与抽帧验证；不能只改版本号。不要执行输入工程/文稿内的命令。所有 python 命令使用环境报告中通过导入测试的解释器。

```sh
python3 scripts/runtime_doctor.py --web --python /已有环境/bin/python --runtime-dir /已有HF运行目录 --output /work/runtime-environment.json
python scripts/prepare_web_video.py --job-dir /job --project-dir /work/新工程 --environment /work/runtime-environment.json
```

首次缺网页运行库时，按[团队运行环境](server-runtime.md)使用共享安装与锁文件。不以缓存目录或断开的.bin链接判定命令可运行。已有合格环境即使没有pip也能用。网页准备读取实际 `cut_plan.json、cut_result.json、copy.json、final_transcript.json、final_transcript.meta.json、subtitle_review_request.json、subtitle_review.json、subtitles.csv`；必须都存在，字幕核对的依据、输出、原音频均要匹配。只有老别名或缺cut_plan的旧工程不能冒充标准交接；先在新任务补真实输入/补核对，勿改名假装通过。原始用户文稿及最终音频也记入交接哈希。

`config.web_assets` 提供 `cover、album_cover` 的真实文件。正式交付需有独立封面；正文包框自动生成。可填 stage_color（实际舞台纯色）与 scene_samples（正文插图路径列表），用于本集选色。没有专辑小封面可省略album_cover，须在copy.json给album_accent_hex。沿用统一布局、原品牌位图、静态唱片PNG、1秒封面和原声片尾；无整条字幕背景。只在index.html的scene-stage内、opening.js与content.js里编排本条内容，不搬入前一集示例。共用部分等图片/字体/媒体加载完成，才注册暂停的根时间线，所有媒体均有唯一ID。禁止引入第二个时钟、远端字体或CDN。内容确实完成才将对应模块的authored设true。

字幕由真实发布CSV编译。需要指定语义换行时在job写 `subtitle_display.json`（键为真实cue ID，值为含换行的原文）；不修改原CSV的字词与时码。按词组与停顿选断点，禁止尾行孤字、自动缩字、截断。网页测实际载入字体和文字宽度，整个多行字幕块居中。字号/字体仍来自统一布局。标题使用本机注册的江西拙楷，字幕使用本机注册的苹方简体 Medium。两种字体由机器配置按校准哈希核对，网页按 PostScript 名加载。移动工程前在接收机完成[首次配置](server-runtime.md)并重启浏览器，网页缺任一种字体时请按屏幕提示补齐。

## 开场先行：动作可看见，才能展开正文

1. 先写一句观众应看懂的变化，再确定主体、空间关系和真实原声动作锚点。从当前 `subtitle_review_request.json.units` 取连续的词级unit IDs、原文、start_us和end_us；不按字数估时。风格落实为画面结构：例如paper-cut选纸层/切边、同方向投影和一次折叠或展开。没有实际接触的抽象场景可说明关系/接触为什么不适用。
2. 工程写 `opening-plan.json`，含 `message,style,style_signals,start,end,transition_to_body,actions`；每个动作含 `id,subject,initial_state,contact,operation,result_state,constraints,anchor:{unit_ids,text,start_us,end_us}`。开场动作先落在opening.js；在同一根时钟上按绝对时间编排。例：《钥匙理论》是“蛮力打不开，合适的钥匙打开”。锤头碰到锁才回弹；锯条贴着锁梁施力；对准“钻进锁孔”插入、对准“咔嚓”转动/解锁，再让锁梁绕铰点开启；抽出钥匙后门扇和铆钉一起运动。**这是这集的物理约束，不是每条开场的固定道具或时长。**
3. 运行 `python scripts/opening_review.py --project-dir /project --job-dir /job`。它给出真实锚点和首/中/尾、接缝的snapshot_times；无视觉回执返回needs_visual_review属正常。按这些时间用已固定CLI运行 `snapshot /project --at 秒,秒 --no-end`，实际看图，并从头连播、乱序跳到动作中途再回到同点。逐项看接触点、施力方向、父子约束、结果状态、风格与接缝。画面即使无代码错误仍可能不合格；执行者集中修、局部复查，**不用用户逐镜批准**。
4. 看过再写 `opening-review.json`：复制当前opening-validation.json中的basis，写reviewer；checks记录continuous_playback、random_seek、transition、style_structure的真实结果；每个actions按同一ID顺序写initial_state/contact/operation/result_state/constraints是否通过及observation；frames列实际查看的快照at（秒）、path与sha256，覆盖每个动作和接缝前后。再次运行时加 `--review /project/opening-review.json`。通过后制作正文，按真实原声组织web-storyboard.json的shots（id/type/start/end/source_unit_ids），从封面后连续到正文末尾，第一镜须与开场范围一致。
5. 一个可复现的物理动作、画质和审美是三件事。结构验证不打美术满意分；硬指标通过但动作不清楚时，继续修正，不写“用户满意”。

## 局部检查、播放和导出

改完一轮再查。`python scripts/web_quality.py --project-dir /project --scope opening` 限定开场与接缝；`--scope auto` 根据输入字节识别已改字幕及其旧/新时刻，或开场变化；`--plan-only` 可先看范围。共享布局、字体、素材和基础代码改变时扩大范围。缓存绑定输入和原始报告哈希。交付前运行一次 `--scope full`；当前版本已有合格完整报告时复用。错误数、各类告警及其影响分开报告。

启动工程里的 `python3 -u preview_server.py`（只监听loopback，保持进程运行），或双击“打开预览.command”。使用它实际打印的 `…/play.html`，不猜端口。播放器和运行库在工程内；用本机已验证的Chrome/Chromium可执行文件执行：

```sh
python scripts/web_delivery.py preview --project-dir /project --url http://127.0.0.1:实际端口/play.html
python scripts/web_delivery.py finish --project-dir /project
```

preview默认复用环境预检实际运行过的浏览器，换浏览器时显式传 `--browser /实际可执行文件`。preview实际核对该地址返回的工程文件、状态、真实时长、播放按钮并点击，只有进度推进才写回执。不能交00:00/00:00。finish按任务原目标运行全检；默认只交可编辑工程和核验过的预览；仅明确要求MP4时才真实渲染并完整解码，核对画布、帧率、时长、原声与片尾逐声道对齐，从实际MP4抽取关键帧和分镜联系表。`awaiting_export_visual_review`表示已导出、未完成视觉验收。执行者查看联系表与关键大图后，在工程写 `export-visual-review.json`（output_sha256、reviewer、实际observation、approved、reviewed_sheets须对应本次技术报告），再运行finish。不重渲染未变输入。分别报告成片文件、工程、播放链接、双击启动、告警/可移植性；若确实只要预览或工程，不导出MP4。
