# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import json

import pytest
from benchmark.prediction_artifacts import (
    PredictionArtifactError,
    load_prediction_artifacts,
    parse_choice_answer,
)


def _write_predictions(directory, case_ids=None) -> None:
    ids = case_ids or [f"video-{index}" for index in range(1, 5)]
    for index, case_id in enumerate(ids, 1):
        (directory / f"prediction-{index}.json").write_text(
            json.dumps(
                {
                    "case_id": case_id,
                    "answer": {"label": "ABCD"[index - 1]},
                }
            ),
            encoding="utf-8",
        )


def test_load_prediction_artifacts_preserves_question_order(tmp_path) -> None:
    expected = tuple(f"video-{index}" for index in range(1, 5))
    _write_predictions(tmp_path, expected)

    observed = load_prediction_artifacts(tmp_path, expected)

    assert [item.case_id for item in observed] == list(expected)
    assert [item.answer.label for item in observed] == list("ABCD")


@pytest.mark.parametrize(
    ("response", "expected"),
    (
        ('{"label":"A"}', "A"),
        ('```json\n{"label":"B"}\n```', "B"),
        ('Explanation before the answer.\n\n```json\n{"label":"C"}\n```', "C"),
        ('Explanation before the answer.\n\n{"label":"D"}', "D"),
    ),
)
def test_parse_choice_answer_accepts_supported_json_forms(response, expected) -> None:
    assert parse_choice_answer(response).label == expected


@pytest.mark.parametrize(
    "response",
    (
        "The answer is A.",
        '```json\n{"label":"A"}\n```\ntrailing text',
        '```json\n{"label":"A"}\n```\n```json\n{"label":"B"}\n```',
    ),
)
def test_parse_choice_answer_rejects_ambiguous_responses(response) -> None:
    with pytest.raises(ValueError):
        parse_choice_answer(response)


@pytest.mark.parametrize("unexpected_name", [None, "prediction-5.json"])
def test_load_prediction_artifacts_requires_exact_file_set(
    tmp_path,
    unexpected_name,
) -> None:
    expected = tuple(f"video-{index}" for index in range(1, 5))
    _write_predictions(tmp_path, expected)
    if unexpected_name is None:
        (tmp_path / "prediction-4.json").unlink()
    else:
        (tmp_path / unexpected_name).write_text("{}", encoding="utf-8")

    with pytest.raises(PredictionArtifactError, match="exactly prediction-1"):
        load_prediction_artifacts(tmp_path, expected)


def test_load_prediction_artifacts_rejects_misordered_case_id(tmp_path) -> None:
    expected = tuple(f"video-{index}" for index in range(1, 5))
    _write_predictions(tmp_path, (expected[1], expected[0], *expected[2:]))

    with pytest.raises(PredictionArtifactError, match="must contain case_id"):
        load_prediction_artifacts(tmp_path, expected)


@pytest.mark.parametrize(
    "payload",
    (
        "not-json",
        json.dumps({"case_id": "video-1", "answer": {"label": "Z"}}),
        json.dumps(
            {
                "case_id": "video-1",
                "answer": {"label": "A", "extra": True},
            }
        ),
        json.dumps(
            {
                "case_id": "video-1",
                "answer": {"label": "A"},
                "extra": True,
            }
        ),
    ),
)
def test_load_prediction_artifacts_rejects_invalid_payload(tmp_path, payload) -> None:
    expected = tuple(f"video-{index}" for index in range(1, 5))
    _write_predictions(tmp_path, expected)
    (tmp_path / "prediction-1.json").write_text(payload, encoding="utf-8")

    with pytest.raises(PredictionArtifactError):
        load_prediction_artifacts(tmp_path, expected)
