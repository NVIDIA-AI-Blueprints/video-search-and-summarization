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

"""Adversarial statistics, retained artifacts, tool bounds and queue settlement."""

import asyncio
import gc
import gzip
import itertools
import os
import sys

import pytest
from pydantic import ValidationError

from benchmarks import artifacts
from benchmarks.backpressure import StressConfiguration, cycle
from benchmarks.media import MediaError, bounded_tool, file_hash
from benchmarks.statistics import describe
from benchmarks.video import balanced_orders
from vss.observability import collect_vss_metrics, record


def test_median_intervals_exact_ranks_and_small_sample_refusal():
    assert describe([0, 1, 2, 3, 4]).interval is None
    six = describe(list(range(6)))
    assert six.interval == (0, 5) and six.coverage == 62 / 64
    ten = describe(list(range(10)))
    assert ten.interval == (1, 8) and ten.coverage == 1002 / 1024
    assert describe([0] * 10).interval == (0, 0)
    assert describe(list(range(99))).p95 is None
    assert describe(list(range(100))).p95 == 94
    # Enumerate equiprobable independent observations on either side of the
    # population median. Coverage must match the finite binomial construction.
    covered = 0
    for signs in itertools.product([-1, 1], repeat=6):
        interval = describe(list(signs)).interval
        assert interval is not None
        covered += interval[0] <= 0 <= interval[1]
    assert covered == 62
    for invalid in ([], [float("nan")], [True], [float("inf")]):
        with pytest.raises(ValueError):
            describe(invalid)


def test_balanced_order_is_fixed_before_any_trial():
    orders = balanced_orders(10, 1729)
    assert orders == balanced_orders(10, 1729)
    assert orders.count("enabled_first") == orders.count("disabled_first") == 5
    assert len(balanced_orders(9, 1729)) == 9


def observations():
    with collect_vss_metrics() as metrics:
        record("summarization.events", 1, library="summarization")
        record("summarization.events", 2, library="summarization")
    return metrics.observations


def test_trace_hash_count_decompression_and_symlink_gates(tmp_path, monkeypatch):
    records = observations()
    ref = artifacts.write_trace(tmp_path, records)
    assert (
        artifacts.verify_trace(tmp_path, ref, artifacts.aggregate(records)) == records
    )
    with pytest.raises(ValueError):
        artifacts.read_trace(tmp_path, ref.model_copy(update={"records": 1}))
    with pytest.raises(ValueError):
        artifacts.read_trace(tmp_path, ref.model_copy(update={"sha256": "0" * 64}))
    path = tmp_path / ref.name
    path.write_bytes(gzip.compress(b"x" * 8193))
    changed = ref.model_copy(
        update={"sha256": file_hash(path), "bytes": path.stat().st_size, "records": 1}
    )
    with pytest.raises(ValueError, match="limit"):
        artifacts.read_trace(tmp_path, changed)
    monkeypatch.setattr(artifacts, "MAX_RAW_BYTES", 1)
    with pytest.raises(ValueError):
        artifacts.write_trace(tmp_path, records)
    assert not list(tmp_path.rglob("*.partial"))


def test_bundle_publish_rolls_back_and_never_overwrites(tmp_path, monkeypatch):
    from benchmarks.results import Configuration

    output = tmp_path / "result.json"
    stage = tmp_path / "staging"
    stage.mkdir()
    (stage / "data").write_text("retained")

    def failure(*args):
        raise OSError("injected publication failure")

    monkeypatch.setattr(artifacts.os, "link", failure)
    with pytest.raises(OSError):
        artifacts.publish_bundle(output, stage, Configuration())
    assert not output.exists() and not output.with_suffix(".artifacts").exists()
    assert not list(tmp_path.glob("*.lock"))
    output.write_text("immutable baseline")
    stage.mkdir()
    with pytest.raises(FileExistsError):
        artifacts.publish_bundle(output, stage, Configuration())
    assert output.read_text() == "immutable baseline" and stage.exists()


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_external_tool_output_is_bounded_during_execution(stream):
    code = (
        f"import sys,time; sys.{stream}.buffer.write(b'x'*(2*1024**2)); "
        f"sys.{stream}.flush(); time.sleep(20)"
    )
    with pytest.raises(MediaError, match="tool_output_limit"):
        bounded_tool([sys.executable, "-c", code], limit=1024, timeout=5)


