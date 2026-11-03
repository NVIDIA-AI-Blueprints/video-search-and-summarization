# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""The eval port of the console's severity-word normalisation (chart-severity-words-v1) and the agent-route-v3 shape."""

import json
from pathlib import Path

import pytest

from evalv2.requests import chart

FIXTURE = json.loads((chart.CONTRACTS_DIR / "fixtures" / "chart-severity-words.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", FIXTURE["cases"], ids=lambda c: c["why"][:40])
def test_shared_cases(case: dict) -> None:
    assert chart.normalize_severity_words(case["input"]) == (case["expected"] if case["expected"] is not None else case["input"])


def test_table_uses_only_canonical_levels_and_bands() -> None:
    table = chart.severity_words()
    assert table["version"] == "chart-severity-words-v2"
    assert table["band_of_level"] == {"1": "low", "2": "low", "3": "medium", "4": "high", "5": "high"}  # finals SQL bands
    assert set(table["word_levels"]) == {"minimal", "very low", "minor", "moderate", "severe", "critical"}
    assert not set(table["unmapped"]) & set(table["word_levels"])
    for word, spec in table["word_levels"].items():  # the rule: all of a word's levels lie in one band
        bands = {table["band_of_level"][str(level)] for level in spec["levels"]}
        assert bands == {table["word_bands"][word]}, word


def test_v3_differs_from_v2_only_in_the_user_message() -> None:
    req = "Show moderate-severity burglary reports from last week."
    v2 = chart.build_chart_request("m", req, "agent-route-v2")
    v3 = chart.build_chart_request("m", req, "agent-route-v3")
    assert v3["messages"][0] == v2["messages"][0]  # the chart prompt bytes are unchanged
    assert v3["messages"][1]["content"] == "Show medium-severity burglary reports from last week."
    assert {k: v for k, v in v3.items() if k != "messages"} == {k: v for k, v in v2.items() if k != "messages"}
    plain = "Show reports by severity."
    assert chart.build_chart_request("m", plain, "agent-route-v3") == chart.build_chart_request("m", plain, "agent-route-v2")


def test_earlier_shapes_do_not_normalise() -> None:
    req = "Show moderate incidents."
    for shape in ("console-gateway-v1", "agent-route-v2"):
        assert chart.build_chart_request("m", req, shape)["messages"][1]["content"] == req
        assert "severity_words_sha256" not in chart.chart_provenance(shape)
    assert len(chart.chart_provenance("agent-route-v3")["severity_words_sha256"]) == 64


def test_registry_hash_guards_the_table(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import shutil
    root = tmp_path / "contracts"
    shutil.copytree(chart.CONTRACTS_DIR / "prompts", root / "prompts")
    (root / "prompts" / "chart" / "severity-words.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(chart, "CONTRACTS_DIR", root)
    with pytest.raises(chart.ChartRequestError, match="registry sha256"):
        chart.normalize_severity_words("moderate incidents")
