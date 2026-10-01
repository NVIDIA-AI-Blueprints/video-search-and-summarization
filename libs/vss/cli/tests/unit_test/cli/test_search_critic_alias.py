# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""`critic_result` alias the VSS UI reads on `vss search run` hits."""

from __future__ import annotations

from typing import Any

import pytest

from vss_cli.search.group import _add_ui_critic_alias


@pytest.mark.parametrize(
    ("verification", "expected"),
    [
        pytest.param(
            {"result": "confirmed", "criteria_met": {"forklift": True}},
            {"result": "confirmed", "criteria_met": {"forklift": True}},
            id="evaluated-confirmed",
        ),
        pytest.param(
            {"result": "rejected", "criteria_met": {"forklift": True, "red": False}},
            {"result": "rejected", "criteria_met": {"forklift": True, "red": False}},
            id="evaluated-rejected",
        ),
        pytest.param(
            {"result": "unverified", "criteria_met": {}},
            {"result": "unverified", "criteria_met": {}},
            id="evaluated-undecided",
        ),
        pytest.param(
            {"result": "unverified", "criteria_met": None},
            None,
            id="not-evaluated",
        ),
    ],
)
def test_alias_copies_only_evaluated_verdicts(verification: dict[str, Any], expected: dict[str, Any] | None) -> None:
    body: dict[str, Any] = {"data": [{"video_name": "a.mp4", "verification": verification}], "search_messages": []}

    _add_ui_critic_alias(body)

    row = body["data"][0]
    assert row.get("critic_result") == expected
    assert row["verification"] == verification


def test_alias_is_independent_of_verification() -> None:
    body: dict[str, Any] = {"data": [{"verification": {"result": "confirmed", "criteria_met": {"forklift": True}}}]}

    _add_ui_critic_alias(body)
    body["data"][0]["critic_result"]["criteria_met"]["forklift"] = False

    assert body["data"][0]["verification"]["criteria_met"] == {"forklift": True}


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({"data": []}, id="no-hits"),
        pytest.param({"data": [{"video_name": "a.mp4"}]}, id="row-without-verification"),
        pytest.param({"search_messages": []}, id="no-data-key"),
        pytest.param(["not", "a", "dict"], id="non-dict-body"),
    ],
)
def test_alias_ignores_bodies_without_verdicts(body: Any) -> None:
    before = repr(body)

    _add_ui_critic_alias(body)

    assert repr(body) == before
