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

"""The review kit: deterministic stratified sampling, the xlsx round trip, applying decisions, and κ."""

from __future__ import annotations

import json
from pathlib import Path

import openpyxl
import pytest

from evalv2 import cli
from evalv2.core.private import PrivatePathError
from evalv2.datasets import records as rec
from evalv2.review import kappa, sampler, workbook

EVAL_DIR = Path(__file__).resolve().parents[2]


def _records(n_per=10):
    out = []
    for category in ("Assault", "Burglary"):
        for i in range(n_per):
            record = rec.new_record(record_id=f"box:{category}{i:03d}:E1:3", dataset="box-references-v0", kind="box",
                                    clip=f"{category}{i:03d}", strata={"category": category},
                                    payload={"second": 3, "box": [i, i, i + 10, i + 10]},
                                    generator={"prompt": "gen-box-select-v1", "model": "claude"})
            if i % 2:
                record = rec.promote_validated(record, checks=[{"name": "c", "passed": True}],
                                               validator={"id": "gen-box-validate-v1", "agrees": True})
            out.append(record)
    out[3] = {**out[3], "flags": ["ambiguous"]}
    return out


def test_sampling_is_deterministic_stratified_and_takes_every_flagged_record():
    records = _records()
    first = sampler.sample_for_review(records, seed=7, fraction=0.2)
    assert first.ids == sampler.sample_for_review(list(reversed(records)), seed=7, fraction=0.2).ids
    assert first.sha256 == sampler.sample_for_review(records, seed=7, fraction=0.2).sha256
    assert "box:Assault003:E1:3" in first.ids and first.flagged == ["box:Assault003:E1:3"]
    assert set(first.strata) == {f"kind=box | tier={t} | strata.category={c}"
                                 for t in ("ai_proposed", "ai_validated") for c in ("Assault", "Burglary")}
    for stratum in first.strata.values():
        assert stratum["sampled"] == 1 and stratum["inclusion"] == 1 / stratum["population"]
    assert sampler.sample_for_review(records, seed=8, fraction=0.2).ids != first.ids


def test_adding_a_record_never_reshuffles_the_existing_choice():
    records = _records()
    before = sampler.sample_for_review(records, seed=7, fraction=0.5)
    extra = rec.new_record(record_id="box:Assault999:E1:3", dataset="box-references-v0", kind="box",
                           clip="Assault999", strata={"category": "Assault"}, payload={},
                           generator={"prompt": "gen-box-select-v1"})
    after = sampler.sample_for_review([*records, extra], seed=7, fraction=0.5)
    key = "kind=box | tier=ai_proposed | strata.category=Assault"
    assert len(set(before.strata[key]["ids"]) - set(after.strata[key]["ids"])) <= 1, "at most one is displaced"
    for other in set(before.strata) - {key}:
        assert before.strata[other] == after.strata[other], "other strata are untouched"


def test_human_verified_records_are_not_sampled_again():
    records = _records()
    done = records[0]
    decision = rec.HumanDecision(done["record_id"], "accept", "Reviewer One", "2026-11-02T09:00:00+00:00",
                                 rec.payload_sha256(done), "f" * 64)
    records[0] = rec.apply_human_decision(done, decision)
    assert done["record_id"] not in sampler.sample_for_review(records, seed=1, fraction=1.0).ids


def test_the_ai_error_estimate_weights_each_stratum_by_its_population():
    records = _records()
    sample = sampler.sample_for_review(records, seed=7, fraction=0.2)
    decisions = {i: "accept" for i in sample.ids}
    one = next(iter(sample.strata.values()))["ids"][0]
    decisions[one] = "reject"
    estimate = sampler.ai_error_rate(sample, decisions)
    population = sum(s["population"] for s in sample.strata.values())
    stratum = next(s for s in sample.strata.values() if one in s["ids"])
    assert estimate["estimated_error_rate"] == pytest.approx(stratum["population"] / population)
    assert estimate["flagged"] == {"reviewed": 1, "not_accepted": 0, "total": 1}
    assert sampler.ai_error_rate(sample, {})["unestimated_strata"]


def _fill(path, rows):
    book = openpyxl.load_workbook(path)
    sheet = book[workbook.SHEET]
    header = [c.value for c in sheet[1]]
    for row in sheet.iter_rows(min_row=2):
        values = rows.get(row[0].value)
        if values:
            for name, value in values.items():
                row[header.index(name)].value = value
    book.save(path)


def test_the_workbook_round_trip_verifies_records(tmp_path):
    records = _records()
    sample = sampler.sample_for_review(records, seed=7, fraction=0.2)
    path = tmp_path / "review.xlsx"
    written = workbook.export_workbook(records, path, sample=sample, links={sample.ids[0]: "frames/x.jpg"})
    assert written["rows"] == len(sample.ids)
    sheet = openpyxl.load_workbook(path)[workbook.SHEET]
    assert sheet.column_dimensions["N"].hidden and sheet.data_validations.dataValidation
    first, second, third = sample.ids[:3]
    _fill(path, {first: {"decision": "accept", "reviewer": "Reviewer One"},
                 second: {"decision": "edit", "reviewer": "Reviewer One",
                          "edited_value": json.dumps({"second": 4, "box": [1, 2, 3, 4]})},
                 third: {"decision": "reject", "reviewer": "Reviewer Two", "note": "wrong object"}})
    decisions = workbook.read_workbook(path)
    assert [d.decision for d in decisions] == ["accept", "edit", "reject"]
    updated, counts = workbook.apply_decisions(records, decisions)
    assert counts == {"applied": 3, "accept": 1, "edit": 1, "reject": 1}
    assert sum(r["tier"] == "human_verified" for r in updated) == 3
    out = tmp_path / "decisions.jsonl"
    workbook.write_decisions(decisions, out)
    assert workbook.read_decisions(out) == decisions


