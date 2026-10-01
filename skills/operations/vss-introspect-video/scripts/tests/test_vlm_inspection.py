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
    assert result["coverage"] == "partial"


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
    assert result["coverage"] == "partial"


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
                }
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
                    },
                },
                {
                    "task_id": "inspect-claim-two-r1",
                    "media_scope": {
                        "type": "sensor",
                        "sensor_id": "sensor-two",
                    },
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
    assert result["coverage"] == "none"
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
