"""阶段状态持久化，支撑断点续跑与批量。

每个 job 目录下有一个 state.json，记录各阶段状态。阶段状态取值：
  pending / running / done / failed / awaiting_human

`run.py` 启动时跳过 done，从第一个未完成阶段续跑。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# 有序阶段表。run 阶段：全自动；human 阶段：暂停等人工输入。
STAGES = [
    "fetch_transcripts",
    "analyze_highlights",   # agent: 选定剧情，脚本登记身份
    "download_audio",       # script: 按固定清单下载
    "cut_speed",
    "subtitles",            # script + agent: 必要拒审词和短句处理
    "storyboard",           # agent: 写分镜/文案/提示词，脚本绑定时码
    "generate_images",
    "review_images",        # script: 自动技术校验，无人工审图
    "prepare_handoff",
    "publish_handoff",
]

HUMAN_STAGES = {
    "analyze_highlights",
    "download_audio",
    "subtitles",
    "storyboard",
}

VALID_STATUS = {"pending", "running", "done", "failed", "awaiting_human"}


class StateError(RuntimeError):
    """state.json 结构错误。"""


def default_state() -> dict[str, Any]:
    return {
        "schema": 1,
        "album_id": None,
        "stages": {stage: "pending" for stage in STAGES},
        "artifacts": {},
    }


def load_state(job_dir: Path) -> dict[str, Any]:
    path = job_dir / "state.json"
    if not path.exists():
        return default_state()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise StateError(f"state.json 无法解析: {path}: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("stages"), dict):
        raise StateError(f"state.json 结构无效: {path}")
    return data


def save_state(job_dir: Path, state: dict[str, Any]) -> Path:
    job_dir.mkdir(parents=True, exist_ok=True)
    path = job_dir / "state.json"
    payload = json.dumps(state, ensure_ascii=False, indent=2) + "\n"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(path)
    return path


def set_stage(state: dict[str, Any], stage: str, status: str) -> None:
    if stage not in STAGES:
        raise StateError(f"未知阶段: {stage}")
    if status not in VALID_STATUS:
        raise StateError(f"未知状态: {status}")
    state["stages"][stage] = status


def first_pending(state: dict[str, Any]) -> str | None:
    """返回第一个非 done 的阶段，作为续跑起点；全部 done 返回 None。"""
    for stage in STAGES:
        if state["stages"].get(stage) != "done":
            return stage
    return None


def stages_downstream(stage: str) -> list[str]:
    """返回某阶段及其之后的所有阶段，用于 --force-stage 时重置。"""
    idx = STAGES.index(stage)
    return STAGES[idx:]
