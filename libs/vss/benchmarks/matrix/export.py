# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""Descriptive CSV/XLSX projections from validated per-trial receipts."""

import csv
import json
import math
import statistics
from pathlib import Path

from benchmarks.e2e.config import fingerprint
from benchmarks.matrix.config import PROFILES
from benchmarks.matrix.runner import validate_receipt
from benchmarks.media import file_hash
from benchmarks.scorecard import METRICS, SUMMARY_FIELDS

INDEX = (
    "run_id",
    "trial_id",
    "kind",
    "repetition",
    "stream",
    "batch_id",
    "cell_id",
    "workload",
    "profile",
    "application_type",
    "backend_type",
    "storage_backend",
    "placement",
    "chunk_duration_s",
    "frames_per_chunk",
    "concurrent_streams",
    "status",
    "failure",
    "blocked",
    "vlm_model",
    "vlm_revision",
    "vlm_engine",
    "vlm_precision",
    "llm_model",
    "llm_revision",
    "llm_engine",
    "llm_precision",
    "caption_max_tokens",
    "summary_max_tokens",
    "requested_caption_generation",
    "requested_summary_generation",
    "normal_model_controls",
    "timing_boundary",
    "workflow_to_result_s",
    "caption_count",
    "event_count",
    "llm_input_tokens",
    "llm_output_tokens",
    "llm_finish_reasons",
    "vlm_input_tokens",
    "vlm_output_tokens",
    "vlm_finish_reasons",
    "vlm_usage_scope",
    "frame_delivery",
    "operational_settlement",
    "semantic_quality",
    "qualified",
    "thresholds_approved",
    "source_sha256",
    "software_sha256",
    "workflow_sha256",
    "requested_work_sha256",
    "intent",
    "receipt",
    "output_capture",
)
STAGES = (
    "upload_and_lookup",
    "captions",
    "store_write",
    "store_read",
    "aggregate_and_summary",
    "normal_service",
    "cleanup",
)
FIELDS = INDEX + tuple("stage_" + s + "_s" for s in STAGES) + SUMMARY_FIELDS


def scalar(value):
    return (
        json.dumps(value, sort_keys=True, allow_nan=False)
        if isinstance(value, (dict, list))
        else value
    )


def csv_write(path, rows, fields):
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fields, extrasaction="raise")
        writer.writeheader()
        for row in rows:
            # Prevent spreadsheet formula execution when opening CSV directly.
            values = {k: scalar(row.get(k)) for k in fields}
            writer.writerow(
                {
                    k: "'" + v
                    if isinstance(v, str)
                    and (
                        v.lstrip().startswith(("=", "+", "-", "@"))
                        or v.startswith(("\t", "\r", "\n"))
                    )
                    else v
                    for k, v in values.items()
                }
            )
    path.chmod(0o600)


def tokens(completions, key):
    if not completions or any(
        c.get("usage_available") is not True or type(c.get(key)) is not int
        for c in completions
    ):
        return None
    return sum(c[key] for c in completions)


