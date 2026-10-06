# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for frame-aware VLM inspection and quality recovery."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "vlm_inspection.py"
SPEC = importlib.util.spec_from_file_location("vlm_inspection", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
vlm = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(vlm)

START = "2026-09-30T00:00:00Z"


def test_short_window_is_not_split() -> None:
    windows = vlm.split_sensor_window(START, "2026-09-30T00:00:10Z", 2)
    assert len(windows) == 1
    assert windows[0]["frames_requested"] == 20


def test_two_fps_window_splits_at_thirty_seconds() -> None:
    windows = vlm.split_sensor_window(START, "2026-09-30T00:02:00Z", 2)
    assert len(windows) == 4
    assert {window["frames_requested"] for window in windows} == {60}
    assert windows[0]["start"] == START
    assert windows[-1]["end"] == "2026-09-30T00:02:00Z"
    assert all(left["end"] == right["start"] for left, right in zip(windows, windows[1:]))


def test_half_fps_allows_two_minute_window() -> None:
    windows = vlm.split_sensor_window(START, "2026-09-30T00:02:00Z", 0.5)
    assert len(windows) == 1
    assert windows[0]["frames_requested"] == 60


def test_invalid_window_and_fps_are_rejected() -> None:
    with pytest.raises(ValueError, match="before"):
        vlm.split_sensor_window(START, START, 1)
    with pytest.raises(ValueError, match="fps"):
        vlm.split_sensor_window(START, "2026-09-30T00:00:10Z", 0)


def test_empty_reasoning_and_repetition_are_degenerate() -> None:
    assert vlm.classify_vlm_output("")["reason"] == "empty"
    assert vlm.classify_vlm_output("<think>private</think>")["reason"] == "empty"
    repeated = "\n".join(f"00:00:{index:02d} worker holds hat" for index in range(10))
    assert vlm.classify_vlm_output(repeated)["reason"] == "repetitive_template"


def test_reverse_chronology_is_degenerate() -> None:
    output = "\n".join(
        [
            "00:00:40 worker has hat",
            "00:00:30 worker removes hat",
            "00:00:20 worker holds hat",
            "00:00:10 worker replaces hat",
        ]
    )
    assert vlm.classify_vlm_output(output)["reason"] == "reverse_chronology"


def test_legitimate_negative_is_usable() -> None:
    verdict = vlm.classify_vlm_output(
        "The worker is occluded, so no hat transition is visibly established."
    )
    assert verdict["usable"] is True


def test_inspection_retries_degenerate_output_once(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = iter(
        [
            {
                "exit_code": 0,
                "job_id": "vlm-1",
                "status": "succeeded",
                "answer": "",
                "usable": False,
                "quality_reason": "empty",
                "error": None,
            },
            {
                "exit_code": 0,
                "job_id": "vlm-2",
                "status": "succeeded",
                "answer": "The worker removes and replaces the hat once.",
                "usable": True,
                "quality_reason": "ok",
                "error": None,
            },
        ]
    )
    monkeypatch.setattr(vlm, "_run_vlm", lambda **_: next(responses))
    result = vlm.inspect_sensor_scope(
        vss_project="/repo/libs/vss",
        sensor="sensor-1",
        start=START,
        end="2026-09-30T00:00:20Z",
        fps=2,
        prompt="Report hat transitions only.",
        calls_budget=2,
    )
    assert result["vlm_calls_used"] == 2
    assert result["complete"] is True
    assert [attempt["retry"] for attempt in result["attempts"]] == [0, 1]


def test_inspection_stops_at_call_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        vlm,
        "_run_vlm",
        lambda **_: {
            "exit_code": 0,
            "job_id": "vlm-1",
            "status": "succeeded",
            "answer": "Visible activity.",
            "usable": True,
            "quality_reason": "ok",
            "error": None,
        },
    )
    result = vlm.inspect_sensor_scope(
        vss_project="/repo/libs/vss",
        sensor="sensor-1",
        start=START,
        end="2026-09-30T00:02:00Z",
        fps=2,
        prompt="Locate the event.",
        calls_budget=2,
    )
    assert len(result["planned_windows"]) == 4
    assert result["vlm_calls_used"] == 2
    assert result["window_inspection"] == "partial"
    assert result["claim_sufficiency"] is None


def test_degenerate_window_does_not_starve_remaining_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = iter(
        [
            {
                "exit_code": 0,
                "job_id": "vlm-empty",
                "status": "succeeded",
                "answer": "",
                "usable": False,
                "quality_reason": "empty",
                "error": None,
            },
            {
                "exit_code": 0,
                "job_id": "vlm-second-window",
                "status": "succeeded",
                "answer": "The event is visible in the second window.",
                "usable": True,
                "quality_reason": "ok",
                "error": None,
            },
        ]
    )
    monkeypatch.setattr(vlm, "_run_vlm", lambda **_: next(responses))
    result = vlm.inspect_sensor_scope(
        vss_project="/repo/libs/vss",
        sensor="sensor-1",
        start=START,
        end="2026-09-30T00:02:00Z",
        fps=1,
        prompt="Locate the event.",
        calls_budget=2,
    )
    assert [attempt["window_index"] for attempt in result["attempts"]] == [0, 1]
    assert [attempt["retry"] for attempt in result["attempts"]] == [0, 0]
    assert result["window_inspection"] == "partial"
    assert result["claim_sufficiency"] is None


def test_degenerate_retry_runs_after_initial_window_coverage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = iter(
        [
            {
                "exit_code": 0,
                "job_id": "vlm-empty",
                "status": "succeeded",
                "answer": "",
                "usable": False,
                "quality_reason": "empty",
                "error": None,
            },
            {
                "exit_code": 0,
                "job_id": "vlm-second-window",
                "status": "succeeded",
                "answer": "The second window is usable.",
                "usable": True,
                "quality_reason": "ok",
                "error": None,
            },
            {
                "exit_code": 0,
                "job_id": "vlm-repair",
                "status": "succeeded",
                "answer": "The first window is usable after repair.",
                "usable": True,
                "quality_reason": "ok",
                "error": None,
            },
        ]
    )
    monkeypatch.setattr(vlm, "_run_vlm", lambda **_: next(responses))
    result = vlm.inspect_sensor_scope(
        vss_project="/repo/libs/vss",
        sensor="sensor-1",
        start=START,
        end="2026-09-30T00:02:00Z",
        fps=1,
        prompt="Locate the event.",
        calls_budget=3,
    )
    assert [attempt["window_index"] for attempt in result["attempts"]] == [0, 1, 0]
    assert [attempt["retry"] for attempt in result["attempts"]] == [0, 0, 1]
    assert result["complete"] is True


def test_wrong_sensor_is_rejected_before_vlm_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        vlm,
        "_run_vlm",
        lambda **_: pytest.fail("wrong-sensor request reached the VLM"),
    )
    with pytest.raises(ValueError, match="sensor mismatch"):
        vlm.inspect_sensor_scope(
            vss_project="/repo/libs/vss",
            sensor="wrong-sensor",
            expected_sensor="canonical-sensor",
            start=START,
            end="2026-09-30T00:00:20Z",
            fps=2,
            prompt="Locate the event.",
            calls_budget=2,
        )


