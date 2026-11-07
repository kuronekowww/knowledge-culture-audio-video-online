"""
rpc调用：喜马拉雅 mosn 专辑音频列表与单条 AI 文稿（queryAiDocByTrackId）获取
"""

from __future__ import annotations

import json
import random
import time
from typing import Any

import requests

DEFAULT_MOSN_BASE = "http://ops.ximalaya.com/mosn"

REQUEST_INTERVAL = 0.5
REQUEST_TIMEOUT = 5
MAX_RETRIES = 3

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/98.0.4758.102 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:97.0) Gecko/20100101 Firefox/97.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.2 Safari/605.1.15",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 15_3 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.0 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Linux; Android 12; SM-G991B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/97.0.4692.99 Mobile Safari/537.36",
]


def _mosn_url(config: dict[str, Any] | None) -> str:
    if not config:
        return DEFAULT_MOSN_BASE
    return str(config.get("mosn_base_url") or DEFAULT_MOSN_BASE).rstrip("/")


def _mosn_numeric_id(val: str | int) -> int:
    #序列化输入的专辑id或者trackid为 数字类型（供后续调用rpc直接作为入参）：
    # 若输入的是字符串，则转换为数字,并返回数字类型，若输入的是数字，则直接返回数字
    if isinstance(val, int):
        return val
    s = str(val).strip()
    if s.isdigit():
        return int(s)
    return val

def query_album_track_ids(album_id: str | int,max_tracks: int,config: dict[str, Any] | None = None,) -> list[Any] | None:
    # 调用rpc接口：queryAlbumTrackIds请求专辑对应的音频列表，获取n个音频trackid列表，返回trackid列表，如果请求失败，则返回None
    url = _mosn_url(config)
    headers = {
        "Connection": "keep-alive",
        "Accept": "application/json, text/plain, */*",
        "User-Agent": random.choice(USER_AGENTS),
        "x-client-app": "album-facade",
        "x-request-group": "album",
        "x-request-scope": "default",
        "x-request-service": "com.ximalaya.service.album.api.thrift.IAlbumFacadeHandler$Iface",
        "x-request-method": "queryAlbumTrackIds",
        "x-request-timeout": "5000",
        "Content-Type": "application/json;charset=UTF-8",
    }
    data = {
        "asc": True,
        "pageSize": 10000,
        "albumId": _mosn_numeric_id(album_id),
        "page": 1,
    }
    try:
        r = requests.post(
            url,
            headers=headers,
            data=json.dumps(data),
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        try:
            ids = r.json()["body"]["ids"]
        except (KeyError, TypeError):
            return None
        if not ids:
            return None
        return ids[: max(1, int(max_tracks))]
    except Exception:
        return None

def get_track_document(track_id: str | int,config: dict[str, Any] | None = None,) -> dict | None:
    # 调用rpc接口：queryAiDocByTrackId请求音频对应的ai文稿，获取音频对应的ai文稿，返回ai文稿，如果请求失败，则返回None

    url = _mosn_url(config)
    headers = {
        "Connection": "keep-alive",
        "Accept": "application/json, text/plain, */*",
        "User-Agent": random.choice(USER_AGENTS),
        "x-client-app": "track-backend",
        "x-request-group": "track-backend",
        "x-request-scope": "default",
        "x-request-service": "com.ximalaya.track.backend.api.thrift.TRemoteTrackBackendService$Iface",
        "x-request-method": "queryAiDocByTrackId",
        "x-request-timeout": "5000",
        "Content-Type": "application/json;charset=UTF-8",
    }
    data = {"trackId": _mosn_numeric_id(track_id)} 
    r = requests.post(
        url,
        headers=headers,
        data=json.dumps(data),
        timeout=REQUEST_TIMEOUT,
    )
    r.raise_for_status()
    try:
        payload = r.json()
        return payload
    except (KeyError, TypeError):
        return None

# 重试获取音频对应的ai文稿，如果获取失败，则重试，最多重试3次，每次重试间隔1-3秒
def get_track_content_with_retry(track_id: str | int,config: dict[str, Any] | None = None,) -> str | None:
    for attempt in range(MAX_RETRIES + 1):
        try:
            content = get_track_content(track_id, config)
            return content or ""
        except Exception:
            if attempt < MAX_RETRIES:
                sleep_time = min(60, (2**attempt) + random.uniform(0, 3.0))
                time.sleep(sleep_time)
            else:
                return None
    return None


def get_track_document_result(track_id, config=None):
    """Keep transport errors separate from successfully returned empty documents."""
    last = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            payload = get_track_document(track_id, config)
            if not isinstance(payload,dict):
                return {'status': 'invalid_response', 'payload': payload, 'attempts': attempt+1}
            rows = payload.get('body')
            if not isinstance(rows, list):
                return {'status': 'invalid_response', 'payload': payload, 'attempts': attempt+1}
            present = any(isinstance(r, dict) and str(r.get('text', '')).strip() for r in rows)
            return {'status': 'ok' if present else 'empty_document', 'payload': payload, 'attempts': attempt+1}
        except requests.exceptions.JSONDecodeError:
            return {'status':'invalid_response','payload':None,'attempts':attempt+1}
        except requests.RequestException as exc:
            last = {'status': 'access_failed', 'payload': None, 'error_type': type(exc).__name__,
                    'http_status': getattr(getattr(exc, 'response', None), 'status_code', None), 'attempts': attempt+1}
            if attempt < MAX_RETRIES: time.sleep(min(60, (2**attempt) + random.uniform(0, 3.0)))
        except (ValueError, TypeError, KeyError):
            return {'status': 'invalid_response', 'payload': None, 'attempts': attempt+1}
    return last


def get_track_document_with_retry(track_id, config=None):
    return get_track_document_result(track_id, config).get('payload')


def get_track_content(track_id, config=None):
    """Compatibility string accessor; new callers retain the coarse timeline."""
    payload = get_track_document(track_id, config)
    return ''.join(row['text'] for row in payload.get('body', [])) if payload else None
