# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""The reference runs RA1, RA2 and RB2 end to end with fakes: plans, blind packets, file handoff, tiers, records."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from evalv2 import cli
from evalv2.datasets import registry
from evalv2.datasets.records import load_records, validate_record
from evalv2.references import boxes as bx
from evalv2.references import dashboard, dry_run, ra1, ra2, rb2
from evalv2.references.detector import FakeDetector
from evalv2.references.passes import (
    DRY_RUN_MODEL,
    FilePass,
    PassError,
    ScriptedPass,
    write_answer,
)

FIXTURE_ALLOWLIST = Path(__file__).resolve().parent / "fixtures" / "chart-allowlist.json"
REAL_DATA_ROOT = Path(os.environ.get(registry.DATA_ROOT_ENV) or registry.DEFAULT_DATA_ROOT)
REAL_GT = (REAL_DATA_ROOT / "ground_truth" / "ground-truth-v1.0").is_dir()


def _entry(category, kind, severity, start, end, description, entities=(), instruments=(), assets=()):
    return {"filename": "x.mp4", "category": category, "p1_shaped": {
        "incident": {"type": kind, "severity_level": severity, "start_timestamp": start, "end_timestamp": end,
                     "description": description},
        "entities": [{"entity_id": e, "type": t, "description": d} for e, t, d in entities],
        "instruments": [{"instrument_id": i, "entity_id": h, "name": n, "description": n, "threat_level": 3}
                        for i, h, n in instruments],
        "assets": [{"asset_id": a, "name": n, "description": n} for a, n in assets]}}


GT = {"by_incident_id": {
    "Assault001": _entry("Assault", "assault", 4, 2, 9, "A man in red hits a man in blue with a wooden stick.",
                         entities=[("E1", "human", "man in red"), ("E2", "human", "man in blue")],
                         instruments=[("I1", "E1", "Wooden stick")]),
    "Assault002": _entry("Assault", "assault", 2, 0, 5, "Two people push each other outside a bar.",
                         entities=[("E1", "human", "woman"), ("E2", "human", "man")]),
    "Burglary003": _entry("Burglary", "burglary", 3, 1, 20, "A person forces a shop door open with a crowbar.",
                          entities=[("E1", "human", "person in hoodie")], instruments=[("I1", "E1", "crowbar")],
                          assets=[("A1", "Shop door")]),
    "Burglary004": _entry("Burglary", "burglary", 3, 4, 30, "A masked man climbs through a window at night.",
                          entities=[("E1", "human", "masked man")], assets=[("A1", "window")]),
    "Road005": _entry("Road Accident", "road accident", 5, 0, 6, "A car hits a cyclist at a junction.",
                      entities=[("E1", "human", "cyclist"), ("E2", "animal", "dog")],
                      assets=[("A1", "car"), ("A2", "bicycle")]),
}}
DURATIONS = {"Assault001": 12.4, "Assault002": 8.0, "Burglary003": 21.7, "Burglary004": 9.6, "Road005": 6.2}
VIDEOS = {clip: f"/videos/{clip}.mp4" for clip in DURATIONS}


def _planned(clips=tuple(DURATIONS)):
    return ra1.plan(GT, list(clips), DURATIONS, videos=VIDEOS)


# --- RA1 ----------------------------------------------------------------------------------------------------------


def test_ra1_plans_every_item_its_query_and_both_frame_plans():
    planned = _planned()
    assert len(planned["items"]) == 3 + 2 + 3 + 2 + 4  # every entity, instrument and asset, clip by clip
    kinds = {(i["clip"], i["item_id"]): (i["kind"], i["query"]) for i in planned["items"]}
    assert kinds[("Assault001", "E1")] == ("entity", "person") and kinds[("Road005", "E2")] == ("entity", "animal")
    assert kinds[("Assault001", "I1")] == ("instrument", "wooden stick")
    assert kinds[("Burglary003", "A1")][1] == "shop door"
    assert planned["queries"]["Assault001"] == ["person", "wooden stick"]
    assert planned["times"]["Assault001"] == {"generator": bx.frame_plan(12.4), "validator": bx.frame_plan(12.4,
                                                                                                         offset_s=0.5)}
    extraction = {c["clip"]: c for c in planned["extraction"]["clips"]}
    assert extraction["Road005"]["times"] == sorted(set(bx.frame_plan(6.2)) | set(bx.frame_plan(6.2, offset_s=0.5)))
    assert extraction["Road005"]["video"] == "/videos/Road005.mp4"


