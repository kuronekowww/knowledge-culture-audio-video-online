# 线上运行检查与恢复

## 无专辑封面

未提供封面不是品牌关闭指令。默认branding_enabled=true；已确认文字画风时可在album_visuals配置：

```json
{"reference_mode":"style_only","background_prompt":"按用户已确认文字画风生成无字竖版背景，纹理向内延伸且中央留白","background_revision":1,"background_size":"1024x1536"}
```

不填reference_image，不造占位封面、不声称用户附件损坏。保留品牌文字、贴片、单贴纸与片尾，仅略过专辑小封面；背景仍每专辑一次生成。只有缺少真实画风选择才询问用户。旧任务若branding_enabled=false，先核对是否确为用户选择；不能从示例或缺封面推导关闭。不要自动改写旧缓存来源或清空收费任务。

## 文字轨道

有品牌时独立文字层包含title_line1、title_line2、album及原声字幕，来源信息由source_label透明图片提供；无品牌时仍需title_line1、title_line2及原声字幕。模型不再额外添加尾CTA/AI示意文字。caption_api与assembly_api在发请求前拒绝缺失/重复角色；保存后核对真实草稿，HTTP成功不算验收。

旧handoff_plan只含caption_api、没有api_sequence，不能让下游只挑字幕或图片拼装。使用当前prepare/publish，上传清单素材且回填真实URL，再运行内置assemble_draft.py。必须执行至save_draft/get_draft/结构核验；awaiting_upload只代表素材完成，不能宣称草稿已创建或视频完成。

## 上传与配置

生图、ASR、XQL凭据可用技能根目录.env或进程环境，环境优先；只报告是否存在，不回显值。通过平台上传时逐项处理upload_request.assets的实际文件，不只上传tar.gz/JSON作为交付。平台真实返回的素材URL回填uploaded_assets.json的asset_id/sha256/url/expires_at；按实际域名配置精确asset_allowed_hosts。当前契约要求HTTPS、无重定向、真实完整字节；仅已验证的URL可发布。内置线上组装只使用配置的capcut.api_base，不换成本机或猜测公开地址。缺少上传能力时保留awaiting_upload，指出具体缺项。

## 字幕反复失败

优先基于当前subtitle_review_request的candidate_id提交accept/patches，保持实际完整语义审阅；不将“全accept”当自动通过。覆盖/顺序/未知unit_ids错误会写subtitle_coverage_diagnostic.json，包括缺漏、重复、未知ID和第一处不符。仅修对应候选，再apply；不反复重写数千unit_ids或使用过期C编号。识别纠错仍必须reason和证据，不放松全量覆盖、语义、时码及内容检查。

直接运行命令并检查exit_code；需要短日志时先重定向到文件再读取，或启用pipefail。不能因最后一个tail成功就当审核通过。视觉审核目标目录由review-request提前创建，记录只在实际查看后写入。

同声音回站证据不得超过源音频真实长度；其他声音也须核对真实内容，不用“连续长篇应该有”替代来源。若未取得实际草稿JSON，只能确认交接与调用链问题，不能断言服务端丢轨或已修复旧草稿。