def test_task_sensor_must_also_match_current_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        vlm,
        "_run_vlm",
        lambda **_: pytest.fail("cross-case sensor request reached the VLM"),
    )
    with pytest.raises(ValueError, match="current case binding"):
        vlm.inspect_sensor_scope(
            vss_project="/repo/libs/vss",
            sensor="other-video",
            expected_sensor="other-video",
            allowed_sensors=("current-video", "current-sensor-uuid"),
            start=START,
            end="2026-09-30T00:00:20Z",
            fps=2,
            prompt="Locate the event.",
            calls_budget=2,
        )


def test_expected_sensor_is_loaded_from_task(tmp_path: Path) -> None:
    task = tmp_path / "task.json"
    task.write_text(
        json.dumps(
            {
                "media_scope": {
                    "type": "sensor",
                    "sensor_id": "canonical-sensor",
                    "start": START,
                    "end": "2026-09-30T00:00:20Z",
                },
                "max_vlm_calls": 2,
            }
        ),
        encoding="utf-8",
    )
    assert vlm.expected_sensor_from_task(str(task)) == "canonical-sensor"


def test_expected_sensor_is_selected_from_task_list(tmp_path: Path) -> None:
    tasks = tmp_path / "tasks.json"
    tasks.write_text(
        json.dumps(
            [
                {
                    "task_id": "inspect-claim-one-r1",
                    "media_scope": {
                        "type": "sensor",
                        "sensor_id": "sensor-one",
                        "start": START,
                        "end": "2026-09-30T00:00:20Z",
                    },
                    "max_vlm_calls": 2,
                },
                {
                    "task_id": "inspect-claim-two-r1",
                    "media_scope": {
                        "type": "sensor",
                        "sensor_id": "sensor-two",
                        "start": START,
                        "end": "2026-09-30T00:00:20Z",
                    },
                    "max_vlm_calls": 2,
                },
            ]
        ),
        encoding="utf-8",
    )
    assert (
        vlm.expected_sensor_from_task(str(tasks), "inspect-claim-two-r1")
        == "sensor-two"
    )


