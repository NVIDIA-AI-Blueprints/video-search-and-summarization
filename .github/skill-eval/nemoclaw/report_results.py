#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Create and aggregate deterministic NemoClaw skill-eval reports.

The workflow uses three subcommands:

``blocked``
    Write a benchmark for a matrix row that was planned as unsupported.
``verdict``
    Bind one row's benchmark and GitHub step outcome into ``verdict.json``.
``aggregate``
    Reconcile every planned matrix row against downloaded verdict/benchmark
    artifacts and write one Markdown and one JSON report.

The aggregate command exits non-zero only when at least one planned row is
``MISSING``.  A reported skill ``FAIL`` or coverage ``BLOCKED`` is a complete
result and therefore does not prevent publishing the combined report.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any, Sequence


SCHEMA_VERSION = 1
STATUSES = ("PASS", "FAIL", "BLOCKED", "MISSING")
KNOWN_STEP_OUTCOMES = {"success", "failure", "cancelled", "skipped"}
EVAL_ROW_COMPLETION_MARKER = "<!-- nemoclaw-eval-row-complete -->"
PLANNED_BLOCKED_COMPLETION_MARKER = (
    "<!-- nemoclaw-planned-blocked-row-complete -->"
)
MAX_INPUT_BYTES = 8 * 1024 * 1024
MAX_BENCHMARK_BYTES = 2 * 1024 * 1024
SAFE_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,199}$")
ROW_FIELDS = (
    "slug",
    "name",
    "skill",
    "spec_stem",
    "spec_path",
    "platform",
    "task_limit",
    "kind",
    "reason",
)
IDENTITY_FIELDS = ROW_FIELDS