@pytest.mark.parametrize("concurrency", [1, 4])
@pytest.mark.parametrize("factor", [1, 5, 20])
async def test_current_queue_stress_settlement_recovery_without_gc(concurrency, factor):
    was_enabled = gc.isenabled()
    gc.disable()
    try:
        evidence = await asyncio.wait_for(cycle(concurrency, factor, 0), timeout=5)
        assert evidence.cleanups == evidence.started == evidence.admitted - 1
        assert (
            evidence.retained_payloads
            == evidence.residual_running
            == evidence.residual_pending
            == 0
        )
    finally:
        if was_enabled:
            gc.enable()


def test_stress_case_matrix_cannot_be_silently_reduced():
    with pytest.raises(ValidationError):
        StressConfiguration(cases=())


def test_statistical_reports_do_not_use_survivors_of_failed_pairs(monkeypatch):
    from benchmarks.report import summarize
    from benchmarks.video_results import VideoConfiguration, VideoResult

    result = VideoResult(
        status="incomplete",
        failure="worker_failure",
        configuration=VideoConfiguration(recipe="tiny"),
        trials=(),
    )
    assert summarize(result)["statistics"] is None


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group supervision")
def test_worker_protocol_bounds_output_before_exit(tmp_path, stream, monkeypatch):
    import subprocess

    from benchmarks.supervisor import TrialCleanupError, isolated_trial

    launched = []
    original = subprocess.Popen

    def tracked(*args, **kwargs):
        child = original(*args, **kwargs)
        launched.append(child)
        return child

    monkeypatch.setattr(subprocess, "Popen", tracked)

    size = 9_000_000 if stream == "stdout" else 2_000_000
    code = (
        f"import sys,time; sys.{stream}.buffer.write(b'x'*{size}); "
        f"sys.{stream}.flush(); time.sleep(20)"
    )
    with pytest.raises((ValueError, TrialCleanupError)) as rejected:
        isolated_trial([sys.executable, "-c", code], cwd=tmp_path, timeout=5)
    context = rejected.value
    reasons = []
    while context is not None:
        reasons.append(str(context))
        context = context.__context__
    assert any("stream limit" in message for message in reasons)
    assert launched and all(child.returncode is not None for child in launched)


def test_scalar_publication_does_not_overwrite_concurrent_evidence(
    tmp_path, monkeypatch
):
    from benchmarks.results import Configuration

    output = tmp_path / "result.json"
    original_link = artifacts.os.link

    def race(source, target):
        target.write_text("concurrent evidence")
        return original_link(source, target)

    monkeypatch.setattr(artifacts.os, "link", race)
    with pytest.raises(FileExistsError):
        artifacts.publish_result(output, Configuration())
    assert output.read_text() == "concurrent evidence"
    assert not list(tmp_path.glob("*.lock"))


def test_inventory_rejects_extra_duplicate_and_symlinked_traces(tmp_path):
    refs = [artifacts.write_trace(tmp_path, observations())]
    artifacts.verify_inventory(tmp_path, refs, complete=True)
    with pytest.raises(ValueError):
        artifacts.verify_inventory(tmp_path, refs * 2, complete=True)
    (tmp_path / "unreferenced.txt").write_text("not allowed")
    with pytest.raises(ValueError):
        artifacts.verify_inventory(tmp_path, refs, complete=True)
    (tmp_path / "unreferenced.txt").unlink()
    ref = refs[0]
    path = tmp_path / ref.name
    original = path.read_bytes()
    path.unlink()
    outside = tmp_path.parent / "outside-trace.gz"
    outside.write_bytes(original)
    try:
        try:
            path.symlink_to(outside)
        except (OSError, NotImplementedError):
            pytest.skip("filesystem cannot create test symlinks")
        with pytest.raises(ValueError):
            artifacts.read_trace(tmp_path, ref)
    finally:
        outside.unlink()


