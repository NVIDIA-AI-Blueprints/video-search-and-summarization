# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Track B crop wiring: evidence of a stored pipeline run scored by A8 through the final stage's matching."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from evalv2.core.netguard import no_network
from evalv2.datasets import records as rec
from evalv2.tasks.crops.scoring import aggregate
from evalv2.tasks.incident_objects import v1
from evalv2.tasks.incident_objects.scoring import embedder_similarity
from evalv2.tasks.pipeline import crops, scoring

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_v2_incident_objects import _embed, _sheets, v2_judge  # noqa: E402
from test_v2_track_b import _execute, _provenance, _zoomed  # noqa: E402

E2_BOX = [100, 200, 600, 400]
STICK = [300, 380, 420, 520]


def _padded(box, padding=0.05):
    h, w = box[2] - box[0], box[3] - box[1]
    return [box[0] - h * padding, box[1] - w * padding, box[2] + h * padding, box[3] + w * padding]


def _box_reference(kind, item_id, boxes, clip="Assault001", human=False):
    record = rec.new_record(record_id=f"box:{clip}:{item_id}", dataset="box-references-v0", kind="box", clip=clip,
                            payload={"clip": clip, "kind": kind, "item_id": item_id,
                                     "boxes": {str(s): b for s, b in boxes.items()}},
                            generator={"prompt": "gen-box-select-v1"})
    record = rec.promote_validated(record, checks=[{"name": "c", "passed": True}],
                                   validator={"id": "gen-box-validate-v1", "agrees": True})
    if human:
        record = rec.apply_human_decision(record, rec.HumanDecision(
            record["record_id"], "accept", "Reviewer One", "2026-11-02T09:00:00+00:00", rec.payload_sha256(record),
            "f" * 64))
    return record


def _answer_with_boxes():
    before, after, _ = _zoomed()
    evidence = [
        {"item_type": "entity", "item_id": "E2", "item_name": "human", "description": "Person in blue jacket",
         "name_before": "human", "description_before": "Person in a blue jacket", "timestamp_seconds": 4,
         "box_seconds": 4, "located": True, "detected_box": E2_BOX, "bounding_box": _padded(E2_BOX)},
        {"item_type": "instrument", "item_id": "I1", "item_name": "stick", "description": "Wooden stick",
         "timestamp_seconds": 5, "box_seconds": 5, "located": True, "bounding_box": _padded(STICK)},
        {"item_type": "entity", "item_id": "E1", "timestamp_seconds": 3, "located": None, "bounding_box": None,
         "image_gap": "crop-call cap reached: crop-call cap of 40 reached"},
        {"item_type": "entity", "item_id": "E3", "timestamp_seconds": 5, "box_seconds": 5, "located": True,
         "detected_box": [1, 1, 50, 50]},
    ]
    experiment = {"p1_report": before, "persisted": False,
                  "crop": {"cap": 40, "calls": 40, "refused_by_cap": 1, "cap_reached": True, "items": 4},
                  "zoom": {"cap": 80, "calls": 6, "refused_by_cap": 0, "cap_reached": False}}
    return 200, {"report": after, "raw_output": json.dumps(before), "finish_reason": "stop", "evidence": evidence,
                 "experiment": experiment, "provenance": _provenance()}


def test_a_runs_evidence_is_scored_through_the_final_stages_matching(tmp_path):
    record, stored = _execute(tmp_path, _answer_with_boxes())
    gt = v1.gt_store(_sheets())
    with no_network():
        final = scoring.score_stage(gt, stored, "final", incident_id="Assault001", run_id=record.work_key,
                                    similarity=embedder_similarity(_embed), judge=v2_judge)
    matches = crops.item_matches(final)
    assert matches["entities"]["E2"] == "E2" and matches["instruments"] == {"I1": "I1"}
    references = crops.reference_index([
        _box_reference("entity", "E2", {4: E2_BOX}, human=True),
        _box_reference("instrument", "I1", {4: STICK}),  # the reference never sees the stick at second 5
        _box_reference("entity", "E2", {1: E2_BOX}, clip="Burglary002"),
    ], "Assault001")
    assert set(references) == {("entities", "E2"), ("instruments", "I1")}
    scores = {s["item_id"]: s for s in crops.score_run(stored, final, references)}
    assert scores["E2"]["status"] == "scored" and scores["E2"]["correct"] is True
    assert scores["E2"]["tier"] == "human_verified" and scores["E2"]["box_source"] == "detected"
    assert scores["I1"]["temporal_hit"] is False and scores["I1"]["box_source"] == "unpadded_estimate"
    assert scores["E1"]["status"] == "unscorable", "a capped item is never a miss"
    assert scores["E3"]["status"] == "unmatched", "an item without a ground-truth match is the object metrics' miss"
    summary = aggregate(scores.values())
    assert summary["primary"]["n"] == 1 and summary["primary"]["correct_evidence_rate"] == 1.0
    assert summary["by_tier"]["ai_validated"]["temporal_hit_rate"] == 0.0


def test_a_run_without_a_final_report_adds_no_crop_scores():
    assert crops.score_run({"evidence": [{"item_type": "entity", "item_id": "E1"}]}, None, {}) == []
    assert crops.item_matches(None) == {}


def test_duplicate_references_for_one_item_are_refused():
    twice = [_box_reference("entity", "E2", {4: E2_BOX}), _box_reference("entity", "E2", {5: E2_BOX})]
    with pytest.raises(ValueError, match="two box references"):
        crops.reference_index(twice, "Assault001")


def test_cap_summary_shows_how_often_each_cap_bound(tmp_path):
    _, capped = _execute(tmp_path / "a", _answer_with_boxes())
    tracked = {"experiment": {"crop": {"cap": 40, "calls": 12, "refused_by_cap": 0, "cap_reached": False},
                              "object_tracking": {"time_cap_reached": True,
                                                  "detector": {"cap": 80, "calls": 80, "refused_by_cap": 3,
                                                               "cap_reached": True}}}}
    summary = crops.cap_summary([capped, tracked, {"experiment": {}}])
    assert summary["runs"] == 3
    assert summary["crop"] == {"runs": 2, "cap_reached": 1, "refused_calls": 1, "max_calls": 40, "caps": [40]}
    assert summary["zoom"]["cap_reached"] == 0 and summary["zoom"]["max_calls"] == 6
    assert summary["object_tracking_detector"]["refused_calls"] == 3
    assert summary["object_tracking_time"] == {"runs": 1, "cap_reached": 1}