def trial_row(root, manifest, item):
    recipe = manifest["recipe"]
    row = dict.fromkeys(FIELDS)
    row.update({k: item[k] for k in INDEX if k in item})
    row.update(
        run_id=manifest["run_id"],
        qualified=False,
        thresholds_approved=False,
        normal_model_controls="unverified_preconfigured_service"
        if item["profile"] == "normal-es"
        else "not_applicable",
        semantic_quality="not_reviewed",
        frame_delivery="not_verified",
        software_sha256=manifest["software_sha256"],
    )
    row.update(
        zip(
            ("application_type", "backend_type", "storage_backend"),
            PROFILES[item["profile"]],
            strict=True,
        )
    )
    placement = item["placement"].split("-")
    for role, where in zip(("vlm", "llm"), placement, strict=True):
        provider = recipe[role + "s"].get(where, {})
        row[role + "_model"] = provider.get("model")
        row[role + "_revision"] = provider.get("revision")
        row[role + "_engine"] = provider.get("engine")
        row[role + "_precision"] = provider.get("precision")
    row.update(
        caption_max_tokens=recipe["caption_max_tokens"],
        summary_max_tokens=recipe["summary_max_tokens"],
    )
    if "intent_sha256" in item:
        if file_hash(root / item["intent"]) != item["intent_sha256"]:
            raise ValueError("intent_changed")
        intent = json.loads((root / item["intent"]).read_bytes())
        w = intent["workflow"]
        row.update(
            workflow_sha256=intent["workflow_sha256"],
            source_sha256=w["video_sha256"],
            requested_caption_generation=w["caption_generation"],
            requested_summary_generation=w["summary_generation"],
        )
        row["requested_work_sha256"] = fingerprint(
            {
                k: w[k]
                for k in (
                    "video_sha256",
                    "duration_s",
                    "chunk_duration_s",
                    "frames_per_chunk",
                    "sampling_contract",
                    "scenario",
                    "events",
                    "vlm_model",
                    "llm_model",
                    "caption_prompt",
                    "summary_prompt",
                    "caption_max_tokens",
                    "summary_max_tokens",
                    "caption_generation",
                    "summary_generation",
                )
            }
        )
    if "receipt_sha256" not in item:
        # Reserved/running rows after a crash are incomplete, never inferred successful.
        if row["status"] == "running":
            row["status"] = "interrupted_receipt_unreconciled"
        return row
    if file_hash(root / item["receipt"]) != item["receipt_sha256"]:
        raise ValueError("receipt_changed")
    report = validate_receipt(root, item)
    if report["status"] != item["status"]:
        raise ValueError("receipt_status_changed")
    op = report.get("operation", {})
    for key in (
        "timing_boundary",
        "workflow_to_result_s",
        "caption_count",
        "event_count",
        "operational_settlement",
    ):
        row[key] = op.get(key, report.get(key))
    row["failure"] = report.get("failure") or op.get("failure")
    for stage in STAGES:
        row["stage_" + stage + "_s"] = op.get("stages_s", {}).get(stage)
    for role, field in (
        ("llm", "llm_provider_observation"),
        ("vlm", "frame_provider_observation"),
    ):
        completions = op.get(field, {}).get("completions", [])
        row[role + "_input_tokens"] = tokens(completions, "input_tokens")
        row[role + "_output_tokens"] = tokens(completions, "output_tokens")
        row[role + "_finish_reasons"] = (
            [c.get("finish_reason") for c in completions] if completions else None
        )
    if op.get("frame_provider_observation", {}).get("completions"):
        row["vlm_usage_scope"] = "frame_compatibility_response_not_native_audit"
    elif op.get("caption_witness", {}).get("chunks"):
        chunks = op["caption_witness"]["chunks"]
        for column in ("input_tokens", "output_tokens"):
            row["vlm_" + column] = (
                sum(c[column] for c in chunks)
                if all(type(c.get(column)) is int for c in chunks)
                else None
            )
        row["vlm_finish_reasons"] = [c.get("finish_reason") for c in chunks]
        row["vlm_usage_scope"] = "rtvi_reported_chunks_not_native_audit"
    row.update({k: report.get("resource_summary", {}).get(k) for k in SUMMARY_FIELDS})
    if op.get("private_output_capture"):
        path = root / "trials" / (item["trial_id"] + ".output.json")
        if file_hash(path) != op["private_output_capture"]["sha256"]:
            raise ValueError("output_capture_changed")
        row["output_capture"] = str(path.relative_to(root))
    return row


def summary(rows, matrix):
    result = []
    for cell in matrix:
        measurements = [
            r
            for r in rows
            if r["cell_id"] == cell["cell_id"] and r["kind"] == "measurement"
        ]
        good = [r for r in measurements if r["status"] == "completed_diagnostic"]
        values = [r["workflow_to_result_s"] for r in good]
        result.append(
            {
                k: cell[k]
                for k in (
                    "cell_id",
                    "workload",
                    "profile",
                    "placement",
                    "chunk_duration_s",
                    "frames_per_chunk",
                    "concurrent_streams",
                )
            }
            | dict(
                planned=len(measurements),
                completed=len(good),
                blocked=sum(r["status"] == "blocked" for r in measurements),
                other_incomplete=len(measurements)
                - len(good)
                - sum(r["status"] == "blocked" for r in measurements),
                latency_mean_s=statistics.mean(values) if values else None,
                latency_median_s=statistics.median(values) if values else None,
                latency_p90_s=sorted(values)[math.ceil(0.90 * len(values)) - 1]
                if values
                else None,
                latency_p95_s=sorted(values)[math.ceil(0.95 * len(values)) - 1]
                if values
                else None,
                timing_boundary=good[0]["timing_boundary"] if good else None,
                scope="descriptive_same_cell_same_run_only",
                qualified=False,
                thresholds_approved=False,
            )
        )
    return result


