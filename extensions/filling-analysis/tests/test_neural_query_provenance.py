# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Model/source provenance and query semantics; deliberately synthetic results."""
import pytest
from backend.tools import answer_question


@pytest.fixture
def result():
    rows = []
    for index, (level, status) in enumerate(((.77, "normal"), (.60, "underfill"), (.79, "overflow"), (None, "uncertain"))):
        start = 10 * index
        rows.append({"id": f"cycle-{index}", "label": f"Bottle {index}", "start_time": start,
                     "end_time": start + 9, "measurement_time": start + 7 if level is not None else None,
                     "final_level": level, "status": status, "confidence": .85, "reference_level": .75,
                     "tolerance": .03, "overflow_time": start + 6 if status == "overflow" else None,
                     "evidence_start": start + 5, "evidence_end": start + 9})
    hashes = {"bottle": "a"*64, "liquid": "b"*64}
    return {"analysis_kind": "bottle-cycles", "algorithm": "rfdetr-mask-cycle-v2", "version": "2.0.0",
            "measurement": {"engine": "rfdetr", "algorithm": "rfdetr-mask-cycle-v2", "model_hashes": hashes},
            "models": {k: {"checkpoint_sha256": v} for k, v in hashes.items()},
            "quality": {"measurement_engine": "rfdetr", "overflow_engine": "calibrated-exterior-color-signal-v1"},
            "source_sha256": "d"*64, "provenance": {"stream_id": "fixture", "actual_start_time": "2025-01-01T00:00:00Z"},
            "cycles": rows, "samples": [],
            "summary": {"total": 4, "normal": 1, "underfill": 1, "overflow": 1, "uncertain": 1}}


def test_neural_query_preserves_models_method_clock_and_current_reference(result):
    answer = answer_question(result, "Which bottles finished below reference?")
    assert [r["id"] for r in answer["cycles"]] == ["cycle-1"]
    assert answer["reference_criterion"]["reference_level"] == .75
    assert answer["evidence"][0]["recorded_at"] == "2025-01-01T00:00:17+00:00"
    for key in ("algorithm", "version", "measurement", "models", "quality", "source_sha256", "provenance"):
        assert answer[key] == result[key]
    assert "does not verify nozzle flow" in answer["answer"]
    assert "not the liquid-mask model" in answer["answer"]


def test_unknown_completion_is_not_counted_as_a_measured_underfill(result):
    answer = answer_question(result, "show underfills")
    assert [r["id"] for r in answer["cycles"]] == ["cycle-1"]
    assert answer["summary"]["uncertain"] == 1


def test_overflow_keeps_exterior_evidence_time_and_explicit_hybrid_method(result):
    answer = answer_question(result, "show overflows")
    assert [r["id"] for r in answer["cycles"]] == ["cycle-2"]
    assert answer["evidence"][0]["t"] == 26
    assert answer["quality"]["overflow_engine"] == "calibrated-exterior-color-signal-v1"
    assert "separately calibrated exterior-color signal" in answer["answer"]


def test_unsupported_question_still_preserves_measurement_identity(result):
    answer = answer_question(result, "What volume in liters?")
    assert not answer["supported"]
    assert answer["measurement"] == result["measurement"]
    assert answer["models"] == result["models"]


def test_summary_keeps_overflow_evidence_when_final_height_is_unreadable(result):
    result["cycles"][2].update(final_level=None, measurement_time=None)
    answer = answer_question(result, "summarize the bottle cycles")
    overflow = next(row for row in answer["cycles"] if row["status"] == "overflow")
    assert overflow["final_level"] is None
    evidence = next(row for row in answer["evidence"] if row["bottle_id"] == overflow["id"])
    assert evidence["t"] == overflow["overflow_time"] == 26
    assert evidence["recorded_at"] == "2025-01-01T00:00:26+00:00"
