# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import importlib.util
import json
import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

PERF_ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    PERF_ROOT.parent.name.replace("-", "_") + "_output_core",
    PERF_ROOT / "benchmark/core/vss_perf_benchmark.py",
)
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)


@pytest.mark.parametrize("destination", ["run", "run/vss-perf-results", "run/result.json"])
def test_failure_report_uses_requested_destination(tmp_path, destination):
    target = tmp_path / destination
    with patch.dict(os.environ, {"OUTPUT_DIR": str(tmp_path / "ignored")}):
        assert core.main([
            "--output-json", str(target), "--stage-failure", "deploy",
            "--failure-message", "deployment failed",
        ]) == 1
    directory = target.parent if target.suffix == ".json" else (
        target if target.name == "vss-perf-results" else target / "vss-perf-results"
    )
    reports = list(directory.glob("*.json"))
    assert len(reports) == 1
    assert "deployment failed" in reports[0].read_text()
    assert json.loads(reports[0].read_text())["test_cases"]
    assert not (directory / "vss-perf-results").exists()
    if target.suffix == ".json":
        assert reports == [target]


def test_environment_output_fallback(tmp_path, monkeypatch):
    monkeypatch.delenv("OUTPUT_DIR", raising=False)
    monkeypatch.setenv("PERF_OUTPUT_DIR", str(tmp_path))
    assert core.result_target(core.parse_args([])) == tmp_path / "vss-perf-results"


def test_output_is_prepared_before_launching_stages(tmp_path):
    output = tmp_path / "run"

    def fail_stage(*args, **kwargs):
        assert Path(kwargs["env"]["OUTPUT_DIR"]) == output
        assert output.is_dir()
        assert (output / "vss-perf-results").is_dir()
        return subprocess.CompletedProcess(args[0], 1)

    with patch.object(core.subprocess, "run", side_effect=fail_stage):
        assert core.main(["--output-json", str(output)]) == 1
    assert list((output / "vss-perf-results").glob("*.json"))


@pytest.mark.parametrize("kind", ["symlink", "file", "unwritable"])
def test_invalid_output_is_rejected_before_running_stages(tmp_path, kind):
    target = tmp_path / "output"
    if kind == "symlink":
        destination = tmp_path / "destination"
        destination.mkdir()
        target.symlink_to(destination, target_is_directory=True)
    elif kind == "file":
        target.write_text("preserve")
    with patch.object(core.subprocess, "run") as run:
        if kind == "unwritable":
            with patch.object(core.os, "access", return_value=False):
                with pytest.raises(PermissionError):
                    core.main(["--output-json", str(target)])
        else:
            with pytest.raises((ValueError, FileExistsError, NotADirectoryError)):
                core.main(["--output-json", str(target)])
        run.assert_not_called()
    if kind == "symlink":
        assert list(destination.iterdir()) == []
    elif kind == "file":
        assert target.read_text() == "preserve"


@pytest.mark.parametrize("kind", ["prepared", "missing", "file", "symlink"])
def test_shell_output_validation(tmp_path, kind):
    output = tmp_path / "output"
    if kind == "prepared":
        output.mkdir()
    elif kind == "file":
        output.write_text("preserve")
    elif kind == "symlink":
        output.symlink_to(tmp_path, target_is_directory=True)
    env = os.environ.copy()
    env["OUTPUT_DIR"] = str(output)
    result = subprocess.run(
        ["bash", "-c", 'source "$1"; validate_output_dir', "bash",
         str(PERF_ROOT / "scripts/common.sh")],
        env=env, capture_output=True, text=True,
    )
    assert (result.returncode == 0) == (kind == "prepared"), result.stderr
    if kind == "missing":
        assert not output.exists()
