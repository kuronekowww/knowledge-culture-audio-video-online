# 同事首次配置与运行环境

把 ZIP 中完整的 `knowledge-culture-audio-video-online` 文件夹放进本机工具的技能目录。更新时保留原来的 `.env` 和任务目录。计时、环境收据和素材缓存写进固定的可写工作区，技能目录可以只读。从工作区运行下列命令，把 `技能目录` 替换为解压后的路径。Windows 可用 `py -3.12` 启动。

| 交付目标 | 首次配置 |
|---|---|
| 纯图片剪映草稿 | Python 3.12+、requests、Pillow、FFmpeg/FFprobe，加一套可用的本机 CapCut Mate。品牌素材随包，字体按下文在本机匹配。 |
| 网页动画工程 | 上述基础 Python/FFmpeg 环境，再加 Node 22.12+、npm、Chrome/Chromium 和下面的共享网页运行库。 |
| OpenFilm 工程 | 基础环境，加所选的本机 OpenFilm 与 Node，按[输出配置](openfilm-output.md)选择项目与浏览器。 |
| 任意后端导出 MP4 | 在对应命令加 `--mp4`，补齐原声对齐使用的 NumPy。 |

标题与专辑名使用原校准江西拙楷 1.000，字幕使用苹方简体 Medium。制作机与打开可编辑工程的同事都需有匹配校准字节的字体。网页还需将这两种字体安装到系统并重启浏览器，让浏览器能按 PostScript 名找到它们。发行包里只带校准信息。第一次运行 `runtime_doctor.py` 会集中列出字体缺项及版本不符项。已持有校准版本时，运行 `python3 "技能目录/scripts/team_fonts.py" --display "本机江西拙楷文件" --subtitle "本机苹方文件"` 保存本机路径，然后继续同一批。若操作系统或授权环境没有这个版本，使用具备这两款校准字体的制作机。

在一个长期使用的工作目录执行一次；跨工作区复用时给 `KNOWLEDGE_SHARED_DIR` 配置同一固定目录。

```sh
python3 "技能目录/scripts/runtime_doctor.py" --install-missing
```

脚本优先试当前机器已有的合格环境，包括上次记录、激活的虚拟环境和工作区里的 CapCut Mate 环境。可用 `--python /指定解释器` 单独指定。仅在缺包时才创建共享专用 Python 环境并调用 pip。省去 `--install-missing` 只检测。Windows 与 macOS 各自使用自己的解释器和缓存。后续 `flow.py` 从共享记录取已验证的解释器，独立组装或网页命令采用记录中的 `python.executable`。使用 `--output` 自定义记录路径时给网页准备命令传同一文件，并为 flow 设置 `KNOWLEDGE_PYTHON` 或使用默认共享目录。

首次选网页时改用 `--web --install-missing`。已配置的运行库可指定 `--runtime-dir`；自备浏览器可指定 `--browser`。新机上按随包 npm 锁文件一次安装 Hyperframes 0.9.19 和 GSAP 3.15.1，复用 npm 缓存及共享运行目录。FFmpeg、Node、Chrome 和 CapCut Mate 根据本机已有环境配置。本地草稿组装支持复用已运行的服务；尚无服务时，团队管理员可以先准备好一份兼容 CapCut Mate 的仓库和虚拟环境，在平台配置里指定 `capcut.local_project_dir`，见[剪映服务配置](draft-assembly.md)。网页和草稿选项互相独立。

同机器、同模式、工具路径及依赖文件未变时直接复用探测收据。更换解释器、浏览器、运行库或修复安装后可加 `--refresh`。错误保留准确缺项，本次输出里查看 `errors`。

首次配置业务凭据时把 `.env.example` 复制为 `.env`（保留已有文件），仅填写所需字段或用进程环境变量。总表已点选声音时可省 XQL 凭据。发行包不含 `.env`。维护者运行 `python3 scripts/package_team.py --output /发行目录/团队版.zip --version 本次版本号`，会刷新文件清单并核对包内每项字节。

沿用原包实际环境变量XIMALAYA_XQL_SIGNATURE（声音目录）、DASHSCOPE_API_KEY（百炼）、GPT_IMAGE_2_TOKEN（生图），具体ASR凭据配置见bailian-asr.md；配置密钥不输出明文。服务地址由实际平台提供，不能因附件出现某地址就切换线上服务。未联网核实价格、额度或服务政策，不把历史文档当作当前保证。

config可配置csv、mosn_base_url、shared_root、asset_allowed_hosts、upload_command、asr、subtitle_review_command。取专辑声音清单流程见xql-audio-source.md；已有本地声音可通过cut_plan.source_audio指定，仍需真实选段与价值证据。shared_root必须持久化且同一用户同专辑跨任务相同，才能复用画风与缓存。默认新命名空间不读旧小说任务。

技能目录可只读，运行产物只写配置的任务/共享目录。每租户隔离缓存、凭据和上传域名；文件锁适用于共同文件系统，不是跨容器任务队列。默认2个job、3个生图提交和3个下载并发；长视频图片多且有费用，按用户范围/预算控制，不自动8条全专辑铺开。部署恢复和检查见performance.md。

本地验证不调用真实收费服务，不证明目标环境网络、账户权限、模型额度、上传、剪映契约或渲染可用。上线按真实返回定位，不能通过猜URL或假回执补齐。

专辑视觉配置见album-visuals.md：用户提供封面参考，声音竖封面带标题；每条上下包框仅生成一次单色PNG，记录采样来源和颜色。无新增依赖，不需要Pillow以外的合成库。输入参考图当前只供Agent视觉分析，/generate未传未经文档支持的图像字段。

品牌唱片与来源标识均使用带透明通道的单帧 PNG，由 add_images 加入图层。

组装版本由runtime_profile.json决定；本地版自动发现/复用capcut-mate与其虚拟环境，线上版读取配置远程地址。本地启动只监听127.0.0.1，结束后关闭本次启动的服务，不停止用户原有服务。配置与命令见[draft-assembly.md](draft-assembly.md)。

总表输入支持CSV/TSV与简单值XLSX，无新增Python依赖；统一media_base用于音频/封面相对路径。索引须落在当前用户持久共享目录，默认KNOWLEDGE_SHARED_DIR/master-catalog.json。总表入口只处理选中声音，不需要XQL签名，最终字幕ASR仍需对应凭据。详见[master-catalog.md](master-catalog.md)。

网页预检也执行Chrome/Chromium的版本命令，并在所选运行目录实际解析浏览器控制依赖；首次可以指定 `--browser /实际可执行文件`。合格路径写入共享记录。建议将固定的网页运行库放在共享目录，勿将某一集的工程或临时缓存当作永久安装。