_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b([a-z0-9_]*(?:api[ _-]?key|access[ _-]?token|refresh[ _-]?token|"
    r"password|passwd|secret|token))\b\s*[:=]\s*"
    r"(?:\"[^\"]*\"|'[^']*'|[^\s|,;]+)"
)
_AUTHORIZATION_RE = re.compile(
    r"(?i)\bauthorization\b\s*[:=]\s*(?:(?:bearer|basic)\s+)?[^\s|,;]+"
)
_TOKEN_RES = (
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{10,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\b(?:sk|nvapi)-[A-Za-z0-9_-]{16,}\b"),
    re.compile(
        r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\."
        r"[A-Za-z0-9_-]{8,}\b"
    ),
)
_STATUS_LINE_RE = re.compile(
    r"^\s*-\s*Status\s*:\s*`?(PASS|FAIL|BLOCKED)`?\s*$",
    re.IGNORECASE,
)
_CELL_STATUS_RE = re.compile(r"^(?:✅\s*|❌\s*|⛔\s*)?(PASS|FAIL|BLOCKED)\b", re.IGNORECASE)


class ReportInputError(ValueError):
    """Raised for malformed planner or artifact input."""


def _redact_secrets(value: str) -> str:
    def redact_assignment(match: re.Match[str]) -> str:
        label = re.sub(r"\s+", "_", match.group(1))
        return f"{label}=<redacted>"

    redacted = _AUTHORIZATION_RE.sub("Authorization=<redacted>", value)
    redacted = _SECRET_ASSIGNMENT_RE.sub(redact_assignment, redacted)
    for pattern in _TOKEN_RES:
        redacted = pattern.sub("<redacted>", redacted)
    return redacted


def _public_text(value: Any, *, limit: int = 500) -> str:
    """Return bounded single-line public text suitable for reports."""

    text = "".join(
        char
        if char in "\n\t" or not unicodedata.category(char).startswith("C")
        else " "
        for char in str(value or "")
    )
    text = _redact_secrets(text)
    text = " ".join(text.split())
    if len(text) > limit:
        text = text[: limit - 3].rstrip() + "..."
    return text


def _markdown_cell(value: Any) -> str:
    text = html.escape(_public_text(value), quote=False)
    text = text.replace("\\", "\\\\")
    for marker in ("|", "`", "[", "]", "*", "_"):
        text = text.replace(marker, f"\\{marker}")
    return text or "—"


def _read_limited(path: Path, *, limit: int) -> bytes:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise ReportInputError(f"cannot read {path.name}") from exc
    if size > limit:
        raise ReportInputError(f"{path.name} exceeds the {limit}-byte report limit")
    try:
        return path.read_bytes()
    except OSError as exc:
        raise ReportInputError(f"cannot read {path.name}") from exc


def _load_json_text(value: str, *, label: str) -> Any:
    if len(value.encode("utf-8")) > MAX_INPUT_BYTES:
        raise ReportInputError(f"{label} exceeds the report input limit")
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise ReportInputError(f"{label} is not valid JSON") from exc


def _load_json_file(path: Path, *, label: str) -> Any:
    raw = _read_limited(path, limit=MAX_INPUT_BYTES)
    try:
        return json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReportInputError(f"{label} is not valid JSON") from exc


def _load_json_argument(
    *,
    json_value: str | None,
    file_value: Path | None,
    label: str,
) -> Any:
    if json_value is not None:
        return _load_json_text(json_value, label=label)
    if file_value is not None:
        return _load_json_file(file_value, label=label)
    raise ReportInputError(f"{label} was not provided")


def _canonical_row(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        raise ReportInputError("matrix row must be a JSON object")
    raw_slug = str(raw.get("slug") or "")
    if not SAFE_SLUG_RE.fullmatch(raw_slug):
        raise ReportInputError("matrix row has an invalid slug")

    row = {field: _public_text(raw.get(field, "")) for field in ROW_FIELDS}
    row["slug"] = raw_slug
    if not row["name"]:
        row["name"] = "/".join(
            part for part in (row["skill"], row["spec_stem"], row["platform"]) if part
        )
    return row


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _write_json(path: Path, value: Any) -> None:
    _write_text(path, json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n")


def write_blocked_benchmark(row: dict[str, str], reason: str, output: Path) -> None:
    public_reason = _public_text(reason, limit=1000) or "No public blocker reason was provided."
    spec = row["spec_path"] or row["spec_stem"]
    report = "\n".join(
        (
            "# Skills Eval Benchmark - NemoClaw",
            "",
            "## Planned coverage blocker",
            "",
            "| Skill | Spec | Platform | Status | Reason |",
            "|---|---|---|---|---|",
            (
                f"| {_markdown_cell(row['skill'])} | {_markdown_cell(spec)} | "
                f"{_markdown_cell(row['platform'])} | BLOCKED | "
                f"{_markdown_cell(public_reason)} |"
            ),
            "",
            "- Status: `BLOCKED`",
            "- This row was planned as unsupported; no live skill trial ran.",
            "",
            PLANNED_BLOCKED_COMPLETION_MARKER,
            "",
        )
    )
    _write_text(output, report)


def _benchmark_markers(text: str) -> set[str]:
    markers: set[str] = set()
    for line in text.splitlines():
        status_line = _STATUS_LINE_RE.match(line)
        if status_line:
            markers.add(status_line.group(1).upper())
        if not line.lstrip().startswith("|"):
            continue
        cells = line.strip().strip("|").split("|")
        for cell in cells:
            candidate = cell.strip().strip("`").strip()
            cell_status = _CELL_STATUS_RE.match(candidate)
            if cell_status:
                markers.add(cell_status.group(1).upper())
            elif candidate.startswith("✅"):
                markers.add("PASS")
            elif candidate.startswith(("❌", "⛔")):
                markers.add("FAIL")
    return markers


def _benchmark_data(path: Path) -> tuple[bytes | None, str | None]:
    if not path.is_file():
        return None, None
    try:
        raw = _read_limited(path, limit=MAX_BENCHMARK_BYTES)
        text = raw.decode("utf-8")
    except (ReportInputError, UnicodeDecodeError):
        return None, None
    if not text.strip():
        return None, None
    return raw, text


def classify_verdict(
    *,
    benchmark_text: str | None,
    step_outcome: str,
    row_kind: str,
) -> tuple[str, str]:
    outcome = step_outcome.strip().lower()
    if not benchmark_text:
        return "MISSING", "benchmark is missing or unreadable"

    kind = row_kind.strip().lower()
    markers = _benchmark_markers(benchmark_text)
    if kind == "blocked":
        if outcome != "success":
            detail = outcome if outcome in KNOWN_STEP_OUTCOMES else "unknown"
            return "MISSING", f"planned-blocked reporting step was {detail}"
        if not benchmark_text.rstrip().endswith(PLANNED_BLOCKED_COMPLETION_MARKER):
            return "MISSING", "planned-blocked benchmark is incomplete"
        if "BLOCKED" in markers:
            return "BLOCKED", "benchmark reports blocked coverage"
        return "MISSING", "planned-blocked benchmark has no BLOCKED result"

    if kind != "eval":
        return "MISSING", "matrix row kind is not reportable"
    if outcome not in KNOWN_STEP_OUTCOMES:
        return "MISSING", "evaluation step outcome is missing or unknown"
    if outcome in {"cancelled", "skipped"}:
        return "MISSING", f"evaluation step was {outcome}"
    if not benchmark_text.rstrip().endswith(EVAL_ROW_COMPLETION_MARKER):
        return "MISSING", "evaluation benchmark did not reach row completion"
    if "FAIL" in markers:
        return "FAIL", "benchmark reports one or more failed scenarios"
    if "BLOCKED" in markers:
        return "BLOCKED", "benchmark reports blocked coverage"
    if outcome == "failure":
        return "FAIL", "evaluation step failed"
    if "PASS" in markers:
        return "PASS", "benchmark reports all captured scenarios passed"
    return "MISSING", "benchmark contains no recognized result status"


def create_verdict(
    *,
    row: dict[str, str],
    step_outcome: str,
    benchmark_path: Path,
    output: Path,
) -> dict[str, Any]:
    raw_benchmark, benchmark_text = _benchmark_data(benchmark_path)
    status, reason = classify_verdict(
        benchmark_text=benchmark_text,
        step_outcome=step_outcome,
        row_kind=row["kind"],
    )
    if status == "BLOCKED" and row["reason"]:
        reason = row["reason"]
    benchmark = {
        "filename": _public_text(benchmark_path.name, limit=120) or "benchmark.md",
        "present": raw_benchmark is not None,
        "sha256": (
            hashlib.sha256(raw_benchmark).hexdigest() if raw_benchmark is not None else None
        ),
        "size_bytes": len(raw_benchmark) if raw_benchmark is not None else 0,
    }
    verdict: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "reason": reason,
        "step_outcome": step_outcome.strip().lower() or "unknown",
        "row": row,
        "benchmark": benchmark,
    }
    _write_json(output, verdict)
    return verdict


def _planned_rows(matrix: Any) -> list[dict[str, str]]:
    if not isinstance(matrix, dict) or not isinstance(matrix.get("include"), list):
        raise ReportInputError("matrix JSON must contain an include array")
    rows = [_canonical_row(raw) for raw in matrix["include"]]
    if not rows:
        raise ReportInputError("matrix include array must contain at least one row")
    duplicates = sorted(
        slug
        for slug, count in Counter(row["slug"] for row in rows).items()
        if count > 1
    )
    if duplicates:
        raise ReportInputError("matrix contains duplicate row slugs")
    return rows


def _same_row(expected: dict[str, str], actual: dict[str, str]) -> bool:
    return all(expected[field] == actual[field] for field in IDENTITY_FIELDS)


def _load_artifact_candidate(path: Path) -> tuple[str | None, dict[str, Any] | None, str | None]:
    try:
        raw = _load_json_file(path, label="verdict artifact")
    except ReportInputError:
        return None, None, "verdict JSON is unreadable"
    if not isinstance(raw, dict):
        return None, None, "verdict JSON is not an object"

    raw_row = raw.get("row")
    raw_slug = str(raw_row.get("slug") or "") if isinstance(raw_row, dict) else ""
    slug = raw_slug if SAFE_SLUG_RE.fullmatch(raw_slug) else None
    try:
        row = _canonical_row(raw_row)
    except ReportInputError:
        return slug, None, "verdict row metadata is invalid"
    if raw.get("schema_version") != SCHEMA_VERSION or raw.get("status") not in STATUSES:
        return row["slug"], None, "verdict schema or status is invalid"

    step_outcome = str(raw.get("step_outcome") or "unknown").strip().lower()
    benchmark_meta = raw.get("benchmark")
    if not isinstance(benchmark_meta, dict):
        return row["slug"], None, "verdict benchmark metadata is invalid"
    filename = str(benchmark_meta.get("filename") or "")
    if not filename or Path(filename).name != filename:
        return row["slug"], None, "verdict benchmark filename is invalid"
    benchmark_path = path.parent / filename
    raw_benchmark, benchmark_text = _benchmark_data(benchmark_path)
    expected_hash = benchmark_meta.get("sha256")
    if raw_benchmark is None or not isinstance(expected_hash, str):
        return row["slug"], None, "benchmark artifact is missing"
    if hashlib.sha256(raw_benchmark).hexdigest() != expected_hash:
        return row["slug"], None, "benchmark artifact hash does not match verdict"

    derived_status, derived_reason = classify_verdict(
        benchmark_text=benchmark_text,
        step_outcome=step_outcome,
        row_kind=row["kind"],
    )
    if raw.get("status") != derived_status:
        return row["slug"], None, "verdict status does not match benchmark"
    if derived_status == "BLOCKED" and row["reason"]:
        derived_reason = row["reason"]
    candidate = {
        "status": derived_status,
        "reason": derived_reason,
        "step_outcome": step_outcome,
        "row": row,
        "benchmark": {
            "filename": filename,
            "sha256": expected_hash,
            "size_bytes": len(raw_benchmark),
        },
    }
    return row["slug"], candidate, None


def _missing_result(row: dict[str, str], reason: str) -> dict[str, Any]:
    return {
        "status": "MISSING",
        "reason": reason,
        "step_outcome": "unknown",
        "row": row,
        "benchmark": None,
    }


def _aggregate_markdown(rows: Sequence[dict[str, Any]], counts: dict[str, int]) -> str:
    lines = [
        "# Skills Eval Benchmark - NemoClaw aggregate",
        "",
        f"Planned rows: {len(rows)}",
        "",
        "| Skill | Spec | Platform | Status | Step outcome | Detail |",
        "|---|---|---|---|---|---|",
    ]
    for result in rows:
        row = result["row"]
        spec = row["spec_path"] or row["spec_stem"]
        lines.append(
            f"| {_markdown_cell(row['skill'])} | {_markdown_cell(spec)} | "
            f"{_markdown_cell(row['platform'])} | {result['status']} | "
            f"{_markdown_cell(result['step_outcome'])} | "
            f"{_markdown_cell(result['reason'])} |"
        )
    lines.extend(
        (
            "",
            "## Totals",
            "",
            "| PASS | FAIL | BLOCKED | MISSING |",
            "|---:|---:|---:|---:|",
            (
                f"| {counts['PASS']} | {counts['FAIL']} | {counts['BLOCKED']} | "
                f"{counts['MISSING']} |"
            ),
            "",
            (
                "The report is complete."
                if counts["MISSING"] == 0
                else "The report is incomplete because one or more planned rows are MISSING."
            ),
            "",
        )
    )
    return "\n".join(lines)


def aggregate_results(
    *,
    matrix: Any,
    artifacts_root: Path,
    markdown_output: Path,
    json_output: Path,
) -> tuple[dict[str, Any], int]:
    planned = _planned_rows(matrix)
    candidates: dict[str, list[tuple[dict[str, Any] | None, str | None]]] = {}
    if artifacts_root.is_dir():
        for verdict_path in sorted(artifacts_root.rglob("verdict.json")):
            slug, candidate, error = _load_artifact_candidate(verdict_path)
            if slug:
                candidates.setdefault(slug, []).append((candidate, error))

    results: list[dict[str, Any]] = []
    planned_slugs = {row["slug"] for row in planned}
    for row in planned:
        found = candidates.get(row["slug"], [])
        if not found:
            results.append(_missing_result(row, "no verdict artifact was found"))
            continue
        if len(found) != 1:
            results.append(_missing_result(row, "multiple verdict artifacts were found"))
            continue
        candidate, error = found[0]
        if candidate is None:
            results.append(_missing_result(row, error or "verdict artifact is invalid"))
            continue
        if not _same_row(row, candidate["row"]):
            results.append(_missing_result(row, "verdict row metadata does not match the plan"))
            continue
        results.append(candidate)

    counter = Counter(result["status"] for result in results)
    counts = {status: counter.get(status, 0) for status in STATUSES}
    unexpected = sorted(slug for slug in candidates if slug not in planned_slugs)
    combined: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "report_status": "INCOMPLETE" if counts["MISSING"] else "COMPLETE",
        "counts": counts,
        "planned_rows": len(planned),
        "unexpected_verdict_slugs": unexpected,
        "rows": results,
    }
    _write_text(markdown_output, _aggregate_markdown(results, counts))
    _write_json(json_output, combined)
    return combined, 1 if counts["MISSING"] else 0


def _add_json_source(parser: argparse.ArgumentParser, noun: str) -> None:
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(f"--{noun}-json", help=f"Inline {noun} JSON")
    source.add_argument(f"--{noun}-file", type=Path, help=f"Path to {noun} JSON")


def _row_from_args(args: argparse.Namespace) -> dict[str, str]:
    return _canonical_row(
        _load_json_argument(
            json_value=args.row_json,
            file_value=args.row_file,
            label="row JSON",
        )
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    blocked = subparsers.add_parser("blocked", help="Write a planned BLOCKED benchmark")
    _add_json_source(blocked, "row")
    blocked.add_argument("--reason", required=True, help="Public planned-blocker reason")
    blocked.add_argument("--benchmark-out", type=Path, required=True)

    verdict = subparsers.add_parser("verdict", help="Create one row verdict.json")
    _add_json_source(verdict, "row")
    verdict.add_argument("--step-outcome", required=True)
    verdict.add_argument("--benchmark", type=Path, required=True)
    verdict.add_argument("--output", type=Path, required=True)

    aggregate = subparsers.add_parser("aggregate", help="Aggregate all planned row verdicts")
    _add_json_source(aggregate, "matrix")
    aggregate.add_argument("--artifacts-root", type=Path, required=True)
    aggregate.add_argument("--markdown-out", type=Path, required=True)
    aggregate.add_argument("--json-out", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "blocked":
            write_blocked_benchmark(_row_from_args(args), args.reason, args.benchmark_out)
            print("status=BLOCKED")
            return 0
        if args.command == "verdict":
            verdict = create_verdict(
                row=_row_from_args(args),
                step_outcome=args.step_outcome,
                benchmark_path=args.benchmark,
                output=args.output,
            )
            print(f"status={verdict['status']}")
            return 0

        matrix = _load_json_argument(
            json_value=args.matrix_json,
            file_value=args.matrix_file,
            label="matrix JSON",
        )
        combined, return_code = aggregate_results(
            matrix=matrix,
            artifacts_root=args.artifacts_root,
            markdown_output=args.markdown_out,
            json_output=args.json_out,
        )
        print(f"report_status={combined['report_status']}")
        return return_code
    except ReportInputError as exc:
        print(f"report input error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