def _passes(scene, *, generator_error=0.0, validator_error=0.0):
    return (dry_run.box_pass(scene, role="generator", prompt=ra1.GENERATOR_PROMPT, frame_error=generator_error),
            dry_run.box_pass(scene, role="validator", prompt=ra1.VALIDATOR_PROMPT, frame_error=validator_error))


def test_ra1_agreeing_passes_make_ai_validated_records_and_count_single_pass_seconds(tmp_path):
    planned = _planned()
    scene = dry_run.SyntheticScene(planned, seed=3, miss_rate=0.0, full_visibility=True)
    generator, validator = _passes(scene)
    summary = ra1.run(planned, detector=scene.detector(tmp_path / "frames"), generator=generator, validator=validator,
                      out=tmp_path / "run", frames_root=tmp_path / "frames", dataset="box-references-v1-dryrun")
    records = load_records(tmp_path / "run" / "records.jsonl")
    assert len(records) == summary["records"] == len(planned["items"])
    assert summary["tiers"] == {"ai_proposed": 0, "ai_validated": len(records), "human_verified": 0}
    assert all(validate_record(r) == [] and r["provenance"]["generator"]["model"] == DRY_RUN_MODEL for r in records)
    assert summary["counts"]["blind_checks_passed"] == len(records) and summary["problems"] == []
    # 12.4 s, 21.7 s and 6.2 s end in .0-.5: the generator's last frame is a second the offset validator never sees
    assert summary["seconds"]["single_pass"] == 3 + 1 + 3 and summary["seconds"]["disagreed"] == 0
    assert summary["drawn_images"]["paths"] == summary["drawn_images"]["frames_in_packets"]
    record = next(r for r in records if r["record_id"] == "box:Assault001:I1")
    assert sorted(map(int, record["payload"]["boxes"])) == list(range(12))
    assert record["provenance"]["validator"]["single_pass_seconds"] == [12]
    assert record["provenance"]["generator"]["detector"]["name"] == "synthetic-dry-run"


def test_ra1_disagreeing_passes_are_flagged_never_validated(tmp_path):
    planned = _planned(["Assault001"])
    scene = dry_run.SyntheticScene(planned, seed=3, miss_rate=0.0, full_visibility=True, distractors=2)
    generator, validator = _passes(scene, validator_error=0.5)
    summary = ra1.run(planned, detector=scene.detector(tmp_path / "f"), generator=generator, validator=validator,
                      out=tmp_path / "run", frames_root=tmp_path / "f", dataset="d")
    assert summary["tiers"]["ai_validated"] == 0 and summary["flags"]["validators_disagree"] == 3
    assert summary["seconds"]["disagreed"] > 0


