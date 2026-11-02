# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""The review workbook: export the sampled records to xlsx, import people's decisions, apply them.

One row per record. A reviewer picks ``accept``, ``edit`` or ``reject`` (a list validation), writes the corrected
JSON for an edit, and signs the row with their name. The hidden ``payload_sha256`` column ties each decision to the
payload the reviewer saw, so a decision on a since-changed record is refused when applied. The workbook and the
decisions file quote reference text, so both are written only to private paths
(:func:`evalv2.core.private.ensure_private`).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from evalv2.core.hashing import canonical_json, sha256_file
from evalv2.core.private import ensure_private
from evalv2.datasets.records import DECISIONS, HumanDecision, RecordError, apply_human_decision, payload_sha256
from evalv2.review.sampler import ReviewSample

SHEET = "Review"
ABOUT = "About"
COLUMNS = ("record_id", "kind", "clip", "tier", "flags", "summary", "proposal", "link", "decision", "edited_value",
           "reviewer", "decided_at", "note", "payload_sha256")
REVIEWER_COLUMNS = frozenset({"decision", "edited_value", "reviewer", "decided_at", "note"})
WIDTHS = {"record_id": 30, "summary": 60, "proposal": 60, "edited_value": 50, "note": 40, "link": 30}
INSTRUCTIONS = (
    "For each row choose a decision: accept (the proposal is right), edit (write the corrected JSON in "
    "edited_value, starting from the proposal), or reject (it is wrong and cannot be fixed here). Write your name "
    "in reviewer. decided_at is optional (ISO time); the import time is used otherwise. Leave decision empty to "
    "skip a row. Never edit record_id or payload_sha256. This workbook quotes ground-truth-derived text: keep it "
    "local and never commit it."
)


class ReviewImportError(ValueError):
    """A review workbook with rows that cannot be imported; every problem is listed."""


def default_summary(record: Mapping[str, Any]) -> str:
    return json.dumps(record["payload"], ensure_ascii=False, sort_keys=True)[:500]


