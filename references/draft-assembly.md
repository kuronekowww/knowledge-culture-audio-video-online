# 本地与线上草稿组装

两包维持相同视频模板、选段和字幕规则，以runtime_profile.json区分默认模式。代码不在失败后偷偷切换服务；`--mode`仅用于用户明确切换的场景。项目来源：[Hommy-master/capcut-mate](https://github.com/Hommy-master/capcut-mate)。本包不捆绑仓库、不克隆/更新/修改项目，也不自动安装其依赖。

## 默认命令

```bash
python scripts/flow.py resume --job-dir /job
python scripts/assemble_draft.py --job-dir /job
```

前序Agent待办按原技能完成，调用组装直到draft_verified。原声/字幕/图片/底图复用。末阶段名仍为publish_handoff：本地包直接进入ready_for_local_assembly，不调用upload_command；线上包上传后ready_for_capcut。flow摘要会识别当前版本已验收草稿。

## Codex本地版

默认local_api_base=http://127.0.0.1:30000，先复用该端口现有兼容服务。若未启动，从当前工作目录和job祖先目录寻找capcut-mate（或明确配置capcut.local_project_dir），用项目.venv解释器启动本包local_capcut_service.py。接口须支持带透明通道的PNG图片及完整音画组装；不调用线上地址补做。

可选config.capcut字段：local_api_base（只能loopback）、local_project_dir、local_python、draft_root。不会把旧任务的远端api_base当本地地址。用户已有服务不停止；本次启动的子进程在完成/异常后退出。中断后若服务缓存丢失，已有部分步骤不盲目重放，先核实服务状态，或用户明确使用--new-build创建独立新草稿。

素材只从当前upload_request.json清单提供，先核对字节哈希，以127.0.0.1:30002的临时只读路由传给本机服务；不提供目录浏览或任意文件路径。端口占用时明确报错；更改runtime_profile.local_asset_port会改变请求版本，需评估是否创建新草稿。本地路由不用公开上传，组装结束即关闭。

草稿下载后绑定原生模板参数，再复制到交付位置，重绑全部媒体和字体，最后对交付草稿做一次完整核验。优先使用config.capcut.draft_root；macOS未指定且标准剪映草稿根目录实际存在时自动导入。其他环境根目录未发现，则交付draft_runs/.../portable目录，可补配根目录后导入。已有目标不覆盖；此草稿接口本身没有MP4导出能力。默认只交核验过的剪映草稿；只有用户明确要求MP4时才实际导出并核验，或使用已配置的OpenFilm配套渲染。`--no-install`仅输出任务内便携草稿。

## 线上版

config.capcut.api_base必须是用户明确配置的远程服务，可为origin或包含/openapi/capcut-mate/v1的地址。不启动本机项目，不把本地路径当URL；使用既有upload_command/平台回填的uploaded_assets.json并经handoff验证。下载URL必须与配置服务同origin、不重定向且属于返回draft_id；CDN部署与本契约不一致时报告差异，不能猜改URL。

线上版用同一完整api_sequence，下载JSON和媒体后核验，并输出任务内portable草稿；若明确配置draft_root则导入该目标。技能目录可只读；运行文件写job及共享目录。.env仅用户自行填写与读取，不随交接上传。

## 保存、核验与恢复

每一步先记submitting及请求哈希，成功后保存done与真实响应。恢复复用done；submitting或请求变化即停止，保留服务端草稿与回执，避免重复素材。网络失败不自动重试写入。`--new-build`显式建立独立attempt，旧草稿与回执保留。

组装→保存下载→绑定字体与坐标→便携导入→对最终目录运行一次完整核验，通过后标记draft_verified。校验报告绑定唯一配置、源素材和字体哈希；剪映桌面实际播放仍需单独复核。默认不导出MP4；用户明确要求MP4时才渲染。

总表点选与最新图片节奏见[master-catalog.md](master-catalog.md)：按声音ID取对应音频/封面，10分钟80～100张正文图、约7秒切换。草稿交付名为专辑名_声音ID_视频标题，不能用随机draft_id代替业务名；创建时仅传服务真实支持的名称字段，当前无此字段的服务仍在下载后的草稿元数据写完整名并明确记录服务端差异。

从剪映原生导出后运行 `python scripts/verify_native_export.py --job-dir /job --mp4 /export/成片.mp4`。检查当前草稿版本、成片解码和逐声道对齐，并从成片抽取联系表。执行者查看关键图与全部联系表，在JSON记录output_sha256、approved、reviewer、observation、reviewed_sheets（路径和哈希来自本次报告），用同命令加 `--review /job/native-visual-review.json`。只有真实成片和所看版本仍一致，flow才报告render_verified。

## 把已做好的一条交给同事

同事在接收电脑解压完整草稿，在剪映里查看本机草稿保存目录，然后在团队技能目录运行一次。脚本使用系统 Python 标准库，不需要安装剪映服务或图像模型。

```text
python scripts/portable_prepare.py "收到的完整草稿文件夹" "接收机剪映草稿根目录" --name "专辑名_声音ID_视频标题"
```

命令在接收端建立新副本。原生工程的两个 JSON 都会重绑材料字体、富文本各样式的字体、图片、视频、音频和本机元数据。新工程使用团队原校准字体。接收端先运行 `python scripts/team_fonts.py` 校验江西拙楷 Regular 和苹方简 Medium；字体文件由接收端本地提供。两个版本的工程都按原字体哈希匹配。缺失或不匹配时报告具体字体，保留原稿。已有同名目标可另选名称。若再次移动已导入的文件夹，按最终位置重新导入。

首次使用某个“剪映版本＋团队模板”组合，可在原生剪映里看一次最长两行标题和双行字幕。若用户反馈异常，先保留草稿，运行 `python scripts/portable_validate.py "接收端最终草稿目录" --json`。工程数值通过后仍有差异时，记录双方实际客户端版本、同一时间点画面及接收端保存的两份 JSON，区分打开时重新排版和历史模板差异。

2026-11-10 在同一台 Mac 的不同目录重现，历史《武则天》第三集含 160 项媒体、470 段文字。旧导入器显示通过却仍有三条文字及其富文本样式指向发送端目录；新版将这些引用改到接收目录。比对轨道、坐标、字号、字距、缩放、换行、时码和原文均一致。归档另有实际字号不同的模板，第二集初版标题／字幕／专辑名是 20／14／15，第三集是 13／12／11，团队版按最新校准值 13／12／10。跨设备原生剪映的像素对比仍需在使用的客户端完成。

已经出现错位时，可用 `python scripts/compare_draft_text.py "发送前原稿" "接收端保存后的同一草稿"` 比较一次。它按原生文字段 ID 对照画布、真实字体文件、字号、富文本样式、位置、宽度、字距、缩放和换行，列出最早五处不同的时间点。请在能访问两套实际字体的电脑上比较两份工程；若原稿引用的字体不可访问，会显示字体差异。若段 ID 已被剪映重建，则显示新增/缺失，不能据此判断像素。仅在排查已报告的问题时调用。