def test_ra1_resumes_through_file_handoff_and_refuses_a_stale_answer(tmp_path):
    planned = _planned(["Road005"])
    scene = dry_run.SyntheticScene(planned, seed=5, miss_rate=0.0, full_visibility=True)
    scripted_gen, scripted_val = _passes(scene)
    generator = FilePass(tmp_path / "run" / "generator", {"model": "claude", "prompt": ra1.GENERATOR_PROMPT})
    validator = FilePass(tmp_path / "run" / "validator", {"model": "claude", "prompt": ra1.VALIDATOR_PROMPT,
                                                          "id": "claude:gen-box-validate-v1"})
    detector = scene.detector(tmp_path / "frames")

    def advance():
        return ra1.run(planned, detector=detector, generator=generator, validator=validator, out=tmp_path / "run",
                       frames_root=tmp_path / "frames", dataset="box-references-v1")

    first = advance()
    assert first["counts"]["pending_generator"] == 4 and first["records"] == 0
    packets = sorted((tmp_path / "run" / "generator" / "packets").glob("*.json"))
    assert len(packets) == 4 and not (tmp_path / "run" / "validator" / "packets").exists()
    for path in packets:
        body = json.loads(path.read_text())
        write_answer(tmp_path / "run" / "generator", body["packet_id"], body["packet"],
                     scripted_gen.answer(body["packet_id"], body["packet"]))
    second = advance()
    assert second["counts"]["pending_validator"] == 4 and second["counts"]["blind_checks_passed"] == 4
    for path in sorted((tmp_path / "run" / "validator" / "packets").glob("*.json")):
        body = json.loads(path.read_text())
        assert "answers" not in json.dumps(body["packet"]["frames"])
        write_answer(tmp_path / "run" / "validator", body["packet_id"], body["packet"],
                     scripted_val.answer(body["packet_id"], body["packet"]))
    third = advance()
    assert third["records"] == 4 and third["tiers"]["ai_validated"] == 4 and third["tiers"]["human_verified"] == 0
    assert (tmp_path / "run" / "proposals" / "Road005.json").exists(), "the detector ran once and was cached"
    stale = json.loads(packets[0].read_text())
    write_answer(tmp_path / "run" / "generator", stale["packet_id"], {**stale["packet"], "prompt": "other"},
                 {"answers": []})
    with pytest.raises(PassError, match="another version"):
        advance()


def test_ra1_an_item_whose_validator_packet_is_not_blind_gets_no_record(tmp_path):
    """A static detector box identical at the generator's and the validator's frames trips ``assert_blind``: the
    item is listed, never validated."""
    planned = _planned(["Road005"])
    box = bx.Proposal((100.0, 100.0, 300.0, 300.0), 0.9, "car")
    times = planned["times"]["Road005"]
    detector = FakeDetector({ra1.frame_path(tmp_path, "Road005", t): [box]
                             for t in set(times["generator"]) | set(times["validator"])})
    pick = ScriptedPass(lambda pid, packet: {"answers": [{"second": f["t"], "choice": 1 if f["marks"] else "none",
                                                          "confidence": 1} for f in packet["frames"]]},
                        {"model": DRY_RUN_MODEL, "prompt": "p", "id": "v"})
    summary = ra1.run(planned, detector=detector, generator=pick, validator=pick, out=tmp_path / "run",
                      frames_root=tmp_path, dataset="d")
    assert [p["item"] for p in summary["problems"] if p["stage"] == "assert_blind"] == ["Road005-A1"]
    assert summary["records"] == 3


def test_ra1_needs_the_detector_or_its_cached_proposals(tmp_path):
    planned = _planned(["Road005"])
    gen, val = _passes(dry_run.SyntheticScene(planned, seed=1))
    with pytest.raises(ValueError, match="X5"):
        ra1.run(planned, detector=None, generator=gen, validator=val, out=tmp_path / "run", frames_root=tmp_path,
                dataset="d")


def test_a_scripted_pass_can_never_pass_for_a_real_generator():
    with pytest.raises(PassError, match="scripted-dry-run"):
        ScriptedPass(lambda pid, packet: None, {"model": "claude"})


# --- RA2 ----------------------------------------------------------------------------------------------------------

HALVES = {"dev": ra2.restrict(GT, ["Assault001", "Burglary003", "Road005"]),
          "test": ra2.restrict(GT, ["Assault002", "Burglary004"])}


