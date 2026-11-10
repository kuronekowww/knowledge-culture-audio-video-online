#!/usr/bin/env python3
"""Batch-generate confirmed storyboards with GPT Image 2.

The manifest contains one independent text-to-image task per cover or story shot.
Authentication comes from GPT_IMAGE_2_TOKEN (process environment or skill-local .env); scripts never persist it in outputs.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import time
import re
from concurrent.futures import ThreadPoolExecutor, as_completed, wait
from pathlib import Path
from typing import Any

import requests
from skill_env import load_skill_env
load_skill_env()
import contextlib
import contextvars
import timing
import image_provenance
import image_finish

BATCH_GATE = None
BATCH_DOWNLOADS = None
from PIL import Image
from artifacts import atomic_copy, atomic_json, digest, file_hash, file_lock, read_json, verified_file


DEFAULT_API_BASE = "https://yitian.xmly.work/llm-proxy/api/v1/gpt-image"
DEFAULT_WORKERS = 3
DEFAULT_QUALITY = "low"
MAX_WORKERS = 8
PROXY_SIZES = {"1024x1024", "1024x1536", "1536x1024", "1024x2048", "2048x1024"}
# Old cached assets and explicitly imported files can already use the delivery canvas.
ALLOWED_SIZES = PROXY_SIZES | {"1080x1920"}
ALLOWED_QUALITIES = {"low", "medium", "high"}


class ManifestError(ValueError):
    """Raised when a manifest cannot be safely executed."""


class GenerationError(RuntimeError):
    """Generation failure is NOT automatically safe to resubmit."""
    def __init__(self, message, http_status=None, request_id=None):
        super().__init__(message); self.http_status=http_status; self.request_id=request_id


class GenerationResult(str):
    def __new__(cls,url,metadata):
        obj=super().__new__(cls,url);obj.metadata=metadata;return obj


def prompt_summary(prompt: str, limit: int = 120) -> str:
    compact = " ".join(prompt.split())
    return compact if len(compact) <= limit else f"{compact[:limit - 1]}…"


def safe_error(error: Exception, token: str) -> str:
    message = " ".join(str(error).split())
    if token:
        message = message.replace(token, "[REDACTED]")
    return f"{type(error).__name__}: {message[:300]}"


def read_manifest(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ManifestError(f"manifest does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ManifestError(f"manifest is not valid JSON: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ManifestError("manifest root must be an object")
    return data


def positive_int(value: Any, field: str, maximum: int = MAX_WORKERS) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise ManifestError(f"{field} must be an integer between 1 and {maximum}")
    return value


def validate_manifest(raw: dict[str, Any], manifest_path: Path) -> dict[str, Any]:
    output_dir_value = raw.get("output_dir")
    if not isinstance(output_dir_value, str) or not output_dir_value.strip():
        raise ManifestError("output_dir must be a non-empty string")
    output_dir = Path(output_dir_value).expanduser()
    if not output_dir.is_absolute():
        output_dir = manifest_path.parent / output_dir
    output_dir = output_dir.resolve()

    quality = raw.get("quality", DEFAULT_QUALITY)
    if quality not in ALLOWED_QUALITIES:
        raise ManifestError(f"quality must be one of: {', '.join(sorted(ALLOWED_QUALITIES))}")
    workers = positive_int(raw.get("max_workers", DEFAULT_WORKERS), "max_workers")
    download_workers = positive_int(raw.get("download_workers", 3), "download_workers")
    images = raw.get("images")
    if not isinstance(images, list) or not images:
        raise ManifestError("images must be a non-empty array")

    ids: set[str] = set()
    filenames: set[str] = set()
    normalized: list[dict[str, str]] = []
    for index, image in enumerate(images, start=1):
        if not isinstance(image, dict):
            raise ManifestError(f"images[{index}] must be an object")
        image_id = image.get("id")
        filename = image.get("filename")
        prompt = image.get("prompt")
        size = image.get("size")
        if not isinstance(image_id, str) or not image_id.strip():
            raise ManifestError(f"images[{index}].id must be a non-empty string")
        if not re.fullmatch(r'[A-Za-z0-9_-]+', image_id):
            raise ManifestError('image id只允许字母、数字、下划线和连字符')
        if not isinstance(filename, str) or not filename.strip():
            raise ManifestError(f"images[{index}].filename must be a non-empty string")
        if Path(filename).name != filename or filename in {".", ".."}:
            raise ManifestError(f"images[{index}].filename must be a single file name")
        if not Path(filename).suffix:
            raise ManifestError(f"images[{index}].filename must have an extension")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ManifestError(f"images[{index}].prompt must be a non-empty string")
        if size not in ALLOWED_SIZES:
            raise ManifestError(f"images[{index}].size must be one of: {', '.join(sorted(ALLOWED_SIZES))}")
        from cover_transport import FIT
        fit = image.get('cover_fit')
        if fit is not None and (image_id != 'cover_portrait' or fit != FIT or size != '1024x1536' or Path(filename).suffix.lower() != '.png'):
            raise ManifestError('最终封面适配需要 cover_portrait、PNG、1024x1536 和 full_width_9x16')
        width, height = map(int, size.split('x'))
        portrait_asset=image_id in {'cover_portrait','album_background'}
        if (portrait_asset and width>=height) or (not portrait_asset and width<=height):
            raise ManifestError('cover_portrait/album_background必须竖向，正文镜头必须横向')
        if image_id in ids:
            raise ManifestError(f"duplicate image id: {image_id}")
        if filename.casefold() in filenames:
            raise ManifestError(f"duplicate filename: {filename}")
        ids.add(image_id)
        filenames.add(filename.casefold())
        image_quality = image.get('quality',quality)
        if image_quality not in ALLOWED_QUALITIES:
            raise ManifestError('invalid per-image quality: '+str(image_quality))
        normalized.append({**({'quality':image_quality} if 'quality' in image else {}),"id": image_id, "filename": filename, "prompt": prompt, "size": size,
                           **({"cover_fit":fit} if fit else {}), "backend": image.get("backend"), "revision": positive_int(image.get("revision", 1), "revision", 2147483647)})

    return {"output_dir": output_dir, "quality": quality, "max_workers": workers, "download_workers": download_workers, "images": normalized}


def check_wire_size(image):
    if image['size'] not in PROXY_SIZES:
        raise ManifestError('该尺寸仅用于交付或旧素材，不能提交给生图接口：'+image['size']+
                            '。新版封面请求 1024x1536，交付 1080x1920。原请求若已超时，请先核对回执。')


def request_image(api_base: str, token: str, image: dict[str, str], quality: str, request_timeout: float) -> str:
    check_wire_size(image)
    response = requests.post(
        f"{api_base.rstrip('/')}/generate",
        headers={"Content-Type": "application/json", "access-token": token},
        json={"prompt": image_finish.finish_prompt(image["prompt"]), "size": image["size"], "quality": quality, "n": 1, "include_b64": False},
        timeout=(10, request_timeout), allow_redirects=False,
    )
    request_id = next((response.headers.get(k) for k in ('x-request-id','request-id') if response.headers.get(k)), None)
    if not isinstance(request_id,str): request_id=None
    if response.status_code != 200:
        raise GenerationError(f"generate returned HTTP {response.status_code}",response.status_code,request_id)
    try:
        data = response.json()
    except ValueError as exc:
        raise GenerationError("generate returned invalid JSON",request_id=request_id) from exc
    records = data.get("data") if isinstance(data, dict) else None
    if not isinstance(records, list) or not records or not isinstance(records[0], dict):
        raise GenerationError("generate response has no image data",request_id=request_id)
    url = records[0].get("url")
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        raise GenerationError("generate response has no valid image URL",request_id=request_id)
    return GenerationResult(url,{'request_id':request_id,'reported_model':data.get('model'),'provider':'gpt_image_proxy'})


def verify_image(path: Path) -> None:
    with Image.open(path) as im:
        im.verify()
    with Image.open(path) as im:
        im.load()


def image_fingerprint(api_base: str, image: dict, quality: str) -> str:
    basis={"schema":2,"backend":api_base.rstrip('/'),"prompt":image['prompt'],"size":image['size'],
           "quality":image.get('quality',quality),"revision":image.get('revision',1)}
    if image.get('backend'): basis['actual_backend']=image['backend']
    if image.get('cover_fit'): basis['cover_fit']=image['cover_fit']
    return digest(basis)


def download_image(url: str, target: Path, download_timeout: float) -> int:
    response = requests.get(url, timeout=download_timeout)
    if response.status_code != 200:
        raise GenerationError(f"image download returned HTTP {response.status_code}")
    content = response.content
    if not content:
        raise GenerationError("image download was empty")
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        verify_image(Path(temporary_name))
        os.replace(temporary_name, target)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise
    return len(content)


def _prepare_one(
    api_base: str,
    token: str,
    image: dict[str, str],
    output_dir: Path,
    quality: str,
    request_timeout: float,
    download_timeout: float,
    cache_dir: Path | None = None,
    retry_generation: bool = False,
    adopt_existing: bool = False,
) -> dict[str, Any] | DownloadTask:
    quality = image.get("quality",quality)
    target = output_dir / image["filename"]
    record: dict[str, Any] = {
        "id": image["id"],
        "filename": image["filename"],
        "prompt_summary": prompt_summary(image["prompt"]),
        "output_path": str(target),
        "attempts": 0, "quality":quality, "requested_size":image['size'], "read_timeout_seconds":request_timeout,
    }
    fingerprint = image_fingerprint(api_base, image, quality)
    record['fingerprint'] = fingerprint
    entry = (cache_dir or output_dir / '.cache') / fingerprint
    receipt = entry / 'request.json'
    media = entry / ('image' + target.suffix)
    local_receipt = output_dir / '.receipts' / (image['id'] + '.json')
    try:
        with file_lock(entry / 'task.lock'):
            saved = read_json(receipt, {})
            local = read_json(local_receipt, {})
            if (saved.get('status') == 'complete' and local.get('fingerprint') == fingerprint
                    and saved.get('sha256') == local.get('sha256') and verified_file(target, saved.get('sha256'))):
                # A complete target is already bound to these exact bytes. Repair only a damaged cache copy.
                verify_image(target)
                if not verified_file(media, saved['sha256']):
                    atomic_copy(target, media)
                return {**record, 'status':'skipped', 'sha256':saved['sha256'], 'size_bytes':target.stat().st_size}
            if verified_file(media, saved.get('sha256')):
                verify_image(media)
                atomic_copy(media, target)
                atomic_json(local_receipt, {'fingerprint': fingerprint, 'sha256': saved['sha256'], 'provenance': saved.get('provenance')})
                return {**record, 'status': 'skipped', 'sha256': saved['sha256'], 'size_bytes': target.stat().st_size}
            if local.get('fingerprint') == fingerprint and verified_file(target, local.get('sha256')):
                verify_image(target)
                atomic_copy(target, media)
                atomic_json(receipt, {'fingerprint': fingerprint, 'status': 'complete', 'sha256': file_hash(media), 'provenance':local.get('provenance')})
                return {**record, 'status': 'skipped', 'sha256': file_hash(target), 'size_bytes': target.stat().st_size}
            if adopt_existing and target.is_file() and image.get('backend'):
                raise ManifestError('已声明外部后端，须用image_provenance.py import保留真实来源，不能legacy-adopt')
            if adopt_existing and target.is_file():
                verify_image(target)
                atomic_copy(target, media)
                sha = file_hash(media)
                with Image.open(media) as im: actual_size=list(im.size)
                atomic_json(receipt, {'fingerprint': fingerprint, 'status': 'complete', 'sha256': sha, 'adopted': True, 'provenance':{'provider':'unknown','model':'unknown','mode':'legacy_adoption','actual_size':actual_size}})
                atomic_json(local_receipt, {**read_json(receipt), 'fingerprint': fingerprint, 'sha256': sha})
                return {**record, 'status': 'skipped', 'sha256': sha, 'adopted': True, 'size_bytes': media.stat().st_size}
            if image.get('backend'):
                raise ManifestError('外部后端素材缺失或失效；使用image_provenance.py import接管，不调用默认接口冒充该后端')
            if target.exists() and not local and not saved:
                raise GenerationError('现有图片无版本凭据；确认对应当前清单后使用 --adopt-existing，或另设文件名')
            if saved.get('status') in {'submitting', 'generation_uncertain', 'generation_failed'} and not retry_generation:
                raise GenerationError('上次生成未确认成功；不会自动重复收费。先确认服务端结果，再显式 --retry-generation')
            url = saved.get('url')
            if retry_generation:
                url = None
            if not url:
                check_wire_size(image)
                if not token:
                    raise ManifestError('GPT_IMAGE_2_TOKEN is required for a new generation')
                # Normalize at submission, after recovering any paid or unresolved historical request.
                wire_image = {**image,'prompt':image_finish.finish_prompt(image['prompt'])}
                provenance={'provider':'gpt_image_proxy','model':'unknown','mode':'api','api_base':api_base,
                            'prompt':wire_image['prompt'],'requested_size':image['size'],'quality':quality,'image_finish':image_finish.VERSION}
                atomic_json(receipt, {'fingerprint': fingerprint, 'status': 'submitting','provenance':provenance})
                record['attempts'] = 1
                sent = None
                try:
                    with timing.span('image_queue', image_id=image['id']):
                        gate = BATCH_GATE or contextlib.nullcontext()
                        gate.__enter__()
                    try:
                        with timing.span('image_request', image_id=image['id']):
                            sent = time.monotonic()
                            url = request_image(api_base, token, wire_image, quality, request_timeout)
                    finally:
                        gate.__exit__(None, None, None)
                except Exception as exc:
                    details=image_provenance.error_details(exc)
                    details.update(read_timeout_seconds=request_timeout, requested_size=image['size'],
                                   elapsed_seconds=round(time.monotonic()-sent, 3) if sent is not None else None)
                    record['diagnosis']=details
                    atomic_json(receipt, {'fingerprint': fingerprint, 'status': 'generation_uncertain',
                                         'provenance':provenance,'error_details':details,'request_id':details['request_id']})
                    raise
                # Persist the expensive result before the first download attempt.
                metadata=getattr(url,'metadata',{})
                provenance['model']=metadata.get('reported_model') or 'unknown'
                provenance['generation_seconds']=round(time.monotonic()-sent, 3)
                provenance['read_timeout_seconds']=request_timeout
                atomic_json(receipt, {'fingerprint': fingerprint, 'status': 'download_pending', 'url': str(url),
                                     'provenance':provenance,'request_id':metadata.get('request_id')})
            return DownloadTask(record, target, entry, token, download_timeout, image.get('cover_fit'))
    except (requests.RequestException, GenerationError, ManifestError, OSError, ValueError) as exc:
        details = record.get('diagnosis') or image_provenance.error_details(exc)
        if isinstance(exc, ManifestError) and record['attempts']==0:
            details = {**details, 'phase':'local_validation','billing_state':'not_submitted',
                       'next_action':'correct_local_input'}
        return {**record, 'status':'failed', 'error':safe_error(exc,token),'error_details':details}


class DownloadTask:
    """A persisted generation result, completed by a separate download executor."""
    def __init__(self, record, target, entry, token, timeout, cover_fit=None):
        self.cover_fit = cover_fit
        self.record, self.target, self.entry = record, target, entry
        self.token, self.timeout = token, timeout
        self.queued_at = time.perf_counter()

    def complete(self):
        record, target, entry = self.record, self.target, self.entry
        timing.emit('download_queue', time.perf_counter()-self.queued_at, image_id=record['id'])
        receipt = entry / 'request.json'
        media = entry / ('image' + target.suffix)
        local_receipt = target.parent / '.receipts' / (record['id'] + '.json')
        fingerprint = record['fingerprint']
        try:
            with file_lock(entry / 'task.lock'):
                saved = read_json(receipt, {})
                if verified_file(media, saved.get('sha256')):
                    verify_image(media)
                    atomic_copy(media, target)
                    atomic_json(local_receipt, {'fingerprint': fingerprint, 'sha256': saved['sha256'], 'provenance': saved.get('provenance')})
                    return {**record, 'status': 'skipped', 'sha256': saved['sha256'], 'size_bytes': target.stat().st_size}
                # Another explicit generation may have replaced this receipt while queued.
                # Never publish a stale URL under the newer request's receipt.
                if saved.get('status') not in {'download_pending', 'complete'} or not saved.get('url'):
                    raise GenerationError('下载凭据发生变化；保留状态供检查，不自动重新生图')
                url = saved['url']
                downloaded = entry/'provider-original' if self.cover_fit else media
                last_error = None
                for attempt in range(3):
                    try:
                        if not (self.cover_fit and verified_file(downloaded, saved.get('native_sha256'))):
                            with timing.span('download', media='image', image_id=record['id'], attempt=attempt+1):
                                download_image(url, downloaded, self.timeout)
                        break
                    except (requests.RequestException, GenerationError, OSError, ValueError) as exc:
                        last_error = exc
                        if attempt < 2:
                            time.sleep(0.5 * 2 ** attempt)
                else:
                    raise GenerationError(f'已获图片 URL；仅下载待恢复：{last_error}')
                provenance = dict(saved.get('provenance') or {})
                if self.cover_fit:
                    from cover_transport import finish
                    # Persist source before postprocessing; an unexpected service size is inspected locally.
                    saved['native_sha256'] = file_hash(downloaded)
                    atomic_json(receipt, saved)
                    provenance.update(finish(downloaded, media), source_path=str(downloaded))
                else:
                    with Image.open(media) as im:
                        provenance['actual_size'] = list(im.size)
                sha = file_hash(media)
                atomic_json(receipt, {**saved,'fingerprint':fingerprint,'status':'complete','url':url,'sha256':sha,'provenance':provenance})
                atomic_copy(media, target)
                atomic_json(local_receipt, {'fingerprint':fingerprint,'sha256':sha,'provenance':provenance})
                return {**record, 'status':'success','sha256':sha,'size_bytes':target.stat().st_size,
                        'download_attempts':attempt+1}
        except (requests.RequestException, GenerationError, ManifestError, OSError, ValueError) as exc:
            return {**record, 'status': 'failed', 'error': safe_error(exc, self.token),'error_details':image_provenance.error_details(exc,'download')}


def generate_one(*args, **kwargs):
    """Compatibility entry point for synchronous single-image callers."""
    prepared = _prepare_one(*args, **kwargs)
    return prepared.complete() if isinstance(prepared, DownloadTask) else prepared


def write_report(output_dir: Path, report: dict[str, Any]) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "image_generation_report.json"
    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    fd, temporary_name = tempfile.mkstemp(prefix=f".{report_path.name}.", suffix=".tmp", dir=output_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, report_path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise
    return report_path


def run_batch(
    config: dict[str, Any],
    token: str,
    api_base: str = DEFAULT_API_BASE,
    workers: int | None = None,
    quality: str | None = None,
    dry_run: bool = False,
    request_timeout: float = 240,
    download_timeout: float = 60,
    cache_dir: Path | None = None,
    only: list[str] | None = None,
    retry_generation: bool = False,
    adopt_existing: bool = False,
    download_workers: int | None = None,
) -> dict[str, Any]:
    selected_downloads = positive_int(download_workers if download_workers is not None else config.get("download_workers", 3), "download_workers")
    selected_workers = positive_int(workers, "workers") if workers is not None else config["max_workers"]
    if quality is not None:
        config = {**config,"images":[{**row,"quality":quality} for row in config["images"]]}
    selected_quality = quality or config["quality"]
    if selected_quality not in ALLOWED_QUALITIES:
        raise ManifestError(f"quality must be one of: {', '.join(sorted(ALLOWED_QUALITIES))}")
    output_dir = config["output_dir"]
    images = config["images"]
    if only:
        unknown = set(only) - {i['id'] for i in images}
        if unknown:
            raise ManifestError(f'未知镜头: {sorted(unknown)}')
        images = [i for i in images if i['id'] in only]
    if dry_run:
        for item in images:
            if not item.get('backend') and not adopt_existing:
                check_wire_size(item)
        results = [
            {
                "id": image["id"],
                "filename": image["filename"],
                "prompt_summary": prompt_summary(image["prompt"]),
                "output_path": str(output_dir / image["filename"]),
                "attempts": 0,
                "status": "dry_run",
            }
            for image in images
        ]
    else:
        results_by_id: dict[str, dict[str, Any]] = {}
        shared_downloads = BATCH_DOWNLOADS
        downloads_context = contextlib.nullcontext(shared_downloads) if shared_downloads is not None else ThreadPoolExecutor(max_workers=selected_downloads)
        with downloads_context as downloads, ThreadPoolExecutor(max_workers=selected_workers) as executor:
            download_futures = {}
            futures = {
                executor.submit(
                    contextvars.copy_context().run,
                    _prepare_one,
                    api_base,
                    token,
                    image,
                    output_dir,
                    image.get("quality",selected_quality) if quality is None else selected_quality,
                    request_timeout,
                    download_timeout,
                    cache_dir,
                    retry_generation,
                    adopt_existing,
                ): image["id"]
                for image in images
            }
            try:
                for future in as_completed(futures):
                    image_id = futures[future]
                    prepared = future.result()
                    if isinstance(prepared, DownloadTask):
                        download_futures[downloads.submit(contextvars.copy_context().run, prepared.complete)] = image_id
                    else:
                        results_by_id[image_id] = prepared
                for future in as_completed(download_futures):
                    results_by_id[download_futures[future]] = future.result()
            finally:
                # Shared executor outlives this job. Finish its writes before releasing the job lock.
                wait(download_futures)
        results = [results_by_id[image["id"]] for image in images]

    report = {
        "backend": "per_image_receipts",
        "review_status": "pending_user_review",
        "api_base": api_base.rstrip("/"),
        "quality": selected_quality,
        "max_workers": selected_workers,
        "download_workers": BATCH_DOWNLOADS._max_workers if BATCH_DOWNLOADS is not None else selected_downloads,
        "dry_run": dry_run,
        "results": results,
    }
    report_path = write_report(output_dir, report)
    report["report_path"] = str(report_path)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True, help="confirmed storyboard image manifest JSON")
    parser.add_argument("--workers", type=int, help=f"parallel requests, 1-{MAX_WORKERS}; default from manifest")
    parser.add_argument("--download-workers", type=int, help="独立下载并发，默认3，范围1–8")
    parser.add_argument("--quality", choices=sorted(ALLOWED_QUALITIES), help="override manifest quality")
    parser.add_argument("--api-base", help="GPT Image 2 API base; defaults to manifest api_base or built-in endpoint")
    parser.add_argument("--dry-run", action="store_true", help="validate and write a report without network requests")
    parser.add_argument('--cache-dir', type=Path, help='跨job共享的图片内容缓存')
    parser.add_argument('--only', nargs='+', help='仅处理指定镜头ID；其他图片不变')
    parser.add_argument('--retry-generation', action='store_true', help='确认上次结果后允许再次提交，可能产生费用')
    parser.add_argument('--adopt-existing', action='store_true', help='确认现有图片对应清单后建立版本凭据，不调用生成')
    args = parser.parse_args(argv)
    try:
        raw = read_manifest(args.manifest)
        folder = args.manifest.resolve().parent
        context = read_json(folder/'web-animation-context.json', {})
        job = Path(context['job_dir']) if context.get('job_dir') else folder
        cfg = read_json(job/'config.json', {})
        production_cover = cfg.get('cover_policy_version') == 2 and any(
            row.get('id') == 'cover_portrait' for row in raw.get('images', []) if isinstance(row, dict))
        if (production_cover or raw.get('compiled_by') == 'team-v1') and not args.dry_run:
            import title_review
            if production_cover:
                title_review.approved_version(job, cfg)
            title_review.require(job, cfg)
            if (not (job/'title_approval.json').is_file() or
                    raw.get('approved_title_version') != read_json(job/'title_approval.json')['title_version']):
                raise ManifestError('清单未绑定当前标题，按当前分镜重编提示词')
        config = validate_manifest(raw, args.manifest.resolve())
        report = run_batch(
            config,
            os.environ.get("GPT_IMAGE_2_TOKEN", ""),
            api_base=args.api_base or raw.get('api_base', DEFAULT_API_BASE),
            workers=args.workers,
            download_workers=args.download_workers,
            quality=args.quality,
            dry_run=args.dry_run,
            cache_dir=args.cache_dir,
            only=args.only,
            retry_generation=args.retry_generation,
            adopt_existing=args.adopt_existing,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if all(result["status"] in {"success", "skipped", "dry_run"} for result in report["results"]) else 1
    except (ValueError, OSError) as exc:
        print(f"ERROR: {exc}", file=os.sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
