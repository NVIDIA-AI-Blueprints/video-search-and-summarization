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

"""The append-only experiment prompt registry (``contracts/experiments/``, PR 6A).

Experiment prompts never change the production contract: ``contracts/incident_extraction_prompt.txt``,
``contracts/VERSION`` and the files ``sync.py`` vendors stay as they are, and nothing here is
vendored. Every registry entry is immutable:

- ``validate`` checks each entry's file against its SHA-256, unique ids, that a parent exists
  and comes earlier, that the contract version is the current one or an older baseline, and
  that the baseline for the current contract is byte-identical to the production prompt;
- ``check_append_only`` compares a registry with an earlier copy of itself (e.g. from main):
  entries may only be appended, never edited, reordered or removed;
- ``prompt_text`` returns a prompt only after its hash matches, so a run can never use an
  edited file under an old id;
- ``add`` appends a new entry (new file, new id, existing parent) and refuses everything else.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from pathlib import Path

import contract

REGISTRY_DIR = contract.CONTRACTS_DIR / "experiments"
REGISTRY_PATH = REGISTRY_DIR / "registry.json"
BASE_ID = "p1-v4-base"  # the V1 baseline; frozen V1 code (Stages 0-3, e2e profile) compares against it
STATUSES = ("baseline", "candidate")  # selection is a decisions.md record, never an edit here
ID_PATTERN = re.compile(r"^p1-v\d+-(base|e\d{2}[a-z0-9-]*)$")
ENTRY_KEYS = ("id", "parent", "file", "sha256", "contract_version", "guidelines", "hypothesis", "created", "status")


class RegistryError(ValueError):
    """The registry or a prompt file is inconsistent, edited, or not append-only."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load(path: Path | None = None) -> dict:
    return json.loads((path or REGISTRY_PATH).read_text())


def current_base_id(contract_version: str | None = None) -> str:
    """The registry id of the production baseline for a contract version (``incident-contract-v4`` -> ``p1-v4-base``).

    ``BASE_ID`` stays the V1 baseline that frozen V1 code compares against; new code that means "the production
    prompt of the current contract" uses this instead, so a contract bump does not need an edit.
    """
    version = contract_version or contract.contract_version()
    match = re.fullmatch(r"incident-contract-v(\d+)", version)
    if not match:
        raise RegistryError(f"unrecognised contract version {version!r}")
    return f"p1-v{match.group(1)}-base"


def validate(registry: dict, directory: Path = REGISTRY_DIR, *, production_prompt: Path = contract.EXTRACTION_PROMPT_PATH,
             current_contract: str | None = None) -> list[str]:
    problems = []
    current_contract = current_contract or contract.contract_version()
    seen: set[str] = set()
    for index, entry in enumerate(registry.get("prompts", [])):
        where = f"entry {index} ({entry.get('id')})"
        if tuple(sorted(entry)) != tuple(sorted(ENTRY_KEYS)):
            problems.append(f"{where}: keys must be exactly {ENTRY_KEYS}")
            continue
        if not ID_PATTERN.match(entry["id"]):
            problems.append(f"{where}: id does not match {ID_PATTERN.pattern}")
        if entry["id"] in seen:
            problems.append(f"{where}: duplicate id")
        if entry["parent"] is None and entry["status"] != "baseline":
            problems.append(f"{where}: only a baseline may have no parent")
        if entry["parent"] is not None and entry["parent"] not in seen:
            problems.append(f"{where}: parent {entry['parent']!r} is not an earlier entry")
        if entry["status"] not in STATUSES:
            problems.append(f"{where}: status {entry['status']!r} not in {STATUSES}")
        if entry["file"] != f"p1/{entry['id']}.txt":
            problems.append(f"{where}: file must be p1/{entry['id']}.txt")
        path = directory / entry["file"]
        if not path.exists():
            problems.append(f"{where}: {entry['file']} is missing")
        elif sha256_bytes(path.read_bytes()) != entry["sha256"]:
            problems.append(f"{where}: {entry['file']} does not match its recorded SHA-256 (prompts are immutable)")
        if entry["contract_version"] == current_contract and entry["status"] == "baseline" \
                and entry["sha256"] != sha256_bytes(production_prompt.read_bytes()):
            problems.append(f"{where}: the {current_contract} baseline must be byte-identical to the production prompt")
        seen.add(entry["id"])
    if BASE_ID not in seen:
        problems.append(f"no {BASE_ID} entry")
    listed = {e.get("file") for e in registry.get("prompts", [])}
    for path in sorted((directory / "p1").glob("*.txt")):
        if f"p1/{path.name}" not in listed:
            problems.append(f"p1/{path.name} is not in the registry")
    return problems


def check_append_only(earlier: dict, later: dict) -> list[str]:
    """Problems if ``later`` edits, reorders or removes any entry of ``earlier``."""
    old, new = earlier.get("prompts", []), later.get("prompts", [])
    problems = []
    if len(new) < len(old):
        problems.append(f"{len(old) - len(new)} entries removed")
    for index, entry in enumerate(old):
        if index >= len(new):
            break
        if new[index] != entry:
            problems.append(f"entry {index} ({entry.get('id')}) was changed or moved")
    return problems


def entry(prompt_id: str, registry: dict | None = None) -> dict:
    for item in (registry or load())["prompts"]:
        if item["id"] == prompt_id:
            return item
    raise RegistryError(f"{prompt_id!r} is not in the prompt registry")


def prompt_text(prompt_id: str, *, registry: dict | None = None, directory: Path | None = None) -> str:
    """The prompt's exact text, only if the file still matches its recorded SHA-256."""
    item = entry(prompt_id, registry)
    data = ((directory or REGISTRY_DIR) / item["file"]).read_bytes()
    if sha256_bytes(data) != item["sha256"]:
        raise RegistryError(f"{item['file']} changed after it was registered")
    return data.decode()


def lineage(prompt_id: str, registry: dict | None = None) -> list[str]:
    """``[prompt_id, parent, grandparent, ..., base]``."""
    registry = registry or load()
    chain = [prompt_id]
    while (parent := entry(chain[-1], registry)["parent"]) is not None:
        chain.append(parent)
    return chain


def add(prompt_id: str, *, parent: str, text: str, hypothesis: str, path: Path | None = None,
        created: str | None = None) -> dict:
    """Append one prompt. Refuses an existing id or file, an unknown parent, or an empty hypothesis."""
    path = path or REGISTRY_PATH
    registry = load(path)
    directory = path.parent
    if any(e["id"] == prompt_id for e in registry["prompts"]):
        raise RegistryError(f"{prompt_id!r} already exists; a changed prompt needs a new id")
    if not ID_PATTERN.match(prompt_id) or prompt_id.endswith("-base"):
        raise RegistryError(f"{prompt_id!r} must look like p1-v4-e01-<topic>")
    entry(parent, registry)
    if not hypothesis.strip():
        raise RegistryError("every experiment prompt needs a written hypothesis")
    target = directory / "p1" / f"{prompt_id}.txt"
    if target.exists():
        raise RegistryError(f"{target.name} already exists")
    target.write_bytes(text.encode())
    new = {"id": prompt_id, "parent": parent, "file": f"p1/{prompt_id}.txt", "sha256": sha256_bytes(text.encode()),
           "contract_version": contract.contract_version(), "guidelines": "annotation-guidelines-v1.0",
           "hypothesis": hypothesis.strip(), "created": created or date.today().isoformat(), "status": "candidate"}
    registry["prompts"].append(new)
    path.write_text(json.dumps(registry, indent=2) + "\n")
    return new
