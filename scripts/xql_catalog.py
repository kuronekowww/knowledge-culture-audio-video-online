"""Fetch an ordered album audio catalog through the XQL asynchronous API."""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import time
from pathlib import Path

import requests
from skill_env import load_skill_env
load_skill_env()
from artifacts import atomic_bytes, atomic_json, digest, file_hash, file_lock, read_json, verified_file

BASE_URL = 'https://m.ximalaya.com/content-hub-web/xql'
SIGNATURE_ENV = 'XIMALAYA_XQL_SIGNATURE'
FIELDS = ['track_id', 'track_name', 'track_play_path']
SNAPSHOT = 'xql_tracks.csv'


def signature() -> str:
    value = os.environ.get(SIGNATURE_ENV, '').strip()
    if not value:
        raise ValueError(f'缺少环境变量 {SIGNATURE_ENV}；请配置 XQL signature')
    return value


def album_sql(album_id: str) -> str:
    album_id = str(album_id)
    if not re.fullmatch(r'[0-9]+', album_id) or int(album_id) <= 0:
        raise ValueError('album_id 必须是正整数')
    return f"""SELECT
    a.track_id,
    b.track_name,
    b.track_play_path
FROM adm.track_base_info_detail_df AS a
JOIN dwd.track_detail_df AS b
    ON a.track_id = b.track_id
   AND b.dt = date_format(date_sub(current_date(), 1), 'yyyyMMdd')
WHERE a.dt = date_format(date_sub(current_date(), 1), 'yyyyMMdd')
  AND a.album_id = {album_id}
ORDER BY a.real_track_order_no"""


def query_id(value) -> str:
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', value):
        raise ValueError('XQL 返回的 queryId 无效')
    return value


def request(method: str, route: str, *, payload=None, timeout=30):
    # Never send signature to a redirected host or include response bodies in errors.
    try:
        response = requests.request(method, BASE_URL + '/' + route,
            headers={'signature': signature(), 'Content-Type': 'application/json'},
            json=payload, timeout=(10, timeout), allow_redirects=False)
    except requests.RequestException as exc:
        raise RuntimeError(f'XQL {route.split("/")[0]} 网络错误（{type(exc).__name__}）；可恢复已有查询') from None
    if response.status_code != 200:
        raise RuntimeError(f'XQL {route.split("/")[0]} HTTP {response.status_code}；检查权限或服务状态')
    return response


def api_data(response) -> dict:
    try:
        envelope = response.json()
    except ValueError:
        raise ValueError('XQL API 未返回有效 JSON') from None
    if not isinstance(envelope, dict) or envelope.get('success') is not True:
        raise ValueError('XQL API 请求失败；检查 signature、权限或 SQL，不自动重新提交')
    data = envelope.get('data')
    if not isinstance(data, dict):
        raise ValueError('XQL API 缺少 data 对象')
    return data


def parse_result(content: bytes, expected_query_id=None) -> list[dict[str, str]]:
    try:
        text = content.decode('utf-8-sig')
    except UnicodeError:
        raise ValueError('XQL 结果不是 UTF-8 CSV/TSV 文件') from None
    if text.lstrip().startswith('{'):
        try:
            envelope = json.loads(text)
        except ValueError:
            raise ValueError('XQL 下载结果不是有效 JSON') from None
        data = envelope.get('data')
        if envelope.get('success') is not True or not isinstance(data, dict) or not isinstance(data.get('csvContent'), str):
            raise ValueError('XQL 下载失败或缺少 data.csvContent')
        if expected_query_id and data.get('queryId') != expected_query_id:
            raise ValueError('XQL 下载结果 queryId 不匹配')
        text = data['csvContent'].lstrip('\ufeff')
    # An HTTP 200 error JSON or login page must never become an empty audio catalog.
    first_line = text.splitlines()[0] if text.strip() else ''
    delimiter = '\t' if '\t' in first_line and ',' not in first_line else ','
    reader = csv.DictReader(io.StringIO(text, newline=''), delimiter=delimiter)
    if not reader.fieldnames or not set(FIELDS) <= set(reader.fieldnames):
        raise ValueError('XQL 结果缺少 track_id、track_name、track_play_path 列；可能是错误响应或格式变化')
    rows, seen = [], set()
    for row in reader:
        if None in row or any(row.get(k) is None for k in FIELDS):
            raise ValueError('XQL 结果行列数不匹配')
        item = {key: row[key].strip() for key in FIELDS}
        tid = item['track_id']
        if not re.fullmatch(r'[0-9]+', tid) or tid in seen:
            raise ValueError('XQL track_id 必须是非空且唯一的数字；请检查查询结果')
        if item['track_play_path'].lower() in {'', 'null', 'none', '\\n'}:
            raise ValueError(f'XQL track_id={tid} 缺少音频路径')
        seen.add(tid)
        rows.append(item)
    if not rows:
        raise ValueError('XQL 返回空清单；检查 album_id、昨日分区及表权限')
    return rows  # Preserve ORDER BY real_track_order_no; never sort by track_id.


def csv_bytes(rows: list[dict]) -> bytes:
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=FIELDS)
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode('utf-8-sig')