def export_workbook(records: Sequence[Mapping[str, Any]], path: Path, *, sample: ReviewSample | None = None,
                    summarize: Callable[[Mapping[str, Any]], str] = default_summary,
                    links: Mapping[str, str] | None = None, meta: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Write the review workbook for ``records`` (only the sampled ones when ``sample`` is given)."""
    ensure_private(path)
    chosen = set(sample.ids) if sample is not None else None
    rows = [r for r in records if chosen is None or r["record_id"] in chosen]
    missing = sorted(chosen - {r["record_id"] for r in rows}) if chosen is not None else []
    if missing:
        raise RecordError(f"sampled records not found: {missing[:10]}")
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = SHEET
    sheet.append(list(COLUMNS))
    for cell in sheet[1]:
        cell.font = Font(bold=True)
        if cell.value in REVIEWER_COLUMNS:
            cell.fill = PatternFill("solid", fgColor="FFF2CC")
    for record in sorted(rows, key=lambda r: (str(r.get("clip")), r["record_id"])):
        sheet.append([record["record_id"], record["kind"], record.get("clip"), record["tier"],
                      ", ".join(record["flags"]), summarize(record),
                      json.dumps(record["payload"], ensure_ascii=False, indent=1, sort_keys=True),
                      (links or {}).get(record["record_id"], ""), None, None, None, None, None,
                      payload_sha256(record)])
    decision_column = get_column_letter(COLUMNS.index("decision") + 1)
    validation = DataValidation(type="list", formula1=f'"{",".join(DECISIONS)}"', allow_blank=True)
    validation.add(f"{decision_column}2:{decision_column}{max(2, sheet.max_row)}")
    sheet.add_data_validation(validation)
    for index, name in enumerate(COLUMNS, start=1):
        letter = get_column_letter(index)
        sheet.column_dimensions[letter].width = WIDTHS.get(name, 14)
        if name in ("summary", "proposal", "edited_value", "note"):
            for cell in sheet[letter][1:]:
                cell.alignment = Alignment(wrap_text=True, vertical="top")
    sheet.column_dimensions[get_column_letter(COLUMNS.index("payload_sha256") + 1)].hidden = True
    sheet.freeze_panes = "B2"
    about = workbook.create_sheet(ABOUT)
    info = {"instructions": INSTRUCTIONS, "exported_at": datetime.now(UTC).isoformat(), "rows": len(rows),
            **({"sample_sha256": sample.sha256, "sample_params": json.dumps(sample.params)} if sample else {}),
            **{key: (value if isinstance(value, str | int | float) else json.dumps(value))
               for key, value in (meta or {}).items()}}
    for key, value in info.items():
        about.append([key, value])
    about.column_dimensions["A"].width, about.column_dimensions["B"].width = 18, 120
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    workbook.save(path)
    return {"path": str(path), "rows": len(rows), "sha256": sha256_file(Path(path))}


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def read_workbook(path: Path, *, now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> list[HumanDecision]:
    """The decisions in a filled-in review workbook; :class:`ReviewImportError` lists every bad row."""
    source = sha256_file(Path(path))
    sheet = openpyxl.load_workbook(path, data_only=True)[SHEET]
    header = [_text(cell.value) for cell in sheet[1]]
    missing = [name for name in COLUMNS if name not in header]
    if missing:
        raise ReviewImportError(f"{path}: missing columns {missing}")
    column = {name: header.index(name) for name in COLUMNS}
    imported_at = now().isoformat()
    decisions: list[HumanDecision] = []
    problems: list[str] = []
    seen: set[str] = set()
    for number, row in enumerate(sheet.iter_rows(min_row=2, values_only=True), start=2):
        record_id = _text(row[column["record_id"]])
        decision = _text(row[column["decision"]]).lower()
        if not record_id or not decision:
            continue
        if record_id in seen:
            problems.append(f"row {number}: {record_id} decided twice")
            continue
        seen.add(record_id)
        edited: Any = None
        raw_edit = _text(row[column["edited_value"]])
        if raw_edit:
            try:
                edited = json.loads(raw_edit)
            except json.JSONDecodeError as exc:
                problems.append(f"row {number}: {record_id}: edited_value is not JSON ({exc.msg})")
                continue
        decided_at = row[column["decided_at"]]
        decided = decided_at.isoformat() if isinstance(decided_at, datetime) else (_text(decided_at) or imported_at)
        item = HumanDecision(record_id=record_id, decision=decision, reviewer=_text(row[column["reviewer"]]),
                             decided_at=decided, payload_sha256=_text(row[column["payload_sha256"]]),
                             source_sha256=source, edited_payload=edited, note=_text(row[column["note"]]))
        row_problems = item.problems()
        if row_problems:
            problems += [f"row {number}: {p}" for p in row_problems]
            continue
        decisions.append(item)
    if problems:
        raise ReviewImportError(f"{path}: " + "; ".join(problems[:30]))
    return decisions


def write_decisions(decisions: Sequence[HumanDecision], path: Path) -> None:
    """The decisions as canonical JSONL, to a private path (an edit quotes reference text)."""
    ensure_private(path)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(b"".join(canonical_json(d.to_json()) + b"\n" for d in decisions))


def read_decisions(path: Path) -> list[HumanDecision]:
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    return [HumanDecision(**json.loads(line)) for line in lines if line.strip()]


def apply_decisions(records: Sequence[Mapping[str, Any]],
                    decisions: Sequence[HumanDecision]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """New records with each decision applied; refuses unknown ids and stale or invalid decisions (all listed)."""
    by_id = {d.record_id: d for d in decisions}
    known = {r["record_id"] for r in records}
    problems = [f"{record_id}: not in the records" for record_id in sorted(set(by_id) - known)]
    out: list[dict[str, Any]] = []
    for record in records:
        decision = by_id.get(record["record_id"])
        if decision is None:
            out.append(dict(record))
            continue
        try:
            out.append(apply_human_decision(record, decision))
        except RecordError as exc:
            problems.append(str(exc))
    if problems:
        raise RecordError("; ".join(problems[:30]))
    counts = {d: sum(1 for x in decisions if x.decision == d) for d in DECISIONS}
    return out, {"applied": len(decisions), **counts}
