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

"""How a reference run gets its generator and validator answers: files a Claude Code session writes, or a script.

A **pass** answers packets (:class:`Pass`). The reference runs (RA1, RA2, RB2) never call a model themselves:

- :class:`FilePass` is the real pass. For a packet it has no answer for, it writes the packet to
  ``<root>/packets/<packet id>.json`` and reports it pending; a Claude Code session reads it and writes
  ``<root>/answers/<packet id>.json``. The next run reads that answer. Every answer names the SHA-256 of the packet
  it answers, so an answer to a packet that has changed since (a re-run detector, a re-worded question) is refused,
  never reused. The generator and the validator get separate roots, and the validator's session must never open
  the generator's root: the packets it is given are blind (each run checks that with ``assert_blind``).
- :class:`ScriptedPass` answers in code. It exists for plumbing dry runs only: its identity names the model
  ``scripted-dry-run``, and records it made are never registered as references.

Both write only to private paths (:func:`evalv2.core.private.ensure_private`): packets quote ground-truth text.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from evalv2.core.hashing import canonical_json, sha256_json
from evalv2.core.private import ensure_private

DRY_RUN_MODEL = "scripted-dry-run"
_PACKET_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,159}$")


class PassError(ValueError):
    """An answer file for another version of its packet, or a malformed answer file."""


class Pass(Protocol):
    identity: Mapping[str, Any]

    def answer(self, packet_id: str, packet: Mapping[str, Any]) -> Any | None:
        """The answer to ``packet``, or ``None`` while it is pending."""
        ...


def packet_sha256(packet: Mapping[str, Any]) -> str:
    return sha256_json(dict(packet))


def _check_id(packet_id: str) -> None:
    if not _PACKET_ID.match(packet_id):
        raise PassError(f"packet id {packet_id!r} must match {_PACKET_ID.pattern}")


@dataclass
class FilePass:
    """Packets out, answers in, through a private directory a Claude Code session works in."""

    root: Path
    identity: Mapping[str, Any]
    written: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.root = Path(self.root)
        ensure_private(self.root)

    def answer(self, packet_id: str, packet: Mapping[str, Any]) -> Any | None:
        _check_id(packet_id)
        digest = packet_sha256(packet)
        answer_path = self.root / "answers" / f"{packet_id}.json"
        if answer_path.exists():
            data = json.loads(answer_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or "answer" not in data:
                raise PassError(f"{answer_path}: an answer file is {{\"packet_sha256\", \"answer\"}}")
            if data.get("packet_sha256") != digest:
                raise PassError(f"{answer_path} answers another version of packet {packet_id} "
                                f"({data.get('packet_sha256')!r}, now {digest})")
            return data["answer"]
        packet_path = self.root / "packets" / f"{packet_id}.json"
        body = canonical_json({"packet_id": packet_id, "packet_sha256": digest, "packet": dict(packet)})
        if not packet_path.exists() or packet_path.read_bytes() != body:
            packet_path.parent.mkdir(parents=True, exist_ok=True)
            packet_path.write_bytes(body)
            self.written.append(packet_id)
        return None


def write_answer(root: Path, packet_id: str, packet: Mapping[str, Any], answer: Any) -> Path:
    """Write the answer file a session would write (used by tools and tests; the session writes the same shape)."""
    _check_id(packet_id)
    ensure_private(root)
    path = Path(root) / "answers" / f"{packet_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json({"packet_id": packet_id, "packet_sha256": packet_sha256(packet),
                                     "answer": answer}))
    return path


@dataclass
class ScriptedPass:
    """Answers computed in code (plumbing dry runs only; never references)."""

    script: Callable[[str, Mapping[str, Any]], Any]
    identity: Mapping[str, Any]

    def __post_init__(self) -> None:
        if self.identity.get("model") != DRY_RUN_MODEL:
            raise PassError(f"a scripted pass must name the model {DRY_RUN_MODEL!r}, so its records can never pass "
                            "for references")

    def answer(self, packet_id: str, packet: Mapping[str, Any]) -> Any | None:
        _check_id(packet_id)
        return self.script(packet_id, packet)
