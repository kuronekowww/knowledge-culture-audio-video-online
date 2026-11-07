# 百炼接口与知识文化模式

本技能使用百炼paraformer-v2识别最终剪辑原声音频，默认热词关闭。字幕一律full核对，按[字幕契约](subtitle-review.md)处理术语、人名、数字、年代和引文；不能沿用原小说版keywords_only。ASR任务提交、轮询、下载与来源凭据由脚本负责，超时保留原任务，不重发未知收费请求。

凭据从DASHSCOPE_API_KEY环境变量或技能根目录.env读取，已有环境变量优先；只报告是否存在，不输出明文。configure_bailian.py用于迁移任务识别设置，不写密钥或环境文件；密钥不放进交接或发布包。asr.backend=bailian_paraformer；默认model=paraformer-v2，hotwords默认关闭，开启前由bailian_hotwords校验并确认实际支持。

配置asr.timeout_seconds默认600、有效1～900；http_timeout_seconds有效1～120。适用于长选段的轮询等待，超时恢复同一任务。音频上传受实际服务策略限制，以真实错误报告，不压短知识单元蒙混。ASR返回由units_from_asr规范成词级unit，保留原始结果和音频/模型/参数哈希；没有可靠词级边界不得按字数估算时码。

知识内容纠错见subtitle-review.md。热词用于改善识别，不替代原声、原文或术语核对；模型/词典参数变化会使相关ASR与字幕版本失效。
