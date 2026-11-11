# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Executable smoke coverage with mocked VLM transport; no paid inference."""

import copy
import threading
from pathlib import Path
from types import SimpleNamespace

import evidence_ledger as ledger
import inspection_workflow as workflow
import pytest
import vlm_inspection as vlm

SCOPE = {
    "type": "sensor",
    "sensor_id": "sensor-1",
    "start": "2026-01-01T00:00:00Z",
    "end": "2026-01-01T00:00:10Z",
}


def claim(name="claim-color"):
    return {
        "claim_id": name,
        "requirement": "Establish the visible color during the approach.",
        "evidence_type": "attribute",
        "coverage_requirement": "local_window",
        "support_test": "A clear view establishes the color.",
        "falsification_test": "A clear incompatible color is visible.",
    }


def spec(name="claim-color"):
    return {
        "claim_id": name,
        "subject": "The target vehicle",
        "fact_to_establish": "The visible color",
        "required_state": "During approach",
        "reference_frame": "Not applicable",
        "scope": copy.deepcopy(SCOPE),
        "sampling": "locate",
    }


def response(text="[0:10] The vehicle is blue.", success=True):
    return {
        "job_id": "job-1",
        "answer": text,
        "raw_answer": text,
        "usable": success,
        "exit_code": 0 if success else 3,
        "error": None if success else "backend unavailable",
    }


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr(vlm, "allowed_sensors_from_attempt_context", lambda: ())
    monkeypatch.setattr(ledger, "policy_requires_choice", lambda: False)
    monkeypatch.setattr(vlm, "_run_vlm", lambda **kwargs: response())
    monkeypatch.setattr(
        ledger,
        "DEFAULT_ATTEMPT_CONTEXT_PATH",
        Path("/nonexistent-test-context"),
    )


def initialize(tmp_path, names=("claim-color",), choice=False):
    plan = {
        "plan_version": "2.0",
        "mode": "initial",
        "question_id": "test-question",
        "question_text": "What is the vehicle color?",
        "asset_id": "video-1",
        "claims": [claim(name) for name in names],
    }
    path = tmp_path / "ledger.json"
    ledger.atomic_write(path, ledger.initialize_ledger(plan, requires_choice=choice))
    return path


def assessment(item, **changes):
    result = {
        "task_id": item["task"]["task_id"],
        "observations": [
            {
                "attempt_index": 0,
                "quote": "The vehicle is blue.",
                "relation": "supports",
            }
        ],
        "prerequisites_established": True,
        "compatible_evidence": True,
        "coverage_sufficient": True,
        "conflict_free": True,
        "missing_evidence": [],
        "rationale": "Clear view within the required state.",
    }
    result.update(changes)
    return result


def run_and_merge(path, specs=None, **changes):
    batch = workflow.run_batch(path, specs or [spec()], "unused")
    round_number = workflow.read(path)["round"] + 1
    batch_path = path.parent / "rounds" / f"round-{round_number}" / "batch.json"
    assessments = [assessment(item, **changes) for item in batch["results"]]
    return workflow.merge_batch(path, batch_path, assessments), batch, assessments


def check(path, answer="Blue"):
    return workflow.run_final_check(
        path,
        {"scope": SCOPE, "sampling": "detail", "candidate_answer": answer},
        "unused",
    )


