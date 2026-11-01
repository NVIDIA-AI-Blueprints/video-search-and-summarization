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

"""The experiment prompt registry (PR 6A): immutable, append-only, and apart from the production contract."""

from __future__ import annotations

import copy
import json
import shutil
import subprocess
from pathlib import Path

import contract
import prompt_registry as pr
import pytest

SYNC = contract.CONTRACTS_DIR / "sync.py"


def test_the_committed_registry_is_valid():
    assert pr.validate(pr.load()) == []


def test_the_baseline_is_byte_identical_to_the_production_prompt():
    """The current contract's baseline (p1-v4-base on incident-contract-v4) is the production prompt, byte for byte."""
    base_id = pr.current_base_id()
    base = pr.entry(base_id)
    assert base["contract_version"] == contract.contract_version() and base["parent"] is None
    assert pr.prompt_text(base_id) == contract.extraction_prompt()
    assert base["sha256"] == pr.sha256_bytes(contract.EXTRACTION_PROMPT_PATH.read_bytes())


def test_the_v1_baseline_stays_registered():
    """BASE_ID is the V1 baseline that frozen V1 configs name; it stays, whatever the current contract."""
    base = pr.entry(pr.BASE_ID)
    assert base["contract_version"] == "incident-contract-v4"
    assert base["status"] == "baseline" and base["parent"] is None


def test_experiment_prompts_are_never_vendored_into_production():
    text = SYNC.read_text()
    assert "experiments" not in text
    vendored = text[text.index("VENDORED_FILES"):text.index("]", text.index("VENDORED_FILES"))]
    assert "registry" not in vendored and "p1/" not in vendored


@pytest.fixture
def registry_dir(tmp_path):
    target = tmp_path / "experiments"
    shutil.copytree(pr.REGISTRY_DIR, target)
    return target


def test_an_edited_prompt_file_is_detected_and_never_served(registry_dir):
    (registry_dir / "p1" / "p1-v4-base.txt").write_text("edited")
    registry = pr.load(registry_dir / "registry.json")
    assert any("immutable" in p for p in pr.validate(registry, registry_dir))
    with pytest.raises(pr.RegistryError, match="changed"):
        pr.prompt_text(pr.BASE_ID, registry=registry, directory=registry_dir)


def test_an_unregistered_prompt_file_is_a_problem(registry_dir):
    (registry_dir / "p1" / "p1-v4-e99-stray.txt").write_text("x")
    assert any("not in the registry" in p for p in pr.validate(pr.load(registry_dir / "registry.json"), registry_dir))


def test_add_appends_with_hash_and_parent(registry_dir):
    path = registry_dir / "registry.json"
    before = pr.load(path)
    new = pr.add("p1-v4-e99-test", parent=pr.BASE_ID, text="prompt text", hypothesis="Rules cut over-rating.",
                 path=path, created="2026-10-27")
    after = pr.load(path)
    assert pr.check_append_only(before, after) == [] and after["prompts"][-1] == new
    assert new["sha256"] == pr.sha256_bytes(b"prompt text") and new["status"] == "candidate"
    assert pr.validate(after, registry_dir) == []
    assert pr.lineage("p1-v4-e99-test", after) == ["p1-v4-e99-test", pr.BASE_ID]


@pytest.mark.parametrize("prompt_id, parent, hypothesis, match", [
    (pr.BASE_ID, pr.BASE_ID, "h", "already exists"),
    ("p1-v4-e02-x", "p1-v4-e77-missing", "h", "not in the prompt registry"),
    ("p1-v4-e03-x", pr.BASE_ID, " ", "hypothesis"),
    ("my-prompt", pr.BASE_ID, "h", "must look like"),
])
def test_add_refuses_anything_but_a_new_child(registry_dir, prompt_id, parent, hypothesis, match):
    with pytest.raises(pr.RegistryError, match=match):
        pr.add(prompt_id, parent=parent, text="t", hypothesis=hypothesis, path=registry_dir / "registry.json")


def test_edits_reorders_and_removals_break_append_only():
    earlier = {"prompts": [{"id": "a", "sha256": "1"}, {"id": "b", "sha256": "2"}]}
    edited = copy.deepcopy(earlier)
    edited["prompts"][1]["sha256"] = "3"
    assert pr.check_append_only(earlier, edited)
    assert pr.check_append_only(earlier, {"prompts": list(reversed(earlier["prompts"]))})
    assert pr.check_append_only(earlier, {"prompts": earlier["prompts"][:1]})
    assert pr.check_append_only(earlier, {"prompts": [*earlier["prompts"], {"id": "c"}]}) == []


def test_current_base_id_follows_the_contract_version():
    assert pr.current_base_id("incident-contract-v4") == "p1-v4-base"
    assert pr.current_base_id("incident-contract-v5") == "p1-v5-base"
    assert pr.current_base_id() == f"p1-v{contract.contract_version().rsplit('-v', 1)[1]}-base"
    with pytest.raises(pr.RegistryError):
        pr.current_base_id("chat-contract-v1")


def test_the_registry_only_appends_relative_to_main():
    """Against the registry on origin/main (when it exists there): this branch may only append."""
    relative = pr.REGISTRY_PATH.relative_to(Path(subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, cwd=pr.REGISTRY_DIR).stdout.strip()))
    shown = subprocess.run(["git", "show", f"origin/main:{relative}"], capture_output=True, text=True,
                           cwd=pr.REGISTRY_DIR)
    if shown.returncode != 0:
        pytest.skip("no registry on origin/main yet")
    assert pr.check_append_only(json.loads(shown.stdout), pr.load()) == []
