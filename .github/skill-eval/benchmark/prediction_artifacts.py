# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Strict loading for worker-produced benchmark prediction artifacts."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class PredictionArtifact:
    case_id: str
    response: str


class PredictionArtifactError(ValueError):
    """Raised when per-question prediction artifacts violate their contract."""


def load_prediction_artifacts(
    artifact_dir: Path,
    expected_case_ids: Sequence[str],
) -> tuple[PredictionArtifact, ...]:
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

    artifacts: list[PredictionArtifact] = []
    for path, expected_id in zip(expected_paths, expected_ids, strict=True):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PredictionArtifactError(f"{path.name} is not valid JSON") from exc
        if not isinstance(raw, dict) or set(raw) != {"case_id", "response"}:
            raise PredictionArtifactError(
                f"{path.name} must contain only case_id and response"
            )
        case_id = raw["case_id"]
        response = raw["response"]
        if not isinstance(case_id, str) or not isinstance(response, str):
            raise PredictionArtifactError(
                f"{path.name} case_id and response must be strings"
            )
        if case_id != expected_id:
            raise PredictionArtifactError(
                f"{path.name} must contain case_id {expected_id!r}"
            )
        artifacts.append(PredictionArtifact(case_id, response))

    return tuple(artifacts)