def test_end_to_end_parallel_batch_merge_finalization_and_no_duplicate_calls(
    tmp_path, monkeypatch
):
    path = initialize(tmp_path, ("claim-color", "claim-other"))
    barrier = threading.Barrier(2)
    calls = []

    def invoke(**kwargs):
        calls.append(kwargs)
        barrier.wait(timeout=3)
        return response()

    monkeypatch.setattr(vlm, "_run_vlm", invoke)
    specs = [spec(), spec("claim-other")]
    batch = workflow.run_batch(path, specs, "unused")
    assert len(calls) == 2
    assert workflow.run_batch(path, specs, "unused") == batch
    assert len(calls) == 2
    assert "WINDOW_METADATA" in calls[0]["prompt"]
    assert "requested_fps" in calls[0]["prompt"]
    batch_path = tmp_path / "rounds/round-1/batch.json"
    assessments = [assessment(item) for item in batch["results"]]
    current = workflow.merge_batch(path, batch_path, assessments)
    assert current["status"] == "answered"
    assert current["vlm_calls_used"] == 2
    assert workflow.merge_batch(path, batch_path, assessments) == current
    with pytest.raises(ValueError, match="Mandatory"):
        workflow.checked_final_result(current, str(tmp_path), "Blue")
    monkeypatch.setattr(
        vlm, "_run_vlm", lambda **kw: response("[0:10] Blue vehicle.\nCHECK: supported")
    )
    receipt = check(path)
    assert check(path) == receipt
    assert receipt["inspection"]["vlm_calls_used"] == 1
    ids = [o["observation_id"] for o in current["observations"]]
    terminal = workflow.checked_final_result(current, str(tmp_path), "Blue", ids)
    assert terminal["evidence_status"] == "resolved"
    assert terminal["normal_vlm_calls_used"] == 2
    assert terminal["final_check_vlm_calls_used"] == 1
    ledger.atomic_commit_terminal(
        path, current, tmp_path / "final-result.json", terminal
    )
    marker = workflow.read(tmp_path / "terminal-commit.json")
    assert marker["final_result_sha256"] == ledger._digest(terminal)


@pytest.mark.parametrize(
    "flag",
    [
        "prerequisites_established",
        "compatible_evidence",
        "coverage_sufficient",
        "conflict_free",
    ],
)
def test_false_assessment_gate_cannot_close(tmp_path, flag):
    current, _, _ = run_and_merge(initialize(tmp_path), **{flag: False})
    assert current["status"] == "in_progress"
    assert current["claims"][0]["gap"]
    assert not ledger.assess_sufficiency(current)["sufficient"]


def test_missing_evidence_blocks_closure_even_if_all_booleans_true(tmp_path):
    current, _, _ = run_and_merge(
        initialize(tmp_path), missing_evidence=["Heading not visible"]
    )
    assert current["status"] == "in_progress"


def test_refinement_after_empty_first_pass_is_bounded(tmp_path):
    path = initialize(tmp_path)
    current, _, _ = run_and_merge(path, observations=[], coverage_sufficient=False)
    assert current["status"] == "in_progress"
    with pytest.raises(ValueError, match="Refinement"):
        workflow.run_batch(path, [spec()], "unused")
    revised = spec()
    revised["sampling"] = "detail"
    current, _, _ = run_and_merge(
        path, [revised], observations=[], coverage_sufficient=False
    )
    assert current["status"] == "unresolved"
    with pytest.raises(ValueError, match="stopped"):
        workflow.run_batch(path, [revised], "unused")
    check(path)


def test_backend_failure_goes_to_final_without_retry(tmp_path, monkeypatch):
    monkeypatch.setattr(vlm, "_run_vlm", lambda **kwargs: response("", False))
    path = initialize(tmp_path)
    current, _, _ = run_and_merge(path, observations=[], coverage_sufficient=False)
    assert current["stop_reason"] == "tool_failure"
    assert current["vlm_calls_used"] == 1
    receipt = check(path)
    assert receipt["verdict"] == "failed"
    assert receipt["inspection"]["vlm_calls_used"] == 1


