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

"""Opt-in real-media CLI evidence and adversarial artifact validation."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from benchmarks.processing_results import ProcessingResult, ProcessingTrial


@pytest.mark.skipif(
    os.getenv("RUN_VSS_CPU_TESTS") != "1" or os.name != "posix",
    reason="requires opted-in native CPU/POSIX profile",
)
def test_real_cpu_profile_independent_workers_and_rejects_tampering(tmp_path: Path):
    output = tmp_path / "processing.json"
    child = subprocess.run(
        [
            sys.executable,
            "-m",
            "benchmarks.processing",
            "--warmups",
            "0",
            "--repetitions",
            "2",
            "--output",
            str(output),
        ],
        capture_output=True,
        timeout=90,
    )
    assert child.returncode == 0, (child.stdout, child.stderr)
    result = ProcessingResult.model_validate_json(output.read_bytes())
    assert result.status == "ok" and not result.acceptance_eligible
    assert len({t.process_id for t in result.trials}) == 2
    assert all(t.process_id != os.getpid() for t in result.trials)
    trial = result.trials[0]
    assert {f.recipe for f in trial.fixtures} == {"cfr", "vfr", "nonzero_origin"}
    assert trial.resources and trial.resources.samples
    for change in (
        {"decoded_frames": 29},
        {"collection_complete": False},
        {"checks_passed": False},
        {
            "observations": [
                o for o in trial.observations if o.name != "processing.extract"
            ]
        },
        {"resources": trial.resources.model_dump() | {"complete": False}},
        {
            "observations": [
                o.model_copy(update={"status": "error"}) if i == 0 else o
                for i, o in enumerate(trial.observations)
            ]
        },
        {"native_builds": {}},
        {
            "observations": [
                o.model_copy(update={"value": 1000})
                if o.name
                in {
                    "processing.native.produced_frames",
                    "processing.native.delivered_frames",
                }
                else o
                for o in trial.observations
            ]
        },
        {
            "observations": [
                o.model_copy(update={"value": 0})
                if o.name
                in {
                    "processing.native.produced_frames",
                    "processing.native.delivered_frames",
                    "processing.native.sessions",
                }
                else o
                for o in trial.observations
            ]
        },
        {
            "observations": [
                o.model_copy(update={"value": 1})
                if o.name == "processing.native.pending_frames"
                else o
                for o in trial.observations
            ]
        },
    ):
        with pytest.raises(ValidationError):
            ProcessingTrial.model_validate(trial.model_dump() | change)
    mismatched = result.trials[1].model_dump() | {"software_sha256": "0" * 64}
    with pytest.raises(ValidationError):
        ProcessingResult.model_validate(
            result.model_dump() | {"trials": [trial.model_dump(), mismatched]}
        )
