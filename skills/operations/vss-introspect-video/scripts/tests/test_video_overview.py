# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
import copy
import json
from types import SimpleNamespace

import evidence_ledger as ledger
import inspection_workflow as workflow
import pytest
import video_overview as overview
import vlm_inspection as vlm
from test_inspection_workflow import SCOPE, assessment, initialize, response, spec


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    monkeypatch.setattr(vlm, "allowed_sensors_from_attempt_context", lambda: ())
    monkeypatch.setattr(vlm, "remote_vlm_spec", lambda: {"model": "unchanged"})
    monkeypatch.setattr(vlm, "_run_vlm", lambda **kw: response())
    monkeypatch.setattr(
        overview, "full_scope", lambda *args: (copy.deepcopy(SCOPE), {"recorded": True})
    )
    monkeypatch.setattr(overview, "run_uniform", lambda *args: response())
    path = initialize(tmp_path, choice=True)
    current = ledger.initialize_ledger(
        workflow.read(path)["plan"], requires_choice=True, overview_required=True
    )
    current = ledger.merge_memory(
        current,
        [
            {
                "claim_id": "claim-color",
                "observations": [],
                "coverage": "none",
                "gap": "No matching summary",
            }
        ],
    )
    ledger.atomic_write(path, current)
    return path


def assessments(receipt, **changes):
    item = {"task": {"task_id": "inspect-claim-color-r1"}}
    return [assessment(item, **changes)]


def test_overview_is_mandatory_and_charged_once_without_round(prepared, monkeypatch):
    with pytest.raises(ValueError, match="overview"):
        workflow.run_batch(prepared, [spec()], None)
    calls = []
    monkeypatch.setattr(
        overview, "run_uniform", lambda *args: calls.append(args) or response()
    )
    receipt = overview.run_overview(prepared, "sensor-1", None)
    assert workflow.read(prepared)["vlm_calls_used"] == 1
    assert workflow.read(prepared)["round"] == 0
    assert overview.run_overview(prepared, "sensor-1", None) == receipt
    assert len(calls) == 1
    current = overview.merge_overview(
        prepared,
        assessments(
            receipt, coverage_sufficient=False, missing_evidence=["Need closer view"]
        ),
    )
    assert current["round"] == 0
    assert current["vlm_calls_used"] == 1
    assert (
        overview.merge_overview(
            prepared,
            assessments(
                receipt,
                coverage_sufficient=False,
                missing_evidence=["Need closer view"],
            ),
        )
        == current
    )
    batch = workflow.run_batch(prepared, [spec()], None)
    merged = workflow.merge_batch(
        prepared,
        prepared.parent / "rounds/round-1/batch.json",
        [assessment(batch["results"][0])],
    )
    assert merged["vlm_calls_used"] == 2
    assert merged["round"] == 1
    assert merged["status"] == "answered"


def test_completed_overview_can_skip_batches_but_not_final(prepared):
    receipt = overview.run_overview(prepared, "sensor-1", None)
    current = overview.merge_overview(prepared, assessments(receipt))
    assert current["status"] == "answered"
    with pytest.raises(ValueError, match="Mandatory"):
        workflow.checked_final_result(
            current,
            str(prepared.parent),
            "Blue",
            current["claims"][0]["observation_ids"],
        )
    assert overview.run_overview(prepared, "sensor-1", None) == receipt


def test_terminal_memory_cannot_skip_overview(prepared):
    current = workflow.read(prepared)
    current = ledger.merge_memory(
        current,
        [
            {
                "claim_id": "claim-color",
                "coverage": "sufficient",
                "gap": None,
                "observations": [
                    {
                        "observation_id": "",
                        "claim_id": "claim-color",
                        "relation": "supports",
                        "text": "Blue",
                        "source": {"type": "memory", "record_id": "m1"},
                    }
                ],
            }
        ],
    )
    ledger.atomic_write(prepared, current)
    with pytest.raises(ValueError, match="overview"):
        workflow.run_final_check(
            prepared,
            {"scope": SCOPE, "sampling": "locate", "candidate_answer": "Blue"},
            None,
        )
    overview.run_overview(prepared, "sensor-1", None)