@pytest.mark.parametrize(("values", "problem"), [
    ({"decision": "approve", "reviewer": "R"}, "not one of"),
    ({"decision": "accept"}, "no reviewer"),
    ({"decision": "accept", "reviewer": "ai:claude"}, "names a model"),
    ({"decision": "edit", "reviewer": "R"}, "edited value"),
    ({"decision": "edit", "reviewer": "R", "edited_value": "{not json"}, "not JSON"),
])
def test_bad_rows_are_all_reported(tmp_path, values, problem):
    records = _records(2)
    path = tmp_path / "review.xlsx"
    workbook.export_workbook(records, path)
    _fill(path, {records[0]["record_id"]: values})
    with pytest.raises(workbook.ReviewImportError, match=problem):
        workbook.read_workbook(path)


def test_a_decision_on_a_changed_record_is_refused(tmp_path):
    records = _records(2)
    path = tmp_path / "review.xlsx"
    workbook.export_workbook(records, path)
    _fill(path, {records[0]["record_id"]: {"decision": "accept", "reviewer": "R"}})
    decisions = workbook.read_workbook(path)
    changed = [{**records[0], "payload": {"second": 9}}, *records[1:]]
    with pytest.raises(rec.RecordError, match="stale"):
        workbook.apply_decisions(changed, decisions)


def test_workbooks_are_never_written_where_git_could_commit_them(tmp_path):
    # Every .xlsx is gitignored in this repository, so a committable name needs another extension.
    with pytest.raises(PrivatePathError):
        workbook.export_workbook(_records(2), EVAL_DIR / "evalv2" / "review" / "leak.json")
    assert not (EVAL_DIR / "evalv2" / "review" / "leak.json").exists()
    with pytest.raises(PrivatePathError):
        workbook.write_decisions([], EVAL_DIR / "evalv2" / "review" / "leak.jsonl")


def test_cohen_kappa_matches_a_worked_example():
    # 50 items: both yes 20, A yes B no 5, A no B yes 10, both no 15 -> p_o 0.7, p_e 0.5, kappa 0.4.
    pairs = ["yy"] * 20 + ["yn"] * 5 + ["ny"] * 10 + ["nn"] * 15
    a = {str(i): p[0] for i, p in enumerate(pairs)}
    b = {str(i): p[1] for i, p in enumerate(pairs)}
    result = kappa.cohen_kappa(a, b)
    assert result["kappa"] == pytest.approx(0.4) and result["n"] == 50
    assert kappa.cohen_kappa({"1": "x", "2": "x"}, {"1": "x", "2": "x"})["kappa"] is None
    assert kappa.cohen_kappa({"1": "x"}, {"2": "x"})["n"] == 0


def test_weighted_kappa_rewards_near_misses_on_ordered_labels():
    a = {"1": 1, "2": 2, "3": 3, "4": 4, "5": 5}
    assert kappa.weighted_kappa(a, a, categories=[1, 2, 3, 4, 5])["kappa"] == pytest.approx(1.0)
    near = kappa.weighted_kappa(a, {"1": 2, "2": 3, "3": 4, "4": 5, "5": 5}, categories=[1, 2, 3, 4, 5])
    far = kappa.weighted_kappa(a, {"1": 5, "2": 5, "3": 1, "4": 1, "5": 1}, categories=[1, 2, 3, 4, 5])
    assert near["kappa"] > 0.5 > far["kappa"]
    with pytest.raises(ValueError, match="outside"):
        kappa.weighted_kappa({"1": 9}, {"1": 1}, categories=[1, 2])


def test_the_review_cli_runs_the_whole_loop(tmp_path, capsys):
    records_path = tmp_path / "records.jsonl"
    rec.write_records(_records(), records_path)
    xlsx = tmp_path / "review.xlsx"
    assert cli.main(["review", "export", "--records", str(records_path), "--out", str(xlsx), "--seed", "7"]) == 0
    sample = sampler.sample_for_review(_records(), seed=7, fraction=0.2)
    _fill(xlsx, {i: {"decision": "accept", "reviewer": "Reviewer One"} for i in sample.ids})
    decisions_path = tmp_path / "decisions.jsonl"
    assert cli.main(["review", "import", "--workbook", str(xlsx), "--out", str(decisions_path)]) == 0
    out = tmp_path / "records-v1.jsonl"
    assert cli.main(["review", "apply", "--records", str(records_path), "--decisions", str(decisions_path),
                     "--out", str(out)]) == 0
    assert sum(r["tier"] == "human_verified" for r in rec.load_records(out)) == len(sample.ids)
    assert cli.main(["review", "kappa", "--a", str(decisions_path), "--b", str(decisions_path)]) == 0
    capsys.readouterr()
    assert cli.main(["review", "export", "--records", str(records_path),
                     "--out", str(EVAL_DIR / "evalv2" / "leak.json")]) == 1
    assert "could be committed" in capsys.readouterr().out