def test_ra2_plans_are_deterministic_eligible_and_per_half():
    plan = ra2.question_plan(HALVES["dev"], half="dev", seed=1, per_class=2)
    assert plan == ra2.question_plan(HALVES["dev"], half="dev", seed=1, per_class=2)
    by_class = {}
    for entry in plan:
        by_class.setdefault(entry["class"], []).append(entry["params"])
    assert len(by_class["incident.type"]) == 2 and len(by_class["incident.instrument_list"]) == 2
    assert {p["incident_id"] for p in by_class["incident.instrument_list"]} == {"Assault001", "Burglary003"}
    assert {p["incident_id"] for p in by_class["incident.asset_list"]} <= {"Burglary003", "Road005"}
    assert all(p["instrument_id"] == "I1" for p in by_class["incident.instrument_holder"])
    assert [p["type"] for p in by_class["library.type_count"]] == ["assault", "burglary", "road accident"]
    assert all(p["corpus"] == "dev" for c in ra2.LIBRARY_CLASSES for p in by_class.get(c, []))
    assert [p["level"] for p in by_class["library.severity_at_least_count"]] == [2, 3, 4, 5]
    test_plan = ra2.question_plan(HALVES["test"], half="test", seed=1, per_class=2)
    assert not any(e["class"] == "incident.instrument_list" for e in test_plan), "no instrument in the test half"


def test_ra2_end_to_end_keeps_halves_apart_and_validates_blind(tmp_path):
    plans = {half: ra2.question_plan(HALVES[half], half=half, seed=1, per_class=2) for half in HALVES}
    summary = ra2.run(HALVES, plans, generator=dry_run.chat_generator(HALVES), validator=dry_run.chat_validator(),
                      out=tmp_path / "run", dataset="chat-references-v1-dryrun")
    records = load_records(tmp_path / "run" / "records.jsonl")
    assert summary["records"] == sum(len(p) for p in plans.values()) == len(records)
    assert summary["recount_problems"] == 0 and summary["tiers"]["human_verified"] == 0
    assert summary["counts"]["blind_checks_passed"] == len(records)
    dev_assaults = next(r for r in records if r["payload"]["class"] == "library.type_count"
                        and r["payload"]["params"] == {"type": "assault", "corpus": "dev"})
    assert dev_assaults["payload"]["answer"] == {"kind": "count", "value": 1}, "the dev library holds one assault"
    validated = [r for r in records if r["tier"] == "ai_validated"]
    assert validated and all(r["provenance"]["validator"]["answer"] for r in validated)


def test_ra2_a_disagreeing_rederivation_is_flagged_with_its_answer(tmp_path):
    plans = {"dev": [{"class": "incident.people_count", "params": {"incident_id": "Assault001"}}]}
    wrong = ScriptedPass(lambda pid, packet: {"kind": "count", "value": 7},
                         {"model": DRY_RUN_MODEL, "prompt": ra2.VALIDATOR_PROMPT, "id": "v"})
    summary = ra2.run({"dev": HALVES["dev"]}, plans, generator=dry_run.chat_generator(HALVES), validator=wrong,
                      out=tmp_path / "run", dataset="d")
    record = load_records(tmp_path / "run" / "records.jsonl")[0]
    assert record["tier"] == "ai_proposed" and "validators_disagree" in record["flags"]
    assert record["provenance"]["validator"]["answer"] == {"kind": "count", "value": 7}
    assert summary["validator_disagreed"] == 1


def test_ra2_file_handoff_writes_packets_for_the_sessions(tmp_path):
    plans = {"dev": [{"class": "incident.type", "params": {"incident_id": "Road005"}}]}
    generator = FilePass(tmp_path / "run" / "generator", {"model": "claude", "prompt": ra2.GENERATOR_PROMPT})
    validator = FilePass(tmp_path / "run" / "validator", {"model": "claude", "prompt": ra2.VALIDATOR_PROMPT,
                                                          "id": "claude:gen-chat-rederive-v1"})
    first = ra2.run({"dev": HALVES["dev"]}, plans, generator=generator, validator=validator, out=tmp_path / "run",
                    dataset="chat-references-v1")
    assert first["counts"] == {"pending_generator": 1} and first["records"] == 0
    (packet,) = (tmp_path / "run" / "generator" / "packets").glob("*.json")
    body = json.loads(packet.read_text())
    assert body["packet"]["computed_answer"] == {"kind": "value", "value": "road accident"}
    write_answer(tmp_path / "run" / "generator", body["packet_id"], body["packet"],
                 {"question": "What kind of incident is shown?", "paraphrases": ["How is it classed?", "Which type?"]})
    second = ra2.run({"dev": HALVES["dev"]}, plans, generator=generator, validator=validator, out=tmp_path / "run",
                     dataset="chat-references-v1")
    assert second["counts"] == {"blind_checks_passed": 1, "pending_validator": 1}
    (val_packet,) = (tmp_path / "run" / "validator" / "packets").glob("*.json")
    assert "computed_answer" not in val_packet.read_text() and "road accident" not in json.dumps(
        {k: v for k, v in json.loads(val_packet.read_text())["packet"].items() if k != "context"})


