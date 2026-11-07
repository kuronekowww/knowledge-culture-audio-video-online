# 本地与线上草稿组装

两包维持相同视频模板、选段和字幕规则，以runtime_profile.json区分默认模式。代码不在失败后偷偷切换服务；`--mode`仅用于用户明确切换的场景。项目来源：[Hommy-master/capcut-mate](https://github.com/Hommy-master/capcut-mate)。本包不捆绑仓库、不克隆/更新/修改项目，也不自动安装其依赖。

## 默认命令

```bash
python scripts/flow.py resume --job-dir /job
python scripts/assemble_draft.py --job-dir /job
```

前序Agent待办按原技能完成，调用组装直到draft_verified。原声/字幕/图片/底图复用。末阶段名仍为publish_handoff：本地包直接进入ready_for_local_assembly，不调用upload_command；线上包上传后ready_for_capcut。flow摘要会识别当前版本已验收草稿。

## Codex本地版

默认local_api_base=http://127.0.0.1:30000，先复用该端口现有兼容服务。若未启动，从当前工作目录和job祖先目录寻找capcut-mate（或明确配置capcut.local_project_dir），用项目.venv解释器启动本包local_capcut_service.py。接口必须支持add_sticker等完整操作；不调用线上地址补做。

可选config.capcut字段：local_api_base（只能loopback）、local_project_dir、local_python、draft_root。不会把旧任务的远端api_base当本地地址。用户已有服务不停止；本次启动的子进程在完成/异常后退出。中断后若服务缓存丢失，已有部分步骤不盲目重放，先核实服务状态，或用户明确使用--new-build创建独立新草稿。

素材只从当前upload_request.json清单提供，先核对字节哈希，以127.0.0.1:30002的临时只读路由传给本机服务；不提供目录浏览或任意文件路径。端口占用时明确报错；更改runtime_profile.local_asset_port会改变请求版本，需评估是否创建新草稿。本地路由不用公开上传，组装结束即关闭。

草稿下载后做完整结构核验，复制到新目录并本地化媒体路径。优先使用config.capcut.draft_root；macOS未指定且标准剪映草稿根目录实际存在时自动导入。其他环境根目录未发现，则交付draft_runs/.../portable目录，可补配根目录后导入。已有目标不覆盖；不直接打开剪映，不自动导出MP4。`--no-install`仅输出任务内便携草稿。

## 线上版

config.capcut.api_base必须是用户明确配置的远程服务，可为origin或包含/openapi/capcut-mate/v1的地址。不启动本机项目，不把本地路径当URL；使用既有upload_command/平台回填的uploaded_assets.json并经handoff验证。下载URL必须与配置服务同origin、不重定向且属于返回draft_id；CDN部署与本契约不一致时报告差异，不能猜改URL。

线上版用同一完整api_sequence，下载JSON和媒体后核验，并输出任务内portable草稿；若明确配置draft_root则导入该目标。技能目录可只读；运行文件写job及共享目录。.env仅用户自行填写与读取，不随交接上传。

## 保存、核验与恢复

每一步先记submitting及请求哈希，成功后保存done与真实响应。恢复复用done；submitting或请求变化即停止，保留服务端草稿与回执，避免重复素材。网络失败不自动重试写入。`--new-build`显式建立独立attempt，旧草稿与回执保留。

组装→保存下载→jianying_profile.finalize绑定字体与坐标→draft_verify→便携路径本地化→再次finalize与核验，全部通过才标记draft_verified。校验报告绑定唯一配置、源素材和字体哈希；剪映桌面实际播放仍需单独复核。用户只要草稿时不导出MP4。

总表点选与最新图片节奏见[master-catalog.md](master-catalog.md)：按声音ID取对应音频/封面，10分钟80～100张正文图、约7秒切换。草稿交付名为专辑名_声音ID_视频标题，不能用随机draft_id代替业务名；创建时仅传服务真实支持的名称字段，当前无此字段的服务仍在下载后的草稿元数据写完整名并明确记录服务端差异。
