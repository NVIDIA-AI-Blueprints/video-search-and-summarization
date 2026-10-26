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

"""Project saved component-inference ledgers; never read private text captures.

This is a descriptive export of explicitly selected campaigns. Its manifest
seals the files read now, not their authenticity at the original capture time.
"""

import argparse
import hashlib
import json
import math
import re
from pathlib import Path

from benchmarks.experiments.export import write_csv
from benchmarks.scorecard import INDEX_FIELDS, SUMMARY_FIELDS

IDENTITY = ("index", "component", "media", "block", "warmup", "arm", "status")
BASE = (
    "campaign",
    "trial_id",
    *IDENTITY,
    "reason",
    "trial_reason_disagrees",
    "service_role",
    "service_model",
    "service_model_evidence",
    "service_scope",
    "index_evidence",
    "resource_evidence",
    "operation_wall_s",
    "timing_scope",
    "source_sha256",
    "input_sha256",
    "request_sha256",
    "request_template_sha256",
    "finish_reason",
    "prompt_tokens",
    "completion_tokens",
    "max_tokens",
    "temperature",
    "top_p",
    "seed",
    "enable_reasoning",
    "enable_audio",
    "effective_settings_verified",
    "quality_reviewed",
    "acceptance_eligible",
    "trial_sha256",
)
CHUNK_FIELDS = (
    "campaign",
    "trial_id",
    "chunk_id",
    "component",
    "media",
    "arm",
    "warmup",
    "service_model",
    "index",
    "frames",
    "start_ns",
    "end_ns",
    "input_tokens",
    "output_tokens",
    "finish_reason",
    "usage_available",
    "content_sha256",
    "latency_status",
    "resource_attribution_status",
)


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _read(root, name, inputs):
    path = root / name
    if (
        path.is_symlink()
        or not path.is_file()
        or not 0 < path.stat().st_size <= 8_000_000
    ):
        raise ValueError("input path/size")
    raw = path.read_bytes()
    data = json.loads(raw, object_pairs_hook=_object, parse_constant=_invalid_constant)
    if not isinstance(data, dict):
        raise ValueError("input object")
    inputs[name] = hashlib.sha256(raw).hexdigest()
    return data


def _invalid_constant(value):
    raise ValueError("nonfinite JSON constant")


def _scalar(value):
    if value is not None and type(value) not in (str, bool, int, float):
        raise ValueError("non-scalar export field")
    if isinstance(value, str) and len(value) > 4096:
        raise ValueError("export label too long")
    if type(value) in (float, int) and not math.isfinite(value):
        raise ValueError("nonfinite export field")
    return value


