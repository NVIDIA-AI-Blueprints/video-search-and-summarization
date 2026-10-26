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

"""Portable matrix lifecycle, real HTTP chains and spreadsheet evidence integrity."""

import csv
import json
import os
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pytest
from pydantic import ValidationError
from test_workflow_qualification import workflow as workflow

from benchmarks.e2e.config import fingerprint
from benchmarks.matrix.config import Matrix, Media, Normal, Provider, cells, select
from benchmarks.matrix.export import export
from benchmarks.matrix.runner import plan, run, schedule, validate_receipt
from benchmarks.media import bounded_tool, file_hash
from benchmarks.workflow.config import Workflow
from benchmarks.workflow.operation import operation

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def restore_backend_registries(monkeypatch):
    import vss.ingestion.backend as backend
    import vss.ingestion.vlm as vlm

    monkeypatch.setattr(backend, "_BACKEND_REGISTRY", dict(backend._BACKEND_REGISTRY))
    monkeypatch.setattr(vlm, "_VLM_BACKEND_REGISTRY", dict(vlm._VLM_BACKEND_REGISTRY))


def recipe(tmp_path):
    return Matrix(
        media=(Media(name="demo", video=tmp_path / "video.mp4", duration_s=2),),
        vlms={
            "local": Provider(
                model="test-vlm",
                revision="fixture.v1",
                url_env="MATRIX_FRAME",
                rtvi_url_env="MATRIX_RTVI",
            )
        },
        llms={
            "local": Provider(
                model="test-llm", revision="fixture.v1", url_env="MATRIX_LLM"
            )
        },
        normal={"local-local": Normal(url_env="MATRIX_NORMAL")},
        profiles=("normal-es", "library-rtvi-memory", "library-rtvi-file"),
        repetitions=1,
        warmups=0,
        observation="minimal",
        work_timeout_s=5,
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("streams", (0,)),
        ("streams", (9,)),
        ("frames_per_chunk", (0,)),
        ("repetitions", 0),
        ("profiles", ("unknown",)),
        ("placements", ("local-local", "local-local")),
        ("caption_generation", {"messages": "bad"}),
        ("summary_generation", {"temperature": float("nan")}),
    ],
)
def test_bad_matrix_dimensions_and_controls_rejected(tmp_path, field, value):
    with pytest.raises(ValidationError):
        Matrix.model_validate(recipe(tmp_path).model_dump() | {field: value})


def test_complete_matrix_and_budget_admission(tmp_path):
    from benchmarks.matrix.config import PLACEMENTS, PROFILES

    data = recipe(tmp_path).model_dump() | dict(
        profiles=tuple(PROFILES), placements=PLACEMENTS
    )
    config = Matrix.model_validate(data)
    rows = cells(config)
    assert len(rows) == 40
    assert sum(not r["blocked"] for r in rows) == 10
    assert all(r["blocked"] for r in rows if r["placement"] != "local-local")
    assert plan(config)["trials"] == 40
    with pytest.raises(ValueError, match="trial budget"):
        cells(config.model_copy(update={"max_trials": 39}))
    with pytest.raises(ValueError, match="256 cells"):
        cells(config.model_copy(update={"streams": (1, 2, 3, 4, 5, 6, 7, 8)}))
    bad_frames = config.model_copy(update={"frames_per_chunk": (4,)})
    assert all(
        "native_frame_backend_fixes_eight_frames" in r["blocked"]
        for r in cells(bad_frames)
        if "-cpu-" in r["profile"] or "-gpu-" in r["profile"]
    )
    assert select("all", ("a", "b"), ("a",)) == ("b",)
    with pytest.raises(ValueError):
        select("typo", ("a",))


