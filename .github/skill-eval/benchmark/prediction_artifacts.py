# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Strict loading for worker-produced benchmark prediction artifacts."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from benchmark.domain import CaseAnswer, ChoiceAnswer
from benchmark.structured_output import extract_json_payload


class PredictionArtifactError(ValueError):
    """Raised when per-question prediction artifacts violate their contract."""


def parse_choice_answer(response: str) -> ChoiceAnswer:
    """Parse an LLM response into the canonical multiple-choice answer."""
    payload = extract_json_payload(response)

    # Enforce the ChoiceAnswer schema after removing transport formatting.
    return ChoiceAnswer.model_validate_json(payload)


def load_prediction_artifacts(
    artifact_dir: Path,
    expected_case_ids: Sequence[str],
) -> tuple[CaseAnswer, ...]:
    expected_ids = tuple(expected_case_ids)
    if len(expected_ids) != 4 or len(set(expected_ids)) != 4:
        raise PredictionArtifactError(
            "a benchmark group must define four unique case IDs"
        )

    expected_paths = tuple(
        artifact_dir / f"prediction-{index}.json"
        for index in range(1, len(expected_ids) + 1)
    )
    actual_paths = set(artifact_dir.glob("prediction-*.json"))
    if actual_paths != set(expected_paths):
        raise PredictionArtifactError(
            "expected exactly prediction-1.json through prediction-4.json"
        )

    artifacts: list[CaseAnswer] = []
    for path, expected_id in zip(expected_paths, expected_ids, strict=True):
        try:
            artifact = CaseAnswer.model_validate_json(
                path.read_text(encoding="utf-8")
            )
        except (OSError, ValidationError) as exc:
            raise PredictionArtifactError(
                f"{path.name} violates the CaseAnswer schema"
            ) from exc
        if artifact.case_id != expected_id:
            raise PredictionArtifactError(
                f"{path.name} must contain case_id {expected_id!r}"
            )
        artifacts.append(artifact)

    return tuple(artifacts)
