# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Regressions from the Evaluation V2 review of the crop scorers: the agent's real gaps, and span-based temporal hits."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from evalv2.datasets import records as rec
from evalv2.tasks.crops.scoring import UNSCORABLE_GAPS, aggregate, score_entry

STICK = [300, 380, 420, 520]
AGENT = Path(__file__).resolve().parents[7] / "services" / "agent" / "src" / "vss_agents"


def _reference(boxes, *, spans=None, disagreed=(), tier="ai_proposed"):
    payload = {"boxes": {str(s): b for s, b in boxes.items()}, "disagreed_seconds": list(disagreed)}
    if spans is not None:
        payload["visible_spans"] = spans
    record = rec.new_record(record_id="box:Assault001:I1", dataset="box-references-v0", kind="box",
                            clip="Assault001", payload=payload, generator={"prompt": "gen-box-select-v1"})
    if tier == "ai_validated":
        record = rec.promote_validated(record, checks=[{"name": "c", "passed": True}],
                                       validator={"id": "gen-box-validate-v1", "agrees": not disagreed})
    return record


def _entry(**overrides):
    return {"item_type": "instrument", "item_id": "I1", "timestamp_seconds": 4, "box_seconds": 4, "located": True,
            "detected_box": STICK, "bounding_box": STICK, "image_gap": None, **overrides}


# --- B4: the agent's infrastructure gaps are unscorable, not misses ------------------------------------------------

# The exact strings the agent writes (crop_evidence.py and incident_report_gen.py on feat/experiment-profiles).
AGENT_GAPS = [
    "no frame produced at 4s (frame unreadable or detector failed)",  # grounding 429/5xx after its retries
    "image upload failed: ClientError: An error occurred (SlowDown) when calling the PutObject operation",
    "privacy blur failed, so no image was stored: RuntimeError",
    "evidence cropping failed: TimeoutError: ",
    "evidence cropping skipped (skip_crops)",
]


@pytest.mark.parametrize("gap", AGENT_GAPS)
def test_an_infrastructure_gap_is_unscorable_not_a_miss(gap):
    score = score_entry(_entry(located=None, detected_box=None, bounding_box=None, image_gap=gap),
                        _reference({4: STICK}))
    assert score["status"] == "unscorable" and score["reason"] == gap


def test_an_entry_the_locate_step_never_judged_is_unscorable_under_any_gap():
    score = score_entry(_entry(located=None, detected_box=None, bounding_box=None, image_gap="a gap added later"),
                        _reference({4: STICK}))
    assert score["status"] == "unscorable" and score["reason"] == "a gap added later"
    silent = score_entry(_entry(located=None, detected_box=None, bounding_box=None), _reference({4: STICK}))
    assert silent["status"] == "unscorable"


def test_rate_limited_grounding_no_longer_lowers_the_located_and_correct_rates():
    reference = _reference({4: STICK}, tier="ai_validated")
    scores = [score_entry(_entry(), reference),
              score_entry(_entry(located=None, detected_box=None, bounding_box=None, image_gap=AGENT_GAPS[0]),
                          reference)]
    summary = aggregate(scores)["by_tier"]["ai_validated"]
    assert summary["n"] == 1 and summary["located_rate"] == 1.0 and summary["correct_evidence_rate"] == 1.0


def test_not_located_at_any_second_is_still_a_miss():
    gap = "object not located at 4s or within 2s either side"
    score = score_entry(_entry(located=False, detected_box=None, bounding_box=None, image_gap=gap),
                        _reference({4: STICK}))
    assert score["status"] == "scored" and score["located"] is False and score["correct"] is False


def _literal_prefix(node):
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else None
    if isinstance(node, ast.JoinedStr):
        prefix = ""
        for part in node.values:
            if not isinstance(part, ast.Constant):
                break
            prefix += part.value
        return prefix
    return None


def _agent_gap_prefixes(path):
    """The literal start of every ``image_gap`` the module writes; a variable is resolved in its own function."""
    prefixes = set()
    for function in ast.walk(ast.parse(path.read_text())):
        if not isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        names, values = {}, []
        for node in ast.walk(function):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        names.setdefault(target.id, []).append(node.value)
                    elif (isinstance(target, ast.Subscript) and isinstance(target.slice, ast.Constant)
                          and target.slice.value == "image_gap"):
                        values.append(node.value)
            elif isinstance(node, ast.keyword) and node.arg == "image_gap":
                values.append(node.value)
        for value in values:
            for resolved in names.get(value.id, []) if isinstance(value, ast.Name) else [value]:
                prefix = _literal_prefix(resolved)
                if prefix:
                    prefixes.add(prefix)
    return prefixes


def test_every_gap_the_agent_writes_is_classified():
    paths = [AGENT / "utils" / "crop_evidence.py", AGENT / "tools" / "incident_report_gen.py"]
    if not all(path.exists() for path in paths):
        pytest.skip("the agent source is not in this checkout")
    prefixes = set().union(*(_agent_gap_prefixes(path) for path in paths))
    assert any(p.startswith("no frame produced at") for p in prefixes), "the parser sees the agent's gaps"
    misses = {p for p in prefixes if p.startswith("object not located")}
    unclassified = sorted(p for p in prefixes - misses if not any(p.startswith(g) for g in UNSCORABLE_GAPS))
    assert unclassified == [], "a new agent gap: classify it as unscorable or as a miss"


# --- B9: a temporal hit is a second inside the reference's visible span ---------------------------------------------


def _span_reference():
    """Visible over [3, 9]; the two passes disagreed at second 6, so it has no agreed box (and stays ai_proposed)."""
    boxes = {s: STICK for s in (3, 4, 5, 7, 8, 9)}
    return _reference(boxes, spans=[[3, 9]], disagreed=[6])


def test_a_second_inside_the_span_without_a_box_is_a_hit_with_unscored_iou():
    score = score_entry(_entry(box_seconds=6, timestamp_seconds=6), _span_reference())
    assert score["temporal_hit"] is True, "second 6 lies inside the visible span [3, 9]"
    assert score["iou"] is None and score["iou_scorable"] is False and score["correct"] is None


def test_seconds_with_a_box_and_outside_the_span_are_scored_as_before():
    hit = score_entry(_entry(box_seconds=7), _span_reference())
    assert hit["temporal_hit"] and hit["iou_scorable"] and hit["correct"] is True
    outside = score_entry(_entry(box_seconds=10), _span_reference())
    assert outside["temporal_hit"] is False and outside["iou"] is None and outside["correct"] is False


def test_unscored_correctness_is_left_out_of_the_rate_and_counted():
    reference = _span_reference()
    scores = [score_entry(_entry(box_seconds=6), reference), score_entry(_entry(box_seconds=4), reference),
              score_entry(_entry(box_seconds=12), reference)]
    summary = aggregate(scores)["by_tier"]["ai_proposed"]
    assert summary["n"] == 3 and summary["temporal_hit_rate"] == round(2 / 3, 4)
    assert summary["correct_evidence_rate"] == 0.5 and summary["correct_unscored"] == 1


def test_a_reference_without_stored_spans_uses_its_boxes_spans():
    reference = _reference({3: STICK, 5: STICK})  # joined into [3, 5] as the box tooling's spans() does
    score = score_entry(_entry(box_seconds=4), reference)
    assert score["temporal_hit"] is True and score["correct"] is None