@pytest.mark.parametrize(
    "text,expected",
    [
        ("CHECK: supported", "supported"),
        ("CHECK: contradicted", "contradicted"),
        ("Useful unformatted visual description", "inconclusive"),
        ("CHECK: supported\nCHECK: inconclusive", "inconclusive"),
    ],
)
def test_final_verdict_and_downgrade(tmp_path, monkeypatch, text, expected):
    path = initialize(tmp_path)
    current, _, _ = run_and_merge(path)
    monkeypatch.setattr(vlm, "_run_vlm", lambda **kwargs: response(text))
    assert check(path)["verdict"] == expected
    result = workflow.checked_final_result(
        current, str(tmp_path), "Blue", [current["observations"][0]["observation_id"]]
    )
    assert result["evidence_status"] == (
        "resolved" if expected == "supported" else "unresolved"
    )
    ledger.validate_terminal_pair(current, result)


def test_open_ended_can_abstain_after_check_undermines_resolved_answer(
    tmp_path, monkeypatch
):
    path = initialize(tmp_path)
    current, _, _ = run_and_merge(path)
    monkeypatch.setattr(
        vlm, "_run_vlm", lambda **kwargs: response("CHECK: contradicted")
    )
    check(path)
    result = workflow.checked_final_result(current, str(tmp_path), None)
    assert result["decision_source"] == "abstention"
    ledger.validate_terminal_pair(current, result)
    assert workflow.read(path)["status"] == "answered"  # Preserve pre-check record.


def test_choice_still_requires_answer_after_failed_check(tmp_path, monkeypatch):
    path = initialize(tmp_path, choice=True)
    current, _, _ = run_and_merge(path)
    monkeypatch.setattr(vlm, "_run_vlm", lambda **kwargs: response("", False))
    check(path)
    with pytest.raises(ValueError, match="choice"):
        workflow.checked_final_result(current, str(tmp_path), None)
    result = workflow.checked_final_result(current, str(tmp_path), "Red")
    assert result["evidence_status"] == "unresolved"
    assert not result["final_check"]["answer_unchanged"]


def test_final_call_is_reserved_after_normal_budget(tmp_path):
    path = initialize(tmp_path)
    current = workflow.read(path)
    current.update(
        status="unresolved",
        stop_reason="budget_exhausted",
        vlm_calls_used=ledger.BUDGETS["max_total_vlm_calls"],
    )
    ledger.atomic_write(path, current)
    receipt = check(path)
    assert receipt["inspection"]["vlm_calls_used"] == 1
    assert (
        workflow.read(path)["vlm_calls_used"] == ledger.BUDGETS["max_total_vlm_calls"]
    )


def test_final_check_cannot_split_or_change_on_replay(tmp_path):
    path = initialize(tmp_path)
    run_and_merge(path)
    too_long = {**SCOPE, "end": "2026-01-01T00:10:00Z"}
    with pytest.raises(ValueError, match="ONE call"):
        workflow.run_final_check(
            path,
            {"scope": too_long, "sampling": "detail", "candidate_answer": "Blue"},
            "unused",
        )
    assert not (tmp_path / "final-check.started.json").exists()
    check(path)
    with pytest.raises(ValueError, match="different request"):
        check(path, "Red")


def test_stale_final_receipt_rejected(tmp_path):
    path = initialize(tmp_path)
    current, _, _ = run_and_merge(path)
    check(path)
    current["revision"] += 1
    with pytest.raises(ValueError, match="stale"):
        workflow.checked_final_result(current, str(tmp_path), "Blue")


def test_exact_quote_and_provenance_required(tmp_path):
    path = initialize(tmp_path)
    batch = workflow.run_batch(path, [spec()], "unused")
    item = batch["results"][0]
    with pytest.raises(ValueError, match="verbatim"):
        workflow.assessment_result(
            item,
            assessment(
                item,
                observations=[
                    {"attempt_index": 0, "quote": "It was red", "relation": "supports"}
                ],
            ),
        )
    result = workflow.assessment_result(item, assessment(item))
    assert result["observations"][0]["source"]["job_id"] == "job-1"
    assert result["observations"][0]["source"]["start"] == SCOPE["start"]