def test_failure_consumes_one_call_and_keeps_targeted_recovery(prepared, monkeypatch):
    def fail(*args):
        raise RuntimeError("provider failure")

    monkeypatch.setattr(overview, "run_uniform", fail)
    receipt = overview.run_overview(prepared, "sensor-1", None)
    assert not receipt["inspection"]["complete"]
    current = overview.merge_overview(
        prepared,
        assessments(
            receipt,
            observations=[],
            coverage_sufficient=False,
            missing_evidence=["Overview failed"],
        ),
    )
    assert current["vlm_calls_used"] == 1
    assert current["status"] == "in_progress"
    assert overview.run_overview(prepared, "sensor-1", None) == receipt


def test_interrupted_call_not_repeated(prepared, monkeypatch):
    real_write = ledger.atomic_write

    def interrupt(path, value):
        if str(path).endswith("overview.json"):
            raise KeyboardInterrupt()
        return real_write(path, value)

    monkeypatch.setattr(ledger, "atomic_write", interrupt)
    with pytest.raises(KeyboardInterrupt):
        overview.run_overview(prepared, "sensor-1", None)
    monkeypatch.setattr(ledger, "atomic_write", real_write)
    monkeypatch.setattr(
        overview,
        "run_uniform",
        lambda *args: pytest.fail("Must not repeat a paid call"),
    )
    receipt = overview.run_overview(prepared, "sensor-1", None)
    assert receipt["inspection"]["vlm_calls_used"] == 1
    assert "Interrupted" in receipt["inspection"]["error"]


def test_unknown_sensor_or_changed_receipt_is_rejected(prepared, monkeypatch):
    monkeypatch.setattr(vlm, "allowed_sensors_from_attempt_context", lambda: ("other",))
    with pytest.raises(ValueError, match="binding"):
        overview.run_overview(prepared, "sensor-1", None)
    assert not (prepared.parent / "overview.started.json").exists()


@pytest.mark.parametrize(
    "policy",
    [
        {"backend": "openai"},
        {"backend": "vllm"},
        {"backend": "rt_vlm", "locked": True, "fps": 1},
        {"backend": "rt_vlm", "locked": True, "max_frames": 60},
        {"backend": "rt_vlm", "locked": True, "chunk_duration": 30},
    ],
)
def test_unsupported_or_locked_sampling_fails_preflight(monkeypatch, policy):
    for name in (
        "VSS_VLM_LOCKED",
        "VSS_VLM_FPS",
        "VSS_VLM_MAX_FRAMES",
        "VSS_VLM_BACKEND",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(
        vlm,
        "_vss_command",
        lambda *a: SimpleNamespace(
            returncode=0, stdout=json.dumps({"vlm": policy}), stderr=""
        ),
    )
    with pytest.raises(ValueError):
        overview.cli_preflight(None)


def test_cli_sends_fixed_count_not_fps(monkeypatch, tmp_path):
    monkeypatch.setattr(vlm, "remote_vlm_spec", lambda: None)
    monkeypatch.setattr(overview, "cli_preflight", lambda *a: {})
    calls = []

    def run(project, *args):
        calls.append(args)
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"answer": "[0:10] Blue vehicle.", "job_id": "job-1"}),
            stderr="",
        )

    monkeypatch.setattr(vlm, "_vss_command", run)
    result = overview.run_uniform(None, SCOPE, "prompt", tmp_path)
    assert "--fps" not in calls[0]
    assert calls[0][calls[0].index("--chunk-duration") + 1] == "0"
    assert calls[0][calls[0].index("--max-frames") + 1] == "48"
    assert result["frames_sent"] is None
    assert result["usable"]


def test_remote_sampling_uses_served_duration_and_caps_images(monkeypatch, tmp_path):
    monkeypatch.setattr(vlm, "remote_vlm_spec", lambda: {"model": "unchanged"})
    monkeypatch.setattr(vlm, "_extract_clip", lambda *args: None)
    monkeypatch.setattr(vlm, "_ffmpeg_bin", lambda: "/test/ffmpeg")
    monkeypatch.setattr(
        overview.subprocess,
        "run",
        lambda *a, **kw: SimpleNamespace(stdout='{"format":{"duration":"240"}}'),
    )

    def sample(clip, dest, fps, count):
        assert fps == 48 / 240  # Not 48 / requested 10 seconds.
        assert count == 48
        dest.mkdir()
        files = [dest / f"{i}.jpg" for i in range(48)]
        for f in files:
            f.write_bytes(b"image")
        return files

    monkeypatch.setattr(vlm, "_sample_frames", sample)
    monkeypatch.setattr(
        vlm,
        "_call_remote_vlm",
        lambda model, prompt, files: {
            "ok": True,
            "job_id": "job-1",
            "text": "[0:10] Blue vehicle.",
        },
    )
    result = overview.run_uniform(None, SCOPE, "prompt", tmp_path)
    assert result["frames_sent"] == 48
    assert len(result["frame_manifest"]) == 48
    assert result["actual_frame_timestamps"] is None