def test_rotated_schedule_preserves_warmup_and_cell_counts(tmp_path):
    config = recipe(tmp_path).model_copy(update={"warmups": 1, "repetitions": 3})
    rows = list(schedule(config, cells(config)))
    assert len(rows) == 12
    assert [r[0] for r in rows[:3]] == ["warmup"] * 3
    assert len({r[2]["cell_id"] for r in rows}) == 3
    assert [r[2]["cell_id"] for r in rows[3:6]] != [r[2]["cell_id"] for r in rows[6:9]]


@pytest.mark.parametrize(
    "replacement",
    [
        None,
        [],
        "invalid",
        {},
        {"cleanup": None},
        {"cleanup": []},
        {"cleanup": {}},
        {"cleanup": {"closed": 1}},
        {"cleanup": {"closed": "true"}},
        {"cleanup": {"closed": False}},
        {"cleanup": {"closed": True, "deleted": False}},
    ],
)
def test_success_receipt_rejects_malformed_operation_and_cleanup(tmp_path, replacement):
    intent = tmp_path / "intent.json"
    intent.write_text(json.dumps({"software_sha256": "a" * 64}))
    row = dict(
        trial_id="t0",
        intent=intent.name,
        receipt="receipt.json",
        intent_sha256=file_hash(intent),
    )
    operation = dict(
        status="completed_diagnostic",
        operational_settlement="outer_completed_owned_cleanup_complete",
        cleanup={"closed": True},
        outer_response_completed=True,
        workflow_to_result_s=1,
    )
    report = dict(
        schema_version="perf_trial.1",
        trial_id=row["trial_id"],
        intent_sha256=row["intent_sha256"],
        qualified=False,
        thresholds_approved=False,
        status="completed_diagnostic",
        software_sha256="a" * 64,
        observer_complete=True,
        operation=operation,
    )
    receipt = tmp_path / row["receipt"]
    receipt.write_text(json.dumps(report))
    assert validate_receipt(tmp_path, row) == report
    report["operation"] = (
        operation | replacement if isinstance(replacement, dict) else replacement
    )
    if replacement == {}:
        report["operation"] = {}
    receipt.write_text(json.dumps(report))
    with pytest.raises(ValueError, match="incomplete_success_receipt"):
        validate_receipt(tmp_path, row)


@pytest.fixture
def controlled_recipe(workflow, tmp_path, monkeypatch):
    if os.name != "posix":
        pytest.skip("POSIX private workflow diagnostics")
    old, fixture, _ = workflow
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("FFmpeg required for actual media preflight")
    config = recipe(tmp_path)
    bounded_tool(
        [
            ffmpeg,
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=64x48:rate=8:duration=2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(config.media[0].video),
        ],
        limit=1024,
    )
    rtvi = os.environ[old.rtvi_url_env]
    fixture.relay_url = (
        rtvi + "/llm/v1"
    )  # configured server calls its local LLM directly
    for name, url in dict(
        MATRIX_FRAME=rtvi + "/v1",
        MATRIX_RTVI=rtvi,
        MATRIX_LLM=rtvi + "/llm/v1",
        MATRIX_NORMAL=rtvi + "/normal",
    ).items():
        monkeypatch.setenv(name, url)
    return config, fixture


