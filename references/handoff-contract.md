# 仅竖版的素材交接

## 上传

handoff prepare生成upload_request.json、handoff_plan.json与handoff_media。平台只上传清单中素材：最终音频、已适配竖版的图片以及品牌开启时的派生贴片/片尾。没有横版时间线，不上传整轨、文稿、配置、密钥或未使用图片。原生封面/底图是竖图，正文镜头是横图，适配图片是1080×1920。

uploaded_assets.json格式：

```json
{"source_version":"从当前upload_request复制","assets":[
 {"asset_id":"清单中的真实ID","sha256":"清单中的真实哈希","url":"https://真实允许域名/对应对象","expires_at":null}
]}
```

需包含全部清单ID，不能只照抄示例一项。只接受实际HTTPS、asset_allowed_hosts精确域名、无重定向；expires_at为Unix秒，长期有效显式null，限时URL至少保留1小时。不能转码后冒用旧哈希。平台配置upload_command为argv数组时可自动回填，追加`--request PATH --output PATH`；包内没有供应商上传实现，凭据由环境提供。

恢复flow batch/resume后远程GET核对完整字节与大小，再发布capcut_handoff.json及限时回执。没有真实URL时状态为待上传，不以本地路径或猜测链接冒充就绪。

## 交接结构

schema=knowledge-culture-handoff/v1，consumer_skill=jianchuang-jianying-assistant，time_unit=microseconds。identity保留album_id/track_id/highlight_id/summary；value_unit保存已交付价值和真实回站来源；creative_selection保存已选画风。

**timelines恰好一条orientation=portrait，canvas=1080×1920。** frames为带标题独立竖封面适配图0～1秒，其后每镜连续覆盖正文；audio从0播放原声音频；subtitles保留最终音频来源时码并隐藏首秒显示；texts仅含按统一配置测宽的上方标题及可选专辑名，均从1秒开始。来源是branding_elements中的透明图片，没有来源文字轨道。没有CTA/AI示意标记。封面不增加总长。无品牌总长=正文，有品牌总长=正文＋当前绑定片尾的完整时长。

品牌元素使用原素材和box_px独立定位；正文、底图、小封面与来源图不预先叠成新图。片尾使用绑定素材的完整时长和原声音轨。所有时序与层级详见branding-layout.md。

每条timeline.api_sequence.steps是已编译的完整顺序请求：创建→分组图片→音频→可选品牌→分组文字→保存。下游逐步执行，只补最新真实draft_url，不能平均分镜、重排正文、重复加caption_api或截正文作片尾。分组值来自spec.api_limits，实际部署限制见caption-api。

## 保存、核验和状态

交接就绪不等于草稿完成。剪映后端保存下载后运行draft_verify.py，只有passed可称草稿已验收，剪映HTTP接口不包含MP4导出协议。OpenFilm后端直接读取同一份本地计划与素材清单，按[输出说明](openfilm-output.md)构建、原生渲染并核验。只有真实导出的视频才称MP4完成；服务不可用时保留就绪素材和真实回执，指出阻塞，不假称成片。

交接版本绑定配置、音频、字幕、分镜、图片及代码/模板；变更仅重跑受影响内容。新版本保存旧指针历史；上传URL更新只重发布，不重新生图。当前回执最长1小时，恢复时由handoff validate检查；过期刷新URL/重新校验。不能只看旧JSON的ready_for_capcut字段。

批次delivery每条内容期望1份竖版草稿。素材数、交接数、核验草稿数和导出视频数分别统计，不计成横竖两份。

正文frames直接引用原始横图，含box_px与原图尺寸；album_background独立资产和轨道，延续至片尾结束。切换后端不重生底图或正文图。

branding_elements含一张静态唱片PNG与可选真实小封面、来源透明图；位置与缩放从唯一配置生成。无真实封面只略过小封面，不能套用其他专辑封面。

首秒严格只显示封面和品牌PNG；小专辑封面与静态唱片从1秒显示到正文音频末。presentation_policy记录这组要求；旧交接在恢复时需重新prepare/publish，复用既有音频、字幕来源、正文图片，不重新生图。旧copy.cta/notice不会显示。

本地版不走公开上传：upload_request作为本机素材清单，内置临时loopback媒体路由向本机服务提供文件。assembly_handoff.json记录本次实际请求，draft_result.json记录模式、保存ID、安装目录及核验报告；完整流程见[draft-assembly.md](draft-assembly.md)。
