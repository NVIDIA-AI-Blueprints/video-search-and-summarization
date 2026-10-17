# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Focused benchmark contracts; fixtures are never hardware measurements."""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

CORE = Path(__file__).parents[1] / "benchmark/core/vss_perf_benchmark.py"
sys.path.insert(0, str(CORE.parent))
import vss_perf_benchmark as benchmark


def test_fps_formats_keep_instantaneous_values_and_zero():
    logs = "**PERF: FPS 0 (Avg)\n**PERF: 30.00000 (3.00000)\t29.00000 (4.00000)\n"
    assert benchmark.parse_fps(logs) == [[30, 29]]
    logs = "**PERF:\n30.00000 (4.00000)\tsource_id : 0\n0.00000 (4.00000)\nActive sources : 1\nPERF(0): 29.00000 (4.00000)\n"
    assert benchmark.parse_fps(logs) == [[30, 0], [29]]
    assert not benchmark.parse_fps("**PERF: FPS 0 (Avg)\nhealthy")


def test_empty_success_is_distinct_from_typed_cli_failure(monkeypatch):
    assert benchmark.listing_count({"count": 0, "sensors": []}, "sensors") == 0
    with pytest.raises(ValueError):
        benchmark.listing_count({"count": 1, "sensors": []}, "sensors")
    calls = []

    def failed(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=4, stderr="service missing", stdout="")

    monkeypatch.setattr(benchmark.subprocess, "run", failed)
    with pytest.raises(RuntimeError, match="exited 4"):
        benchmark.run(["vss", "vios", "list"])
    assert len(calls) == 1


def test_real_probe_loop_keeps_empty_incidents_and_fps(monkeypatch):
    monitor = SimpleNamespace(
        start=lambda: None,
        sample=lambda: None,
        close=lambda: None,
        result=dict,
        identities={"0": {"model": "test GPU"}},
        handles={"0": 0},
    )
    monkeypatch.setattr(benchmark, "ResourceMonitor", lambda ids: monitor)

    def command(argv, **kwargs):
        if "check" in argv:
            assert kwargs["json_output"] is False
            return "  version        3.3.0-rc0", 0
        if "logs" in argv:
            return "**PERF: 30.00000 (1.00000)", 0
        key = "incidents" if "incidents" in argv else "sensors"
        rows = [] if key == "incidents" else [{}]
        return {"count": len(rows), key: rows}, 0.01

    monkeypatch.setattr(benchmark, "run", command)
    cases, _ = benchmark.measure(
        {"duration_seconds": 0.000001, "interval_seconds": 1, "warmup_iterations": 0},
        "0",
        "rt-cv",
    )
    assert len(cases) == 5 and all(case["success"] for case in cases)
    assert cases[3]["metrics"]["throughput"]["aggregate_fps_mean"] == 30

    def failed(command, **kwargs):
        if "check" in command:
            return "", 0
        raise RuntimeError("vss exited 3: backend unavailable")

    monkeypatch.setattr(benchmark, "run", failed)
    with pytest.raises(RuntimeError, match="exited 3"):
        benchmark.measure(
            {"duration_seconds": 1, "interval_seconds": 1, "warmup_iterations": 0},
            "0",
            "rt-cv",
        )