@pytest.mark.parametrize("streams", [1, 2])
def test_actual_full_matrix_worker_pipeline_and_export(
    controlled_recipe, tmp_path, streams
):
    config, fixture = controlled_recipe
    config = config.model_copy(
        update={"streams": (streams,), "observation": "resources"}
    )
    output = tmp_path / "run"
    manifest = run(config, output)
    assert manifest["status"] == "finished", manifest
    assert len(manifest["trials"]) == 3 * streams
    assert all(r["status"] == "completed_diagnostic" for r in manifest["trials"]), (
        manifest["trials"]
    )
    assert not fixture.assets and fixture.llm_calls == 3 * streams
    rows = export(output)
    assert len(rows) == 3 * streams
    assert all(r["frames_per_chunk"] == 8 and r["qualified"] is False for r in rows)
    assert all(r["worker_trace_status"] == "complete" for r in rows)
    for row in rows:
        assert row["output_capture"] and row["workflow_to_result_s"] > 0
        if row["application_type"] == "normal":
            assert row["llm_output_tokens"] is None
        else:
            assert row["llm_output_tokens"] == 4
            assert row["llm_finish_reasons"] == ["stop"]
            assert row["requested_summary_generation"] == config.summary_generation
    assert all(
        b["completed"] == streams
        and b["actual_overlap_streams"] in range(1, streams + 1)
        for b in manifest["batches"]
    )
    with (output / "summary.csv").open() as stream:
        assert all(int(r["completed"]) == streams for r in csv.DictReader(stream))
    with zipfile.ZipFile(output / "results.xlsx") as x:
        assert b"MetricDictionary" in x.read("xl/workbook.xml")
        assert not any(
            b"<f>" in x.read(n) for n in x.namelist() if n.startswith("xl/worksheets/")
        )
    assert (output / "run.json").stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        run(config, output)
    item = manifest["trials"][0]
    receipt = output / item["receipt"]
    receipt.write_bytes(receipt.read_bytes() + b" ")
    with pytest.raises(ValueError, match="receipt_changed"):
        export(output)


def test_warmup_batch_identity_survives_workbook_export(controlled_recipe, tmp_path):
    config, _ = controlled_recipe
    config = config.model_copy(
        update={"profiles": ("library-rtvi-memory",), "warmups": 1, "repetitions": 2}
    )
    output = tmp_path / "warmup-batches"
    manifest = run(config, output)
    assert manifest["status"] == "finished"
    expected = []
    for batch in manifest["batches"]:
        label = (batch["kind"], str(batch["repetition"]), batch["cell_id"])
        expected.append(label)
        trials = [r for r in manifest["trials"] if r["trial_id"] in batch["trial_ids"]]
        assert trials and all(
            (r["kind"], str(r["repetition"]), r["cell_id"]) == label for r in trials
        )
    assert [(kind, repetition) for kind, repetition, _ in expected] == [
        ("warmup", "0"),
        ("measurement", "0"),
        ("measurement", "1"),
    ]
    with (output / "summary.csv").open() as stream:
        assert next(csv.DictReader(stream))["completed"] == "2"
    with zipfile.ZipFile(output / "results.xlsx") as workbook:
        strings = [
            "".join(si.itertext())
            for si in ET.fromstring(workbook.read("xl/sharedStrings.xml"))
        ]
        rows = []
        for row in ET.fromstring(workbook.read("xl/worksheets/sheet4.xml")).findall(
            ".//{*}row"
        ):
            values = []
            for cell in row.findall("{*}c")[:4]:
                value = cell.findtext("{*}v")
                values.append(strings[int(value)] if cell.get("t") == "s" else value)
            rows.append(values)
    assert rows[0] == ["batch_id", "kind", "repetition", "cell_id"]
    assert [tuple(row[1:]) for row in rows[1:]] == expected


@pytest.mark.skipif(os.name != "posix", reason="POSIX diagnostic runtime")
def test_blocked_prerequisites_still_export_complete_coverage(tmp_path, monkeypatch):
    config = recipe(tmp_path)
    monkeypatch.delenv("MATRIX_RTVI", raising=False)
    output = tmp_path / "blocked"
    result = run(config, output)
    assert all(t["status"] == "blocked" for t in result["trials"])
    assert (output / "results.xlsx").is_file()
    assert len(export(output)) == 3
    assert not list((output / "trials").glob("*.receipt.json"))


