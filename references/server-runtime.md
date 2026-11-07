# 线上运行环境与依赖

本目录作为独立技能部署。剪映后端内置HTTP组装与保存核验，实际剪映导出仍依赖目标环境；已有jianchuang-jianying-assistant可逐项执行同一请求，但不要重复组装。用户指定OpenFilm时使用[独立输出后端](openfilm-output.md)，复用本地素材并调用本机OpenFilm渲染MP4，无需先生成剪映草稿。

Python3.12+，requirements.txt中的requests/Pillow/numpy（numpy用于OpenFilm音频核验），PATH中的ffmpeg/ffprobe。OpenFilm另外需要本机Node、OpenFilm与Chromium，详见[OpenFilm输出](openfilm-output.md)。无需桌面剪映、GPU或本地ASR模型。首次配置示例：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
export KNOWLEDGE_JOBS_DIR=/data/knowledge-jobs
export KNOWLEDGE_SHARED_DIR=/data/knowledge-shared
export KNOWLEDGE_PYTHON=/absolute/path/to/.venv/bin/python
```

本技能根目录提供`.env`空白配置文件，填写`GPT_IMAGE_2_TOKEN=你的token`即可，另外可填`DASHSCOPE_API_KEY`、`XIMALAYA_XQL_SIGNATURE`。flow/run/config及独立生图、百炼和目录脚本会自动读取这三个凭据；无需source或新依赖。已有进程环境变量优先，空白值不注入；支持整值单/双引号，不执行shell命令、变量替换或插值。填好后重新运行命令生效。

`.env`是用户私有配置，不参与release_manifest.files哈希、交接或缓存指纹；安装/更新仅在文件缺失时创建，保留已填内容。打包只能放空白模板，不能把用户填好的文件带入ZIP；本地文件权限600，并由.gitignore忽略。技能只读取该文件，不把token回显到日志、文稿或输出JSON。

沿用原包实际环境变量XIMALAYA_XQL_SIGNATURE（声音目录）、DASHSCOPE_API_KEY（百炼）、GPT_IMAGE_2_TOKEN（生图），具体ASR凭据配置见bailian-asr.md；配置密钥不输出明文。服务地址由实际平台提供，不能因附件出现某地址就切换线上服务。未联网核实价格、额度或服务政策，不把历史文档当作当前保证。

config可配置csv、mosn_base_url、shared_root、asset_allowed_hosts、upload_command、asr、subtitle_review_command。取专辑声音清单流程见xql-audio-source.md；已有本地声音可通过cut_plan.source_audio指定，仍需真实选段与价值证据。shared_root必须持久化且同一用户同专辑跨任务相同，才能复用画风与缓存。默认新命名空间不读旧小说任务。

技能目录可只读，运行产物只写配置的任务/共享目录。每租户隔离缓存、凭据和上传域名；文件锁适用于共同文件系统，不是跨容器任务队列。默认2个job、3个生图提交和3个下载并发；长视频图片多且有费用，按用户范围/预算控制，不自动8条全专辑铺开。部署恢复和检查见performance.md。

本地验证不调用真实收费服务，不证明目标环境网络、账户权限、模型额度、上传、剪映契约或渲染可用。上线按真实返回定位，不能通过猜URL或假回执补齐。

专辑视觉配置见album-visuals.md：用户提供封面参考，声音竖封面带标题；共享底图在专辑锁内只生成一次，并保存来源、版本和无字检查。无新增依赖，不需要Pillow以外的合成库。输入参考图当前只供Agent视觉分析，/generate未传未经文档支持的图像字段。

新版品牌模板使用原生播放贴纸与来源透明PNG。下游服务需支持add_sticker和贴纸资源7017820454807276814，以及带alpha的PNG add_images；来源不再依赖唐瑜体或文字轨道，也不依赖GIF视频导入。前序不自动安装或修改远端服务，缺失时保留交接并报告差异。贴纸请求参数和保存核验见branding-layout.md。

组装版本由runtime_profile.json决定；本地版自动发现/复用capcut-mate与其虚拟环境，线上版读取配置远程地址。本地启动只监听127.0.0.1，结束后关闭本次启动的服务，不停止用户原有服务。配置与命令见[draft-assembly.md](draft-assembly.md)。

总表输入支持CSV/TSV与简单值XLSX，无新增Python依赖；统一media_base用于音频/封面相对路径。索引须落在当前用户持久共享目录，默认KNOWLEDGE_SHARED_DIR/master-catalog.json。总表入口只处理选中声音，不需要XQL签名，最终字幕ASR仍需对应凭据。详见[master-catalog.md](master-catalog.md)。
