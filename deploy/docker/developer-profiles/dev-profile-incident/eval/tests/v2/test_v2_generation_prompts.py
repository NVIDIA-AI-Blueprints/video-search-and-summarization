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

"""The generation-prompt registry is append-only and hash-pinned."""

from __future__ import annotations

import json
import shutil

import pytest

from evalv2 import cli
from evalv2.datasets import generation_prompts as gp


@pytest.fixture
def registry_copy(tmp_path):
    directory = tmp_path / "generation_prompts"
    shutil.copytree(gp.REGISTRY_DIR, directory)
    return directory / "registry.json"


def test_the_committed_registry_is_valid():
    assert gp.validate(gp.load()) == []


def test_add_pins_the_text_and_refuses_reuse(registry_copy):
    item = gp.add("gen-box-select-v1", purpose="box_reference", role="generator", text="Pick the box.\n",
                  path=registry_copy, created="2026-11-01")
    registry = gp.load(registry_copy)
    assert gp.prompt_text("gen-box-select-v1", registry=registry, directory=registry_copy.parent) == "Pick the box.\n"
    assert item["file"] == "box_reference/gen-box-select-v1.txt"
    with pytest.raises(gp.GenerationPromptError, match="already exists"):
        gp.add("gen-box-select-v1", purpose="box_reference", role="generator", text="x", path=registry_copy)
    child = gp.add("gen-box-select-v2", purpose="box_reference", role="generator", text="Pick one box.\n",
                   parent="gen-box-select-v1", path=registry_copy)
    assert child["parent"] == "gen-box-select-v1"


def test_bad_entries_are_refused_without_leaving_a_file(registry_copy):
    with pytest.raises(gp.GenerationPromptError, match="parent"):
        gp.add("gen-chat-answer-v1", purpose="chat_reference", role="generator", text="x", parent="gen-nope-v1",
               path=registry_copy)
    assert not (registry_copy.parent / "chat_reference" / "gen-chat-answer-v1.txt").exists()
    with pytest.raises(gp.GenerationPromptError, match="id does not match"):
        gp.add("box-select", purpose="box_reference", role="generator", text="x", path=registry_copy)


def test_an_edited_prompt_file_is_detected(registry_copy):
    gp.add("gen-box-validate-v1", purpose="box_reference", role="validator", text="Check it.\n", path=registry_copy)
    (registry_copy.parent / "box_reference" / "gen-box-validate-v1.txt").write_text("Check it loosely.\n")
    registry = gp.load(registry_copy)
    assert any("changed" in p for p in gp.validate(registry, registry_copy.parent))
    with pytest.raises(gp.GenerationPromptError, match="changed"):
        gp.prompt_text("gen-box-validate-v1", registry=registry, directory=registry_copy.parent)


def test_the_registry_is_append_only(registry_copy):
    before = gp.load(registry_copy)
    gp.add("gen-box-select-v1", purpose="box_reference", role="generator", text="Pick.\n", path=registry_copy)
    after = gp.load(registry_copy)
    assert gp.check_append_only(before, after) == []
    edited = json.loads(json.dumps(after))
    edited["prompts"][0]["notes"] = "rewritten"
    assert gp.check_append_only(after, edited)


def test_datasets_cli_lists_and_verifies(capsys, tmp_path):
    assert cli.main(["datasets", "list"]) == 0
    listed = {row["id"] for row in json.loads(capsys.readouterr().out)}
    assert {"split-v2", "ground-truth-v1.0", "dataset-manifest-v1"} <= listed
    assert cli.main(["datasets", "verify", "--data-root", str(tmp_path)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["datasets"]["split-v2"] == "ok" and report["datasets"]["ground-truth-v1.0"] == "absent"
    assert cli.main(["datasets", "verify", "--data-root", str(tmp_path), "--id", "ground-truth-v1.0"]) == 1
    assert cli.main(["datasets", "verify", "--id", "no-such-dataset"]) == 1