def test_jpeg_header_rejects_missing_truncated_and_bomb_geometry():
    from benchmarks.video import jpeg_dimensions

    valid = b"\xff\xd8\xff\xc0\x00\x08\x08\x00\x60\x00\xa0\x03"
    assert jpeg_dimensions(valid) == (160, 96)
    for data in [b"", b"not jpeg", valid[:9], b"\xff\xd8\xff\xda"]:
        with pytest.raises(ValueError):
            jpeg_dimensions(data)
    bomb = b"\xff\xd8\xff\xc0\x00\x08\x08\xff\xff\xff\xff\x03"
    assert jpeg_dimensions(bomb) == (
        65535,
        65535,
    )  # geometry is checked before imdecode


async def test_soak_boundary_requires_actual_elapsed_time():
    from benchmarks.backpressure import StressResult
    from benchmarks.resources import ResourceSample, ResourceTrace
    from benchmarks.run import environment

    rows = []
    for pair in StressConfiguration().cases:
        row = await cycle(*pair, 0)
        rows.extend([row] * 20)
    trace = ResourceTrace(
        interval_s=1,
        complete=True,
        samples=(
            ResourceSample(
                elapsed_s=0, self_rss_bytes=1, tree_rss_bytes=1, observed_processes=1
            ),
            ResourceSample(
                elapsed_s=1, self_rss_bytes=1, tree_rss_bytes=1, observed_processes=1
            ),
        ),
        self_cpu_time_s=0,
        reaped_children_cpu_time_s=0,
        io_scope="unsupported",
    )
    # Synthetic accounting objects test the exact boundary; they are not actual
    # soak execution evidence and never enter the retained qualification reports.
    data = dict(
        status="ok",
        configuration=StressConfiguration(duration_s=1800),
        environment=environment(),
        software_sha256="1" * 64,
        wall_s=1800,
        checkpoints=tuple(rows),
        resources=trace,
        wall_clock_soak_completed=True,
    )
    assert StressResult(**data).wall_clock_soak_completed
    with pytest.raises(ValidationError):
        StressResult(
            **(data | {"wall_s": 1799.999, "wall_clock_soak_completed": False})
        )
    with pytest.raises(ValidationError):
        StressResult(
            **(
                data
                | {
                    "configuration": StressConfiguration(duration_s=0),
                    "wall_clock_soak_completed": True,
                }
            )
        )
    with pytest.raises(ValidationError):
        StressResult(
            **(data | {"resources": trace.model_copy(update={"complete": False})})
        )
    with pytest.raises(ValidationError):
        StressResult(**(data | {"checkpoints": tuple(rows[:10])}))


def test_resource_sample_plus_one_marks_evidence_incomplete(monkeypatch):
    from types import SimpleNamespace

    from benchmarks.resources import ResourceSampler

    process = SimpleNamespace(
        memory_info=lambda: SimpleNamespace(rss=1),
        children=lambda **kw: [],
        io_counters=lambda: SimpleNamespace(read_bytes=0, write_bytes=0),
    )
    monkeypatch.setitem(
        sys.modules,
        "psutil",
        SimpleNamespace(
            Process=lambda pid: process,
            Error=RuntimeError,
            NoSuchProcess=ProcessLookupError,
        ),
    )
    with ResourceSampler(1) as sampler:
        for _ in range(5999):
            sampler._sample()
    assert len(sampler.trace.samples) == 6000 and not sampler.trace.complete


@pytest.mark.parametrize(
    "change",
    [
        {"best_effort_timestamp": "0"},
        {"best_effort_timestamp": "-1"},
        {"best_effort_timestamp": "bad"},
    ],
)
def test_malformed_pts_rejected_before_rgb_allocation(tmp_path, monkeypatch, change):
    import json

    import benchmarks.media as media

    source = tmp_path / "source.mp4"
    source.write_bytes(b"admitted source")
    calls = []
    document = {
        "streams": [{"time_base": "1/1", "width": 160, "height": 96}],
        "frames": [{"best_effort_timestamp": "0"}, change],
    }

    def tool(command, **kwargs):
        calls.append(command)
        return json.dumps(document).encode()

    monkeypatch.setattr(media, "bounded_tool", tool)
    with pytest.raises((ValueError, MediaError)):
        media.inspect(source, {"ffprobe": "probe", "ffmpeg": "rgb"})
    assert len(calls) == 1


