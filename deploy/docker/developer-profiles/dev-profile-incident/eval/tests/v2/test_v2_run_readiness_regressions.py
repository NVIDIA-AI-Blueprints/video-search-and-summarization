# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Regressions found by the run-readiness dry runs (RA1, RA2, RB2 on the frozen ground truth, with fakes)."""

from __future__ import annotations

import json

import pytest

from evalv2 import cli
from evalv2.datasets.records import new_record, promote_validated, write_records
from evalv2.references import boxes as bx
from evalv2.references import chat
from evalv2.requests import vss
from evalv2.tasks.base import WorkItem
from evalv2.tasks.pipeline.runner import PipelineTask

# --- Box packets: two items of one clip never share a drawn image ---------------------------------------------------


def test_two_items_of_one_clip_get_their_own_drawn_frames():
    """``scripts/v2_box_frames.py draw`` writes each packet frame to its ``image`` path. The path named only the clip,
    so the second item drawn in a clip overwrote the first's frames, whose marks are numbered differently."""
    times = [0.0, 1.0]
    proposals = {t: [bx.Proposal((100.0, 100.0, 400.0, 300.0), 0.9, "person"),
                     bx.Proposal((500.0, 500.0, 900.0, 700.0), 0.8, "person")] for t in times}
    first = bx.generator_packet({"clip": "Assault001", "kind": "entity", "item_id": "E1", "text": "human: a"}, times,
                                proposals, prompt_id="gen-box-select-v1")
    second = bx.generator_packet({"clip": "Assault001", "kind": "entity", "item_id": "E2", "text": "human: b"}, times,
                                 proposals, prompt_id="gen-box-select-v1")
    paths = [frame["image"] for packet in (first, second) for frame in packet["frames"]]
    assert len(set(paths)) == len(paths) == 4
    assert first["frames"][0]["image"] == "Assault001/E1/generator/t0000.000.jpg"


# --- Chat leakage: a question states an answer only in whole words ------------------------------------------------


@pytest.mark.parametrize(("text", "value", "stated"), [
    ("What property was damaged or carried off?", "car", False),
    ("Was the attacker in combat gear?", "bat", False),
    ("On a scale of 1 to 5, how severe is this incident?", 5, False),
    ("How serious was it, from 1 to 5?", 1, False),
    ("Rate it out of 5.", 5, False),
    ("Was it a level 3 incident?", 3, True),
    ("Which cars were damaged?", "car", True),
    ("Were boxes knocked over?", "box", True),
    ("Was a wooden stick used as the weapon?", "wooden stick", True),
    ("Was a stick used?", "wooden stick", False),
])
def test_a_question_states_an_answer_in_whole_words_only(text, value, stated):
    assert chat.states(text, value) is stated


def test_a_leakage_false_positive_no_longer_fails_the_check():
    gt = {"by_incident_id": {"Burglary001": {"p1_shaped": {
        "incident": {"type": "burglary", "severity_level": 5, "description": "A man breaks a car window."},
        "entities": [], "instruments": [], "assets": [{"asset_id": "A1", "name": "Car", "description": "a car"}]}}}}
    record = chat.build_record("incident.asset_list", {"incident_id": "Burglary001"}, gt,
                               question="What property was damaged or carried off?",
                               paraphrases=["Which objects were affected?", "What things were harmed?"],
                               dataset="d", generator={"model": "m"}, rederived={"kind": "list", "value": ["Car"]},
                               validator={"id": "v"})
    assert record["payload"]["leakage"]["answer_in_question"] == [] and record["tier"] == "ai_validated"
    severity = chat.build_record("incident.severity", {"incident_id": "Burglary001"}, gt,
                                 question="On a scale of 1 to 5, how severe is this incident?",
                                 paraphrases=["How serious was it, from 1 to 5?", "What severity would you give?"],
                                 dataset="d", generator={"model": "m"}, rederived={"kind": "value", "value": 5},
                                 validator={"id": "v"})
    assert severity["payload"]["leakage"]["answer_in_question"] == [] and severity["tier"] == "ai_validated"


# --- Chat agreement: the blind validator copies values as the report writes them ----------------------------------


