#!/usr/bin/env python3
"""Validate that a Jianying draft is portable and all referenced media exists."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlparse


MEDIA_EXTENSIONS = {
    ".jpg", ".jpeg", ".jpe", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".heic", ".heif",
    ".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v", ".flv", ".wmv", ".ts", ".mpeg", ".mpg", ".3gp",
    ".mp3", ".wav", ".aac", ".m4a", ".flac", ".ogg", ".wma", ".aiff", ".aif", ".opus", ".amr",
}
JSON_FILES = ("draft_content.json", "draft_info.json")


def iter_path_values(value: Any) -> Iterator[tuple[str, str]]:
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "path" and isinstance(child, str):
                yield key, child
            yield from iter_path_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_path_values(child)


def is_url(value: str) -> bool:
    return urlparse(value).scheme.lower() in {"http", "https", "file"}


def is_media_path(value: str) -> bool:
    return bool(value) and not is_url(value) and Path(value).suffix.lower() in MEDIA_EXTENSIONS


def is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def load_json(path: Path, errors: list[str]) -> Any | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        errors.append(f"missing required JSON: {path}")
    except json.JSONDecodeError as exc:
        errors.append(f"invalid JSON: {path}: {exc}")
    except OSError as exc:
        errors.append(f"cannot read {path}: {exc}")
    return None


def validate(draft_dir: Path) -> dict[str, Any]:
    draft_dir = draft_dir.expanduser().resolve()
    errors: list[str] = []
    checked_files: set[str] = set()
    media_paths: set[str] = set()

    if not draft_dir.is_dir():
        errors.append(f"draft directory does not exist: {draft_dir}")
        return {"ok": False, "errors": errors, "media_count": 0, "checked_files": []}

    assets_dir = draft_dir / "assets"
    if not assets_dir.is_dir():
        errors.append(f"missing assets directory: {assets_dir}")

    data_by_file: dict[str, Any] = {}
    for name in JSON_FILES:
        path = draft_dir / name
        data = load_json(path, errors)
        if data is not None:
            if not isinstance(data, dict):
                errors.append(f"JSON root must be an object: {path}")
            else:
                data_by_file[name] = data

    metadata_path = draft_dir / "draft_meta_info.json"
    if metadata_path.is_file():
        metadata = load_json(metadata_path, errors)
        if metadata is not None and not isinstance(metadata, dict):
            errors.append(f"JSON root must be an object: {metadata_path}")

    seen_errors: set[str] = set()
    for data in data_by_file.values():
        for _, raw_path in iter_path_values(data):
            if not is_media_path(raw_path):
                if is_url(raw_path) and Path(raw_path).suffix.lower() in MEDIA_EXTENSIONS:
                    message = f"remote media URL is not portable: {raw_path}"
                    if message not in seen_errors:
                        errors.append(message)
                        seen_errors.add(message)
                continue
            path_value = Path(raw_path).expanduser()
            resolved = path_value if path_value.is_absolute() else draft_dir / path_value
            try:
                resolved = resolved.resolve()
            except OSError:
                resolved = resolved.absolute()
            resolved_string = str(resolved)
            if not is_within(resolved, draft_dir):
                message = f"media path is outside draft directory: {raw_path}"
                if message not in seen_errors:
                    errors.append(message)
                    seen_errors.add(message)
                continue
            checked_files.add(resolved_string)
            media_paths.add(resolved_string)
            if not resolved.is_file():
                message = f"media file is missing: {resolved}"
                if message not in seen_errors:
                    errors.append(message)
                    seen_errors.add(message)

    return {
        "ok": not errors,
        "errors": errors,
        "media_count": len(media_paths),
        "checked_files": sorted(checked_files),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("draft_dir", type=Path, help="draft directory to validate")
    parser.add_argument("--json", action="store_true", dest="as_json", help="emit machine-readable JSON")
    args = parser.parse_args()
    result = validate(args.draft_dir)
    if args.as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif result["ok"]:
        print(
            f"PASS: {args.draft_dir}\n"
            f"media references: {result['media_count']}\n"
            f"media files checked: {len(result['checked_files'])}"
        )
    else:
        print(f"FAIL: {args.draft_dir}")
        for error in result["errors"]:
            print(f"- {error}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
