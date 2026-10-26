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

"""Projection never hides incomplete slots or invents service/chunk timing."""

import csv
import json

import pytest

from benchmarks.experiments.inference_export import export
from benchmarks.scorecard import resource_summary


def campaign(root):
    root.mkdir()
    slot = dict(
        index=0,
        component="vlm",
        media="clip",
        block=0,
        warmup=False,
        arm="direct",
        status="completed_diagnostic",
        reason=None,
        operation_wall_s=2,
        result="trial-000.json",
    )
    trial = {key: value for key, value in slot.items() if key != "result"}
    trial.update(
        reason="not_reached",
        run_index={
            "index_schema_version": "benchmark_run_index.1",
            "vlm_model": "=model",
        },
        resource_summary=resource_summary(None, None),
        requested_settings={"enable_reasoning": False, "max_tokens": 1024},
        effective_settings_verified=False,
        private_prompt="DO NOT EXPORT",
        caption_witness={
            "contract": "final_file_chunk_set.v1",
            "chunks": [
                dict(
                    index=0,
                    frames=8,
                    start_ns=0,
                    end_ns=30_000_000_000,
                    input_tokens=20,
                    output_tokens=10,
                    finish_reason="not_reported",
                )
            ],
        },
    )
    (root / "runtime.json").write_text(
        json.dumps({"vlm_model": "=model", "llm_model": "llm"})
    )
    (root / "trial-000.json").write_text(json.dumps(trial))
    slots = [
        slot,
        dict(
            index=1,
            component="llm",
            media="clip",
            block=1,
            warmup=False,
            arm="library",
            status="not_run",
            reason="budget_admission",
        ),
    ]
    (root / "final.json").write_text(json.dumps({"slots": slots}))
    return trial, slots


def test_explicit_scope_retains_unrun_private_text_excluded_and_no_chunk_latency(
    tmp_path,
):
    root, output = tmp_path / "input", tmp_path / "out"
    campaign(root)
    manifest = export([("demo", root)], output)
    assert manifest["slots"] == 2 and manifest["not_run"] == 1
    assert manifest["measured_completed"] == 1 and manifest["chunks"] == 1
    assert manifest["acceptance_eligible"] is False
    with (output / "requests.csv").open() as stream:
        rows = list(csv.DictReader(stream))
    assert rows[0]["service_model"] == "'=model"
    assert rows[0]["reason"] == "" and rows[0]["trial_reason_disagrees"] == "True"
    assert rows[1]["reason"] == "budget_admission"
    assert rows[0]["enable_reasoning"] == "False"
    with (output / "chunks.csv").open() as stream:
        chunk = next(csv.DictReader(stream))
    assert chunk["chunk_id"] == "demo:0:0"
    assert "not_captured" in chunk["latency_status"]
    assert (
        "operation_wall_s" not in chunk and "device_vram_sample_mean_bytes" not in chunk
    )
    assert all("DO NOT EXPORT" not in file.read_text() for file in output.iterdir())
    with pytest.raises(FileExistsError):
        export([("demo", root)], output)


@pytest.mark.parametrize(
    "mutation", ["path", "identity", "timing", "schema", "duplicate", "nan", "symlink"]
)
def test_invalid_evidence_rejected_before_output_reserved(tmp_path, mutation):
    root, output = tmp_path / "input", tmp_path / "out"
    trial, slots = campaign(root)
    if mutation == "path":
        slots[0]["result"] = "../private.json"
    elif mutation == "identity":
        trial["arm"] = "library"
    elif mutation == "timing":
        trial["operation_wall_s"] = 3
    elif mutation == "schema":
        trial["resource_summary"]["resource_summary_schema_version"] = "other"
    elif mutation == "duplicate":
        slots.append(slots[0])
    elif mutation == "nan":
        trial["operation_wall_s"] = float("nan")
    (root / "final.json").write_text(json.dumps({"slots": slots}))
    path = root / "trial-000.json"
    path.write_text(json.dumps(trial))
    if mutation == "symlink":
        renamed = root / "real.json"
        path.rename(renamed)
        path.symlink_to(renamed)
    with pytest.raises(ValueError):
        export([("demo", root)], output)
    assert not output.exists()


def test_multiple_campaigns_keep_composite_identities(tmp_path):
    campaigns = [(name, tmp_path / name) for name in ("traffic", "warehouse")]
    for _, root in campaigns:
        campaign(root)
    output = tmp_path / "out"
    export(campaigns, output)
    with (output / "chunks.csv").open() as stream:
        assert {row["chunk_id"] for row in csv.DictReader(stream)} == {
            "traffic:0:0",
            "warehouse:0:0",
        }


def test_failure_before_observer_keeps_denominator_and_missing_evidence(tmp_path):
    root, output = tmp_path / "input", tmp_path / "out"
    trial, slots = campaign(root)
    slots[0].update(status="failed", reason="upload_failed")
    trial.update(status="failed", reason="upload_failed")
    for key in (
        "resource_summary",
        "caption_witness",
        "run_index",
        "requested_settings",
    ):
        trial.pop(key)
    (root / "trial-000.json").write_text(json.dumps(trial))
    (root / "final.json").write_text(json.dumps({"slots": slots}))
    manifest = export([("demo", root)], output)
    assert manifest["failed"] == 1 and manifest["measured_completed"] == 0
    with (output / "requests.csv").open() as stream:
        row = next(csv.DictReader(stream))
    assert row["status"] == "failed" and row["reason"] == "upload_failed"
    assert row["resource_evidence"] == "not_captured_failed_trial"
    assert row["device_gpu_sample_mean_pct"] == ""


def test_discovery_failure_does_not_require_runtime_that_was_never_captured(tmp_path):
    root, output = tmp_path / "input", tmp_path / "out"
    _, slots = campaign(root)
    (root / "runtime.json").unlink()
    for slot in slots:
        slot.update(status="not_run", reason="discovery_failed")
        slot.pop("result", None)
    (root / "final.json").write_text(json.dumps({"slots": slots}))
    manifest = export([("demo", root)], output)
    assert manifest["not_run"] == 2 and manifest["completed"] == 0
    with (output / "requests.csv").open() as stream:
        row = next(csv.DictReader(stream))
    assert (
        row["service_model"] == "" and row["service_model_evidence"] == "not_captured"
    )
    assert set(manifest["inputs"]["demo"]) == {"final.json"}
