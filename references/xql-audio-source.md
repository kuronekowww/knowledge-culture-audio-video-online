# XQL 专辑音频来源

默认提供专辑 ID 即可取得音频清单，不再要求用户提供带音频 URL 的表格。XQL 只负责声音 ID、名称和播放路径；AI 文稿仍走共享缓存/MOSN，后续用flow脚本推进；默认高光由Agent选定、图片自动技术校验，无用户审图。

## 配置与运行

把用户提供的 signature 注入运行进程环境变量 `XIMALAYA_XQL_SIGNATURE`。脚本不保存或回显其值，不能硬编码到 skill、job 配置或发行包。已有完整 job 快照或有效共享清单可离线复用；提交和续查远程任务需要此变量。

```bash
python3 scripts/run.py run --album-id 41152491 --job-id 41152491-01
python3 scripts/run.py resume --job-dir jobs/41152491-01
```

`--n`（默认100）只限制按专辑顺序拉取文稿的条数；完整音频清单保留。每条高光使用不同 job-id，同专辑相同共享根目录可复用一次查询和已下载音频。专辑 ID 必须是正整数，示例 `41152491` 不得写死成所有任务的专辑。

单独准备清单或下载选中声音：

```bash
python3 scripts/xql_catalog.py --album-id 41152491 --output /绝对路径/音频清单.csv
python3 scripts/download_audio.py --album-id 41152491 --track-ids 330217947 --output-dir /绝对路径/audio
```

这两个命令支持 `--shared-dir`；默认共享目录来自 `KNOWLEDGE_SHARED_DIR`，否则为 `jobs/_shared`。下载全部音频需要显式 `--all`。只选一个高光时不要下载整个专辑。

## 接口与 SQL

固定基址 `https://m.ximalaya.com/content-hub-web/xql`。三步均带 `signature` 请求头：

1. `POST /commit`，JSON 请求体为 `{"sql":"...","limit":0}`；从 `data.queryId` 读取任务 ID 并立即落盘。
2. `GET /query-status/{queryId}`，外层 `success=true` 仅表示状态请求成功。只有 `data.finished=true` 且 `data.success=true`、`data.hasResult=true` 才下载。未完成时不因内层 `success=false` 判定 SQL 失败。
3. `GET /download-result/{queryId}`，实测返回 JSON，CSV 文本位于 `data.csvContent`；校验外层 `success` 和返回的 `data.queryId`。同时兼容直接返回 UTF-8 CSV/TSV 的文件响应。

```sql
SELECT
    a.track_id,
    b.track_name,
    b.track_play_path
FROM adm.track_base_info_detail_df AS a
JOIN dwd.track_detail_df AS b
    ON a.track_id = b.track_id
   AND b.dt = date_format(date_sub(current_date(), 1), 'yyyyMMdd')
WHERE a.dt = date_format(date_sub(current_date(), 1), 'yyyyMMdd')
  AND a.album_id = 41152491
ORDER BY a.real_track_order_no
```

仅替换经过数字校验的专辑 ID；两个表都保留昨日分区限制。昨日由查询服务的 `current_date()` 决定，不是本机时区推算的分区。保留返回顺序，不按 track_id 再排序。`limit:0` 按用户接口契约提交，不人为截断清单。

结果必须包含三列，track_id 必须非空且唯一，播放路径不能为空；空清单、重复 ID、缺列、错误 JSON/HTML 均明确失败。标题可包含逗号，按标准 CSV 解析。相对 `track_play_path` 使用现有 `AUDIO_CDN_BASE`（默认 `https://aod.cos.tx.xmcdn.com/`）拼接，完整 HTTP(S) URL 直接使用。音频仍经 ffprobe 和 SHA-256 验证。

## 缓存、恢复与刷新

- `<shared>/albums/<album_id>/xql/query.json` 保存查询 ID、状态、SQL 哈希、条数及文件哈希；`tracks.csv` 保存完整有序清单。共享锁避免同专辑并发 job 重复提交。完成清单缓存24小时，新任务过期后重新查询；日期变化不会改写已有 job。
- 每个 job 保存 `xql_tracks.csv` 和 `xql_tracks.json` 来源凭据。读取时验证专辑和 SHA-256。版本指纹只读本地文件，不触发网络。共享刷新不覆盖已开始任务的快照。
- 默认每3秒查状态，一次最多等待120秒（单次网络超时另计）。超时保留 queryId，`resume` 继续原查询。job 可设 `xql_wait_seconds`；独立清单命令可设 `--wait-seconds`。
- 下载失败、结果格式错误或缓存损坏时，下次继续同一 queryId 查询/下载，不重新提交 SQL。服务端明确失败时保留失败状态，修复后显式刷新。无结果、鉴权失败或未知状态时报告问题，不退回旧表格或猜测 URL。
- 提交前写入 `submitting`，若网络中断造成 queryId 未知，不盲目重发。核实服务端后用 `--query-id 已确认ID` 接续（必须人工确认属于同一专辑和 SQL），或用 `--refresh` 明确重新查询。

新批次需要最新清单时：

```bash
python3 scripts/xql_catalog.py --album-id 41152491 --refresh
```

只有明确要更新某个已有 XQL job 的来源时，在该 job 停止运行后显式写入它的快照：

```bash
python3 scripts/xql_catalog.py --album-id 41152491 --refresh --output /绝对路径/job/xql_tracks.csv
python3 scripts/run.py resume --job-dir /绝对路径/job
```

`resume` 根据实际路径/版本变化重跑相关阶段；仍遵守现有内容审核门禁。如果 URL 未变但远端音频内容已替换，需用 `download_audio.py --overwrite` 刷新共享音频并显式重跑 `download_audio`，不能假定查询刷新等于音频更新。

## 旧 CSV 与已有 job

显式 `--csv` 保留旧入口，配置了 `csv` 的 job 不会静默改成 XQL，也不要求 XQL signature。CSV 可继续携带 `transcript`、`source_version`；`cut_plan.source_audio` 仍优先使用确切的本地音频。

旧 CSV job 要迁移时，推荐用同一专辑的新 job-id，不带 `--csv` 创建，复用共享音频及文稿缓存；不要只写入 XQL 快照却保留旧 config.csv 并误以为已换源。单独 `fetch_transcripts.py` 是原 MOSN 文稿工具，不负责 XQL 音频来源；默认制作使用 `run.py`。