@pytest.fixture
def setup_run(tmp_path, monkeypatch):
    (tmp_path / "configs").mkdir()
    (tmp_path / ".runtime/GPU_EXACT_ID").mkdir(parents=True)
    (tmp_path / "configs/perf-configs.yaml").write_text(
        yaml.safe_dump({"perf_configs": [{"id": "GPU_EXACT_ID", "rtCvGpus": "1"}]})
    )
    (tmp_path / "configs/config.yaml").write_text(
        yaml.safe_dump(
            {
                "global": {"rt_cv_container": "rt-cv"},
                "test_scenarios": {
                    "quick_test": {"duration_seconds": 30, "interval_seconds": 2}
                },
            }
        )
    )
    metadata = {
        "fixture": {
            "kind": "video_file",
            "sha256": "a" * 64,
            "width": 1280,
            "height": 720,
            "fps": 29.97,
        },
        "deployed_images": [{"image_id": "sha256:" + "b" * 64}],
    }
    (tmp_path / ".runtime/GPU_EXACT_ID/metadata.json").write_text(json.dumps(metadata))
    monkeypatch.setattr(benchmark, "PERF_ROOT", tmp_path)
    fixed = datetime(2026, 10, 14, 12, 30, tzinfo=timezone.utc)  # noqa: UP017 - Python 3.10.
    monkeypatch.setattr(
        benchmark,
        "datetime",
        SimpleNamespace(now=lambda tz: fixed),
    )
    monkeypatch.setenv("VSS_PERF_SOURCE_REVISION", "test-source")
    monkeypatch.setenv("MINIO_ACCESS_KEY", "unit-test")
    monkeypatch.setenv("MINIO_SECRET_KEY", "unit-test")
    monkeypatch.setattr(
        benchmark.SHARED,
        "discover_platform",
        lambda: {"gpu": {"model": "host", "count": 2}},
    )
    calls = []
    monkeypatch.setattr(
        benchmark, "run", lambda *args, **kwargs: (calls.append("cleanup") or "", 0)
    )

    def measured(scenario, gpu, container):
        calls.append(gpu)
        return (
            [{"test_case_id": "measured", "success": True, "metrics": {}}],
            {"model": "selected", "count": 1},
        )

    monkeypatch.setattr(benchmark, "measure", measured)

    def upload(*args, **kwargs):
        calls.append("upload")
        result = json.loads(Path(args[0]).read_text())
        assert calls[-2] == "cleanup"
        assert result["metadata"]["config"]["runtime"] == metadata
        return True

    monkeypatch.setattr(benchmark.SHARED, "upload_result_file", upload)
    args = ["--config-id", "GPU_EXACT_ID", "--output-json", str(tmp_path / "results")]
    return args, calls, tmp_path


def test_cleanup_precedes_single_validated_result_and_upload(setup_run):
    args, calls, root = setup_run
    assert benchmark.main([*args, "--upload", "--release", "requested-label"]) == 0
    assert calls == ["1", "cleanup", "upload"]
    paths = list((root / "results").glob("*.json"))
    assert len(paths) == 1
    assert paths[0].name == "compose-base_GPU_EXACT_ID_20261014_123000.json"
    result = json.loads(paths[0].read_text())
    benchmark.SHARED.VSSBenchmarkResult.model_validate(result)
    assert result["metadata"]["config"]["config_id"] == "GPU_EXACT_ID"
    assert result["metadata"]["run_info"]["release"] == "requested-label"
    assert result["metadata"]["platform"]["gpu"]["count"] == 1
    assert (
        benchmark.main(args) == 1
    )  # Existing same-second results are never overwritten.


def test_cleanup_failure_forces_fail_before_publication(setup_run, monkeypatch):
    args, calls, root = setup_run

    def failed(*args, **kwargs):
        calls.append("cleanup")
        raise RuntimeError("Docker cleanup failed")

    monkeypatch.setattr(benchmark, "run", failed)
    assert benchmark.main([*args, "--upload"]) == 1
    assert calls == ["1", "cleanup", "upload"]
    result = json.loads(next((root / "results").glob("*.json")).read_text())
    assert result["summary"]["overall_status"] == "FAIL"
    assert result["test_cases"][-1]["test_case_id"] == "teardown"


def test_stage_failure_also_cleans_up_and_writes_one_result(setup_run):
    args, calls, root = setup_run
    assert (
        benchmark.main(
            [
                *args,
                "--stage-failure",
                "deploy",
                "--failure-message",
                "image pull failed",
            ]
        )
        == 1
    )
    assert calls == ["cleanup"]
    result = json.loads(next((root / "results").glob("*.json")).read_text())
    assert result["summary"]["overall_status"] == "FAIL"
    assert "image pull failed" in result["test_cases"][0]["error"]


def test_upload_failure_returns_nonzero(setup_run, monkeypatch):
    args, _, root = setup_run
    monkeypatch.setattr(benchmark.SHARED, "upload_result_file", lambda *a, **kw: False)
    assert benchmark.main([*args, "--upload"]) == 1
    paths = list((root / "results").glob("*.json"))
    assert len(paths) == 1
    assert json.loads(paths[0].read_text())["summary"]["overall_status"] == "FAIL"


@pytest.mark.parametrize("identifier", ["../outside", "a/b", "a\\b", "bad\nname"])
def test_unsafe_identifiers_are_rejected(identifier):
    with pytest.raises(SystemExit):
        benchmark.main(["--config-id", identifier])


def test_sample_uses_shared_result_contract():
    benchmark.SHARED.VSSBenchmarkResult.model_validate(
        json.loads((CORE.parents[2] / "tests/expected_result.json").read_text())
    )
