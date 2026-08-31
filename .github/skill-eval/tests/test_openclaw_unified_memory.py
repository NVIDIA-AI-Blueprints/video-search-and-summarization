# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import json
from pathlib import Path
import shlex
import subprocess

from benchmark import structured_output
from agents.openclaw_unified_memory import (
    GROUP_PREFIX,
    GROUP_SUFFIX,
    _group_envelope,
    _openclaw_setup_commands,
    _prediction_extractor_command,
)


def test_group_envelope_requires_four_turns() -> None:
    video_id = "vss-sample-warehouse-4min"
    payload = {
        "kind": "unified-memory-group",
        "group_id": video_id,
        "turns": [
            {"case_id": f"{video_id}-{index}", "prompt": "q"} for index in range(1, 5)
        ],
    }
    instruction = f"preamble\n{GROUP_PREFIX}{json.dumps(payload)}{GROUP_SUFFIX}\n"
    assert _group_envelope(instruction) == payload


def test_openclaw_setup_removes_stale_runtime_first() -> None:
    reset, setup = _openclaw_setup_commands(
        "openclaw setup --baseline --workspace ."
    )

    assert reset == (
        "rm -f ~/.openclaw/openclaw.json && rm -rf ~/.openclaw/state"
    )
    assert setup.endswith(
        'openclaw setup --baseline --workspace "$HOME/.openclaw/workspace"'
    )
    assert "--workspace ." not in setup


def test_openclaw_setup_adds_configured_workspace_when_missing() -> None:
    _, setup = _openclaw_setup_commands("openclaw setup --baseline")

    assert setup.endswith(
        'openclaw setup --baseline --workspace "$HOME/.openclaw/workspace"'
    )


def _run_prediction_pipeline(
    payload: dict,
    case_id: str,
    log_path: str,
    temporary_path: str,
    prediction_path: str,
) -> None:
    encoded = json.dumps(payload)
    command = (
        "set -o pipefail; "
        f"printf %s {shlex.quote(encoded)} "
        f"| tee {shlex.quote(log_path)} "
        f"| {_prediction_extractor_command(case_id, str(Path(structured_output.__file__)))} "
        f"> {shlex.quote(temporary_path)} "
        f"&& mv {shlex.quote(temporary_path)} {shlex.quote(prediction_path)}"
    )
    subprocess.run(
        ["bash", "-c", command],
        check=True,
        capture_output=True,
        text=True,
    )


def test_prediction_pipeline_preserves_complete_log(tmp_path) -> None:
    payload = {
        "payloads": [{"text": json.dumps({"label": "B"})}],
        "meta": {"status": "ok"},
    }
    log_path = tmp_path / "openclaw-turn-1.txt"
    prediction_path = tmp_path / "prediction-1.json"

    _run_prediction_pipeline(
        payload,
        "video-1",
        str(log_path),
        str(tmp_path / "prediction-1.json.tmp"),
        str(prediction_path),
    )

    assert json.loads(log_path.read_text(encoding="utf-8")) == payload
    assert json.loads(prediction_path.read_text(encoding="utf-8")) == {
        "case_id": "video-1",
        "answer": {"label": "B"},
    }


def test_prediction_pipeline_rejects_non_json_response(tmp_path) -> None:
    response = 'The answer is "B".'
    prediction_path = tmp_path / "prediction-1.json"
    try:
        _run_prediction_pipeline(
            {"payloads": [{"text": response}]},
            "video-1",
            str(tmp_path / "openclaw.txt"),
            str(tmp_path / "prediction-1.json.tmp"),
            str(prediction_path),
        )
    except subprocess.CalledProcessError:
        pass
    else:
        raise AssertionError("invalid response unexpectedly produced a prediction")
    assert not prediction_path.exists()


def test_prediction_pipeline_accepts_final_fenced_json(tmp_path) -> None:
    response = 'Explanation before the answer.\n\n```json\n{"label":"B"}\n```'
    prediction_path = tmp_path / "prediction-1.json"

    _run_prediction_pipeline(
        {"payloads": [{"text": response}]},
        "video-1",
        str(tmp_path / "openclaw.txt"),
        str(tmp_path / "prediction-1.json.tmp"),
        str(prediction_path),
    )

    assert json.loads(prediction_path.read_text(encoding="utf-8")) == {
        "case_id": "video-1",
        "answer": {"label": "B"},
    }


def test_prediction_pipeline_accepts_final_unfenced_json(tmp_path) -> None:
    response = 'Explanation before the answer.\n\n{"label":"C"}'
    prediction_path = tmp_path / "prediction-1.json"

    _run_prediction_pipeline(
        {"payloads": [{"text": response}]},
        "video-1",
        str(tmp_path / "openclaw.txt"),
        str(tmp_path / "prediction-1.json.tmp"),
        str(prediction_path),
    )

    assert json.loads(prediction_path.read_text(encoding="utf-8")) == {
        "case_id": "video-1",
        "answer": {"label": "C"},
    }


def test_four_prediction_artifacts_are_numbered_in_order(tmp_path) -> None:
    expected = ["B", "A", "D", "C"]
    for index, label in enumerate(expected, 1):
        _run_prediction_pipeline(
            {"payloads": [{"text": json.dumps({"label": label})}]},
            f"video-{index}",
            str(tmp_path / f"turn-{index}.txt"),
            str(tmp_path / f"prediction-{index}.json.tmp"),
            str(tmp_path / f"prediction-{index}.json"),
        )
    actual = [
        json.loads((tmp_path / f"prediction-{index}.json").read_text())["answer"][
            "label"
        ]
        for index in range(1, 5)
    ]

    assert actual == expected


def test_prediction_pipeline_rejects_missing_or_non_string_text(tmp_path) -> None:
    for index, payload in enumerate(
        (
            {"payloads": []},
            {"payloads": [{"text": 2}]},
        ),
        1,
    ):
        prediction_path = tmp_path / f"prediction-{index}.json"
        try:
            _run_prediction_pipeline(
                payload,
                f"video-{index}",
                str(tmp_path / f"invalid-{index}.txt"),
                str(tmp_path / f"prediction-{index}.json.tmp"),
                str(prediction_path),
            )
        except subprocess.CalledProcessError:
            pass
        else:
            raise AssertionError("invalid response unexpectedly produced a prediction")
        assert not prediction_path.exists()