def test_full_scope_rejects_gaps(monkeypatch):
    segments = [
        {"start_time": SCOPE["start"], "end_time": SCOPE["end"]},
        {"start_time": "2026-01-01T00:01:00Z", "end_time": "2026-01-01T00:02:00Z"},
    ]
    monkeypatch.setattr(
        vlm,
        "_vss_command",
        lambda *a: SimpleNamespace(
            returncode=0, stdout=json.dumps({"segments": segments}), stderr=""
        ),
    )
    with pytest.raises(ValueError, match="gaps"):
        overview.full_scope(None, "sensor-1")


def test_two_claim_budget_never_exceeds_seven_after_overview(prepared, monkeypatch):
    # The remaining call allocator sees the overview's canonical budget charge.
    receipt = overview.run_overview(prepared, "sensor-1", None)
    overview.merge_overview(
        prepared,
        assessments(
            receipt, coverage_sufficient=False, missing_evidence=["Need detail"]
        ),
    )
    before = workflow.read(prepared)
    assert workflow.workflow_contract(before)["ledger"]["normal_calls_remaining"] == 7


def test_two_claim_large_scopes_stop_at_eight_normal_calls(prepared):
    from test_inspection_workflow import claim

    plan = workflow.read(prepared)["plan"]
    plan["claims"].append(claim("claim-other"))
    current = ledger.initialize_ledger(
        plan, requires_choice=True, overview_required=True
    )
    current = ledger.merge_memory(
        current,
        [
            {
                "claim_id": c["claim_id"],
                "observations": [],
                "coverage": "none",
                "gap": "Need detail",
            }
            for c in plan["claims"]
        ],
    )
    ledger.atomic_write(prepared, current)
    overview.run_overview(prepared, "sensor-1", None)
    values = [
        assessment(
            {"task": {"task_id": f"inspect-{c['claim_id']}-r1"}},
            coverage_sufficient=False,
            missing_evidence=["Need detail"],
        )
        for c in plan["claims"]
    ]
    overview.merge_overview(prepared, values)
    specs = [spec(c["claim_id"]) for c in plan["claims"]]
    for item in specs:
        item["scope"]["end"] = "2026-01-01T00:10:00Z"
    batch = workflow.run_batch(prepared, specs, None)
    assert [r["task"]["max_vlm_calls"] for r in batch["results"]] == [4, 3]
    current = workflow.merge_batch(
        prepared,
        prepared.parent / "rounds/round-1/batch.json",
        [
            assessment(
                item,
                coverage_sufficient=False,
                missing_evidence=["Incomplete sampling"],
            )
            for item in batch["results"]
        ],
    )
    assert current["vlm_calls_used"] == 8
    assert current["stop_reason"] == "budget_exhausted"
    check = workflow.run_final_check(
        prepared,
        {"scope": SCOPE, "sampling": "detail", "candidate_answer": "Blue"},
        None,
    )
    assert check["inspection"]["vlm_calls_used"] == 1
    assert workflow.read(prepared)["vlm_calls_used"] == 8


def test_locked_environment_rate_overrides_saved_unlocked_policy(monkeypatch):
    monkeypatch.setenv("VSS_VLM_LOCKED", "true")
    monkeypatch.setenv("VSS_VLM_FPS", "1")
    monkeypatch.setenv("VSS_VLM_BACKEND", "rt_vlm")
    monkeypatch.setattr(
        vlm,
        "_vss_command",
        lambda *a: SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"vlm": {"backend": "rt_vlm", "locked": False}}),
            stderr="",
        ),
    )
    with pytest.raises(ValueError, match="Locked FPS"):
        overview.cli_preflight(None)
