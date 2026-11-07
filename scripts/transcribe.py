#!/usr/bin/env python3
"""Convert audio safely and transcribe it with a configured whisper.cpp CLI."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import json
from pathlib import Path


FORMAT_FLAGS = {
    "json": "-oj",
    "srt": "-osrt",
    "txt": "-otxt",
    "vtt": "-ovtt",
}


def resolve_program(value: str, label: str) -> str:
    path = shutil.which(value)
    if not path:
        raise RuntimeError(f"required {label} executable not found: {value}")
    return path


def run(command: list[str], label: str) -> None:
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"{label} failed with exit code {result.returncode}: {detail or 'no diagnostic output'}")


def atomic_copy(source: Path, destination: Path) -> None:
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    try:
        with source.open("rb") as input_stream, os.fdopen(descriptor, "wb") as output_stream:
            shutil.copyfileobj(input_stream, output_stream)
            output_stream.flush()
            os.fsync(output_stream.fileno())
        os.replace(temporary, destination)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Convert any ffmpeg-readable audio to temporary 16 kHz mono WAV, then "
            "transcribe it with a user-configured whisper.cpp whisper-cli executable and model."
        )
    )
    parser.add_argument("input", type=Path, help="source audio file (never modified)")
    parser.add_argument("output", type=Path, help="final transcript path, written atomically")
    parser.add_argument("--model", required=True, help="whisper.cpp model path or model argument; no default is embedded")
    parser.add_argument(
        "--whisper-cli", default="whisper-cli",
        help="whisper.cpp executable name or path (default: whisper-cli)",
    )
    parser.add_argument("--format", choices=sorted(FORMAT_FLAGS), default="json", help="transcript format (default: json)")
    parser.add_argument("--language", default="auto", help="language code passed to whisper-cli (default: auto)")
    parser.add_argument("--threads", type=int, help="optional whisper-cli thread count")
    parser.add_argument('--fine-timestamps', action='store_true', help='完整JSON及短token分段，不均分时间')
    parser.add_argument('--prompt', default='', help='经确认的人物/专名提示')
    parser.add_argument(
        "--whisper-arg", action="append", default=[], metavar="ARG",
        help="additional whisper-cli argument; repeat as needed (use --whisper-arg=-x for values beginning with '-')",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if not args.input.is_file():
            raise RuntimeError(f"input audio not found: {args.input}")
        if args.output.resolve() == args.input.resolve():
            raise RuntimeError("output must not overwrite the input audio")
        if args.output.exists():
            raise RuntimeError(f"refusing to overwrite existing output: {args.output}")
        if args.threads is not None and args.threads <= 0:
            raise ValueError("--threads must be positive")

        ffmpeg = resolve_program("ffmpeg", "ffmpeg")
        whisper_cli = resolve_program(args.whisper_cli, "whisper-cli")
        args.output.parent.mkdir(parents=True, exist_ok=True)

        with tempfile.TemporaryDirectory(prefix="novel-audio-transcribe-") as temporary_dir:
            temporary_root = Path(temporary_dir)
            wav_path = temporary_root / "audio-16k-mono.wav"
            run(
                [
                    ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                    "-i", str(args.input), "-map", "0:a:0", "-vn", "-ar", "16000",
                    "-ac", "1", "-c:a", "pcm_s16le", str(wav_path),
                ],
                "ffmpeg conversion",
            )
            if not wav_path.is_file() or wav_path.stat().st_size == 0:
                raise RuntimeError("ffmpeg reported success but produced no usable temporary WAV")

            output_prefix = temporary_root / "transcript"
            command = [
                whisper_cli, "-m", args.model, "-f", str(wav_path), "-of", str(output_prefix),
                FORMAT_FLAGS[args.format], "-l", args.language,
            ]
            if args.threads is not None:
                command.extend(["-t", str(args.threads)])
            if args.fine_timestamps:
                if args.format != 'json':
                    raise ValueError('--fine-timestamps requires --format json')
                command.extend(['-ojf', '-ml', '1'])
            if args.prompt:
                command.extend(['--prompt', args.prompt])
            command.extend(args.whisper_arg)
            run(command, "whisper-cli transcription")

            generated = output_prefix.with_suffix(f".{args.format}")
            if not generated.is_file() or generated.stat().st_size == 0:
                raise RuntimeError(
                    "whisper-cli reported success but the expected transcript is missing or empty: "
                    f"{generated.name}"
                )
            if args.format == 'json':
                payload = json.loads(generated.read_text())
                if not isinstance(payload, dict) or not payload.get('transcription'):
                    raise ValueError('ASR JSON lacks non-empty transcription')
            atomic_copy(generated, args.output)

        print(str(args.output))
        return 0
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
