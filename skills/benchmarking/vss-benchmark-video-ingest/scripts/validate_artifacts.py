#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Validate the finished run's required artifacts without contacting VSS.

Checks structure, declared measurement scope, and accidental ES-token disclosure.
Review summary prose separately: a structural check cannot establish whether an
interpretation claims an unsupported internal bottleneck or server-side timing.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path
import sys
from typing import Any

from artifacts import CORPUS_FIELDS, ERROR_FIELDS, SUMMARY_FIELDS
from httpio import AUTH_ENV_VAR
from upload import UploadRecord


CSV_HEADERS = {
    "ingest_corpus.csv": CORPUS_FIELDS,
    "ingest_requests.csv": UploadRecord.CSV_FIELDS,
    "ingest_summary.csv": SUMMARY_FIELDS,
    "ingest_errors.csv": ERROR_FIELDS,
}
CHART_NAMES = frozenset(
    {
        "ingest-throughput-vs-concurrency.png",
        "ingest-latency-p95-by-concurrency.png",
        "ingest-outcome-by-concurrency.png",
    }
)


def _read_object(path: Path, label: str, errors: list[str]) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        errors.append(f"{label} must exist and contain valid JSON")
        return {}
    if not isinstance(value, dict):
        errors.append(f"{label} must contain a JSON object")
        return {}
    return value


def _contains_token(path: Path, token: str) -> bool:
    """Scan every byte, including matches crossing read boundaries.

    Include JSON's escaped form because records may serialize an echoed token.
    The token is never included in a diagnostic.
    """
    needles = {
        token.encode("utf-8"),
        json.dumps(token, ensure_ascii=True)[1:-1].encode("utf-8"),
        json.dumps(token, ensure_ascii=False)[1:-1].encode("utf-8"),
    }
    overlap = max(len(needle) for needle in needles) - 1
    tail = b""
    with path.open("rb") as handle:
        while chunk := handle.read(64 * 1024):
            data = tail + chunk
            if any(needle in data for needle in needles):
                return True
            tail = data[-overlap:] if overlap else b""
    return False


def validate_artifacts(results_dir: Path) -> list[str]:
    """Return safe diagnostics; an empty list means all artifact checks passed."""
    results_dir = Path(results_dir)
    errors: list[str] = []
    token = os.environ.get(AUTH_ENV_VAR, "").strip()
    if not results_dir.is_dir():
        return ["Results directory does not exist or is not a directory"]

    for name, expected in CSV_HEADERS.items():
        try:
            with (results_dir / "csv" / name).open(newline="", encoding="utf-8") as handle:
                header = next(csv.reader(handle), None)
            if header != list(expected):
                errors.append(f"csv/{name} has missing or unexpected CSV headers")
        except (OSError, UnicodeError, csv.Error):
            errors.append(f"csv/{name} must exist and contain readable CSV headers")

    metadata = _read_object(results_dir / "run-metadata.json", "run-metadata.json", errors)
    summary = _read_object(results_dir / "visualizations" / "summary.json", "visualizations/summary.json", errors)
    if not isinstance(metadata.get("run_id"), str) or not metadata.get("run_id"):
        errors.append("run-metadata.json must contain a nonempty run_id")
    if metadata.get("telemetry_collected") != "none":
        errors.append("run-metadata.json must declare telemetry_collected as none")
    scope = metadata.get("measurement_scope")
    if not isinstance(scope, str) or not scope.strip():
        errors.append("run-metadata.json must declare its client-observed measurement_scope")
    if not isinstance(summary.get("run_id"), str) or not summary.get("run_id"):
        errors.append("visualizations/summary.json must contain a nonempty run_id")
    elif summary["run_id"] != metadata.get("run_id"):
        errors.append("Summary run_id does not match run-metadata.json")
    readiness = summary.get("readiness_stops_clock_at")
    if not isinstance(readiness, str) or not readiness.strip():
        errors.append("visualizations/summary.json must contain readiness_stops_clock_at")
    if summary.get("measurement_scope") != scope:
        errors.append("Summary measurement_scope does not match run-metadata.json")

    compatibility = metadata.get("version_compatibility")
    if not isinstance(compatibility, dict) or compatibility.get("status") != "compatible":
        errors.append("run-metadata.json must record a confirmed version_compatibility check")
    else:
        for field in ("skill_version", "deployed_vss_version", "requires_vss", "version_url", "checked_at_utc"):
            if not isinstance(compatibility.get(field), str) or not compatibility[field].strip():
                errors.append(f"version_compatibility must contain {field}")
        if summary.get("version_compatibility") != compatibility:
            errors.append("Summary version_compatibility does not match run-metadata.json")

    raw_path = results_dir / "raw" / "upload_details.jsonl"
    try:
        records = 0
        with raw_path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                if not isinstance(json.loads(line), dict):
                    raise ValueError("Record must be an object")
                records += 1
        if not records:
            errors.append("raw/upload_details.jsonl must contain upload records")
    except (OSError, UnicodeError, ValueError):
        errors.append("raw/upload_details.jsonl must exist and contain JSON objects, one per line")

    visualization_dir = results_dir / "visualizations"
    try:
        actual_charts = {p.name for p in visualization_dir.glob("*.png") if p.is_file()}
        if actual_charts != CHART_NAMES:
            errors.append("visualizations/ must contain exactly the three required PNG charts")
    except OSError:
        errors.append("Cannot list visualizations/ to validate the required charts")
    for name in sorted(CHART_NAMES | {"summary.md"}):
        try:
            path = visualization_dir / name
            if not path.is_file() or path.stat().st_size == 0:
                errors.append(f"visualizations/{name} must exist and be nonempty")
        except OSError:
            errors.append(f"visualizations/{name} is not readable")

    if token:
        try:
            for path in results_dir.rglob("*"):
                if path.is_file():
                    try:
                        if _contains_token(path, token):
                            errors.append(f"ES auth token found in {path.relative_to(results_dir)}")
                    except OSError:
                        errors.append(f"Cannot scan {path.relative_to(results_dir)} for ES auth token")
        except OSError:
            errors.append("Cannot scan the results directory for ES auth token")
        errors = [message.replace(token, "[REDACTED]") for message in errors]
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate finished VSS ingest benchmark artifacts.")
    parser.add_argument("--results-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    errors = validate_artifacts(args.results_dir)
    for error in errors:
        print(f"ERROR  {error}")
    if errors:
        return 2
    print("Artifact validation passed. Review summary interpretations against the client-only measurement scope.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
