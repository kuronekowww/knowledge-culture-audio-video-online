#!/usr/bin/env python3
"""Create a portable Jianying draft copy with destination-local media paths."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlparse


MEDIA_EXTENSIONS = {
    ".jpg", ".jpeg", ".jpe", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".heic", ".heif",
    ".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v", ".flv", ".wmv", ".ts", ".mpeg", ".mpg", ".3gp",
    ".mp3", ".wav", ".aac", ".m4a", ".flac", ".ogg", ".wma", ".aiff", ".aif", ".opus", ".amr",
}

JSON_FILES = ("draft_content.json", "draft_info.json")


class DraftError(RuntimeError):
    """A user-actionable draft preparation failure."""


def iter_path_values(value: Any) -> Iterator[tuple[dict[str, Any], str, str]]:
    """Yield mutable objects containing string ``path`` properties."""
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "path" and isinstance(child, str):
                yield value, key, child
            yield from iter_path_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_path_values(child)


def is_url(value: str) -> bool:
    return urlparse(value).scheme.lower() in {"http", "https", "file"}


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise DraftError(f"required JSON is missing: {path}") from exc
    except json.JSONDecodeError as exc:
        raise DraftError(f"JSON cannot be parsed: {path}: {exc}") from exc
    except OSError as exc:
        raise DraftError(f"cannot read {path}: {exc}") from exc


def is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def resolve_asset(raw_path: str, source_dir: Path, source_assets: Path) -> Path | None:
    """Resolve a media path to one unambiguous file below source/assets."""
    if not raw_path or is_url(raw_path):
        return None
    extension = Path(raw_path).suffix.lower()
    if extension not in MEDIA_EXTENSIONS:
        return None

    raw_candidate = Path(raw_path).expanduser()
    candidate = raw_candidate if raw_candidate.is_absolute() else source_dir / raw_candidate
    try:
        candidate = candidate.resolve()
    except OSError:
        candidate = candidate.absolute()

    if candidate.is_file() and is_within(candidate, source_assets):
        return candidate

    basename = Path(raw_path).name
    matches = [path for path in source_assets.rglob(basename) if path.is_file()]
    if len(matches) == 1:
        return matches[0].resolve()
    if len(matches) > 1:
        choices = ", ".join(str(path.relative_to(source_assets)) for path in matches)
        raise DraftError(f"ambiguous media basename {basename!r}; candidates: {choices}")
    raise DraftError(f"media file not found in source assets: {raw_path}")


def rewrite_paths(data: Any, source_dir: Path, target_dir: Path) -> tuple[int, list[Path]]:
    source_assets = source_dir / "assets"
    target_assets = target_dir / "assets"
    rewritten = 0
    media_files: set[Path] = set()
    for owner, key, raw_path in iter_path_values(data):
        source_asset = resolve_asset(raw_path, source_dir, source_assets)
        if source_asset is None:
            continue
        relative = source_asset.relative_to(source_assets.resolve())
        target_asset = target_assets / relative
        owner[key] = str(target_asset)
        rewritten += 1
        media_files.add(target_asset)
    return rewritten, sorted(media_files)


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def choose_target_dir(source_dir: Path, target_root: Path, requested_name: str | None) -> Path:
    if requested_name:
        if requested_name in {".", ".."} or Path(requested_name).name != requested_name:
            raise DraftError("--name must be a single directory name, not a path")
        name = requested_name
    else:
        stamp = datetime.now().strftime("%Y%m%d%H%M%S")
        name = f"capcut-mate-{source_dir.name}-{stamp}"
    target_dir = target_root / name
    if target_dir.exists():
        raise DraftError(f"target already exists; refusing to overwrite: {target_dir}")
    return target_dir


def prepare(source_dir: Path, target_root: Path, requested_name: str | None) -> dict[str, Any]:
    source_dir = source_dir.expanduser().resolve()
    target_root = target_root.expanduser().resolve()
    if not source_dir.is_dir():
        raise DraftError(f"source draft directory does not exist: {source_dir}")
    if not (source_dir / "assets").is_dir():
        raise DraftError(f"source assets directory does not exist: {source_dir / 'assets'}")

    source_data = {name: load_json(source_dir / name) for name in JSON_FILES}
    meta_path = source_dir / "draft_meta_info.json"
    metadata = load_json(meta_path) if meta_path.is_file() else None
    target_root.mkdir(parents=True, exist_ok=True)
    target_dir = choose_target_dir(source_dir, target_root, requested_name)

    try:
        shutil.copytree(source_dir, target_dir)
        rewritten = 0
        media_files: set[Path] = set()
        for name, data in source_data.items():
            count, files = rewrite_paths(data, source_dir, target_dir)
            rewritten += count
            media_files.update(files)
            write_json(target_dir / name, data)

        if metadata is not None:
            if not isinstance(metadata, dict):
                raise DraftError(f"draft_meta_info.json must contain an object: {meta_path}")
            metadata["draft_name"] = target_dir.name
            metadata["draft_fold_path"] = str(target_dir)
            metadata["draft_root_path"] = str(target_root)
            write_json(target_dir / "draft_meta_info.json", metadata)

        for media_path in media_files:
            if not media_path.is_file():
                raise DraftError(f"copied media is missing from target: {media_path}")
    except Exception:
        shutil.rmtree(target_dir, ignore_errors=True)
        raise

    return {
        "source_dir": str(source_dir),
        "target_dir": str(target_dir),
        "rewritten_count": rewritten,
        "media_files": [str(path) for path in sorted(media_files)],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_draft", type=Path, help="source draft directory")
    parser.add_argument("target_root", type=Path, help="Jianying com.lveditor.draft root")
    parser.add_argument("--name", help="destination directory name; defaults to a unique name")
    args = parser.parse_args()
    try:
        print(json.dumps(prepare(args.source_draft, args.target_root, args.name), ensure_ascii=False))
        return 0
    except DraftError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"ERROR: file operation failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
