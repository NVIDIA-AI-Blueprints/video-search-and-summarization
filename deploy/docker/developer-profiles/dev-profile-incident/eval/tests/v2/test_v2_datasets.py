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

"""The dataset registry, the ground-truth and split wrappers, and the config loader (no real ground truth needed)."""

from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from evalv2.core.hashing import sha256_bytes
from evalv2.datasets import ground_truth, registry
from evalv2.datasets.loader import load_for_config
from evalv2.datasets.records import new_record, write_records

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import ground_truth_freeze as gtf  # noqa: E402

REAL = registry.load_registry()


def _gt_records():
    return {
        "gt_incidents": [
            {"Filename": "Assault001_x264.mp4", "Incident_ID (P_key)": "Assault001", "Type": "Assault",
             "Start_Timestamp": 2, "End_Timestamp": 9, "Duration": 7, "Description": "Two people fight.",
             "Severity_Level": 3},
            {"Filename": "Burglary002_x264.mp4", "Incident_ID (P_key)": "Burglary002", "Type": "Burglary",
             "Start_Timestamp": 0, "End_Timestamp": 20, "Duration": 20, "Description": "A door is forced.",
             "Severity_Level": 2},
        ],
        "gt_entities": [{"Filename": "Assault001_x264.mp4", "Incident_ID (P_key, F_key)": "Assault001",
                         "Entity_ID (P_key)": "E1", "Type": "human", "Description": "Person in red"}],
        "gt_instruments": [],
        "gt_assets": [{"Filename": "Burglary002_x264.mp4", "Incident_ID (P_key, F_key)": "Burglary002",
                       "Asset_ID (P_key)": "A1", "Name": "door", "Description": "Shop door"}],
    }


def _split():
    return {"version": "split-v2", "seed": [1], "dev": ["Assault001"], "test": ["Burglary002"],
            "by_category": {"Assault": {"dev": ["Assault001"], "test": []},
                            "Burglary": {"dev": [], "test": ["Burglary002"]}}}


@pytest.fixture
def fixture_registry(tmp_path):
    """A registry whose split and ground truth are small synthetic files in a temporary data root."""
    data_root, eval_root = tmp_path / "data", tmp_path / "evalroot"
    gt_dir = data_root / "ground_truth" / "ground-truth-v1.0"
    gt_dir.mkdir(parents=True)
    split = _split()
    files = {"ground_truth.json": gtf.canonical_json({"version": gtf.VERSION, "status": "frozen",
                                                      "sheets": _gt_records()}),
             "split-v2.json": gtf.canonical_json({**split, "excluded": []})}
    for name, content in files.items():
        (gt_dir / name).write_bytes(content)
    hashes = {name: sha256_bytes(content) for name, content in files.items()}
    (gt_dir / "MANIFEST.json").write_text(json.dumps({"status": "frozen", "blocking": [], "files": hashes,
                                                      "records_sha256": "a" * 64}))
    committed = eval_root / "eval_reproducibility" / "split-v2.json"
    committed.parent.mkdir(parents=True)
    committed.write_text(json.dumps(split))
    reg = copy.deepcopy(REAL)
    by_id = {item["id"]: item for item in reg["datasets"]}
    by_id["split-v2"]["files"] = {"split-v2.json": sha256_bytes(committed.read_bytes())}
    by_id["split-v2"]["counts"] = {"dev": 1, "test": 1}
    by_id["ground-truth-v1.0"]["files"] = hashes
    by_id["ground-truth-v1.0"]["records_sha256"] = "a" * 64
    return SimpleNamespace(registry=reg, data_root=data_root, eval_root=eval_root, gt_dir=gt_dir)


@pytest.fixture
def eval_dir(monkeypatch, fixture_registry):
    monkeypatch.setattr(registry, "EVAL_DIR", fixture_registry.eval_root)
    return fixture_registry


def test_the_committed_registry_is_valid_and_pins_no_private_text():
    assert registry.validate_registry(REAL) == []
    for item in REAL["datasets"]:
        assert item["committed"] == (item["location"]["root"] == "eval")
    text = registry.REGISTRY_PATH.read_text()
    assert "Description" not in text and "Severity_Level" not in text


def test_committed_datasets_resolve_with_their_pinned_hashes():
    split = ground_truth.load_split_v2()
    assert (len(split["dev"]), len(split["test"])) == (25, 55)
    assert registry.resolve("dataset-manifest-v1").path.name == "dataset-manifest-v1.json"


def test_the_registry_is_append_only():
    later = copy.deepcopy(REAL)
    later["datasets"].append({**copy.deepcopy(REAL["datasets"][0]), "id": "dataset-manifest-v2"})
    assert registry.check_append_only(REAL, later) == []
    edited = copy.deepcopy(REAL)
    edited["datasets"][1]["counts"] = {"dev": 24, "test": 56}
    assert "edited or reordered" in registry.check_append_only(REAL, edited)[0]
    assert "removed" in registry.check_append_only(REAL, {"datasets": REAL["datasets"][:1]})[0]


@pytest.mark.parametrize(("change", "problem"), [
    ({"kind": "box_references", "location": {"root": "eval", "path": "x"}, "committed": True}, "private"),
    ({"committed": False}, "committed must be true"),
    ({"files": {"x.json": "not-a-hash"}}, "SHA-256"),
    ({"id": "Bad_Id"}, "id does not match"),
    ({"parent": "nope"}, "parent"),
])
def test_registry_rules(change, problem):
    bad = copy.deepcopy(REAL)
    bad["datasets"][0].update(change)
    assert any(problem in p for p in registry.validate_registry(bad))


