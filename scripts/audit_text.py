#!/usr/bin/env python3
"""Scan CSV text fields against the supplied reject-word table."""

from __future__ import annotations

import argparse
import csv
import re
import sys
import unicodedata
from pathlib import Path
from typing import Iterable


REPORT_FIELDS = [
    "asset_type",
    "asset_id",
    "field",
    "status",
    "matched_terms",
    "rule_section",
    "action",
    "notes",
]

HARD_SOURCE_CATEGORIES = {
    "涉政敏感",
    "涉军",
    "公序良俗",
    "不良行为",
    "低俗",
    "色情",
    "宗教",
    "封建迷信",
    "涉赌",
    "恐怖、暴力、血腥",
    "危险化学用品",
    "不文明用语",
}


def _normalized(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def _compact(value: str) -> str:
    """Remove separators so inserting spaces/punctuation cannot bypass a hit."""
    normalized = _normalized(value)
    return "".join(char for char in normalized if char.isalnum())


def load_rejected_terms(path: Path) -> list[str]:
    """Load non-empty terms after the supplied table's header row."""
    return [term for term, _ in load_rejected_term_records(path)]


def load_rejected_term_records(path: Path) -> list[tuple[str, str]]:
    """Load (term, category) pairs while carrying categories down grouped rows."""
    terms: list[str] = []
    seen: set[str] = set()
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))

    header_found = False
    term_start = 1
    current_category = ""
    records: list[tuple[str, str]] = []
    for row in rows:
        cleaned = [cell.strip() for cell in row]
        if "拒绝词汇" in cleaned:
            header_found = True
            term_start = cleaned.index('拒绝词汇')
            continue
        if not header_found:
            continue
        if cleaned and cleaned[0]:
            current_category = cleaned[0]
        for cell in cleaned[term_start:]:
            if not cell:
                continue
            key = _normalized(cell)
            if key not in seen:
                seen.add(key)
                terms.append(cell)
                records.append((cell, current_category))

    if not header_found:
        # A small custom table is useful in offline tests and local extensions.
        for row in rows:
            for cell in (cell.strip() for cell in row):
                if cell and _normalized(cell) not in seen:
                    seen.add(_normalized(cell))
                    terms.append(cell)
                    records.append((cell, "自定义词表"))
    if not records:
        raise ValueError(f"拒审词汇表为空: {path}")
    return records


def scan_text(text: str, terms: Iterable[str]) -> list[str]:
    """Return matched terms in first-seen order using strict substring matching."""
    normalized = _normalized(text)
    compact = _compact(text)
    matched: list[str] = []
    for term in terms:
        compact_term = _compact(term)
        if _normalized(term) in normalized or (compact_term and compact_term in compact):
            matched.append(term)
    return matched


def audit_csv(
    input_csv: Path,
    fields: list[str],
    terms: list[str],
    generated: bool,
    term_categories: dict[str, str] | None = None,
) -> list[dict[str, str]]:
    reports: list[dict[str, str]] = []
    with input_csv.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        headers = reader.fieldnames or []
        missing = [field for field in fields if field not in headers]
        if missing:
            raise ValueError(f"输入 CSV 缺少字段: {', '.join(missing)}")
        for row_number, row in enumerate(reader, start=2):
            asset_id = (
                row.get("track_id")
                or row.get("sound_id")
                or row.get("album_id")
                or str(row_number)
            ).strip()
            for field in fields:
                value = (row.get(field) or "").strip()
                if not value:
                    continue
                matched = scan_text(value, terms)
                if not matched:
                    status = "pass"
                    action = ""
                    notes = "未命中拒审词"
                elif field == "transcript" and any(
                    term_categories and term_categories.get(term) in HARD_SOURCE_CATEGORIES
                    for term in matched
                ):
                    status = "reject"
                    action = "淘汰该声音或重新选取不含风险词的自然音频段"
                    notes = "原声音频不得静默改写"
                elif field == "transcript":
                    status = "pass"
                    action = "保留音频，生成字幕时移除或改写该词"
                    notes = "通用/语境词命中，不单独阻断源音频"
                elif generated:
                    status = "rewrite"
                    action = "结合语义校对可见文字，记录依据后重新扫描"
                    notes = "生成文本命中拒审词"
                else:
                    status = "manual_review"
                    action = "人工判断或改写后重新扫描"
                    notes = "未声明为生成字段"
                reports.append(
                    {
                        "asset_type": "text",
                        "asset_id": asset_id,
                        "field": field,
                        "status": status,
                        "matched_terms": "|".join(matched),
                        "rule_section": "各渠道媒体拒审词汇.csv",
                        "action": action,
                        "notes": notes,
                    }
                )
    return reports


def write_report(path: Path, reports: list[dict[str, str]], force: bool) -> None:
    if path.exists() and not force:
        raise ValueError(f"报告文件已存在（如需覆盖请显式加 --force）: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=REPORT_FIELDS)
        writer.writeheader()
        writer.writerows(reports)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-csv", required=True, type=Path)
    parser.add_argument("--fields", required=True, help="逗号分隔的待审字段名")
    parser.add_argument("--terms", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--generated", action="store_true", help="将非 transcript 命中标记为 rewrite")
    parser.add_argument("--force", action="store_true", help="允许覆盖已有报告")
    args = parser.parse_args()

    fields = [field.strip() for field in args.fields.split(",") if field.strip()]
    if not fields:
        raise SystemExit("--fields 至少需要一个字段")
    try:
        term_records = load_rejected_term_records(args.terms.expanduser())
        terms = [term for term, _ in term_records]
        categories = {term: category for term, category in term_records}
        reports = audit_csv(
            args.input_csv.expanduser(), fields, terms, args.generated, categories
        )
        write_report(args.output.expanduser(), reports, args.force)
    except (OSError, ValueError) as exc:
        print(f"审核失败: {exc}", file=sys.stderr)
        return 2

    blocked = [row for row in reports if row["status"] != "pass"]
    print(
        f"已写入审核报告: {args.output}（字段 {len(reports)}，命中 {len(blocked)}）",
        file=sys.stderr,
    )
    return 1 if blocked else 0


if __name__ == "__main__":
    raise SystemExit(main())