def _integer(value, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError("invalid integer")
    return value


def project(label, root):
    """Validate ledger identities, then project only allowlisted scalar fields."""
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", label):
        raise ValueError("campaign label")
    if root.is_symlink() or not root.is_dir():
        raise ValueError("campaign path")
    inputs = {}
    final = _read(root, "final.json", inputs)
    runtime_path = root / "runtime.json"
    runtime = (
        _read(root, "runtime.json", inputs)
        if runtime_path.exists() or runtime_path.is_symlink()
        else {}
    )
    slots = final.get("slots")
    if not isinstance(slots, list) or not 1 <= len(slots) <= 1024:
        raise ValueError("ledger slots")
    requests, chunks, seen = [], [], set()
    for slot in slots:
        index = _integer(slot["index"])
        if index in seen or slot["component"] not in {"vlm", "llm"}:
            raise ValueError("duplicate slot/component")
        seen.add(index)
        if slot["arm"] not in {"direct", "library"} or type(slot["warmup"]) is not bool:
            raise ValueError("slot arm/warmup")
        _integer(slot["block"], -1)
        if slot["status"] not in {"completed_diagnostic", "failed", "not_run"}:
            raise ValueError("slot status")
        role = slot["component"]
        model = runtime.get(f"{role}_model")
        if (model is None and slot["status"] == "completed_diagnostic") or (
            model is not None and (not isinstance(model, str) or not model)
        ):
            raise ValueError("service model")
        row = {
            "campaign": label,
            "trial_id": f"{label}:{index}",
            **{key: slot[key] for key in IDENTITY},
            "reason": slot.get("reason"),
            "service_role": role,
            "service_model": model,
            "service_model_evidence": "runtime_record"
            if model is not None
            else "not_captured",
            "service_scope": "component_client_boundary_not_server_internal_timing",
            "index_evidence": "not_captured_slot",
            "resource_evidence": "not_captured_slot",
            "acceptance_eligible": False,
        }
        reference = slot.get("result")
        if reference is None:
            if slot["status"] == "completed_diagnostic":
                raise ValueError("completed slot without result")
        else:
            if reference != f"trial-{index:03d}.json":
                raise ValueError("trial reference")
            trial = _read(root, reference, inputs)
            if any(trial[key] != slot[key] for key in IDENTITY):
                raise ValueError("trial/ledger identity mismatch")
            wall = trial.get("operation_wall_s")
            if slot["status"] == "completed_diagnostic" and (
                type(wall) not in (int, float)
                or not math.isfinite(wall)
                or wall <= 0
                or wall != slot.get("operation_wall_s")
            ):
                raise ValueError("trial/ledger operation timing")
            complete = slot["status"] == "completed_diagnostic"
            run_index = trial.get("run_index")
            summary = trial.get("resource_summary")
            for value, key, schema in (
                (run_index, "index_schema_version", "benchmark_run_index.1"),
                (summary, "resource_summary_schema_version", "benchmark_resources.1"),
            ):
                if (value is None and complete) or (
                    value is not None
                    and (not isinstance(value, dict) or value.get(key) != schema)
                ):
                    raise ValueError("index/resource schema")
            row["index_evidence"] = (
                "captured" if run_index is not None else "not_captured_failed_trial"
            )
            row["resource_evidence"] = (
                "captured" if summary is not None else "not_captured_failed_trial"
            )
            run_index, summary = run_index or {}, summary or {}
            reported_model = (
                run_index.get("vlm_model") if role == "vlm" else trial.get("llm_model")
            )
            if (complete or reported_model is not None) and reported_model != model:
                raise ValueError("trial/runtime model mismatch")
            for key in (
                "operation_wall_s",
                "timing_scope",
                "source_sha256",
                "input_sha256",
                "request_sha256",
                "request_template_sha256",
                "finish_reason",
                "effective_settings_verified",
                "quality_reviewed",
            ):
                row[key] = trial.get(key)
            row.update({key: run_index.get(key) for key in INDEX_FIELDS})
            row.update({key: summary.get(key) for key in SUMMARY_FIELDS})
            settings, usage = (
                trial.get("requested_settings", {}),
                trial.get("usage", {}),
            )
            for key in (
                "max_tokens",
                "temperature",
                "top_p",
                "seed",
                "enable_reasoning",
                "enable_audio",
            ):
                row[key] = settings.get(key)
            for key in ("prompt_tokens", "completion_tokens"):
                row[key] = usage.get(key)
            row.update(
                reason=slot.get("reason"),
                trial_reason_disagrees=trial.get("reason") != slot.get("reason"),
                trial_sha256=inputs[reference],
                acceptance_eligible=False,
            )
            witness = trial.get("caption_witness")
            if witness is not None:
                if (
                    role != "vlm"
                    or witness.get("contract") != "final_file_chunk_set.v1"
                ):
                    raise ValueError("caption witness schema")
                captured = witness.get("chunks")
                if not isinstance(captured, list) or len(captured) > 64:
                    raise ValueError("caption chunk limit")
                indexes = set()
                for chunk in captured:
                    number = _integer(chunk["index"])
                    if number in indexes:
                        raise ValueError("duplicate caption chunk")
                    indexes.add(number)
                    start, end = _integer(chunk["start_ns"]), _integer(chunk["end_ns"])
                    if end <= start:
                        raise ValueError("caption time range")
                    _integer(chunk["frames"])
                    for key in ("input_tokens", "output_tokens"):
                        if chunk.get(key) is not None:
                            _integer(chunk[key])
                    chunks.append(
                        {
                            **{
                                key: row[key]
                                for key in (
                                    "campaign",
                                    "trial_id",
                                    "component",
                                    "media",
                                    "arm",
                                    "warmup",
                                    "service_model",
                                )
                            },
                            **{
                                key: chunk.get(key)
                                for key in (
                                    "index",
                                    "frames",
                                    "start_ns",
                                    "end_ns",
                                    "input_tokens",
                                    "output_tokens",
                                    "finish_reason",
                                    "usage_available",
                                    "content_sha256",
                                )
                            },
                            "chunk_id": f"{label}:{index}:{number}",
                            "latency_status": (
                                "not_captured_do_not_divide_request_latency"
                            ),
                            "resource_attribution_status": (
                                "not_captured_device_and_host_shared"
                            ),
                        }
                    )
        requests.append(row)
    for row in requests + chunks:
        for value in row.values():
            _scalar(value)
    return requests, chunks, inputs


def export(campaigns, output):
    if not campaigns or len(campaigns) > 8 or len(dict(campaigns)) != len(campaigns):
        raise ValueError("campaign count/duplicates")
    requests, chunks, inputs = [], [], {}
    for label, root in campaigns:
        current, captions, hashes = project(label, root)
        requests.extend(current)
        chunks.extend(captions)
        inputs[label] = hashes
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    write_csv(
        output / "requests.csv", (*BASE, *INDEX_FIELDS, *SUMMARY_FIELDS), requests
    )
    write_csv(output / "chunks.csv", CHUNK_FIELDS, chunks)
    measured = sum(
        r["status"] == "completed_diagnostic" and not r["warmup"] for r in requests
    )
    manifest = {
        "schema_version": "component_inference_export.1",
        "scope": "explicit_selected_campaigns_only",
        "integrity_scope": "hashes_at_export_not_original_capture_authentication",
        "inputs": inputs,
        "slots": len(requests),
        "completed": sum(r["status"] == "completed_diagnostic" for r in requests),
        "measured_completed": measured,
        "warmup_completed": sum(
            r["status"] == "completed_diagnostic" and r["warmup"] for r in requests
        ),
        "not_run": sum(r["status"] == "not_run" for r in requests),
        "failed": sum(r["status"] == "failed" for r in requests),
        "chunks": len(chunks),
        "acceptance_eligible": False,
        "outputs": {
            name: hashlib.sha256((output / name).read_bytes()).hexdigest()
            for name in ("requests.csv", "chunks.csv")
        },
    }
    with (output / "manifest.json").open("x") as stream:
        json.dump(manifest, stream, indent=2, allow_nan=False)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--campaign",
        action="append",
        required=True,
        help="LABEL=directory containing final.json and runtime.json",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    campaigns = []
    for item in args.campaign:
        label, separator, directory = item.partition("=")
        if not separator:
            parser.error("campaign must be LABEL=directory")
        campaigns.append((label, Path(directory)))
    manifest = export(campaigns, args.output)
    print(
        json.dumps(
            {
                key: manifest[key]
                for key in (
                    "slots",
                    "measured_completed",
                    "warmup_completed",
                    "not_run",
                    "failed",
                    "chunks",
                )
            }
        )
    )


if __name__ == "__main__":
    main()