@pytest.mark.parametrize(("reference", "rederived", "agrees"), [
    ({"kind": "list", "value": ["wooden stick"]}, {"kind": "list", "value": ["Wooden stick"]}, True),
    ({"kind": "list", "value": ["car"]}, {"kind": "list", "value": ["Car", "car"]}, True),
    ({"kind": "list", "value": ["shop door"]}, {"kind": "list", "value": ["the shop door"]}, True),
    ({"kind": "list", "value": ["car", "shop door"]}, {"kind": "list", "value": ["car"]}, False),
    ({"kind": "list", "value": ["Assault006"]}, {"kind": "list", "value": ["Assault006"]}, True),
    ({"kind": "list", "value": ["car"]}, {"kind": "value", "value": ["car"]}, False),
    ({"kind": "value", "value": "road accident"}, {"kind": "value", "value": "Road accident"}, True),
    ({"kind": "value", "value": "road accident"}, {"kind": "value", "value": "assault"}, False),
    ({"kind": "value", "value": 3}, {"kind": "value", "value": "3"}, True),
    ({"kind": "value", "value": 3}, {"kind": "value", "value": 4}, False),
    ({"kind": "count", "value": 2}, {"kind": "value", "value": 2}, True),
    ({"kind": "count", "value": 2}, {"kind": "refusal", "value": None}, False),
    ({"kind": "window", "value": [2, 9]}, {"kind": "window", "value": ["2", "9.0"]}, True),
    ({"kind": "window", "value": [2, 9]}, {"kind": "window", "value": {"start": 2, "end": 9}}, True),
    ({"kind": "window", "value": [2, 9]}, {"kind": "window", "value": [2, 10]}, False),
    ({"kind": "refusal", "value": None}, {"kind": "refusal"}, True),
    ({"kind": "refusal", "value": None}, {"kind": "value", "value": "none"}, False),
])
def test_agreement_compares_canonical_answers(reference, rederived, agrees):
    assert chat.answers_agree(reference, rederived) is agrees


# --- Review sampling: chat and dashboard records can be stratified by class ---------------------------------------


def _chat_records(count_per_class):
    records = []
    for question_class, count in count_per_class.items():
        for n in range(count):
            record = new_record(record_id=f"chat:{question_class}:{n}", dataset="d", kind="chat_question",
                                payload={"n": n}, strata={"class": question_class, "scope": "incident"},
                                generator={"model": "m"})
            records.append(promote_validated(record, checks=[{"name": "c", "passed": True}],
                                             validator={"id": "v", "agrees": True}))
    return records


def test_review_sampling_can_stratify_chat_records_by_class(tmp_path, capsys):
    """The default strata (kind, tier, strata.category) leave chat and dashboard records with one stratum, so a 20%
    sample can miss a class entirely; ``--strata`` names the field to stratify by."""
    path = tmp_path / "records.jsonl"  # outside every work tree, so private
    write_records(_chat_records({"incident.type": 5, "incident.window": 5, "incident.severity": 5}), path)
    out = tmp_path / "sample.json"
    assert cli.main(["review", "sample", "--records", str(path), "--seed", "7", "--strata", "kind,tier,strata.class",
                     "--out", str(out)]) == 0
    sample = json.loads(out.read_text())
    sampled_classes = {record_id.split(":")[1] for record_id in sample["ids"]}
    assert sampled_classes == {"incident.type", "incident.window", "incident.severity"}
    assert sample["params"]["strata"] == ["kind", "tier", "strata.class"]
    assert cli.main(["review", "sample", "--records", str(path), "--seed", "7", "--strata", " , "]) == 1
    assert "names no field" in capsys.readouterr().out


# --- Track B: the caption-pass profile is budgeted now and never sent while pending -------------------------------


def test_the_caption_profile_declares_and_budgets_the_caption_stage():
    profile = vss.profile_for("production-v4-captions")
    assert "caption" in profile.stages() and profile.pending == "X16"
    assert profile.worst_case()["caption"] == (vss.CAPTION_CALLS_WORST_CASE,
                                               vss.CAPTION_CALLS_WORST_CASE * vss.CAPTION_TOKENS_BOUND)
    assert profile.body(video_url="u", model="m", duration_seconds=None)["max_caption_calls"] == 60
    base = vss.profile_for("production-v4")
    assert "caption" not in base.stages() and "caption" not in base.worst_case()
    assert "max_caption_calls" not in base.body(video_url="u", model="m", duration_seconds=None)
    with pytest.raises(ValueError, match="without a caption pass"):
        vss.profile_for("production-v4", {"max_caption_calls": 5})
    with pytest.raises(ValueError, match="max_caption_calls in 1"):
        vss.profile_for("production-v4-captions", {"max_caption_calls": 0})


