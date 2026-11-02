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

"""Box-reference tooling: frame plans, numbering, blind packets, per-second boxes, records and the detector port."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from evalv2.datasets import generation_prompts as gp
from evalv2.datasets.records import validate_record
from evalv2.references import boxes as bx
from evalv2.references.detector import DetectorError, FakeDetector, SubprocessDetector, parse_reply

ITEM = {"clip": "Assault001", "kind": "instrument", "item_id": "I1", "text": "stick: Wooden stick",
        "category": "Assault"}
PERSON = (100.0, 200.0, 600.0, 400.0)
STICK = (300.0, 380.0, 420.0, 520.0)
CHAIR = (500.0, 700.0, 800.0, 900.0)


def _proposals(times, *, shift=0.0):
    return {t: [bx.Proposal(PERSON, 0.9, "person"), bx.Proposal((STICK[0] + shift, *STICK[1:]), 0.7, "stick"),
                bx.Proposal(CHAIR, 0.4, "chair")] for t in times}


def _answers(packet, box):
    out = []
    for frame in packet["frames"]:
        number = next((n for n, b in frame["marks"].items() if tuple(b) == tuple(box)), "none")
        out.append({"second": frame["t"], "choice": int(number) if number != "none" else "none", "confidence": 0.9})
    return out


def test_frame_plans_cover_the_clip_and_offset_for_the_validator():
    assert bx.frame_plan(4.2) == [0.0, 1.0, 2.0, 3.0, 4.0]
    assert bx.frame_plan(4.2, offset_s=0.5) == [0.5, 1.5, 2.5, 3.5]
    with pytest.raises(bx.BoxReferenceError):
        bx.frame_plan(0)


def test_numbering_is_deterministic_and_differs_between_passes():
    proposals = _proposals([1.0])[1.0]
    first = bx.number_proposals(proposals, seed="generator:x")
    assert first == bx.number_proposals(list(reversed(proposals)), seed="generator:x")
    orders = {tuple(p.label for p in bx.number_proposals(proposals, seed=f"validator:{i}").values()) for i in range(12)}
    assert len(orders) > 1, "numbers carry no ranking: the order depends only on the seed"


def test_boxes_geometry():
    assert bx.valid_box(STICK) and not bx.valid_box((10, 10, 10, 50)) and not bx.valid_box((0, 0, 1001, 10))
    assert bx.iou(STICK, STICK) == 1.0 and bx.iou(PERSON, CHAIR) == 0.0
    assert 0 < bx.iou(PERSON, STICK) < 1
    with pytest.raises(bx.BoxReferenceError):
        bx.Proposal((5, 5, 2, 9), 0.5)


def _packets(duration=4.2):
    gen_times, val_times = bx.frame_plan(duration), bx.frame_plan(duration, offset_s=bx.VALIDATOR_OFFSET_S)
    generator = bx.generator_packet(ITEM, gen_times, _proposals(gen_times), prompt_id="gen-box-select-v1")
    validator = bx.validator_packet(ITEM, val_times, _proposals(val_times, shift=8.0), prompt_id="gen-box-validate-v1")
    return generator, validator


def test_the_validator_packet_is_provably_blind():
    generator, validator = _packets()
    answers = _answers(generator, STICK)
    bx.assert_blind(validator, generator, answers)
    assert "answers" not in json.dumps({k: v for k, v in validator.items() if k != "answer_format"})
    leaked = json.loads(json.dumps(validator))
    leaked["frames"][0]["choice"] = 2
    with pytest.raises(bx.BoxReferenceError, match="answer field"):
        bx.assert_blind(leaked, generator, answers)
    same_frames = bx.validator_packet(ITEM, [0.0, 1.0], _proposals([0.0, 1.0], shift=8.0), prompt_id="v")
    with pytest.raises(bx.BoxReferenceError, match="reuses a frame"):
        bx.assert_blind(same_frames, generator, answers)
    copied_boxes = bx.validator_packet(ITEM, [0.5], _proposals([0.5]), prompt_id="v")
    with pytest.raises(bx.BoxReferenceError, match="box the generator chose"):
        bx.assert_blind(copied_boxes, generator, answers)
    other_item = bx.validator_packet({**ITEM, "item_id": "I2"}, [0.5], _proposals([0.5], shift=8.0), prompt_id="v")
    with pytest.raises(bx.BoxReferenceError, match="another item"):
        bx.assert_blind(other_item, generator, answers)


def test_packets_never_show_model_output_about_the_item():
    generator, _ = _packets()
    assert set(generator["item"]) == {"clip", "kind", "item_id", "text"}


def test_answers_must_cover_every_frame_with_a_shown_mark():
    generator, _ = _packets()
    answers = _answers(generator, STICK)
    assert set(bx.parse_answers(answers, generator)) == {0.0, 1.0, 2.0, 3.0, 4.0}
    with pytest.raises(bx.BoxReferenceError, match="no answer"):
        bx.parse_answers(answers[:-1], generator)
    with pytest.raises(bx.BoxReferenceError, match="not shown"):
        bx.parse_answers([{**answers[0], "choice": 99}, *answers[1:]], generator)
    with pytest.raises(bx.BoxReferenceError, match="two answers"):
        bx.parse_answers([*answers, answers[0]], generator)


def test_agreeing_passes_make_an_ai_validated_per_second_reference():
    generator, validator = _packets()
    generated = bx.boxes_by_second(generator, _answers(generator, STICK))
    validated = bx.boxes_by_second(validator, _answers(validator, (STICK[0] + 8.0, *STICK[1:])),
                                   offset_s=bx.VALIDATOR_OFFSET_S)
    assert set(generated) == {0, 1, 2, 3, 4} and set(validated) == {0, 1, 2, 3}
    record = bx.build_record(ITEM, generated, validated, duration_s=4.2, dataset="box-references-v0",
                             generator={"prompt": "gen-box-select-v1", "model": "claude"},
                             validator={"id": "gen-box-validate-v1"})
    assert record["tier"] == "ai_proposed" and "validators_disagree" in record["flags"], \
        "second 4 was seen by the first pass only, so the passes do not fully agree"
    agreed_only = {s: b for s, b in generated.items() if s in validated}
    record = bx.build_record(ITEM, agreed_only, validated, duration_s=4.2, dataset="box-references-v0",
                             generator={"prompt": "gen-box-select-v1", "model": "claude"},
                             validator={"id": "gen-box-validate-v1"})
    assert record["tier"] == "ai_validated" and validate_record(record) == []
    assert record["payload"]["boxes"]["2"] == list(STICK) and record["payload"]["visible_spans"] == [[0, 3]]


def test_disagreement_and_absence_are_flagged_not_hidden():
    generator, validator = _packets()
    generated = bx.boxes_by_second(generator, _answers(generator, STICK))
    validated = bx.boxes_by_second(validator, _answers(validator, CHAIR), offset_s=bx.VALIDATOR_OFFSET_S)
    record = bx.build_record(ITEM, generated, validated, duration_s=4.2, dataset="d", generator={"m": 1},
                             validator={"id": "v"})
    assert record["payload"]["boxes"] == {} and record["tier"] == "ai_proposed"
    assert {"no_candidate", "validators_disagree", "check_failed"} <= set(record["flags"])


def test_spans_join_short_gaps():
    assert bx.spans([0, 1, 2, 4, 5, 9]) == [[0, 5], [9, 9]]
    assert bx.spans([3, 7], max_gap=0) == [[3, 3], [7, 7]]


def test_the_box_prompts_are_registered_and_hash_pinned():
    for prompt_id, role in (("gen-box-select-v1", "generator"), ("gen-box-validate-v1", "validator")):
        entry = next(p for p in gp.load()["prompts"] if p["id"] == prompt_id)
        assert entry["role"] == role and gp.prompt_text(prompt_id).startswith("You are")
    assert "earlier answer" in gp.prompt_text("gen-box-validate-v1")


def test_the_detector_protocol_and_its_identity(tmp_path):
    script = tmp_path / "fake_detector.py"
    script.write_text(
        "import json, sys\n"
        "req = json.load(sys.stdin)\n"
        "print(json.dumps({'detector': {'name': 'owl-test', 'version': '1', 'weights_sha256': 'a' * 64},\n"
        "                  'frames': {f: [{'box': [100, 100, 300, 300], 'score': 0.8, 'label': q}]\n"
        "                             for f in req['frames'] for q in req['queries'][:1]}}))\n")
    detector = SubprocessDetector([sys.executable, str(script)])
    proposals = detector.propose(["a.jpg", "b.jpg"], ["stick"])
    assert detector.identity["name"] == "owl-test" and proposals["a.jpg"][0].box == (100, 100, 300, 300)
    failing = tmp_path / "fail.py"
    failing.write_text("import sys\nsys.exit(3)\n")
    with pytest.raises(DetectorError, match="exited 3"):
        SubprocessDetector([sys.executable, str(failing)]).propose(["a.jpg"], ["x"])
    with pytest.raises(DetectorError, match="identify"):
        parse_reply({"frames": {"a.jpg": []}}, ["a.jpg"])
    with pytest.raises(DetectorError, match="no proposals"):
        parse_reply({"detector": {"name": "n", "version": "v", "weights_sha256": "w"}, "frames": {}}, ["a.jpg"])
    assert FakeDetector({"a.jpg": [bx.Proposal(STICK, 0.5)]}).propose(["a.jpg", "b.jpg"], ["x"])["b.jpg"] == []


def test_the_frame_script_runs_in_its_own_environment():
    script = Path(__file__).resolve().parents[2] / "scripts" / "v2_box_frames.py"
    header = script.read_text().splitlines()[:4]
    assert header[0] == "# /// script" and "opencv-python-headless" in header[2]
    compile(script.read_text(), str(script), "exec")


def test_the_detector_timeout_grows_with_the_clip_so_long_clips_finish():
    from evalv2.references.detector import SubprocessDetector

    d = SubprocessDetector(command=["x"])
    assert d.timeout_for(10) == 3600.0 and d.timeout_for(393) == 393 * 30.0 and d.timeout_for(666) == 666 * 30.0
