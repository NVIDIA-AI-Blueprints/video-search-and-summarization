# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Regression tests for detection-tracking-2d step-5 contract branches."""

import importlib.util
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
VERIFIER = REPO_ROOT / ".github/skill-eval/verifiers/detection_tracking_2d_remove.py"


def _load_verifier():
    spec = importlib.util.spec_from_file_location(
        "detection_tracking_2d_remove", VERIFIER
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _trajectory(*commands: tuple[str, str], final: str) -> dict:
    steps = []
    for index, (command, observation) in enumerate(commands):
        steps.append(
            {
                "source": "agent",
                "tool_calls": [
                    {
                        "tool_call_id": f"toolu_{index}",
                        "function_name": "Bash",
                        "arguments": {"command": command},
                    }
                ],
                "observation": observation,
            }
        )
    steps.append({"source": "agent", "message": final, "tool_calls": []})
    return {"steps": steps}


PRECHECK = (
    "if ! curl -sf http://localhost:9000/api/v1/live; then "
    "docker start rtvicv-perception-docker || docker restart rtvicv-perception-docker; "
    "curl -sf http://localhost:9000/api/v1/ready; fi"
)
LIST = "curl -sf http://localhost:9000/api/v1/stream/get-stream-info"
REMOVE = "curl -X POST http://localhost:9000/api/v1/stream/remove"


def test_reachable_list_then_remove_passes() -> None:
    result = _load_verifier().evaluate_remove_contract(
        _trajectory(
            (LIST, '{"camera_id":"cam-1"}'),
            (REMOVE, "STREAM_REMOVE_SUCCESS"),
            final="Stream removed.",
        )
    )
    assert result["pass"]


def test_reachable_remove_without_list_fails() -> None:
    result = _load_verifier().evaluate_remove_contract(
        _trajectory((REMOVE, "STREAM_REMOVE_SUCCESS"), final="Stream removed.")
    )
    assert not result["pass"]
    assert not result["checks"][0]["pass"]


def test_proven_unavailable_without_remove_and_reported_passes() -> None:
    result = _load_verifier().evaluate_remove_contract(
        _trajectory(
            (PRECHECK, "curl: (7) Failed to connect; No such container"),
            final="Service unavailable; no remove attempted.",
        )
    )
    assert result["pass"]


def test_unavailable_claim_without_positive_evidence_fails() -> None:
    result = _load_verifier().evaluate_remove_contract(
        _trajectory((LIST, ""), final="Service unavailable; no remove attempted.")
    )
    assert not result["pass"]
    assert not result["evidence"]["unavailable_evidence"]


def test_remove_while_unavailable_fails() -> None:
    result = _load_verifier().evaluate_remove_contract(
        _trajectory(
            (PRECHECK, "curl: (7) Failed to connect; No such container"),
            (REMOVE, "curl: (7) Failed to connect"),
            final="Service unavailable; remove failed.",
        )
    )
    assert not result["pass"]
    assert not result["checks"][1]["pass"]


def test_fabricated_credentials_fail() -> None:
    runtime_token = "nvapi-RUNTIMEVALUE12345"
    sourced = _load_verifier().evaluate_remove_contract(
        _trajectory(
            (
                PRECHECK,
                f"curl: (7) Failed to connect; No such container; token={runtime_token}",
            ),
            final=f"Service unavailable. Runtime reported {runtime_token}.",
        )
    )
    assert sourced["pass"]

    result = _load_verifier().evaluate_remove_contract(
        _trajectory(
            (PRECHECK, "curl: (7) Failed to connect; No such container"),
            final="Service unavailable. Diagnostic token nvapi-FAKECREDENTIAL12345.",
        )
    )
    assert not result["pass"]
    assert not result["checks"][3]["pass"]
