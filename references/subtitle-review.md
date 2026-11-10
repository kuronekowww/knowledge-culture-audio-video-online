# 基于原声的知识文化字幕核对

默认按声音 ID 获取并缓存带毫秒时码的 AI 文稿。若已有原始接口 JSON，可在选段清单的每条声音上指定 `segments[].ai_doc_path`。保留 `startTime`、`endTime` 和 `text`；已合并的 TXT 或 Markdown 可作核对文字。全篇语义核对仍用 full 模式。

## 文稿如何跟上原声

- 默认 `subtitle_source=auto`。同一最终原声已有匹配的完整词稿时直接复用。对短句文稿，脚本一次抽取每段剪辑前、中、后的实际语音，与文稿短语比对，换算剪点、倍速和经证实的固定偏移。仅在时间对应可靠且句长适合上屏的范围直接使用句级时码。
- 如果全篇以长段为主，且选段中完整长段已占至少 80% 时长，直接获取整轨词稿一次。校准、断句和缺口判断都使用同一份结果。
- 对以短句为主的文稿，遇到太长的段落、被剪断的句子、异常重叠、明显缺口和指定的逐词动画区间，改用最终原声的词级时码。匹配的文稿短语可以恢复标点；不确定的词保留给核对环节。缺文稿、时间对应不可靠或局部无法可靠拼接时，回到整轨识别。只想使用整轨词稿时可设 `subtitle_source=asr`。
- 网页动画默认给前 15 秒准备词级时码。其他逐词区间可设 `subtitle_word_ranges`，例如 `[[32,38]]` 表示最终正文音轨的第 32 至 38 秒。普通句级字幕可整句显示，不能从句内平均估算每个词的位置。
- 来源报告 `caption_source_report.json` 记录本次采用文稿、混合、完整词稿缓存或整轨识别。混合路线可查看各剪辑的偏移、直用句以及补对齐范围。局部识别遇到收费请求状态未定时，保留回执，先恢复原任务。

## 流程

flow在subtitles待办时已生成subtitle_review_request.json；也可用`python scripts/run.py subtitles --job-dir /job`。读取当前request的完整units、context、suggested_cues、document_differences、词典与matched_terms。核对人名、术语、数字/单位、年代、否定、引文、因果和观点归属；只有真实完成检查才写checks=true。历史词表按quality-rules的contextual/strict解释，不为了消除命中而伪造字幕。

文稿未覆盖、识别也没有词的短暂停顿由脚本处理。若 timing_source.wordless_observations 列出达到一条字幕时长（4 秒）的无词区间，集中抽听这些范围。确认没有漏话时，在响应 gap_checks 逐项填写 start_us、end_us、no_missing_speech=true 和具体 observation；听到漏话须先补齐对应时码。

一次通读后，用 `"accept":"remaining"` 接受其余已确认字幕，只在 patches 填写修改项。已有适配器也可继续提供逐条 accept 编号。确有听辨分歧时，将时间点、原话候选和影响集中在一次询问中，收到答复只改相应字幕。大型请求可在独立工作文件中按语义段落分批阅读/编写，但最终响应必须带**同一request_hash**，按原序完整覆盖全部候选，由脚本统一校验；不能把整条长视频只检查首段。

每条字幕只引用真实unit_ids，不填写start/end。发布文字单行，保留句中必要标点，去除末尾标点；1～3秒为常见目标，最多4秒、22字、14字/秒为可读性检查阈值，不能因此均分时间。术语、人名和数量单位保持完整。边界缺失时重新获得可靠对齐，不按字数造时码。

## 差异响应

```json
{
 "request_hash":"复制当前真实请求哈希",
 "reviewer":"实际执行者",
 "checks":{"proper_names":true,"semantics":true,"content_risk":true,"timing_boundaries":true,"simplified_chinese":true,"segmentation":true},
 "protected_phrases":["本段实际术语"],
 "accept":"remaining",
 "patches":[{
  "candidate_ids":["本次实际需修正的候选ID"],
  "cues":[{
   "unit_ids":["当前请求中实际连续unit ID"],
   "corrected_text":"忠实于原声且有依据校正后的文字",
   "text":"相同内容的发布字幕","action":"keep",
   "reason":"具体修改理由","evidence":"具体原文位置或实际听音依据",
   "rule_ids":[],"needs_listen":false,"boundary_reason":"完整语义在此结束",
   "alignment_review":{"mode":"same_span","evidence":"实际等长同音纠正依据，首尾锚点保持不变"}
  }]
 }]
}
```

示例不是已完成的审核，ID、内容、理由必须换为真实结果。不得同时提交cues和accept/patches。patch可合并或拆分连续候选，但其units必须全部覆盖、保持顺序、无重复；也支持用完整cues响应，字段相同。

仅允许action=keep，text与corrected_text保持同样内容。ASR纠错与改写事实不同：corrected_text改变识别时需reason/evidence，等长局部同音可用alignment_review.mode=same_span；实际核对过局部音频/可靠对齐才能用audio_checked并记录具体区间；证据不足标uncertain/needs_listen，进入队列。不能声称做过未执行的复听。原声自身明显错误时换选段或报告，禁止rewrite/omit掩盖。

每条patch字幕填写boundary_reason。不足0.5秒、孤立单字、跨明显停顿需给具体short_utterance_reason/pause_reason或调整到真实unit边界，规则由subtitle_segmentation校验。

写完 subtitle_review_response.json 后直接执行一次 `python scripts/flow.py resume --job-dir /job`；批量任务用 `flow.py batch --manifest batch.json --only K01 K03`。同一次运行会套用响应、核对单位覆盖、输出字幕并推进下一阶段。仅单独诊断字幕时才使用 run.py subtitles --apply。needs_review 时先处理 subtitle_repair_queue.json；确实无法判断的听音疑点再交用户。

## 来源、缓存与自动适配器

final_transcript.json 与来源记录绑定最终音频；文稿路线同时绑定文稿、剪点、倍速、识别设置与逐词范围。输入变动时重新选择对应的时间轴。subtitle_history保留识别快照、精简响应、展开响应和变更证据；asr_corrected保留忠实校正版。字幕通过核对后写入同目录的 subtitles.csv 与 subtitles.srt；失败保留旧文件但不凭存在放行。相同上下文的已核对候选可复用，仍需确认本次语境，缓存不等于本条全部审核。

可配置subtitle_review_command为argv数组，接受`--request PATH --output PATH`；长视频合理提高subtitle_review_timeout（如900秒），不得以shell字符串执行。未配置时由当前Agent完成语义核对，不要求额外购买服务。ASR轮询超时保留既有任务，resume继续查询，不因等待超时重发。
