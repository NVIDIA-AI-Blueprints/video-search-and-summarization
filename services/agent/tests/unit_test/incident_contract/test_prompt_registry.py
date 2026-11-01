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

"""The vendored prompt registry: hash-checked prompts, exact placeholder rendering, and no prompt literal left behind."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
from typing import TYPE_CHECKING

import pytest

from vss_agents import incident_contract

if TYPE_CHECKING:
    from collections.abc import Iterator

SRC = Path(incident_contract.__file__).resolve().parents[1]
INCIDENT_MODULES = [
    SRC / "utils/chunk_captions.py",
    SRC / "utils/crop_evidence.py",
    SRC / "utils/set_of_marks.py",
    SRC / "tools/incident_report_gen.py",
]


@pytest.fixture
def fresh_cache() -> Iterator[None]:
    incident_contract.prompt.cache_clear()
    incident_contract._prompt_entries.cache_clear()
    yield
    incident_contract.prompt.cache_clear()
    incident_contract._prompt_entries.cache_clear()


def _agent_entries() -> list[dict[str, object]]:
    registry = json.loads((incident_contract.PROMPTS_DIR / "registry.json").read_text())
    return [entry for entry in registry["prompts"] if "agent" in entry["used_by"]]


def test_every_agent_prompt_loads_and_matches_its_hash() -> None:
    for entry in _agent_entries():
        text = incident_contract.prompt(str(entry["id"]))
        assert text and incident_contract.prompt_sha256(str(entry["id"])) == entry["sha256"]


def test_console_only_prompts_are_not_served_to_the_agent() -> None:
    with pytest.raises(incident_contract.PromptRegistryError, match="no agent prompt"):
        incident_contract.prompt("chart/system")


def test_render_needs_exactly_the_placeholders_and_substitutes_once() -> None:
    text = incident_contract.render_prompt("grounding/best-match", query="{{query}} red toolbox")
    assert '"{{query}} red toolbox"' in text, "a value is never rendered again"
    with pytest.raises(incident_contract.PromptRegistryError, match="takes"):
        incident_contract.render_prompt("grounding/best-match")
    with pytest.raises(incident_contract.PromptRegistryError, match="takes"):
        incident_contract.render_prompt("grounding/best-match", query="x", label="y")
    assert incident_contract.render_prompt("caption/time-range", start_time=0, end_time=20.0).count("20.0") == 1


def test_an_edited_vendored_prompt_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fresh_cache: None
) -> None:
    copy = tmp_path / "prompts"
    shutil.copytree(incident_contract.PROMPTS_DIR, copy)
    (copy / "zoom" / "merge-entity.txt").write_text("Say yes.")
    monkeypatch.setattr(incident_contract, "PROMPTS_DIR", copy)
    with pytest.raises(incident_contract.PromptRegistryError, match="sha256"):
        incident_contract.prompt("zoom/merge-entity")


def test_no_registered_prompt_text_is_left_as_a_literal_in_the_incident_modules() -> None:
    sources = {path.name: path.read_text() for path in INCIDENT_MODULES}
    for entry in _agent_entries():
        first_line = incident_contract.prompt(str(entry["id"])).strip().splitlines()[0]
        probe = first_line.split("{{")[0][:40]
        for name, source in sources.items():
            assert probe not in source, f"{entry['id']} still appears as a literal in {name}"