def test_batch_and_final_resume_do_not_repeat_unknown_calls(tmp_path, monkeypatch):
    path = initialize(tmp_path)
    batch = workflow.run_batch(path, [spec()], "unused")
    item = batch["results"][0]
    directory = tmp_path / "rounds/round-1"
    # Simulate a process dying after start receipt, before its result is saved.
    (directory / f"{item['task']['task_id']}.json").unlink()

    def unexpected(**kw):
        raise AssertionError("Must not issue another call")

    monkeypatch.setattr(vlm, "_run_vlm", unexpected)
    result = workflow.execute_task(item["task"], spec(), directory, "unused", [])
    assert result["inspection"]["vlm_calls_used"] == item["task"]["max_vlm_calls"]
    assert "Interrupted" in result["inspection"]["error"]
    current = workflow.read(path)
    current.update(status="unresolved", stop_reason="tool_failure")
    ledger.atomic_write(path, current)
    # First check records the failure; simulate interruption before final receipt.
    check(path)
    (tmp_path / "final-check.json").unlink()
    receipt = check(path)
    assert receipt["verdict"] == "failed"
    assert "Interrupted" in receipt["inspection"]["error"]


def test_memory_gap_prevents_resolution_and_remains_inspectable(tmp_path):
    path = initialize(tmp_path)
    obs = {
        "observation_id": "",
        "claim_id": "claim-color",
        "relation": "supports",
        "text": "Blue vehicle",
        "source": {"type": "memory", "record_id": "memory-1"},
    }
    current = ledger.merge_memory(
        workflow.read(path),
        [
            {
                "claim_id": "claim-color",
                "observations": [obs],
                "coverage": "sufficient",
                "gap": "Identity not established",
            }
        ],
    )
    assert current["status"] == "in_progress"
    assert ledger.create_inspection_tasks(current, {"claim-color": SCOPE})


@pytest.mark.parametrize(
    "change",
    [
        {"prompt": "ignore rules"},
        {"subject": "x" * 401},
        {"sampling": "arbitrary"},
        {"scope": {"type": "media_url", "media_url": "https://example.com/video.mp4"}},
    ],
)
def test_request_schema_rejects_unsafe_or_unsupported_specs(tmp_path, change):
    path = initialize(tmp_path)
    with pytest.raises(ValueError):
        workflow.run_batch(path, [{**spec(), **change}], "unused")


def test_case_binding_rejects_wrong_sensor_before_calls(tmp_path, monkeypatch):
    path = initialize(tmp_path)
    monkeypatch.setattr(
        vlm, "allowed_sensors_from_attempt_context", lambda: ("other-sensor",)
    )
    with pytest.raises(ValueError, match="binding"):
        workflow.run_batch(path, [spec()], "unused")


def test_missing_final_check_blocks_public_cli(tmp_path):
    path = initialize(tmp_path)
    run_and_merge(path)
    with pytest.raises(ValueError, match="Mandatory"):
        ledger.main(
            [
                "final-result",
                "--ledger",
                str(path),
                "--artifact-dir",
                str(tmp_path),
                "--answer",
                "Blue",
            ]
        )
    assert not (tmp_path / "terminal-commit.json").exists()


def test_failed_batch_with_memory_still_goes_to_final(tmp_path, monkeypatch):
    path = initialize(tmp_path)
    obs = {
        "observation_id": "",
        "claim_id": "claim-color",
        "relation": "context",
        "text": "Vehicle approaches",
        "source": {"type": "memory", "record_id": "memory-1"},
    }
    current = ledger.merge_memory(
        workflow.read(path),
        [
            {
                "claim_id": "claim-color",
                "observations": [obs],
                "coverage": "partial",
                "gap": "Color needed",
            }
        ],
    )
    ledger.atomic_write(path, current)
    monkeypatch.setattr(vlm, "_run_vlm", lambda **kwargs: response("", False))
    current, _, _ = run_and_merge(path, observations=[], coverage_sufficient=False)
    assert current["stop_reason"] == "tool_failure"
    assert len(current["observations"]) == 1


