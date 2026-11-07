# 基于原声的知识文化字幕核对

默认full模式，百炼paraformer-v2识别**最终剪辑音频**取得真实词级unit。旧本地识别后端仅兼容解析，不默认安装模型。文稿粗时轴用于定位、语义和标点参考，不能按字数分配时码或直接替换实际原声。

## 流程

flow在subtitles待办时已生成subtitle_review_request.json；也可用`python scripts/run.py subtitles --job-dir /job`。读取当前request的完整units、context、suggested_cues、document_differences、词典与matched_terms。核对人名、术语、数字/单位、年代、否定、引文、因果和观点归属；只有真实完成检查才写checks=true。历史词表按quality-rules的contextual/strict解释，不为了消除命中而伪造字幕。

对无问题候选返回accept编号；仅问题项写patches。每个候选必须恰好出现一次，不能省略即默认通过。大型请求可在独立工作文件中按语义段落分批阅读/编写，但最终响应必须带**同一request_hash**，按原序完整覆盖全部候选，由脚本统一校验；不能把整条长视频只检查首段。

每条字幕只引用真实unit_ids，不填写start/end。发布文字单行，保留句中必要标点，去除末尾标点；1～3秒为常见目标，最多4秒、22字、14字/秒为可读性检查阈值，不能因此均分时间。术语、人名和数量单位保持完整。边界缺失时重新获得可靠对齐，不按字数造时码。

## 差异响应

```json
{
 "request_hash":"复制当前真实请求哈希",
 "reviewer":"实际执行者",
 "checks":{"proper_names":true,"semantics":true,"content_risk":true,"timing_boundaries":true,"simplified_chinese":true,"segmentation":true},
 "protected_phrases":["本段实际术语"],
 "accept":["当前请求中实际已检查的candidate_id"],
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

写subtitle_review_response.json后执行`python scripts/run.py subtitles --job-dir /job --apply`。passed再resume。needs_review优先解决subtitle_repair_queue.json，只有确实无法判断的听音疑点交用户；不直接改最终CSV或伪造checks放行。

## 来源、缓存与自动适配器

final_transcript.json与meta绑定最终音频哈希、后端、参数和词典；输入改变旧时码失效。subtitle_history保留识别快照、精简响应、展开响应和变更证据；asr_corrected保留忠实校正版。字幕发布CSV仅在校验通过后生成；失败保留旧文件但不凭存在放行。相同上下文的已核对候选可复用，仍需确认本次语境，缓存不等于本条全部审核。

可配置subtitle_review_command为argv数组，接受`--request PATH --output PATH`；长视频合理提高subtitle_review_timeout（如900秒），不得以shell字符串执行。未配置时由当前Agent完成语义核对，不要求额外购买服务。ASR轮询超时保留既有任务，resume继续查询，不因等待超时重发。
