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

"""The append-only registry of prompts that generate or validate V2 references (``generation_prompts/``).

These prompts make references (box choices, chat questions and answers, dashboard requests); they are never sent
to a subject. Each entry pins its file's SHA-256, and every reference record names the prompt id that made it, so
a reference set can be traced to the exact text. A ``validator`` prompt is written for a blind pass that never
sees the generator's answer. Changing a prompt means a new entry, optionally naming the old one as ``parent``;
entries are never edited or removed (:func:`check_append_only`).
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any

from evalv2.core.hashing import sha256_bytes

REGISTRY_DIR = Path(__file__).with_name("generation_prompts")
REGISTRY_PATH = REGISTRY_DIR / "registry.json"
ID_PATTERN = re.compile(r"^gen-[a-z0-9]+(?:-[a-z0-9]+)*-v\d+$")
PURPOSES = ("box_reference", "chat_reference", "dashboard_reference", "matching_label")
ROLES = ("generator", "validator")
ENTRY_KEYS = ("id", "purpose", "role", "file", "sha256", "parent", "created", "notes")


class GenerationPromptError(ValueError):
    """The registry or a prompt file is inconsistent, edited, or not append-only."""


def load(path: Path = REGISTRY_PATH) -> dict[str, Any]:
    registry: dict[str, Any] = json.loads(Path(path).read_text(encoding="utf-8"))
    return registry


def validate(registry: dict[str, Any], directory: Path = REGISTRY_DIR) -> list[str]:
    """Every entry well formed, its file present with the pinned hash, ids unique, parents earlier."""
    problems: list[str] = []
    seen: dict[str, dict[str, Any]] = {}
    for number, item in enumerate(registry.get("prompts", []), start=1):
        label = item.get("id", f"entry {number}")
        if tuple(item) != ENTRY_KEYS:
            problems.append(f"{label}: keys must be {ENTRY_KEYS} in that order")
            continue
        if not ID_PATTERN.match(item["id"]):
            problems.append(f"{label}: id does not match {ID_PATTERN.pattern}")
        if item["id"] in seen:
            problems.append(f"{label}: duplicate id")
        if item["purpose"] not in PURPOSES or item["role"] not in ROLES:
            problems.append(f"{label}: purpose must be one of {PURPOSES} and role one of {ROLES}")
        parent = item["parent"]
        if parent is not None and (parent not in seen or seen[parent]["purpose"] != item["purpose"]):
            problems.append(f"{label}: parent {parent!r} is not an earlier entry with the same purpose")
        path = directory / item["file"]
        if not path.is_file():
            problems.append(f"{label}: file {item['file']} is missing")
        elif sha256_bytes(path.read_bytes()) != item["sha256"]:
            problems.append(f"{label}: {item['file']} changed since it was registered")
        seen[item["id"]] = item
    return problems


def check_append_only(earlier: dict[str, Any], later: dict[str, Any]) -> list[str]:
    old, new = earlier.get("prompts", []), later.get("prompts", [])
    if len(new) < len(old):
        return [f"{len(old) - len(new)} entries removed"]
    pairs = enumerate(zip(old, new, strict=False))
    return [f"entry {i + 1} ({a.get('id')}) was edited or reordered" for i, (a, b) in pairs if a != b]


def prompt_text(prompt_id: str, *, registry: dict[str, Any] | None = None, directory: Path = REGISTRY_DIR) -> str:
    """A prompt's text, only after its file matches the pinned hash."""
    for item in (registry or load())["prompts"]:
        if item["id"] == prompt_id:
            raw = (directory / item["file"]).read_bytes()
            if sha256_bytes(raw) != item["sha256"]:
                raise GenerationPromptError(f"{prompt_id}: {item['file']} changed since it was registered")
            return raw.decode("utf-8")
    raise GenerationPromptError(f"no generation prompt {prompt_id!r}")


def add(prompt_id: str, *, purpose: str, role: str, text: str, parent: str | None = None, notes: str = "",
        path: Path = REGISTRY_PATH, created: str | None = None) -> dict[str, Any]:
    """Write a new prompt file and append its entry; refuses an existing id or file and any invalid result."""
    registry = load(path)
    directory = Path(path).parent
    relative = f"{purpose}/{prompt_id}.txt"
    if (directory / relative).exists() or any(item["id"] == prompt_id for item in registry["prompts"]):
        raise GenerationPromptError(f"{prompt_id} already exists; a changed prompt is a new id")
    item = {"id": prompt_id, "purpose": purpose, "role": role, "file": relative,
            "sha256": sha256_bytes(text.encode("utf-8")), "parent": parent,
            "created": created or date.today().isoformat(), "notes": notes}
    updated = {**registry, "prompts": [*registry["prompts"], item]}
    (directory / relative).parent.mkdir(parents=True, exist_ok=True)
    (directory / relative).write_text(text, encoding="utf-8")
    problems = validate(updated, directory) + check_append_only(registry, updated)
    if problems:
        (directory / relative).unlink()
        raise GenerationPromptError("; ".join(problems))
    Path(path).write_text(json.dumps(updated, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return item