async def test_direct_completion_validates_real_provider_finish_and_cleanup(
    workflow, monkeypatch
):
    profile, fixture, _ = workflow
    rtvi = os.environ[profile.rtvi_url_env]
    monkeypatch.setenv("DIRECT_LLM", rtvi + "/llm/v1")
    p = profile.model_copy(
        update={
            "relay_control_env": None,
            "llm_url_env": "DIRECT_LLM",
            "store_backend": "memory",
            "summary_generation": {
                "temperature": 0.2,
                "top_p": 0.9,
                "reasoning_effort": "low",
            },
        }
    )
    result = await operation(p, 1)
    assert result["status"] == "completed_diagnostic", result
    assert "llm_relay" not in result
    assert "relay_sealed_complete" not in result["cleanup"]
    settings = result["llm_provider_observation"]["inputs"][0]["generation"]
    assert settings["temperature"] == 0.2 and settings["reasoning_effort"] == "low"
    fixture.reason = "length"
    result = await operation(p, 1)
    assert result["status"] == "failed"
    assert not fixture.assets


@pytest.mark.skipif(os.name != "posix", reason="POSIX diagnostic runtime")
def test_plan_shell_does_not_need_live_services_or_credentials(tmp_path):
    env = dict(os.environ, PERF_PYTHON=sys.executable)
    result = subprocess.run(
        [
            "bash",
            "benchmarks/run_perf.sh",
            "plan",
            "--profiles",
            "all",
            "--placements",
            "all",
            "--repetitions",
            "1",
            "--warmups",
            "0",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert len(data["cells"]) == 40
    assert data["thresholds_approved"] is False


def test_normal_manifest_model_mismatch_blocks_without_contact(
    controlled_recipe, tmp_path
):
    from benchmarks.matrix.config import workflow

    config, _ = controlled_recipe
    path = tmp_path / "deployment.json"
    path.write_text(json.dumps({"vlm_model": "different"}))
    config = config.model_copy(
        update={
            "normal": {
                "local-local": Normal(url_env="MATRIX_NORMAL", deployment_manifest=path)
            }
        }
    )
    with pytest.raises(ValueError, match="does not match"):
        workflow(config, cells(config)[0])


def test_metric_formula_safety(tmp_path):
    from benchmarks.matrix.export import csv_write

    csv_write(tmp_path / "values.csv", [{"model": "=1+2"}], ("model",))
    assert "'=1+2" in (tmp_path / "values.csv").read_text()


def test_workflow_config_keys_are_private_environment_refs(tmp_path):
    data = recipe(tmp_path)
    from benchmarks.matrix.config import workflow

    profile = workflow(
        data.model_copy(
            update={"media": (data.media[0].model_copy(update={"sha256": "0" * 64}),)}
        ),
        cells(data)[1],
    )
    body = profile.model_dump(mode="json")
    body["llm_key_env"] = "Bearer secret-key"
    with pytest.raises(ValidationError):
        Workflow.model_validate(body)
    assert fingerprint(data.model_dump(mode="json"))


def test_all_model_placements_route_to_declared_services(
    controlled_recipe, tmp_path, monkeypatch
):
    """Remote-role stand-ins are owned loopback services, not external models."""
    from http.server import ThreadingHTTPServer

    from test_workflow_qualification import Fixture, running

    from benchmarks.matrix.config import PLACEMENTS

    config, fixture = controlled_recipe
    local_base = os.environ["MATRIX_RTVI"]
    fixture.placement = "local"
    alternate = ThreadingHTTPServer(("127.0.0.1", 0), Fixture)
    alternate.daemon_threads = True
    for name in (
        "assets",
        "count",
        "event_count",
        "duration",
        "reverse",
        "cleanup_failure",
        "slow",
        "caption_transform",
        "llm_calls",
        "frame_calls",
        "frame_model_override",
        "normal_delay",
        "llm_started",
        "slow_release",
        "normal_started",
        "normal_release",
        "normal_finished",
        "reason",
        "usage",
        "query",
    ):
        setattr(alternate, name, getattr(fixture, name))
    alternate.placement = "remote"
    seen = []
    original = Fixture.do_POST
    with running(alternate) as remote_base:
        bases = {"local": local_base, "remote": remote_base}

        def record(handler):
            seen.append(
                (
                    handler.server.placement,
                    handler.path,
                    handler.headers.get("Authorization"),
                )
            )
            if "/normal-" in handler.path:
                placement = handler.path.split("/normal-")[1].split("/")[0]
                handler.server.relay_url = bases[placement.split("-")[1]] + "/llm/v1"
            return original(handler)

        monkeypatch.setattr(Fixture, "do_POST", record)
        vlms, llms, normal = {}, {}, {}
        for role in ("local", "remote"):
            upper = role.upper()
            monkeypatch.setenv("MATRIX_" + upper + "_VLM", bases[role] + "/v1")
            monkeypatch.setenv("MATRIX_" + upper + "_RTVI", bases[role])
            monkeypatch.setenv("MATRIX_" + upper + "_LLM", bases[role] + "/llm/v1")
            monkeypatch.setenv("MATRIX_" + upper + "_KEY", role + "-fake-private-key")
            vlms[role] = Provider(
                model="test-vlm",
                revision="fixture.v1",
                url_env="MATRIX_" + upper + "_VLM",
                rtvi_url_env="MATRIX_" + upper + "_RTVI",
                rtvi_key_env="MATRIX_" + upper + "_KEY",
                key_env="MATRIX_" + upper + "_KEY",
            )
            llms[role] = Provider(
                model="test-llm",
                revision="fixture.v1",
                url_env="MATRIX_" + upper + "_LLM",
                key_env="MATRIX_" + upper + "_KEY",
                generation={"temperature": 0.3, "reasoning_effort": "low"},
            )
        for placement in PLACEMENTS:
            name = "MATRIX_NORMAL_" + placement.replace("-", "_").upper()
            monkeypatch.setenv(name, local_base + "/normal-" + placement)
            normal[placement] = Normal(url_env=name)
        config = config.model_copy(
            update={
                "vlms": vlms,
                "llms": llms,
                "normal": normal,
                "placements": PLACEMENTS,
                "profiles": ("normal-es", "library-rtvi-memory"),
            }
        )
        output = tmp_path / "placements"
        manifest = run(config, output)
        assert all(t["status"] == "completed_diagnostic" for t in manifest["trials"]), (
            manifest["trials"]
        )
        rows = export(output)
    assert len(rows) == 8 and not fixture.assets
    for placement in PLACEMENTS:
        assert any(path.startswith("/normal-" + placement + "/") for _, path, _ in seen)
    for role in ("local", "remote"):
        assert any(
            server == role
            and path == "/v1/generate_captions"
            and auth == "Bearer " + role + "-fake-private-key"
            for server, path, auth in seen
        )
        assert any(
            server == role
            and path.endswith("/llm/v1/chat/completions")
            and auth == "Bearer " + role + "-fake-private-key"
            for server, path, auth in seen
        )
    assert "fake-private-key" not in (output / "trials.csv").read_text()
    assert "fake-private-key" not in (output / "run.json").read_text()


async def test_unused_role_credentials_do_not_block_execution(workflow, monkeypatch):
    profile, fixture, _ = workflow
    base = os.environ[profile.rtvi_url_env]
    fixture.relay_url = base + "/llm/v1"
    monkeypatch.setenv("DIRECT_LLM", fixture.relay_url)
    monkeypatch.delenv("UNUSED_PRIVATE_KEY", raising=False)
    normal = profile.model_copy(
        update={
            "arm": "normal",
            "relay_control_env": None,
            "llm_key_env": "UNUSED_PRIVATE_KEY",
            "frame_key_env": "UNUSED_PRIVATE_KEY",
        }
    )
    assert (await operation(normal, 1))["status"] == "completed_diagnostic"
    library = profile.model_copy(
        update={
            "relay_control_env": None,
            "llm_url_env": "DIRECT_LLM",
            "store_backend": "memory",
            "normal_key_env": "UNUSED_PRIVATE_KEY",
            "frame_key_env": "UNUSED_PRIVATE_KEY",
        }
    )
    assert (await operation(library, 1))["status"] == "completed_diagnostic"


def test_media_is_pinned_once_and_changed_video_is_never_pooled(
    controlled_recipe, tmp_path, monkeypatch
):
    from benchmarks.matrix import runner
    from benchmarks.media import file_hash

    config, _ = controlled_recipe
    config = config.model_copy(
        update={"profiles": ("library-rtvi-memory",), "repetitions": 2}
    )
    before = file_hash(config.media[0].video)
    original = runner.run_wave
    calls = []

    def mutate(*args):
        result = original(*args)
        if not calls:
            with config.media[0].video.open("ab") as stream:
                stream.write(b"media changed between trials")
        calls.append(True)
        return result

    monkeypatch.setattr(runner, "run_wave", mutate)
    output = tmp_path / "pinned"
    manifest = run(config, output)
    rows = export(output)
    assert manifest["recipe"]["media"][0]["sha256"] == before
    assert sum(r["status"] == "completed_diagnostic" for r in rows) == 1
    assert all(r["source_sha256"] == before for r in rows)
    assert rows[1]["status"] == "failed"


@pytest.mark.skipif(os.name != "posix", reason="POSIX diagnostic runtime")
def test_large_legal_matrix_exports_without_excel_cell_overflow(tmp_path):
    from benchmarks.matrix.config import PLACEMENTS, PROFILES

    config = recipe(tmp_path).model_copy(
        update={
            "profiles": tuple(PROFILES),
            "placements": PLACEMENTS,
            "streams": (1, 2, 3, 4, 5, 6),
            "max_trials": 1000,
        }
    )
    output = tmp_path / "wide"
    result = run(config, output)
    assert len(result["plan"]["cells"]) == 240
    assert len(export(output)) == 840
    assert (output / "results.xlsx").stat().st_size > 0


def test_cleanup_failure_halts_new_service_admissions(controlled_recipe, tmp_path):
    config, fixture = controlled_recipe
    fixture.cleanup_failure = True
    result = run(config, tmp_path / "cleanup-failure")
    assert result["status"] == "interrupted"
    assert result["trials"][0]["status"] == "failed"
    assert all(t["status"] == "not_run" for t in result["trials"][1:])
    assert fixture.llm_calls == 1


@pytest.mark.skipif(os.name != "posix", reason="POSIX diagnostic runtime")
def test_concurrent_timeout_cancels_together_and_forces_one_shared_deadline(
    tmp_path, monkeypatch
):
    import signal
    from types import SimpleNamespace

    from benchmarks.matrix import runner

    children, signals = [], []

    class Process:
        def __init__(self, *args, **kwargs):
            self.pid = 100 + len(children)
            self.returncode = None
            children.append(self)

        def poll(self):
            return self.returncode

        def wait(self, timeout):
            assert self.returncode == -signal.SIGKILL
            return self.returncode

    def kill(pid, sig):
        signals.append((pid, sig))
        if sig == signal.SIGKILL:
            children[pid - 100].returncode = -signal.SIGKILL

    clock = iter(range(100, 10000))
    monkeypatch.setattr(
        runner,
        "time",
        SimpleNamespace(monotonic=lambda: next(clock), sleep=lambda _: None),
    )
    monkeypatch.setattr(runner.subprocess, "Popen", Process)
    monkeypatch.setattr(runner.os, "killpg", kill)
    rows = [
        dict(log=f"{i}.log", intent=f"{i}.json", receipt=f"{i}.receipt")
        for i in range(2)
    ]
    stop = dict(cancelled=False)
    assert runner.run_wave(tmp_path, rows, recipe(tmp_path), stop, 103, None)
    assert stop["cancelled"]
    assert signals == [
        (100, signal.SIGTERM),
        (101, signal.SIGTERM),
        (100, signal.SIGKILL),
        (101, signal.SIGKILL),
    ]
    assert all(r["exit_code"] == -signal.SIGKILL for r in rows)


def test_observer_intervals_fit_longest_worker_budget():
    from benchmarks.workflow.observation import Observers

    observer = Observers("resources", maximum_duration_s=1220)
    assert 1220 / observer.policy["worker_interval_s"] < 6000
    assert 1220 / observer.policy["gpu_interval_s"] < 4000


def test_huge_cartesian_product_is_rejected_before_allocation(tmp_path, monkeypatch):
    from benchmarks.matrix import config as matrix_config

    config = recipe(tmp_path).model_copy(
        update={
            "chunk_durations": tuple(range(1, 121)),
            "frames_per_chunk": tuple(range(1, 65)),
            "streams": tuple(range(1, 9)),
        }
    )

    def forbidden(*args):
        raise AssertionError("must reject before enumerating combinations")

    monkeypatch.setattr(matrix_config.itertools, "product", forbidden)
    with pytest.raises(ValueError, match="256 cells"):
        cells(config)


def test_cpu_frame_profile_uses_actual_library_pipeline(controlled_recipe, tmp_path):
    import importlib.util

    if importlib.util.find_spec("cv2") is None:
        pytest.skip("caller-installed OpenCV required")
    config, fixture = controlled_recipe
    config = config.model_copy(
        update={
            "profiles": ("library-cpu-memory",),
            "frame_sampling": "shared_duration_grid.v1",
            # A fresh OpenCV installation can exceed the HTTP-only fixture's
            # five-second budget on its first native import. Keep the actual
            # decoder workflow bounded without treating cold startup as a hang.
            "work_timeout_s": 30,
        }
    )
    output = tmp_path / "cpu-frame"
    result = run(config, output)
    assert result["trials"][0]["status"] == "completed_diagnostic", result["trials"]
    row = export(output)[0]
    assert row["backend_type"] == "cpu_vlm"
    assert row["vlm_output_tokens"] == 4
    assert row["vlm_usage_scope"] == "frame_compatibility_response_not_native_audit"
    assert fixture.frame_calls == 1 and not fixture.assets


def test_unsupported_runtime_rejects_before_output_creation(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from benchmarks.matrix import runner

    monkeypatch.setattr(runner, "os", SimpleNamespace(name="nt"))
    output = tmp_path / "unsupported"
    with pytest.raises(ValueError, match="requires POSIX"):
        runner.run(recipe(tmp_path), output)
    assert not output.exists()


def test_work_identity_distinguishes_sampling_and_normal_task_controls(tmp_path):
    from benchmarks.matrix.config import workflow as configure
    from benchmarks.matrix.export import trial_row
    from benchmarks.media import file_hash

    config = recipe(tmp_path)
    config = config.model_copy(
        update={
            "media": (config.media[0].model_copy(update={"sha256": "0" * 64}),),
            "profiles": ("library-cpu-memory",),
        }
    )
    cell = cells(config)[0]
    profile = configure(config, cell)
    manifest = dict(
        recipe=config.model_dump(mode="json"),
        run_id="fixture",
        software_sha256="1" * 64,
    )

    def identity(changes):
        value = profile.model_copy(update=changes).model_dump(mode="json")
        path = tmp_path / "intent.json"
        path.write_text(
            json.dumps(dict(workflow=value, workflow_sha256=fingerprint(value)))
        )
        row = dict(
            cell, intent=path.name, intent_sha256=file_hash(path), status="not_run"
        )
        return trial_row(tmp_path, manifest, row)["requested_work_sha256"]

    original = identity({})
    assert identity({"sampling_contract": "shared_duration_grid.v1"}) != original
    assert identity({"scenario": "different task"}) != original
    assert identity({"events": ("different event",)}) != original
    assert identity({"store_backend": "file"}) == original