def test_a_changed_or_missing_dataset_is_refused(eval_dir, tmp_path):
    reg = eval_dir.registry
    resolved = registry.resolve("ground-truth-v1.0", data_root_path=eval_dir.data_root, registry=reg)
    assert resolved.file("split-v2.json") == eval_dir.gt_dir / "split-v2.json"
    with pytest.raises(registry.DatasetMissingError, match="data root"):
        registry.resolve("ground-truth-v1.0", data_root_path=tmp_path / "elsewhere", registry=reg)
    (eval_dir.gt_dir / "ground_truth.json").write_text("{}")
    with pytest.raises(registry.DatasetError, match="changed since it was registered"):
        registry.resolve("ground-truth-v1.0", data_root_path=eval_dir.data_root, registry=reg)


def test_the_data_root_comes_from_the_flag_then_the_environment(monkeypatch, tmp_path):
    monkeypatch.setenv(registry.DATA_ROOT_ENV, str(tmp_path))
    assert registry.data_root() == tmp_path and registry.data_root("/x") == Path("/x")
    monkeypatch.delenv(registry.DATA_ROOT_ENV)
    assert registry.data_root() == registry.DEFAULT_DATA_ROOT


def test_ground_truth_is_read_by_v1s_loader_and_cross_checked(eval_dir):
    index = ground_truth.load_gt_v1(data_root=eval_dir.data_root, registry=eval_dir.registry)
    assert set(index["by_incident_id"]) == {"Assault001", "Burglary002"} and index["status"] == "frozen"
    reg = copy.deepcopy(eval_dir.registry)
    next(i for i in reg["datasets"] if i["id"] == "ground-truth-v1.0")["records_sha256"] = "b" * 64
    with pytest.raises(registry.DatasetError, match="records_sha256"):
        ground_truth.load_gt_v1(data_root=eval_dir.data_root, registry=reg)


def test_split_checks(eval_dir):
    split = ground_truth.load_split_v2(registry=eval_dir.registry)
    assert ground_truth.half(split, "test") == ["Burglary002"]
    assert ground_truth.split_problems({**split, "test": ["Assault001"]}) != []
    assert ground_truth.consistency_with_ground_truth(split, {**split, "excluded": ["Burglary002"], "test": []}) == []
    assert ground_truth.half(split, "test", gt_split={"excluded": ["Burglary002"]}) == []
    with pytest.raises(registry.DatasetError):
        ground_truth.half(split, "train")


def _config(datasets, half="dev"):
    return SimpleNamespace(split=SimpleNamespace(version="split-v2", half=half), datasets=datasets)


def test_the_config_loader_returns_clips_roles_and_manifest_inputs(eval_dir, monkeypatch):
    loaded = load_for_config(_config({"incident_gt": "ground-truth-v1.0"}), data_root=eval_dir.data_root,
                             registry=eval_dir.registry)
    assert loaded["clips"] == ["Assault001"] and set(loaded["incident_gt"]["by_incident_id"]) == {"Assault001",
                                                                                                  "Burglary002"}
    assert set(loaded["inputs"]) == {"split-v2/split-v2.json", "ground-truth-v1.0/ground_truth.json",
                                     "ground-truth-v1.0/split-v2.json"}
    with pytest.raises(registry.DatasetError, match="only split-v2"):
        load_for_config(SimpleNamespace(split=SimpleNamespace(version="split-v9", half="dev"), datasets={}),
                        registry=eval_dir.registry)


def test_the_config_loader_reads_reference_records(eval_dir):
    path = eval_dir.data_root / "references" / "box-references-v0" / "records.jsonl"
    record = new_record(record_id="box:Assault001:E1:3", dataset="box-references-v0", kind="box",
                        payload={"second": 3, "box": [100, 100, 400, 300]}, clip="Assault001",
                        generator={"prompt": "gen-box-select-v1", "model": "claude"})
    digest = write_records([record], path)
    reg = copy.deepcopy(eval_dir.registry)
    reg["datasets"].append({"id": "box-references-v0", "kind": "box_references", "description": "test",
                            "location": {"root": "data", "path": "references/box-references-v0"},
                            "files": {"records.jsonl": digest}, "verification": "tiered per record",
                            "committed": False, "created": "2026-11-01"})
    assert registry.validate_registry(reg) == []
    loaded = load_for_config(_config({"boxes": "box-references-v0"}), data_root=eval_dir.data_root, registry=reg)
    assert loaded["boxes"]["records"][0]["tier"] == "ai_proposed"


def test_append_validates_the_new_entry(tmp_path):
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(REAL))
    with pytest.raises(registry.DatasetError, match="duplicate id"):
        registry.append(copy.deepcopy(REAL["datasets"][0]), path)
    registry.append({**copy.deepcopy(REAL["datasets"][0]), "id": "dataset-manifest-v2"}, path)
    assert registry.load_registry(path)["datasets"][-1]["id"] == "dataset-manifest-v2"


REAL_DATA_ROOT = Path(os.environ.get(registry.DATA_ROOT_ENV) or registry.DEFAULT_DATA_ROOT)


@pytest.mark.skipif(not (REAL_DATA_ROOT / "ground_truth" / "ground-truth-v1.0").is_dir(),
                    reason="the frozen ground truth is private and not on this machine")
def test_the_real_frozen_ground_truth_matches_the_registry():
    index = ground_truth.load_gt_v1(data_root=REAL_DATA_ROOT)
    assert len(index["by_incident_id"]) == 80
    split = ground_truth.load_split_v2()
    assert ground_truth.consistency_with_ground_truth(split, index["split"]) == []
