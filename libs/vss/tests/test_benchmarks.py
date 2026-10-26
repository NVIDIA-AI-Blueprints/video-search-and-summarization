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

"""Offline runner identity, invalid evidence, and immutable baseline checks."""

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from benchmarks.results import Configuration, RunResult, Trial, compare
from benchmarks.run import worker


def test_worker_correctness_and_rejects_tampered_evidence() -> None:
    trial = asyncio.run(worker(Configuration(chunks=3, warmups=0)))
    assert trial.chunks == trial.events == 3
    for update in (
        {"latency_s": float("nan")},
        {"collection_complete": False},
        {"events": 4},
        {"observations": []},
        {"latency_s": trial.latency_s * 2},
        {
            "observations": [
                o
                for o in trial.observations
                if o.name
                in {
                    "summarization.total",
                    "summarization.chunks",
                    "summarization.events",
                }
            ]
        },
        {"rss_scope": "unsupported", "rss_peak_bytes": 1},
        {
            "observations": [
                o.model_copy(update={"run_id": "0" * 32}) if i == 0 else o
                for i, o in enumerate(trial.observations)
            ]
        },
    ):
        data = trial.model_dump() | update
        with pytest.raises(ValidationError):
            Trial.model_validate(data)


def test_cli_isolated_trials_identity_and_baseline_preservation(tmp_path: Path) -> None:
    baseline, candidate = tmp_path / "baseline.json", tmp_path / "candidate.json"
    command = [
        sys.executable,
        "-m",
        "benchmarks.run",
        "--chunks",
        "2",
        "--repetitions",
        "2",
        "--warmups",
        "0",
    ]
    first = subprocess.run(command + ["--output", str(baseline)], capture_output=True)
    assert first.returncode == 0, first.stderr.decode()
    original = baseline.read_bytes()
    result = RunResult.model_validate_json(original)
    assert not result.acceptance_eligible
    assert len({t.process_id for t in result.trials}) == 2
    assert all(t.process_id != os.getpid() for t in result.trials)
    second = subprocess.run(
        command + ["--output", str(candidate), "--compare", str(baseline)],
        capture_output=True,
    )
    assert second.returncode == (
        3 if result.environment.cpu_model_sha256 is None else 0
    ), second.stderr.decode()
    assert baseline.read_bytes() == original
    compared = RunResult.model_validate_json(candidate.read_bytes())
    assert compared.comparison is not None
    assert compared.comparison.status == (
        "incompatible" if result.environment.cpu_model_sha256 is None else "descriptive"
    )
    known_environment = compared.environment.model_copy(
        update={"cpu_model_sha256": "1" * 64}
    )
    known_candidate = compared.model_copy(update={"environment": known_environment})
    known_baseline = result.model_copy(update={"environment": known_environment})
    assert compare(known_candidate, known_baseline).status == "descriptive"
    mismatch = compared.model_copy(
        update={"configuration": Configuration(chunks=2, repetitions=2, warmups=1)}
    )
    assert compare(mismatch, result).status == "incompatible"
    incomplete = compared.model_copy(update={"status": "incomplete"})
    assert compare(incomplete, result).status == "invalid"
    no_hardware = compared.model_copy(
        update={
            "environment": compared.environment.model_copy(
                update={"cpu_model_sha256": None}
            )
        }
    )
    assert compare(no_hardware, result).reason == "hardware_identity_unavailable"
    other_hardware = compared.model_copy(
        update={
            "environment": compared.environment.model_copy(
                update={"cpu_model_sha256": "0" * 64}
            )
        }
    )
    assert compare(other_hardware, result).status == "incompatible"
    conflict = subprocess.run(
        command + ["--output", str(baseline), "--compare", str(baseline)],
        capture_output=True,
    )
    assert conflict.returncode == 2 and baseline.read_bytes() == original
    assert "offline-corpus-v1" not in original.decode()
    assert "Offline fixture summary" not in original.decode()
    assert json.loads(original)["status"] == "ok"


def test_cli_timeout_retains_incomplete_report(tmp_path: Path) -> None:
    output = tmp_path / "timeout.json"
    child = subprocess.run(
        [
            sys.executable,
            "-m",
            "benchmarks.run",
            "--timeout",
            "0.000001",
            "--output",
            str(output),
        ],
        capture_output=True,
    )
    assert child.returncode == 3
    result = RunResult.model_validate_json(output.read_bytes())
    assert result.status == "incomplete" and result.failure == "timeout"
    assert not result.trials
