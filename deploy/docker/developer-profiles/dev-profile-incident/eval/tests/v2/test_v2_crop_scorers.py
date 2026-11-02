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

"""Crop scorers: operational, temporal and same-second IoU per reference tier; capped items are never misses."""

from __future__ import annotations

import pytest

from evalv2.datasets import records as rec
from evalv2.references.boxes import iou
from evalv2.tasks.crops.scoring import aggregate, predicted_box, score_entry, score_video, unpad

STICK = [300, 380, 420, 520]


def _reference(boxes, tier="ai_validated", item_id="I1"):
    record = rec.new_record(record_id=f"box:Assault001:{item_id}", dataset="box-references-v0", kind="box",
                            clip="Assault001", payload={"boxes": {str(s): b for s, b in boxes.items()}},
                            generator={"prompt": "gen-box-select-v1"})
    if tier in ("ai_validated", "human_verified"):
        record = rec.promote_validated(record, checks=[{"name": "c", "passed": True}],
                                       validator={"id": "gen-box-validate-v1", "agrees": True})
    if tier == "human_verified":
        decision = rec.HumanDecision(record["record_id"], "accept", "Reviewer One", "2026-11-02T09:00:00+00:00",
                                     rec.payload_sha256(record), "f" * 64)
        record = rec.apply_human_decision(record, decision)
    return record


def _padded(box, padding=0.05):
    h, w = box[2] - box[0], box[3] - box[1]
    return [box[0] - h * padding, box[1] - w * padding, box[2] + h * padding, box[3] + w * padding]


def _entry(**overrides):
    return {"item_type": "instrument", "item_id": "I1", "timestamp_seconds": 4, "box_seconds": 4, "located": True,
            "bounding_box": _padded(STICK), "image_gap": None, **overrides}


def test_unpadding_inverts_the_agents_padding_away_from_edges():
    assert unpad(_padded(STICK)) == pytest.approx(STICK)
    edge = unpad([0, 380, 420, 520])
    assert edge[0] == 0 and edge[2] < 420


def test_a_recorded_detector_box_is_preferred_to_an_estimate():
    assert predicted_box(_entry(detected_box=STICK)) == (STICK, "detected")
    box, source = predicted_box(_entry())
    assert source == "unpadded_estimate" and iou(box, STICK) == pytest.approx(1.0)
    assert predicted_box(_entry(bounding_box=None)) == (None, None)


def test_a_correct_box_at_a_visible_second():
    score = score_entry(_entry(), _reference({4: STICK}))
    assert score["status"] == "scored" and score["correct"] and score["temporal_hit"]
    assert score["iou"] == pytest.approx(1.0) and score["tier"] == "ai_validated"


def test_the_box_second_wins_over_the_requested_second():
    score = score_entry(_entry(timestamp_seconds=4, box_seconds=5), _reference({5: STICK}))
    assert score["second"] == 5 and score["correct"]


def test_a_wrong_object_and_a_wrong_moment_are_misses():
    wrong_object = score_entry(_entry(detected_box=[600, 600, 900, 900]), _reference({4: STICK}))
    assert wrong_object["temporal_hit"] and wrong_object["iou"] == 0.0 and not wrong_object["correct"]
    wrong_moment = score_entry(_entry(), _reference({9: STICK}))
    assert not wrong_moment["temporal_hit"] and wrong_moment["iou"] is None and not wrong_moment["correct"]
    not_located = score_entry(_entry(located=False, bounding_box=None,
                                     image_gap="object not located at any second tried"), _reference({4: STICK}))
    assert not_located["status"] == "scored" and not_located["located"] is False and not not_located["correct"]


@pytest.mark.parametrize("gap", ["crop-call cap reached: crop-call cap of 80 reached",
                                 "no evidence detector is configured (VLM grounding key missing)",
                                 "no evidence image store is configured (R2 settings missing)",
                                 "crop failed: TimeoutError: slow"])
def test_capped_or_unconfigured_items_are_unscorable_not_misses(gap):
    score = score_entry(_entry(located=None, bounding_box=None, image_gap=gap), _reference({4: STICK}))
    assert score["status"] == "unscorable" and score["reason"] == gap


def test_a_rejected_or_missing_reference_is_not_scored():
    rejected = _reference({4: STICK}, tier="ai_proposed")
    decision = rec.HumanDecision(rejected["record_id"], "reject", "Reviewer One", "2026-11-02T09:00:00+00:00",
                                 rec.payload_sha256(rejected), "f" * 64)
    assert score_entry(_entry(), rec.apply_human_decision(rejected, decision))["status"] == "reference_rejected"
    assert score_entry(_entry(), None)["status"] == "no_reference"


def test_videos_map_predicted_items_to_ground_truth_items_and_tiers_stay_apart():
    references = {("instruments", "I1"): _reference({4: STICK}, tier="human_verified"),
                  ("entities", "E1"): _reference({4: [100, 200, 600, 400]}, tier="ai_validated", item_id="E1")}
    evidence = [_entry(),
                {"item_type": "entity", "item_id": "E2", "box_seconds": 4, "located": True,
                 "detected_box": [100, 200, 600, 400]},
                {"item_type": "asset", "item_id": "A9", "box_seconds": 1, "located": True,
                 "detected_box": [1, 1, 50, 50]},
                _entry(item_id="I2", located=None, bounding_box=None, image_gap="crop-call cap reached: x")]
    matches = {"instruments": {"I1": "I1", "I2": "I1"}, "entities": {"E2": "E1"}, "assets": {}}
    scores = score_video(evidence, references, matches)
    assert [s["status"] for s in scores] == ["scored", "scored", "unmatched", "unscorable"]
    assert scores[1]["gt_item_id"] == "E1" and scores[1]["tier"] == "ai_validated"
    summary = aggregate(scores)
    assert summary["primary"]["n"] == 1 and summary["primary"]["correct_evidence_rate"] == 1.0
    assert summary["by_tier"]["ai_validated"]["n"] == 1, "AI-tier results are reported beside, never pooled"
    assert summary["status"] == {"scored": 2, "unmatched": 1, "unscorable": 1}
    assert summary["primary"]["estimated_boxes"] == 1
