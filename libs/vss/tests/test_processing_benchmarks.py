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

"""Failed trial retention, descendant cleanup and resource-sampling failures."""

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from benchmarks.processing import worker
from benchmarks.processing_results import ProcessingConfiguration, ProcessingResult
from benchmarks.resources import ResourceSampler, ResourceTrace
from benchmarks.supervisor import isolated_trial


def test_missing_native_prerequisite_is_typed_retained_evidence(monkeypatch):
    def missing():
        raise ImportError("unavailable")

    monkeypatch.setattr("benchmarks.processing.preflight", missing)
    trial = asyncio.run(worker(ProcessingConfiguration(warmups=0)))
    assert trial.status == "unsupported" and trial.failure == "prerequisite"
    report = ProcessingResult(
        status="unsupported",
        configuration=ProcessingConfiguration(),
        trials=(trial,),
        failure="worker_failure",
    )
    assert not report.acceptance_eligible and report.trials[0].environment.python


def test_denied_sampler_never_claims_complete_resource_evidence(monkeypatch):
    class Process:
        def memory_info(self):
            return SimpleNamespace(rss=10)

        def children(self, **kwargs):
            raise PermissionError("sandbox denied")

        def io_counters(self):
            raise NotImplementedError

    monkeypatch.setitem(
        sys.modules,
        "psutil",
        SimpleNamespace(Process=lambda pid: Process(), Error=RuntimeError),
    )
    with ResourceSampler(1) as sampler:
        pass
    assert not sampler.trace.complete and not sampler.trace.samples
    assert sampler.trace.io_scope == "unsupported"
    with pytest.raises(ValidationError):
        ResourceTrace.model_validate(sampler.trace.model_dump() | {"complete": True})


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group supervision")
def test_timeout_kills_owned_descendant_ignoring_term(tmp_path: Path):
    marker = tmp_path / "descendant-alive"
    ready = tmp_path / "ready"
    # The descendant ignores TERM and would write after the trial timeout unless
    # the group receives KILL. Do not use process-list availability as an oracle.
    descendant = (
        "import signal,time,pathlib; "
        "signal.signal(signal.SIGTERM,signal.SIG_IGN); "
        f"pathlib.Path({str(ready)!r}).write_text('ready'); time.sleep(1.5); "
        f"pathlib.Path({str(marker)!r}).write_text('leaked')"
    )
    parent = (
        "import subprocess,time,sys; "
        f"subprocess.Popen([sys.executable,'-c',{descendant!r}]); time.sleep(20)"
    )
    with pytest.raises(subprocess.TimeoutExpired):
        isolated_trial([sys.executable, "-c", parent], cwd=tmp_path, timeout=0.8)
    assert ready.exists()
    # A separate short-lived check process makes the late-write oracle reliable.
    subprocess.run([sys.executable, "-c", "import time; time.sleep(1)"], check=True)
    assert not marker.exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group supervision")
def test_cpu_cli_timeout_retains_partial_result(tmp_path: Path, monkeypatch):
    from benchmarks.processing import main

    output = tmp_path / "timeout.json"

    def timed_out(*args, **kwargs):
        raise subprocess.TimeoutExpired("worker", 1)

    monkeypatch.setattr("benchmarks.processing.isolated_trial", timed_out)
    monkeypatch.setattr(sys, "argv", ["processing", "--output", str(output)])
    assert main() == 3
    result = ProcessingResult.model_validate_json(output.read_bytes())
    assert result.status == "incomplete" and result.failure == "timeout"
    assert result.trials == ()