def test_allowed_sensors_are_loaded_from_attempt_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = tmp_path / "context.json"
    context.write_text(
        json.dumps(
            {
                "video_id": "current-video",
                "sensor_id": "current-sensor-uuid",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv(vlm.ATTEMPT_CONTEXT_ENV, str(context))
    assert vlm.allowed_sensors_from_attempt_context() == (
        "current-video",
        "current-sensor-uuid",
    )


def test_exact_duplicate_window_call_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invoked = False

    def unexpected_call(**_: object) -> dict:
        nonlocal invoked
        invoked = True
        raise AssertionError("duplicate call reached VLM")

    monkeypatch.setattr(vlm, "_run_vlm", unexpected_call)
    prompt = "Locate the forklift crossing."
    result = vlm.inspect_sensor_scope(
        vss_project="/repo/libs/vss",
        sensor="sensor-1",
        start=START,
        end="2026-09-30T00:00:20Z",
        fps=2,
        prompt=prompt,
        calls_budget=2,
        prior_attempts=[
            {
                "sensor_id": "sensor-1",
                "start": START,
                "end": "2026-09-30T00:00:20Z",
                "fps": 2,
                "prompt_sha256": vlm._prompt_digest(prompt),
            }
        ],
    )
    assert invoked is False
    assert result["vlm_calls_used"] == 0
    assert result["window_inspection"] == "none"
    assert result["reused_evidence"] == []
    assert result["rejected_duplicates"][0]["reason"] == "duplicate_window_call"


def test_higher_density_or_different_prompt_is_not_a_duplicate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        vlm,
        "_run_vlm",
        lambda **_: {
            "exit_code": 0,
            "job_id": "vlm-2",
            "status": "succeeded",
            "answer": "The forklift crosses the intersection.",
            "usable": True,
            "quality_reason": "ok",
            "error": None,
        },
    )
    result = vlm.inspect_sensor_scope(
        vss_project="/repo/libs/vss",
        sensor="sensor-1",
        start=START,
        end="2026-09-30T00:00:20Z",
        fps=2,
        prompt="Verify the forklift color.",
        calls_budget=1,
        prior_attempts=[
            {
                "sensor_id": "sensor-1",
                "start": START,
                "end": "2026-09-30T00:00:20Z",
                "fps": 2,
                "prompt_sha256": vlm._prompt_digest("Locate the forklift crossing."),
            }
        ],
    )
    assert result["vlm_calls_used"] == 1
    assert result["complete"] is True
    assert result["rejected_duplicates"] == []


def _completed(answer: str, *, code: int = 0, job_id: str = "vlm-1") -> object:
    class Completed:
        returncode = code
        stdout = ""
        stderr = ""

    body = {"answer": answer, "job_id": job_id, "status": "completed"}
    marker = {
        "event": "vss_job_completed",
        "group": "vlm",
        "job_id": job_id,
        "status": "completed",
        "persisted": code != 6,
        "exit_hint": code,
    }
    completed = Completed()
    completed.stdout = json.dumps(body) + "\n" + json.dumps(marker, separators=(",", ":")) + "\n"
    completed.stderr = ""
    return completed


def test_parser_prefers_answer_over_completion_marker() -> None:
    compact = _completed("A worker wears a hard hat.").stdout
    parsed = vlm.parse_vlm_stdout(compact)
    assert parsed["answer"] == "A worker wears a hard hat."
    assert parsed["job_id"] == "vlm-1"

    pretty_body = {
        "answer": "A worker wears a hard hat.",
        "job_id": "vlm-1",
        "status": "completed",
    }
    pretty = (
        json.dumps(pretty_body, indent=2)
        + "\n"
        + json.dumps(
            {
                "event": "vss_job_completed",
                "job_id": "vlm-1",
                "status": "completed",
            },
            separators=(",", ":"),
        )
        + "\n"
    )
    assert vlm.parse_vlm_stdout(pretty)["answer"] == "A worker wears a hard hat."


def test_parser_keeps_escaped_answer_text() -> None:
    stdout = (
        '{"answer":"line1\\nline2 {not a marker}","job_id":"vlm-9","status":"completed"}\n'
        '{"event":"vss_job_completed","job_id":"vlm-9","status":"completed"}\n'
    )
    parsed = vlm.parse_vlm_stdout(stdout)
    assert parsed["answer"] == "line1\nline2 {not a marker}"
    assert parsed["job_id"] == "vlm-9"


def test_parser_keeps_marker_identity_without_inventing_an_answer() -> None:
    parsed = vlm.parse_vlm_stdout(
        '{"event":"vss_job_completed","job_id":"vlm-1","status":"completed","persisted":true}\n'
    )
    assert "answer" not in parsed
    assert parsed["job_id"] == "vlm-1"
    assert parsed["status"] == "completed"
    assert vlm.parse_vlm_stdout("{not json\n") == {}
    assert vlm.parse_vlm_stdout('{"status":"completed","job_id":"vlm-1"}\n') == {}


def test_pretty_output_is_one_call_and_exit_6_keeps_the_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def fake_run(command: list[str], **_: object) -> object:
        calls.append(command)
        body = {
            "answer": "The hard hat is visible.",
            "status": "completed",
        }
        marker = {
            "event": "vss_job_completed",
            "job_id": "vlm-6",
            "status": "completed",
            "persisted": False,
            "exit_hint": 6,
        }
        class Completed:
            returncode = 6
            stderr = "persistence failed"

        completed = Completed()
        completed.stdout = (
            json.dumps(body, indent=2) + "\n" + json.dumps(marker, separators=(",", ":")) + "\n"
        )
        return completed

    monkeypatch.setattr(vlm.subprocess, "run", fake_run)
    result = vlm.inspect_sensor_scope(
        vss_project="/repo/libs/vss",
        sensor="sensor-1",
        start=START,
        end="2026-09-30T00:00:10Z",
        fps=1,
        prompt="Is a hard hat visible?",
        calls_budget=2,
    )
    assert len(calls) == 1
    assert "--raw" in calls[0]
    assert result["vlm_calls_used"] == 1
    attempt = result["attempts"][0]
    assert attempt["answer"] == "The hard hat is visible."
    assert attempt["job_id"] == "vlm-6"
    assert attempt["usable"] is True
    assert attempt["persistence_limited"] is True
    assert attempt["exit_code"] == 6


def _rejecting_run() -> object:
    def unexpected(**_: object) -> dict:
        raise AssertionError("inference ran outside the assignment")

    return unexpected


def _task(sensor: str = "sensor-1", start: str = START, end: str = "2026-09-30T00:00:20Z", calls: int = 2) -> dict:
    return {
        "task_id": "inspect-claim-color-r1",
        "max_vlm_calls": calls,
        "media_scope": {
            "type": "sensor",
            "sensor_id": sensor,
            "start": start,
            "end": end,
        },
    }


def test_assignment_violations_make_no_inference_call(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(vlm, "_run_vlm", _rejecting_run())
    assigned = {
        "expected_sensor": "sensor-1",
        "assigned_start": START,
        "assigned_end": "2026-09-30T00:00:20Z",
        "task_call_allocation": 2,
    }
    common = {
        "vss_project": "/repo/libs/vss",
        "fps": 1,
        "prompt": "Locate the event.",
        "calls_budget": 1,
        **assigned,
    }
    with pytest.raises(ValueError, match="sensor mismatch"):
        vlm.inspect_sensor_scope(
            sensor="other-sensor",
            start=START,
            end="2026-09-30T00:00:10Z",
            **common,
        )
    with pytest.raises(ValueError, match="outside the assigned"):
        vlm.inspect_sensor_scope(
            sensor="sensor-1",
            start="2026-09-29T23:59:50Z",
            end="2026-09-30T00:00:10Z",
            **common,
        )
    with pytest.raises(ValueError, match="outside the assigned"):
        vlm.inspect_sensor_scope(
            sensor="sensor-1",
            start=START,
            end="2026-09-30T00:00:30Z",
            **common,
        )
    with pytest.raises(ValueError, match="before end"):
        vlm.inspect_sensor_scope(
            sensor="sensor-1",
            start=START,
            end=START,
            **common,
        )
    with pytest.raises(ValueError, match="before end"):
        vlm.inspect_sensor_scope(
            sensor="sensor-1",
            start="2026-09-30T00:00:10Z",
            end=START,
            **common,
        )
    with pytest.raises(ValueError, match="exceeds the task allocation"):
        vlm.inspect_sensor_scope(
            sensor="sensor-1",
            start=START,
            end="2026-09-30T00:00:10Z",
            **{**common, "calls_budget": 3},
        )

    missing = tmp_path / "tasks.json"
    missing.write_text(json.dumps([_task(), _task(sensor="sensor-2")]), encoding="utf-8")
    with pytest.raises(ValueError, match="task_id"):
        vlm.load_selected_task(str(missing), None)
    ambiguous = tmp_path / "ambiguous.json"
    ambiguous.write_text(
        json.dumps([_task(), {**_task(), "task_id": "inspect-claim-color-r1"}]),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="exactly one"):
        vlm.load_selected_task(str(ambiguous), "inspect-claim-color-r1")
    invalid = tmp_path / "invalid.json"
    invalid.write_text(
        json.dumps({**_task(), "media_scope": {"type": "file", "path": "/tmp/clip.mp4"}}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="sensor media_scope"):
        vlm.load_selected_task(str(invalid))


def test_assigned_subwindow_and_exact_window_are_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []

    def fake_run(**kwargs: str) -> dict:
        calls.append((kwargs["start"], kwargs["end"]))
        return {
            "exit_code": 0,
            "job_id": "vlm-1",
            "status": "succeeded",
            "answer": "The event is visible.",
            "usable": True,
            "quality_reason": "ok",
            "error": None,
            "persistence_limited": False,
        }

    monkeypatch.setattr(vlm, "_run_vlm", fake_run)
    bounds = {
        "expected_sensor": "sensor-1",
        "assigned_start": START,
        "assigned_end": "2026-09-30T00:00:20Z",
        "task_call_allocation": 2,
    }
    subwindow = vlm.inspect_sensor_scope(
        vss_project="/repo/libs/vss",
        sensor="sensor-1",
        start=START,
        end="2026-09-30T00:00:10Z",
        fps=1,
        prompt="Locate the event.",
        calls_budget=1,
        **bounds,
    )
    exact = vlm.inspect_sensor_scope(
        vss_project="/repo/libs/vss",
        sensor="sensor-1",
        start=START,
        end="2026-09-30T00:00:20Z",
        fps=1,
        prompt="Locate the event.",
        calls_budget=1,
        **bounds,
    )
    assert subwindow["vlm_calls_used"] == 1
    assert exact["vlm_calls_used"] == 1
    assert calls == [
        (START, "2026-09-30T00:00:10Z"),
        (START, "2026-09-30T00:00:20Z"),
    ]


def test_occlusion_text_stays_usable_without_resolving_the_claim() -> None:
    occluded = vlm.classify_vlm_output(
        "The worker is occluded, so no hat transition is visibly established."
    )
    visible = vlm.classify_vlm_output(
        "The worker is partly occluded, and the yellow vest is clearly visible."
    )
    assert occluded["usable"] is True
    assert visible["usable"] is True


def test_prior_usable_windows_are_reused_without_a_new_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"count": 0}

    def fake_run(**_: object) -> dict:
        calls["count"] += 1
        return {
            "exit_code": 0,
            "job_id": "vlm-b",
            "status": "succeeded",
            "answer": "Window B shows the vest.",
            "usable": True,
            "quality_reason": "ok",
            "error": None,
            "persistence_limited": False,
        }

    monkeypatch.setattr(vlm, "_run_vlm", fake_run)
    prompt = "Is the vest visible?"
    windows = vlm.split_sensor_window(START, "2026-09-30T00:02:00Z", 1)
    assert len(windows) == 2
    prior = {
        "sensor_id": "sensor-1",
        "claim_id": "claim-color",
        "start": windows[0]["start"],
        "end": windows[0]["end"],
        "fps": 1,
        "prompt_sha256": vlm._prompt_digest(prompt),
        "usable": True,
        "answer": "Window A shows the vest.",
        "job_id": "vlm-a",
        "observation_id": "obs-" + "a" * 24,
        "status": "completed",
        "exit_code": 0,
    }
    mixed = vlm.inspect_sensor_scope(
        vss_project="/repo/libs/vss",
        sensor="sensor-1",
        claim_id="claim-color",
        start=START,
        end="2026-09-30T00:02:00Z",
        fps=1,
        prompt=prompt,
        calls_budget=1,
        prior_attempts=[prior],
    )
    assert calls["count"] == 1
    assert mixed["vlm_calls_used"] == 1
    assert mixed["window_inspection"] == "complete"
    assert mixed["claim_sufficiency"] is None
    assert mixed["reused_evidence"][0]["observation_id"] == prior["observation_id"]
    assert mixed["reused_evidence"][0]["job_id"] == "vlm-a"
    assert [item["window_index"] for item in mixed["attempts"]] == [1]

    covered = vlm.inspect_sensor_scope(
        vss_project="/repo/libs/vss",
        sensor="sensor-1",
        claim_id="claim-color",
        start=START,
        end="2026-09-30T00:00:20Z",
        fps=1,
        prompt=prompt,
        calls_budget=1,
        prior_attempts=[
            {
                **prior,
                "start": START,
                "end": "2026-09-30T00:00:20Z",
            }
        ],
    )
    assert covered["vlm_calls_used"] == 0
    assert covered["complete"] is True
    assert covered["window_inspection"] == "complete"
    assert covered["reused_evidence"][0]["observation_id"] == prior["observation_id"]
    assert calls["count"] == 1


def test_failed_or_incompatible_priors_do_not_count_as_coverage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"count": 0}

    def fake_run(**_: object) -> dict:
        calls["count"] += 1
        return {
            "exit_code": 0,
            "job_id": "vlm-new",
            "status": "succeeded",
            "answer": "The vest is visible.",
            "usable": True,
            "quality_reason": "ok",
            "error": None,
            "persistence_limited": False,
        }

    monkeypatch.setattr(vlm, "_run_vlm", fake_run)
    prompt = "Is the vest visible?"
    failed = {
        "sensor_id": "sensor-1",
        "claim_id": "claim-color",
        "start": START,
        "end": "2026-09-30T00:00:20Z",
        "fps": 1,
        "prompt_sha256": vlm._prompt_digest(prompt),
        "usable": False,
        "answer": "",
        "job_id": "vlm-failed",
    }
    failed_result = vlm.inspect_sensor_scope(
        vss_project="/repo/libs/vss",
        sensor="sensor-1",
        claim_id="claim-color",
        start=START,
        end="2026-09-30T00:00:20Z",
        fps=1,
        prompt=prompt,
        calls_budget=1,
        prior_attempts=[failed],
    )
    assert calls["count"] == 0
    assert failed_result["window_inspection"] == "none"
    assert failed_result["reused_evidence"] == []

    other_sensor = vlm.inspect_sensor_scope(
        vss_project="/repo/libs/vss",
        sensor="sensor-1",
        claim_id="claim-color",
        start=START,
        end="2026-09-30T00:00:20Z",
        fps=1,
        prompt=prompt,
        calls_budget=1,
        prior_attempts=[{**failed, "usable": True, "answer": "Seen elsewhere.", "sensor_id": "sensor-2"}],
    )
    other_prompt = vlm.inspect_sensor_scope(
        vss_project="/repo/libs/vss",
        sensor="sensor-1",
        claim_id="claim-color",
        start=START,
        end="2026-09-30T00:00:20Z",
        fps=1,
        prompt=prompt,
        calls_budget=1,
        prior_attempts=[
            {
                **failed,
                "usable": True,
                "answer": "A different question.",
                "prompt_sha256": vlm._prompt_digest("Count the forklifts."),
            }
        ],
    )
    assert calls["count"] == 2
    assert other_sensor["reused_evidence"] == []
    assert other_prompt["reused_evidence"] == []
    assert other_sensor["window_inspection"] == "complete"
    assert other_prompt["window_inspection"] == "complete"