@pytest.mark.parametrize("terminal", ["timeout", "cancelled", "invalid_child_evidence"])
def test_soak_interruption_retains_typed_incomplete_result(
    tmp_path, monkeypatch, terminal
):
    import subprocess

    from benchmarks import backpressure

    output = tmp_path / "interrupted.json"

    def fail(*args, **kwargs):
        # Reservation covers the worker and its checkpoint, preventing a second
        # same-name run from starting while the first is active.
        with pytest.raises(FileExistsError):
            with artifacts.reserve(output):
                pass
        if terminal == "timeout":
            raise subprocess.TimeoutExpired("worker", 1)
        if terminal == "cancelled":
            raise KeyboardInterrupt
        return b"malformed evidence"

    monkeypatch.setattr(backpressure, "isolated_trial", fail)
    monkeypatch.setattr(
        sys, "argv", ["queue", "--duration", "1800", "--output", str(output)]
    )
    assert backpressure.main() == 3
    result = backpressure.StressResult.model_validate_json(output.read_bytes())
    assert result.failure == terminal and result.status == "incomplete"
    assert not result.wall_clock_soak_completed and not result.checkpoints
    assert not result.acceptance_eligible


def test_checkpoint_limit_plus_one_and_zero_success_rejected():
    from benchmarks.backpressure import StressResult
    from benchmarks.run import environment

    base = dict(
        status="incomplete",
        failure="checkpoint_limit",
        configuration=StressConfiguration(),
        environment=environment(),
        software_sha256="1" * 64,
        wall_s=0,
        checkpoints=(),
    )
    assert StressResult(**base).status == "incomplete"
    with pytest.raises(ValidationError):
        StressResult(**(base | {"status": "ok", "failure": None}))
    # Pydantic rejects cardinality before attempting to validate child rows.
    with pytest.raises(ValidationError):
        StressResult(**(base | {"checkpoints": (None,) * 3601}))


def test_registered_candidate_seal_detects_coherent_edits(tmp_path):
    from benchmarks.report import CandidateSeal, load
    from benchmarks.video_results import VideoConfiguration, VideoResult

    path = tmp_path / "result.json"
    root = path.with_suffix(".artifacts")
    root.mkdir()
    result = VideoResult(
        status="incomplete",
        failure="timeout",
        configuration=VideoConfiguration(recipe="tiny"),
        trials=(),
    )
    path.write_text(result.model_dump_json())
    seal = CandidateSeal(
        evidence_sha256=file_hash(path), traces={}, bytes=path.stat().st_size
    )
    (tmp_path / "seal.json").write_text(seal.model_dump_json())
    assert load(path) == result
    path.write_text(
        result.model_copy(update={"failure": "cancelled"}).model_dump_json()
    )
    with pytest.raises(ValueError, match="seal mismatch"):
        load(path)


def test_media_tool_zero_exit_with_error_stderr_cannot_qualify():
    code = (
        "import sys; sys.stdout.write('valid output'); "
        "sys.stderr.write('decoder error')"
    )
    with pytest.raises(MediaError, match="tool_reported_error"):
        bounded_tool([sys.executable, "-c", code], limit=1024, timeout=5)


def test_oracle_container_count_mismatch_rejected_before_rgb(tmp_path, monkeypatch):
    import json

    import benchmarks.media as media

    source = tmp_path / "source.mp4"
    source.write_bytes(b"unchanged source")
    document = {"streams": [{"nb_frames": "3"}], "frames": [{}, {}]}
    calls = []

    def tool(*args, **kwargs):
        calls.append(args)
        return json.dumps(document).encode()

    monkeypatch.setattr(media, "bounded_tool", tool)
    with pytest.raises(MediaError, match="oracle_frame_count"):
        media.inspect(source, {"ffprobe": "probe", "ffmpeg": "rgb"})
    assert len(calls) == 1
