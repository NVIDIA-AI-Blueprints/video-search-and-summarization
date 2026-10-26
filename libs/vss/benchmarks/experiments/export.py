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

"""Export verified native campaign slots and descriptive groups to immutable CSVs."""

import argparse
import csv
import json
import math
from pathlib import Path

from benchmarks.experiments.native import atomic_json, load_receipt
from benchmarks.media import file_hash
from benchmarks.scorecard import (
    INDEX_FIELDS,
    SUMMARY_FIELDS,
    native_index,
    resource_summary,
)


def safe_cell(value):
    if value is None:
        return ""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if not math.isfinite(value):
            raise ValueError("nonfinite spreadsheet number")
        return value
    text = str(value)
    if text.lstrip().startswith(("=", "+", "-", "@")) or text.startswith(
        ("\t", "\r", "\n")
    ):
        return "'" + text
    return text


def write_csv(path, fields, rows):
    with path.open("x", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: safe_cell(row.get(k)) for k in fields})


def export(root, output):
    receipt, trials = load_receipt(root)
    indexed = {trial.slot.index: trial for trial in trials}
    slots = []
    for row in receipt["rows"]:
        record = {
            k: row[k] for k in ("index", "block", "backend", "task", "policy", "status")
        }
        record.update(
            failure=row.get("failure"),
            trial_ref=row.get("trial_ref"),
            trial_sha256=row.get("trial_sha256"),
            video_sha256=receipt.get("video_sha256"),
            software_sha256=receipt.get("software_sha256"),
            lock_sha256=receipt.get("lock_sha256"),
            campaign_status=receipt["status"],
            threshold_status="unapproved",
            overhead_status="unqualified",
            **native_index(row["backend"]),
            **resource_summary(None, None),
        )
        trial = indexed.get(row["index"])
        if trial is not None:
            record.update(
                resource_summary(
                    trial.resources,
                    trial.gpu_trace,
                    gpu_capacity_bytes=trial.gpu.memory_bytes if trial.gpu else None,
                )
            )
            record.update(
                operation_wall_s=trial.operation_wall_s,
                policy_wall_s=trial.policy_wall_s,
                policy_cpu_s=trial.policy_cpu_s,
                delivered_frames=trial.frames,
                delivered_fps=trial.frames / trial.operation_wall_s,
                encoded_base64_bytes=trial.encoded_base64_bytes,
                maximum_rgb_mae=trial.maximum_rgb_mae,
                sampled_rss_max_bytes=max(
                    s.tree_rss_bytes for s in trial.resources.samples
                )
                if trial.resources is not None
                else None,
                gpu_uuid_sha256=trial.gpu.uuid_sha256 if trial.gpu else None,
                sampled_worker_gpu_memory_max_bytes=max(
                    (
                        s.worker_tree_used_bytes
                        for s in trial.gpu_trace.samples
                        if s.worker_tree_used_bytes is not None
                    ),
                    default=None,
                )
                if trial.gpu_trace is not None
                else None,
                resource_status="sampled_lower_bound"
                if trial.resources is not None
                else "not_collected",
            )
        slots.append(record)
    # Validate first, reserve once, refuse retries even after interrupted export.
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    fields = [
        "index",
        "block",
        "backend",
        "task",
        "policy",
        "status",
        "failure",
        "operation_wall_s",
        "policy_wall_s",
        "policy_cpu_s",
        "delivered_frames",
        "delivered_fps",
        "encoded_base64_bytes",
        "maximum_rgb_mae",
        "sampled_rss_max_bytes",
        "sampled_worker_gpu_memory_max_bytes",
        "resource_status",
        "gpu_uuid_sha256",
        "video_sha256",
        "software_sha256",
        "lock_sha256",
        "campaign_status",
        "threshold_status",
        "overhead_status",
        "trial_ref",
        "trial_sha256",
        *INDEX_FIELDS,
        *SUMMARY_FIELDS,
    ]
    write_csv(output / "trials.csv", fields, slots)
    write_csv(
        output / "groups.csv",
        [
            "task",
            "backend",
            "policy",
            "n",
            "mean_operation_s",
            "median_operation_s",
            "median_policy_s",
            "median_fps",
            "interval",
            "p95",
        ],
        receipt["summary"]["groups"],
    )
    write_csv(
        output / "comparisons.csv",
        [
            "kind",
            "task",
            "backend",
            "policy",
            "baseline",
            "candidate",
            "median_paired_ratio",
            "interval",
        ],
        receipt["summary"]["comparisons"],
    )
    manifest = {
        "schema_version": "native_csv_export.2",
        "receipt_sha256": file_hash(root / "campaign.json"),
        "source_status": receipt["status"],
        "scheduled_slots": len(slots),
        "files": {
            name: file_hash(output / name)
            for name in ("trials.csv", "groups.csv", "comparisons.csv")
        },
        "thresholds_approved": False,
        "notes": [
            "Blank counters are unavailable, not zero.",
            "FPS is delivered host RGB, not physical NVDEC frames or inference.",
            "Initial campaign reports no confidence intervals or p95.",
            "No grouped statistics for an incomplete fixed plan.",
            "Run indexes separate requested settings from observed delivery; "
            "native VLM/chunk fields are not applicable.",
            "Resource means/p90 summarize observer-lifecycle samples, "
            "not latency percentiles or per-model attribution.",
        ],
    }
    atomic_json(output / "manifest.json", manifest)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        manifest = export(args.campaign.resolve(), args.output.resolve())
    except (ValueError, OSError, KeyError) as error:
        parser.error("invalid evidence or existing output: " + type(error).__name__)
    print(json.dumps(manifest))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