def fetch_catalog(album_id: str, cache_dir: Path, *, refresh=False, resume_query_id=None,
                  wait_seconds=120, poll_seconds=3, ttl_seconds=86400) -> tuple[list[dict], dict]:
    sql = album_sql(album_id)
    if wait_seconds < 0 or poll_seconds <= 0 or ttl_seconds < 0:
        raise ValueError('等待/缓存时间不能为负，轮询间隔必须大于 0')
    cache_dir = Path(cache_dir)
    state_path, result_path = cache_dir / 'query.json', cache_dir / 'tracks.csv'
    basis = {'album_id': str(album_id), 'sql_hash': digest(sql), 'endpoint': BASE_URL}
    with file_lock(cache_dir / '.query.lock'):
        state = read_json(state_path, {})
        if state and any(state.get(k) != v for k, v in basis.items()):
            raise ValueError('XQL 缓存不属于当前专辑/SQL/接口；请使用独立缓存目录')
        if refresh or resume_query_id:
            if state:
                atomic_json(cache_dir / 'history' / f'{time.time_ns()}.json', state)
            state = {}
        if state.get('phase') == 'complete':
            if verified_file(result_path, state.get('sha256')):
                if time.time() - state['completed_at'] < ttl_seconds:
                    return parse_result(result_path.read_bytes()), state
                state = {}  # New jobs may query again after TTL; existing job snapshots remain fixed.
        if not state:
            signature()  # Do not leave an uncertain commit marker for a missing local credential.
            state = {**basis, 'phase': 'submitting', 'submitted_at': time.time()}
            if resume_query_id:
                state.update(query_id=query_id(resume_query_id), phase='submitted')
                atomic_json(state_path, state)
            else:
                atomic_json(state_path, state)
                data = api_data(request('POST', 'commit', payload={'sql': sql, 'limit': 0}))
                state.update(query_id=query_id(data.get('queryId')), phase='submitted')
                atomic_json(state_path, state)
        if not state.get('query_id'):
            raise RuntimeError('上次 XQL 提交结果未知；先查服务端，再用 --query-id 接续或 --refresh 显式重新查询')
        qid = query_id(state['query_id'])
        if state.get('phase') == 'failed':
            raise RuntimeError(f'XQL 查询 {qid} 已失败；修复原因后使用 --refresh，不自动重新提交')
        deadline = time.monotonic() + wait_seconds
        while True:
            status = api_data(request('GET', 'query-status/' + qid))
            if status.get('queryId') != qid or type(status.get('finished')) is not bool:
                raise ValueError('XQL 状态的 queryId/finished 格式异常')
            state['last_status'] = {k: status.get(k) for k in ('execState', 'finished', 'success', 'hasResult')}
            atomic_json(state_path, state)
            if status['finished']:
                if status.get('success') is not True:
                    state['phase'] = 'failed'
                    atomic_json(state_path, state)
                    raise RuntimeError(f'XQL 查询 {qid} 执行失败；检查服务端错误、昨日分区及表权限')
                if status.get('hasResult') is not True:
                    raise ValueError(f'XQL 查询 {qid} 已完成但没有可下载结果')
                break
            if time.monotonic() >= deadline:
                raise TimeoutError(f'XQL 查询 {qid} 尚未完成；已保存 queryId，resume 将继续查询，不重复提交')
            time.sleep(min(poll_seconds, max(0, deadline - time.monotonic())))
        rows = parse_result(request('GET', 'download-result/' + qid, timeout=120).content, qid)
        atomic_bytes(result_path, csv_bytes(rows))
        state.update(phase='complete', completed_at=time.time(), rows=len(rows), sha256=file_hash(result_path))
        atomic_json(state_path, state)
        return rows, state


def write_snapshot(path: Path, rows: list[dict], receipt: dict):
    atomic_bytes(path, csv_bytes(rows))
    atomic_json(path.with_suffix('.json'), {**receipt, 'sha256': file_hash(path)})


def read_snapshot(path: Path, album_id: str) -> list[dict]:
    meta = read_json(path.with_suffix('.json'), {})
    if meta.get('album_id') != str(album_id) or not verified_file(path, meta.get('sha256')):
        raise ValueError('XQL 清单快照来源/哈希不匹配；用 xql_catalog.py 显式恢复快照')
    return parse_result(path.read_bytes())


def ensure_job_catalog(job: Path, cfg: dict) -> list[dict]:
    if cfg.get('csv'):
        raise ValueError('CSV 兼容任务不应自动覆盖为 XQL 来源')
    path = job / SNAPSHOT
    if path.exists():
        return read_snapshot(path, cfg['album_id'])
    from shared_album import album_root
    rows, receipt = fetch_catalog(cfg['album_id'], album_root(cfg) / 'xql',
        wait_seconds=float(cfg.get('xql_wait_seconds', 120)))
    write_snapshot(path, rows, receipt)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--album-id', required=True)
    parser.add_argument('--shared-dir', type=Path, help='共享根目录，默认 KNOWLEDGE_SHARED_DIR 或 jobs/_shared')
    parser.add_argument('--output', type=Path, help='可选输出清单快照，同时写入同名 JSON 来源凭据')
    parser.add_argument('--refresh', action='store_true', help='显式提交新查询，不替换其他 job 已固定的快照')
    parser.add_argument('--query-id', help='人工核实属于同一专辑/SQL 的已有 queryId，用于恢复未知提交')
    parser.add_argument('--wait-seconds', type=float, default=120)
    args = parser.parse_args()
    try:
        from shared_album import album_root
        cfg = {'album_id': args.album_id}
        if args.shared_dir:
            cfg['shared_root'] = str(args.shared_dir)
        rows, receipt = fetch_catalog(args.album_id, album_root(cfg) / 'xql',
            refresh=args.refresh, resume_query_id=args.query_id, wait_seconds=args.wait_seconds)
        if args.output:
            write_snapshot(args.output.expanduser().resolve(), rows, receipt)
        print(f'XQL 清单就绪：专辑 {args.album_id}，{len(rows)} 条，queryId={receipt["query_id"]}')
        return 0
    except (ValueError, OSError, RuntimeError) as exc:
        print(str(exc))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