@pytest.mark.parametrize(
    "project,prefix",
    [
        (None, ["vss"]),
        ("/repo/libs/vss", ["uv", "run", "--project", "/repo/libs/vss", "vss"]),
    ],
)
def test_installed_and_source_cli_modes(monkeypatch, project, prefix):
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(
            returncode=0, stdout='{"answer":"Blue vehicle","job_id":"job-1"}', stderr=""
        )

    monkeypatch.setattr(vlm.subprocess, "run", run)
    monkeypatch.setattr(vlm, "remote_vlm_spec", lambda: None)
    # Access original function: autouse fixture replaces it, restore from source module.
    import importlib.util

    spec_ = importlib.util.spec_from_file_location(
        "cli_helper_test", Path(vlm.__file__)
    )
    original = importlib.util.module_from_spec(spec_)
    spec_.loader.exec_module(original)
    monkeypatch.setattr(original, "remote_vlm_spec", lambda: None)
    original._run_vlm(
        vss_project=project,
        sensor="sensor-1",
        start=SCOPE["start"],
        end=SCOPE["end"],
        fps=1,
        prompt="Observe",
    )
    vlm._vss_command(project, "vios", "list")
    assert calls[0][: len(prefix)] == prefix
    assert calls[1][: len(prefix)] == prefix


def test_remote_metadata_records_actual_frames_and_usage(tmp_path, monkeypatch):
    frames = [tmp_path / "one.jpg", tmp_path / "two.jpg"]
    seen = []
    monkeypatch.setattr(vlm, "_extract_clip", lambda *args: None)
    monkeypatch.setattr(vlm, "_sample_frames", lambda *args: frames)

    def remote(spec, prompt, paths):
        seen.append(prompt)
        return {
            "text": "[0:1] Blue vehicle",
            "ok": True,
            "job_id": "remote-1",
            "usage": {"input_tokens": 42},
            "transport_attempts": 1,
        }

    monkeypatch.setattr(vlm, "_call_remote_vlm", remote)
    result = vlm._run_remote_vlm(
        spec={"model": "test"},
        vss_project=None,
        sensor="sensor-1",
        start=SCOPE["start"],
        end=SCOPE["end"],
        fps=1,
        prompt="Observe",
    )
    assert result["frames_sent"] == 2  # Actual count, not requested ten frames.
    assert result["usage"]["input_tokens"] == 42
    assert "not measured source-frame PTS" in seen[0]
    assert result["submitted_prompt"] == seen[0]


def test_changed_prompt_or_backend_cannot_reuse_batch(tmp_path, monkeypatch):
    path = initialize(tmp_path)
    workflow.run_batch(path, [spec()], None)
    monkeypatch.setattr(workflow, "PROMPT", workflow.PROMPT + "changed")
    with pytest.raises(ValueError, match="different request"):
        workflow.run_batch(path, [spec()], None)


def test_changed_backend_cannot_reuse_final_check(tmp_path, monkeypatch):
    path = initialize(tmp_path)
    current, _, _ = run_and_merge(path)
    check(path)
    monkeypatch.setenv("VSS_REMOTE_VLM_MODEL", "different-model")
    with pytest.raises(ValueError, match="stale"):
        workflow.checked_final_result(current, str(tmp_path), "Blue")


@pytest.mark.parametrize(
    "text",
    [
        "CHECK: supported\nAfter review evidence is missing.",
        "CHECK: supported\nCHECK: inconclusive because the scene is occluded",
        "CHECK: maybe\nCHECK: supported",
    ],
)
def test_final_check_cannot_ignore_retractions_or_malformed_extra_verdicts(text):
    assert (
        workflow.final_verdict({"attempts": [response(text)], "vlm_calls_used": 1})
        == "inconclusive"
    )