# --- RB2 ----------------------------------------------------------------------------------------------------------

ALLOWLIST = json.loads(FIXTURE_ALLOWLIST.read_text())


def test_rb2_plan_covers_every_dimension_and_period_with_distinct_packets():
    planned = rb2.spec_plan(ALLOWLIST, seed=1)
    specs = [e["expected"] for e in planned if not dashboard.is_refusal(e["expected"])]
    assert len(specs) == 14 * 4 + 2 * 2 and len(planned) - len(specs) == 15
    assert {s["dimension"] for s in specs} == set(ALLOWLIST["dimensions"])
    assert sorted({sum(1 for s in specs if s["period"] == p) for p in ALLOWLIST["periods"]}) == [12]
    assert all(dashboard.parse_spec(s, ALLOWLIST) == s for s in specs)
    packets = [json.dumps(rb2._generator_packet(e), sort_keys=True) for e in planned]
    assert len(set(packets)) == len(packets), "every generator packet differs, unsupported ones too"
    assert planned == rb2.spec_plan(ALLOWLIST, seed=1) and planned != rb2.spec_plan(ALLOWLIST, seed=2)


def test_rb2_end_to_end_validates_exact_specs_and_flags_the_rest(tmp_path):
    planned = rb2.spec_plan(ALLOWLIST, seed=1)
    exact = rb2.run(ALLOWLIST, planned, generator=dry_run.dashboard_generator(),
                    validator=dry_run.dashboard_validator(seed=1), out=tmp_path / "a", dataset="d")
    assert exact["records"] == 75 and exact["tiers"] == {"ai_proposed": 0, "ai_validated": 75, "human_verified": 0}
    wrong = rb2.run(ALLOWLIST, planned, generator=dry_run.dashboard_generator(),
                    validator=dry_run.dashboard_validator(seed=1, error=1.0), out=tmp_path / "b", dataset="d")
    assert wrong["flags"] == {"validators_disagree": 60}, "every spec re-derived with a wrong period; refusals agree"


def test_rb2_reports_duplicate_requests(tmp_path):
    planned = [e for e in rb2.spec_plan(ALLOWLIST, seed=1) if dashboard.is_refusal(e["expected"])][:2]
    same = ScriptedPass(lambda pid, packet: {"request": "Chart the weather.", "paraphrases": ["a", "b"]},
                        {"model": DRY_RUN_MODEL, "prompt": rb2.GENERATOR_PROMPT})
    summary = rb2.run(ALLOWLIST, planned, generator=same, validator=dry_run.dashboard_validator(seed=1),
                      out=tmp_path / "run", dataset="d")
    assert summary["records"] == 1 and summary["problems"][0]["problem"] == "duplicate request"


def test_rb2_counts_specs_whose_reference_chart_has_rows(tmp_path):
    reports = {clip: entry["p1_shaped"] for clip, entry in GT["by_incident_id"].items()}
    specs = [{"metric": "incidents", "dimension": "incident_type", "period": "all_time", "incidentType": None,
              "severity": None},
             {"metric": "incidents", "dimension": "incident_type", "period": "all_time", "incidentType": "explosion",
              "severity": None}]
    figures = rb2.corpus_figures(reports, specs, ALLOWLIST, taken_at="2026-11-01T12:00:00+00:00",
                                 model_name="ground-truth-v1.0", out=tmp_path)
    assert figures["rows"] == {"videos": 5, "model_runs": 5, "report_revisions": 5}
    assert figures["reference_charts"] == {"chart_with_rows": 1, "chart_empty": 1}
    assert (tmp_path / "corpus-snapshot.json").exists()


