# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Regression tests for the IN-2 RT-CV delta contract."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SPEC = (
    REPO_ROOT
    / "skills"
    / "vss-build-vision-agent"
    / "eval"
    / "profile_in_2_rt_cv_person_detection_harbor.json"
)
SEARCH_PROFILE = (
    REPO_ROOT / "deploy" / "docker" / "developer-profiles" / "dev-profile-search"
)
RT_CV_KEYS = ("DS_MODEL_FAMILY", "RT_CV_DEVICE_ID", "NUM_STREAMS")


def _search_env_value(key: str) -> str:
    for line in (SEARCH_PROFILE / ".env").read_text().splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip().strip("\"'")
    raise AssertionError(f"{key} is missing from the Search Foundation")


def _search_rt_cv_model_family() -> str:
    compose = (SEARCH_PROFILE / "video-analytics-2d-app" / "compose.yml").read_text()
    match = re.search(r"^\s+DS_MODEL_FAMILY:\s*(\S+)\s*$", compose, re.MULTILINE)
    assert match is not None
    return match.group(1)


def _expected_effective_values(check: str) -> dict[str, str]:
    return dict(
        re.findall(
            r"`(DS_MODEL_FAMILY|RT_CV_DEVICE_ID|NUM_STREAMS)=([^`]+)`",
            check,
        )
    )


def _effective_check_accepts(check: str, values: dict[str, str]) -> bool:
    expected = _expected_effective_values(check)
    return set(expected) == set(RT_CV_KEYS) and all(
        values.get(key) == value for key, value in expected.items()
    )


def test_in_2_separates_lean_delta_from_effective_rt_cv_values() -> None:
    spec = json.loads(SPEC.read_text())
    env = spec["env"]
    checks = spec["expects"][0]["checks"]

    assert all(
        value in env
        for value in (
            "DS_MODEL_FAMILY=rtdetr-warehouse",
            "RT_CV_DEVICE_ID=0",
            "NUM_STREAMS=16",
        )
    )
    assert "valid when inherited from the Search Foundation" in env

    delta_check = next(check for check in checks if check.startswith("The delta "))
    assert "only customized environment values" in delta_check
    assert "RT-CV model/device settings" not in delta_check
    assert "source count" not in delta_check
    assert "remain absent from `override.env`" in delta_check
    assert all(key in delta_check for key in RT_CV_KEYS)

    effective_check = next(
        check
        for check in checks
        if check.startswith("The effective `perception-2d-fusion` configuration")
    )
    assert "whether each value is inherited or explicitly customized" in effective_check
    assert all(
        value in effective_check
        for value in (
            "DS_MODEL_FAMILY=rtdetr-warehouse",
            "RT_CV_DEVICE_ID=0",
            "NUM_STREAMS=16",
        )
    )


def test_in_2_accepts_effective_values_inherited_from_search() -> None:
    spec = json.loads(SPEC.read_text())
    effective_check = next(
        check
        for check in spec["expects"][0]["checks"]
        if check.startswith("The effective `perception-2d-fusion` configuration")
    )
    inherited = {
        "DS_MODEL_FAMILY": _search_rt_cv_model_family(),
        "RT_CV_DEVICE_ID": _search_env_value("RT_CV_DEVICE_ID"),
        "NUM_STREAMS": _search_env_value("NUM_STREAMS"),
    }

    assert _effective_check_accepts(effective_check, inherited)


@pytest.mark.parametrize(
    ("key", "wrong_value"),
    [
        ("DS_MODEL_FAMILY", "cnn"),
        ("RT_CV_DEVICE_ID", "1"),
        ("NUM_STREAMS", "8"),
    ],
)
def test_in_2_rejects_wrong_effective_rt_cv_value(key: str, wrong_value: str) -> None:
    spec = json.loads(SPEC.read_text())
    effective_check = next(
        check
        for check in spec["expects"][0]["checks"]
        if check.startswith("The effective `perception-2d-fusion` configuration")
    )
    effective = _expected_effective_values(effective_check)
    effective[key] = wrong_value

    assert not _effective_check_accepts(effective_check, effective)
