#!/usr/bin/env python3
"""按 XQL 专辑清单下载喜马拉雅音频，兼容旧 CSV（track_play_path + CDN 前缀）。

CSV 结构：序号,track_id,track_name,track_play_path
  track_play_path 是相对路径（如 storages/.../xxx.mp3），
  完整地址 = CDN_BASE + track_play_path。

用法：
  python3 download_audio.py --album-id 41152491 --track-ids 330217947 330214363
  python3 download_audio.py --csv 簪中录.csv --all --output-dir /path/to/audio
  python3 download_audio.py --csv 簪中录.csv --track-ids 1004488340 --output-dir /path/to/audio --overwrite
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import tempfile
import time
import subprocess
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import requests
import timing
from artifacts import atomic_copy, atomic_json, digest, file_hash, file_lock, read_json, verified_file

# CDN 前缀：优先环境变量 AUDIO_CDN_BASE，默认喜马拉雅公网 CDN
CDN_BASE = os.environ.get("AUDIO_CDN_BASE", "https://aod.cos.tx.xmcdn.com/")
DEFAULT_OUTPUT = Path("audio")
TIMEOUT = (15.0, 180.0)
CHUNK_SIZE = 256 * 1024


def load_rows(csv_path: Path) -> list[dict[str, str]]:
    if not csv_path.is_file():
        raise SystemExit(f"CSV 不存在: {csv_path}")
    with csv_path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    # 兼容 BOM/空字段
    required = {"track_id", "track_play_path"}
    for col in required:
        if col not in (reader.fieldnames or []):
            raise SystemExit(f"CSV 缺少列 {col!r}，当前列: {reader.fieldnames}")
    return rows


def build_url(track_play_path: str) -> str:
    path = track_play_path.strip().lstrip("/")
    if path.startswith("http://") or path.startswith("https://"):
        return path
    return urljoin(CDN_BASE, path)


def safe_name(track_name: str, track_id: str, suffix: str) -> str:
    name = (track_name or track_id).strip()
    # 去掉文件系统不友好字符
    for ch in '\\/:*?"<>|':
        name = name.replace(ch, "_")
    name = name.strip(" ._") or track_id
    return f"{track_id}_{name}{suffix}"


def _download_uncached(row: dict[str, str], output_dir: Path, overwrite: bool) -> dict:
    track_id = row["track_id"].strip()
    track_name = row.get("track_name", "").strip()
    play_path = row["track_play_path"].strip()
    url = build_url(play_path)
    suffix = Path(urlsplit(play_path).path).suffix or ".mp3"

    # 已有同名文件（按 track_id 前缀匹配）
    existing = sorted(output_dir.glob(f"{track_id}_*")) if output_dir.is_dir() else []
    if existing and not overwrite:
        return {"track_id": track_id, "status": "skipped", "path": str(existing[0])}

    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / safe_name(track_name, track_id, suffix)

    result = {"track_id": track_id, "track_name": track_name, "url": url}
    try:
        with timing.span('download', media='audio', track_id=track_id), requests.get(url, timeout=TIMEOUT, stream=True) as resp:
            resp.raise_for_status()
            fd, tmp = tempfile.mkstemp(prefix=f".{track_id}.", suffix=".part", dir=output_dir)
            written = 0
            try:
                with open(fd, "wb") as out:
                    for chunk in resp.iter_content(CHUNK_SIZE):
                        if chunk:
                            out.write(chunk)
                            written += len(chunk)
                if written == 0:
                    raise RuntimeError("下载结果为空")
                Path(tmp).replace(target)
            except Exception:
                try:
                    Path(tmp).unlink(missing_ok=True)
                except OSError:
                    pass
                raise
        result.update({"status": "downloaded", "path": str(target), "bytes": written})
    except Exception as exc:
        result.update({"status": "failed", "error": f"{type(exc).__name__}: {exc}"})
    return result


def download_one(row: dict[str, str], output_dir: Path, overwrite: bool,
                 cache_dir: Path | None = None) -> dict:
    track_id = row['track_id'].strip()
    if not track_id.isdigit():
        raise ValueError('track_id必须是数字')
    key = digest({'schema': 2, 'url': build_url(row['track_play_path']),
                  'source_version': row.get('source_version', '')})
    entry = (cache_dir or output_dir / '.cache') / key
    receipt = entry / 'audio.json'
    with file_lock(entry / 'download.lock'):
        saved = read_json(receipt, {})
        source = entry / saved.get('filename', 'missing')
        if not overwrite and verified_file(source, saved.get('sha256')):
            target = output_dir / safe_name(row.get('track_name', ''), track_id, source.suffix)
            atomic_copy(source, target)
            return {'track_id': track_id, 'status': 'skipped', 'path': str(target.resolve()),
                    'sha256': saved['sha256'], 'source_key': key}
        result = _download_uncached(row, entry, True)
        if result['status'] != 'downloaded':
            return result
        source = Path(result['path'])
        check = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'a:0',
            '-show_entries', 'stream=codec_type', '-of', 'default=nw=1:nk=1', str(source)],
            capture_output=True, text=True, timeout=30)
        if check.returncode or 'audio' not in check.stdout:
            source.unlink(missing_ok=True)
            return {'track_id': track_id, 'status': 'failed', 'error': '下载内容不是有效音频'}
        sha = file_hash(source)
        atomic_json(receipt, {'filename': source.name, 'sha256': sha, 'source_key': key})
        target = output_dir / safe_name(row.get('track_name', ''), track_id, source.suffix)
        atomic_copy(source, target)
        return {**result, 'path': str(target.resolve()), 'sha256': sha, 'source_key': key}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--album-id", help="通过 XQL 查询专辑音频清单")
    source.add_argument("--csv", type=Path, help="兼容旧 CSV 或已生成的 XQL 清单")
    parser.add_argument("--shared-dir", type=Path, help="专辑共享根目录")
    parser.add_argument("--track-ids", nargs="+", help="只下载指定的 track_id")
    parser.add_argument("--all", action="store_true", help="下载清单里全部音频")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--overwrite", action="store_true", help="覆盖已有文件")
    parser.add_argument('--cache-dir', type=Path, help='专辑共享音频缓存；同URL内容更新用--overwrite或CSV source_version')
    parser.add_argument("--interval", type=float, default=0.5, help="请求间隔秒数，默认 0.5")
    args = parser.parse_args()

    if not args.track_ids and not args.all:
        raise SystemExit("需指定 --track-ids 或 --all")
    if args.album_id:
        from shared_album import album_root
        from xql_catalog import fetch_catalog
        cfg = {'album_id': args.album_id}
        if args.shared_dir:
            cfg['shared_root'] = str(args.shared_dir)
        try:
            root = album_root(cfg)
            rows, _ = fetch_catalog(args.album_id, root / 'xql')
        except (ValueError, OSError, RuntimeError) as exc:
            raise SystemExit(str(exc)) from None
        if args.cache_dir is None:
            args.cache_dir = root / 'audio'
    else:
        rows = load_rows(args.csv)
    if args.track_ids:
        wanted = set(args.track_ids)
        missing = wanted - {r["track_id"].strip() for r in rows}
        if missing:
            raise SystemExit("清单缺少 track_id: " + ", ".join(sorted(missing)))
        rows = [r for r in rows if r["track_id"].strip() in wanted]
    elif not args.all:
        raise SystemExit("需指定 --track-ids 或 --all")

    if not rows:
        raise SystemExit("没有匹配的音频")

    print(f"准备下载 {len(rows)} 条音频 → {args.output_dir.resolve()}", file=sys.stderr)
    results = []
    for i, row in enumerate(rows, 1):
        res = download_one(row, args.output_dir, args.overwrite, args.cache_dir)
        results.append(res)
        status = res["status"]
        print(f"[{i}/{len(rows)}] {res['track_id']} {status}", file=sys.stderr)
        if i < len(rows) and args.interval > 0:
            time.sleep(args.interval)

    ok = sum(1 for r in results if r["status"] in ("downloaded", "skipped"))
    failed = sum(1 for r in results if r["status"] == "failed")
    print(f"\n完成：成功/跳过 {ok}，失败 {failed}", file=sys.stderr)
    # 输出 JSON 摘要
    import json
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