# --- CLI ----------------------------------------------------------------------------------------------------------


def test_a_real_reference_run_names_the_sessions_models(tmp_path, capsys):
    assert cli.main(["references", "rb2", "--out", str(tmp_path / "run"), "--allowlist", str(FIXTURE_ALLOWLIST)]) == 1
    assert "--generator-model" in capsys.readouterr().out
    assert cli.main(["references", "rb2", "--out", str(tmp_path / "run"), "--allowlist", str(FIXTURE_ALLOWLIST),
                     "--generator-model", "claude", "--validator-model", "claude",
                     "--dataset", "dashboard-references-v1-dryrun"]) == 1
    assert "dry" in capsys.readouterr().out


@pytest.mark.skipif(not REAL_GT, reason="the frozen ground truth is private and not on this machine")
@pytest.mark.parametrize("run", ["ra1", "ra2", "rb2"])
def test_the_dry_runs_complete_on_the_frozen_ground_truth(tmp_path, run, capsys):
    extra = ["--allowlist", str(FIXTURE_ALLOWLIST)] if run == "rb2" else ["--half", "dev"]
    assert cli.main(["references", run, "--out", str(tmp_path / run), "--data-root", str(REAL_DATA_ROOT),
                     "--dry-run", *extra]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["tiers"]["human_verified"] == 0 and summary["problems"] == []
    assert summary["dataset"].endswith("-dryrun")


def test_ra1_plan_only_writes_the_extraction_plan_without_a_detector(tmp_path, monkeypatch, capsys):
    from evalv2.datasets import ground_truth

    dev = sorted(ground_truth.load_split_v2()["dev"])[:2]
    fake = {"by_incident_id": {clip: _entry("Assault", "assault", 3, 0, 5, "x", entities=[("E1", "human", "a")])
                               for clip in dev}, "split": {"dev": dev, "test": [], "excluded": []}}
    monkeypatch.setattr(ground_truth, "load_gt_v1", lambda **kwargs: fake)
    monkeypatch.setattr(ground_truth, "load_split_v2", lambda **kwargs: {"dev": dev, "test": []})
    assert cli.main(["references", "ra1", "--out", str(tmp_path / "run"), "--half", "dev", "--plan-only"]) == 0
    assert json.loads(capsys.readouterr().out)["items"] == 2
    plan = json.loads((tmp_path / "run" / "extraction-plan.json").read_text())
    assert [c["clip"] for c in plan["clips"]] == dev and all(c["video"].endswith(".mp4") for c in plan["clips"])


def test_the_frame_script_draws_the_wrapped_packets_a_run_writes(tmp_path):
    pil = pytest.importorskip("PIL.Image")
    import importlib.util

    path = Path(__file__).resolve().parents[2] / "scripts" / "v2_box_frames.py"
    spec = importlib.util.spec_from_file_location("v2_box_frames", path)
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    (tmp_path / "frames" / "Road005").mkdir(parents=True)
    pil.new("RGB", (320, 240)).save(tmp_path / "frames" / "Road005" / "t0001.000.jpg")
    packet = bx.generator_packet({"clip": "Road005", "kind": "asset", "item_id": "A1", "text": "car: car"}, [1.0],
                                 {1.0: [bx.Proposal((100.0, 100.0, 300.0, 300.0), 0.9, "car")]},
                                 prompt_id=ra1.GENERATOR_PROMPT)
    generator = FilePass(tmp_path / "generator", {"model": "claude", "prompt": ra1.GENERATOR_PROMPT})
    assert generator.answer("ra1-gen-Road005-A1", packet) is None
    written = tmp_path / "generator" / "packets" / "ra1-gen-Road005-A1.json"
    assert script.draw(written, tmp_path / "frames", tmp_path / "drawn") == 1
    assert (tmp_path / "drawn" / "Road005" / "A1" / "generator" / "t0001.000.jpg").exists()
