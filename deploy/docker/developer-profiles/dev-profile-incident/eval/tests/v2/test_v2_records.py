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

"""Reference records and tiers: AI passes never make a record human_verified; only a person's decision does."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from evalv2.core.private import PrivatePathError, ensure_private, is_private
from evalv2.datasets import records as rec

EVAL_DIR = Path(__file__).resolve().parents[2]
CHECKS = [{"name": "box_inside_frame", "passed": True}, {"name": "second_in_item_span", "passed": True}]


def _record(record_id="box:Assault001:E1:3", **overrides):
    record = rec.new_record(record_id=record_id, dataset="box-references-v0", kind="box", clip="Assault001",
                            strata={"category": "Assault", "item_type": "entity"},
                            payload={"second": 3, "box": [100, 120, 400, 300]},
                            generator={"prompt": "gen-box-select-v1", "model": "claude"})
    record.update(overrides)
    return record


def _decision(record, decision="accept", reviewer="Heng Zheng Kai", **overrides):
    values = {"record_id": record["record_id"], "decision": decision, "reviewer": reviewer,
              "decided_at": "2026-11-02T09:00:00+00:00", "payload_sha256": rec.payload_sha256(record),
              "source_sha256": "f" * 64, "edited_payload": None, "note": ""}
    values.update(overrides)
    return rec.HumanDecision(**values)


def test_a_new_record_is_ai_proposed_and_valid():
    record = _record()
    assert record["tier"] == "ai_proposed" and record["human"] is None and rec.validate_record(record) == []


def test_validation_promotes_only_to_ai_validated():
    record = _record()
    validated = rec.promote_validated(record, checks=CHECKS, validator={"id": "gen-box-validate-v1", "agrees": True})
    assert validated["tier"] == "ai_validated" and rec.validate_record(validated) == []
    assert record["tier"] == "ai_proposed", "the input record is never changed"
    with pytest.raises(rec.RecordError, match="only an ai_proposed"):
        rec.promote_validated(validated, checks=CHECKS, validator={"id": "v", "agrees": True})


@pytest.mark.parametrize(("checks", "validator", "flag"), [
    ([{"name": "c", "passed": False}], {"id": "v", "agrees": True}, "check_failed"),
    ([], {"id": "v", "agrees": True}, "check_failed"),
    (CHECKS, {"id": "v", "agrees": False}, "validators_disagree"),
])
def test_a_failed_check_or_disagreement_flags_and_keeps_the_tier(checks, validator, flag):
    out = rec.promote_validated(_record(), checks=checks, validator=validator)
    assert out["tier"] == "ai_proposed" and flag in out["flags"]


def test_an_ai_record_cannot_claim_a_higher_tier():
    forged = _record(tier="ai_validated")
    assert any("deterministic check" in p for p in rec.validate_record(forged))
    claimed = _record(tier="human_verified")
    assert any("without a human decision" in p for p in rec.validate_record(claimed))


def test_only_a_person_can_verify():
    record = _record()
    verified = rec.apply_human_decision(record, _decision(record))
    assert verified["tier"] == "human_verified" and rec.validate_record(verified) == []
    for reviewer in ("ai:claude", "Claude", "nemotron-judge", "model/gpt", " "):
        with pytest.raises(rec.RecordError):
            rec.apply_human_decision(record, _decision(record, reviewer=reviewer))


def test_a_stale_or_repeated_decision_is_refused():
    record = _record()
    with pytest.raises(rec.RecordError, match="stale"):
        rec.apply_human_decision(record, _decision(record, payload_sha256="0" * 64))
    verified = rec.apply_human_decision(record, _decision(record))
    with pytest.raises(rec.RecordError, match="already human_verified"):
        rec.apply_human_decision(verified, _decision(verified))


def test_decision_rules():
    record = _record()
    assert any("edited value" in p for p in _decision(record, "edit").problems())
    assert any("decision is 'accept'" in p for p in _decision(record, edited_payload={"x": 1}).problems())
    assert any("not one of" in p for p in _decision(record, "approve").problems())
    assert any("ISO" in p for p in _decision(record, decided_at="yesterday").problems())


def test_effective_payload_and_reporting_groups():
    a, b, c = _record("box:a"), _record("box:b"), _record("box:c")
    edited = rec.apply_human_decision(b, _decision(b, "edit", edited_payload={"second": 4, "box": [1, 2, 3, 4]}))
    rejected = rec.apply_human_decision(c, _decision(c, "reject"))
    assert rec.effective_payload(a) == a["payload"] and rec.effective_payload(edited)["second"] == 4
    assert rec.effective_payload(rejected) is None
    groups = rec.split_by_tier([a, edited, rejected])
    assert [r["record_id"] for r in groups["primary"]] == ["box:b"]
    assert [r["record_id"] for r in groups["rejected"]] == ["box:c"]
    assert [r["record_id"] for r in groups["ai_proposed"]] == ["box:a"]


def test_records_round_trip_through_a_private_jsonl(tmp_path):
    path = tmp_path / "records.jsonl"
    digest = rec.write_records([_record("box:a"), _record("box:b")], path)
    assert len(digest) == 64 and [r["record_id"] for r in rec.load_records(path)] == ["box:a", "box:b"]
    path.write_text(path.read_text() + path.read_text().splitlines()[0] + "\n")
    with pytest.raises(rec.RecordError, match="duplicate"):
        rec.load_records(path)


def test_unknown_keys_and_flags_are_refused():
    assert rec.validate_record({**_record(), "extra": 1})
    with pytest.raises(rec.RecordError, match="unknown flags"):
        rec.new_record(record_id="x", dataset="d", kind="box", payload={}, generator={"m": 1}, flags=["typo"])


def test_private_paths_are_ignored_or_outside_the_repository(tmp_path):
    assert is_private(tmp_path / "anything.jsonl")
    assert is_private(EVAL_DIR / "eval_data" / "review" / "boxes.jsonl")
    assert is_private(EVAL_DIR / "evalv2" / "boxes.xlsx"), "the repository ignores every .xlsx"
    assert not is_private(EVAL_DIR / "evalv2" / "boxes.jsonl")
    with pytest.raises(PrivatePathError):
        ensure_private(EVAL_DIR / "tests" / "v2" / "leak.jsonl")
    with pytest.raises(PrivatePathError):
        rec.write_records([copy.deepcopy(_record())], EVAL_DIR / "evalv2" / "datasets" / "leak.jsonl")
    assert not (EVAL_DIR / "evalv2" / "datasets" / "leak.jsonl").exists()
