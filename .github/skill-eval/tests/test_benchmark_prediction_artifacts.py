# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import json

import pytest
from benchmark.prediction_artifacts import (
    PredictionArtifactError,
    load_prediction_artifacts,
)


def _write_predictions(directory, case_ids=None) -> None:
    ids = case_ids or [f"video-{index}" for index in range(1, 5)]
    for index, case_id in enumerate(ids, 1):
        (directory / f"prediction-{index}.json").write_text(
            json.dumps({"case_id": case_id, "response": "ABCD"[index - 1]}),
            encoding="utf-8",
        )


def test_load_prediction_artifacts_preserves_question_order(tmp_path) -> None:
    expected = tuple(f"video-{index}" for index in range(1, 5))
    _write_predictions(tmp_path, expected)

    observed = load_prediction_artifacts(tmp_path, expected)

    assert [item.case_id for item in observed] == list(expected)
    assert [item.response for item in observed] == list("ABCD")


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
        json.dumps({"case_id": "video-1", "response": 1}),
        json.dumps({"case_id": "video-1", "response": "A", "extra": True}),
    ),
)
def test_load_prediction_artifacts_rejects_invalid_payload(tmp_path, payload) -> None:
    expected = tuple(f"video-{index}" for index in range(1, 5))
    _write_predictions(tmp_path, expected)
    (tmp_path / "prediction-1.json").write_text(payload, encoding="utf-8")

    with pytest.raises(PredictionArtifactError):
        load_prediction_artifacts(tmp_path, expected)