def dictionary():
    definitions = {
        "workflow_to_result_s": (
            "s",
            (
                "RTVI upload-through-result, or local video-through-result; "
                "excludes setup and cleanup. Never compare differing boundaries as"
                " equal work."
            ),
        ),
        "llm_output_tokens": (
            "tokens",
            (
                "Sum of directly witnessed library summary completions. Missing "
                "for unobserved normal-service internal LLM calls."
            ),
        ),
        "requested_work_sha256": (
            "sha256",
            (
                "Declared media/model/prompt/sampling/generation identity; "
                "equality does not verify service-effective input."
            ),
        ),
        "frames_per_chunk": (
            "frames",
            "Requested sampling count; actual frame delivery remains unverified.",
        ),
        "concurrent_streams": (
            "streams",
            (
                "Target parallel workers. Batches sheet reports observed overlap "
                "and aggregate throughput."
            ),
        ),
        "qualified": (
            "bool",
            (
                "Always false: this harness reports diagnostics; acceptance and "
                "quality require independent review."
            ),
        ),
    }
    for _, _, prefix, unit in METRICS:
        for suffix in ("sample_mean", "sample_p90", "sampled_max"):
            definitions[f"{prefix}_{suffix}_{unit}"] = (
                unit,
                (
                    "Lifecycle sampled resource statistic. GPU/host values are shared,"
                    " not per-model attribution; p90 is not request-latency p90. "
                    "Unsupported metrics stay blank."
                ),
            )
    return [
        dict(
            field=f,
            unit=definitions.get(f, ("", ""))[0],
            definition=definitions.get(
                f,
                ("", "Receipt-backed index/evidence field; see benchmarks/README.rst."),
            )[1],
        )
        for f in FIELDS
    ]


def metadata_rows(value, prefix=""):
    if isinstance(value, dict):
        for name, child in value.items():
            yield from metadata_rows(child, f"{prefix}.{name}" if prefix else name)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from metadata_rows(child, f"{prefix}[{index}]")
    else:
        yield {"key": prefix, "value": value}


def export(root: Path):
    manifest = json.loads((root / "run.json").read_bytes())
    if (
        manifest.get("schema_version") != "perf_run.1"
        or fingerprint(manifest["recipe"]) != manifest["recipe_sha256"]
    ):
        raise ValueError("run_manifest_identity")
    rows = [trial_row(root, manifest, row) for row in manifest["trials"]]
    summaries = summary(rows, manifest["plan"]["cells"])
    csv_write(root / "trials.csv", rows, FIELDS)
    csv_write(
        root / "summary.csv",
        summaries,
        tuple(summaries[0]) if summaries else ("cell_id",),
    )
    import xlsxwriter

    workbook = xlsxwriter.Workbook(
        root / "results.xlsx",
        {
            "strings_to_formulas": False,
            "strings_to_urls": False,
            "nan_inf_to_errors": False,
        },
    )
    header = workbook.add_format({"bold": True, "bg_color": "#E7F3D4"})
    tables = [
        ("Summary", summaries, tuple(summaries[0]) if summaries else ("cell_id",)),
        ("Trials", rows, FIELDS),
        ("Matrix", manifest["plan"]["cells"], tuple(manifest["plan"]["cells"][0])),
        (
            "Batches",
            manifest["batches"],
            (
                "batch_id",
                "kind",
                "repetition",
                "cell_id",
                "trial_ids",
                "completed",
                "forced_termination",
                "telemetry_scope",
                "actual_overlap_streams",
                "throughput_workflows_per_s",
                "operation_span_s",
            ),
        ),
        ("MetricDictionary", dictionary(), ("field", "unit", "definition")),
        (
            "Run",
            list(
                metadata_rows(
                    {
                        k: v
                        for k, v in manifest.items()
                        if k not in {"trials", "batches"}
                    }
                )
            ),
            ("key", "value"),
        ),
    ]
    try:
        for name, records, fields in tables:
            sheet = workbook.add_worksheet(name)
            sheet.freeze_panes(1, 0)
            sheet.write_row(0, 0, fields, header)
            for i, row in enumerate(records, 1):
                for j, f in enumerate(fields):
                    v = scalar(row.get(f))
                    if isinstance(v, str) and len(v) > 32767:
                        raise ValueError("spreadsheet_cell_limit")
                    sheet.write(i, j, v)
            sheet.set_column(0, len(fields) - 1, 22)
            sheet.autofilter(0, 0, len(records), len(fields) - 1)
    finally:
        workbook.close()
    (root / "results.xlsx").chmod(0o600)
    return rows