def test_the_caption_window_bound_covers_the_longest_clip():
    """19 windows of 20 s overlapping by 2 s cover the longest clip (332.6 s); each window has at most 3 attempts."""
    step, window = 18.0, 20.0
    windows = 1 + -(-(332.6 - window) // step)
    assert windows == 19 and windows * 3 <= vss.CAPTION_CALLS_WORST_CASE


def test_a_pending_profile_is_planned_but_never_sent():
    profile = vss.profile_for("production-v4-captions")
    with pytest.raises(ValueError, match="pending X16"):
        vss.build_analyze_request(profile, video_url="https://r2/clip.mp4", model="m", duration_seconds=40.0)

    class Adapter:
        id, model = "a", "nvidia/cosmos-3-nano-reasoner"

        def analyze(self, *args, **kwargs):
            raise AssertionError("a pending profile must never reach the agent")

    task = PipelineTask(video_url=lambda clip: f"https://r2/{clip}.mp4")
    item = WorkItem("pipeline", "nano-pipeline", "Assault001", 1,
                    {"model": "nvidia/cosmos-3-nano-reasoner", "profile": "production-v4-captions", "overrides": {}})
    with pytest.raises(ValueError, match="pending X16"):
        task.execute(item, Adapter(), ctx=None)


# --- Review packets for box records: a sheet of frames per record, linked from the workbook ------------------------


def _frame_script():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "scripts" / "v2_box_frames.py"
    spec = importlib.util.spec_from_file_location("v2_box_frames", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_review_seconds_show_every_disagreement_then_spread_agreement():
    script = _frame_script()
    payload = {"boxes": {str(s): [1, 1, 9, 9] for s in range(30)}, "disagreed_seconds": [31, 33]}
    seconds = script.review_seconds(payload, limit=6)
    assert {31, 33} <= set(seconds) and len(seconds) == 6 and {0, 29} <= set(seconds)
    assert script.review_seconds({"boxes": {}, "disagreed_seconds": [4]}) == [4]


def test_box_records_get_review_sheets_linked_from_the_workbook(tmp_path):
    pil = pytest.importorskip("PIL.Image")
    script = _frame_script()
    frames = tmp_path / "frames" / "Road005"
    frames.mkdir(parents=True)
    for second in range(5):
        pil.new("RGB", (320, 240), (90, 90, 90)).save(frames / f"t{float(second):08.3f}.jpg")
    record = promote_validated(new_record(record_id="box:Road005:A1", dataset="d", kind="box", clip="Road005",
                                          payload={"clip": "Road005", "kind": "asset", "item_id": "A1",
                                                   "boxes": {"0": [100, 100, 300, 300], "1": [100, 100, 300, 300]},
                                                   "visible_spans": [[0, 1]], "disagreed_seconds": [3]},
                                          strata={"category": "Road Accident", "item_kind": "asset"},
                                          generator={"model": "m"}),
                               checks=[{"name": "c", "passed": True}], validator={"id": "v", "agrees": False})
    records = tmp_path / "records.jsonl"
    write_records([record], records)
    result = script.sheet(records, tmp_path / "frames", tmp_path / "sheets")
    links = json.loads((tmp_path / "sheets" / "links.json").read_text())
    assert result == {"sheets": 1, "missing_frames": 0} and links == {"box:Road005:A1": "box_Road005_A1.jpg"}
    assert pil.open(tmp_path / "sheets" / links["box:Road005:A1"]).size == (3 * 320, 240)
    workbook = tmp_path / "review.xlsx"
    assert cli.main(["review", "export", "--records", str(records), "--out", str(workbook),
                     "--links", str(tmp_path / "sheets" / "links.json")]) == 0
    import openpyxl
    sheet = openpyxl.load_workbook(workbook)["Review"]
    header = [cell.value for cell in sheet[1]]
    assert sheet.cell(row=2, column=header.index("link") + 1).value == "box_Road005_A1.jpg"
