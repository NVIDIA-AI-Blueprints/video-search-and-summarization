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

"""Reference records and their tiers (methodology v2.0 §3).

A record is one reference item: a box for a ground-truth item at one second, a chat question with its answer, a
dashboard request with its spec. Its tier says how far it has been checked:

- ``ai_proposed``: generated, not checked;
- ``ai_validated``: every deterministic check passed and a separate blind validation pass agrees
  (:func:`promote_validated`);
- ``human_verified``: a person accepted, edited or rejected it (:func:`apply_human_decision`).

**Only an imported human decision makes a record ``human_verified``**; nothing an AI pass returns can, and a
reviewer id that names a model is refused. A rejected record stays ``human_verified`` with decision ``reject`` and
drops out of the metrics; an edited one counts with the person's payload (:func:`effective_payload`). Functions
return new records and never change their input, so an earlier version stays as it was.
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from evalv2.core.hashing import canonical_json, sha256_bytes, sha256_json
from evalv2.core.private import ensure_private

TIERS = ("ai_proposed", "ai_validated", "human_verified")
DECISIONS = ("accept", "edit", "reject")
FLAGS = ("ambiguous", "validators_disagree", "check_failed", "generator_unsure", "no_candidate")
RECORD_KEYS = frozenset({"record_id", "dataset", "kind", "clip", "strata", "payload", "tier", "provenance", "flags",
                         "human"})
_RECORD_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/@-]*$")
# A reviewer id naming a model or an AI pass is refused: tiers must never confuse AI agreement with human review.
_AI_REVIEWER = re.compile(r"^(ai|model|bot)[:/_-]|claude|gpt|gemini|nemotron|cosmos|llama|qwen|mistral|assistant",
                          re.IGNORECASE)


class RecordError(ValueError):
    """A malformed record, or a tier change the rules forbid."""


@dataclass(frozen=True)
class HumanDecision:
    """One person's decision on one record, as imported from a review workbook."""

    record_id: str
    decision: str
    reviewer: str
    decided_at: str
    payload_sha256: str
    source_sha256: str
    edited_payload: Any = None
    note: str = ""

    def problems(self) -> list[str]:
        out = []
        if self.decision not in DECISIONS:
            out.append(f"{self.record_id}: decision {self.decision!r} is not one of {DECISIONS}")
        if not self.reviewer.strip():
            out.append(f"{self.record_id}: no reviewer")
        elif is_ai_reviewer(self.reviewer):
            out.append(f"{self.record_id}: reviewer {self.reviewer!r} names a model; only people verify")
        if self.decision == "edit" and self.edited_payload is None:
            out.append(f"{self.record_id}: an edit needs the edited value")
        if self.decision != "edit" and self.edited_payload is not None:
            out.append(f"{self.record_id}: an edited value is given but the decision is {self.decision!r}")
        try:
            datetime.fromisoformat(self.decided_at)
        except ValueError:
            out.append(f"{self.record_id}: decided_at {self.decided_at!r} is not an ISO timestamp")
        return out

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def is_ai_reviewer(reviewer: str) -> bool:
    return bool(_AI_REVIEWER.search(reviewer.strip()))


def payload_sha256(record: Mapping[str, Any]) -> str:
    return sha256_json(record["payload"])


def new_record(*, record_id: str, dataset: str, kind: str, payload: Any, clip: str | None = None,
               strata: Mapping[str, Any] | None = None, generator: Mapping[str, Any],
               flags: Sequence[str] = ()) -> dict[str, Any]:
    """An ``ai_proposed`` record with its generator's provenance (prompt id, model, run)."""
    record = {"record_id": record_id, "dataset": dataset, "kind": kind, "clip": clip, "strata": dict(strata or {}),
              "payload": payload, "tier": "ai_proposed", "provenance": {"generator": dict(generator)},
              "flags": sorted(set(flags)), "human": None}
    problems = validate_record(record)
    if problems:
        raise RecordError("; ".join(problems))
    return record


def validate_record(record: Mapping[str, Any]) -> list[str]:
    """Problems with one record's shape and with its tier's evidence."""
    label = record.get("record_id", "?")
    missing, extra = RECORD_KEYS - set(record), set(record) - RECORD_KEYS
    if missing or extra:
        return [f"{label}: missing keys {sorted(missing)}, unknown keys {sorted(extra)}"]
    problems = []
    if not isinstance(record["record_id"], str) or not _RECORD_ID.match(record["record_id"]):
        problems.append(f"{label}: record_id must match {_RECORD_ID.pattern}")
    unknown_flags = set(record["flags"]) - set(FLAGS)
    if unknown_flags:
        problems.append(f"{label}: unknown flags {sorted(unknown_flags)}")
    provenance = record["provenance"] or {}
    if not provenance.get("generator"):
        problems.append(f"{label}: no generator provenance")
    tier, human = record["tier"], record["human"]
    if tier not in TIERS:
        problems.append(f"{label}: unknown tier {tier!r}")
    elif tier == "human_verified":
        if not human:
            problems.append(f"{label}: human_verified without a human decision")
        else:
            decision = HumanDecision(**human)
            problems += decision.problems()
            if decision.record_id != record["record_id"]:
                problems.append(f"{label}: the decision is for {decision.record_id}")
    else:
        if human:
            problems.append(f"{label}: a human decision on a record still marked {tier}")
        if tier == "ai_validated":
            checks = provenance.get("checks") or []
            validator = provenance.get("validator") or {}
            if not checks or not all(check.get("passed") is True for check in checks):
                problems.append(f"{label}: ai_validated needs every deterministic check to pass")
            if validator.get("agrees") is not True or not validator.get("id"):
                problems.append(f"{label}: ai_validated needs an agreeing blind validator with an id")
    return problems


def promote_validated(record: Mapping[str, Any], *, checks: Sequence[Mapping[str, Any]],
                      validator: Mapping[str, Any]) -> dict[str, Any]:
    """``ai_proposed`` to ``ai_validated`` when every check passes and the validator agrees; flagged otherwise.

    ``checks`` are ``{"name", "passed", "detail"?}``; ``validator`` is ``{"id", "agrees", "prompt"?, ...}``. The
    result is never ``human_verified``, whatever the inputs say.
    """
    if record["tier"] != "ai_proposed":
        raise RecordError(f"{record['record_id']}: only an ai_proposed record can be validated "
                          f"(it is {record['tier']})")
    out = copy.deepcopy(dict(record))
    out["provenance"] = {**out["provenance"], "checks": [dict(c) for c in checks], "validator": dict(validator)}
    flags = set(out["flags"])
    if not checks or not all(check.get("passed") is True for check in checks):
        flags.add("check_failed")
    if validator.get("agrees") is not True:
        flags.add("validators_disagree")
    out["flags"] = sorted(flags)
    if not flags & {"check_failed", "validators_disagree"}:
        out["tier"] = "ai_validated"
    return out


def apply_human_decision(record: Mapping[str, Any], decision: HumanDecision) -> dict[str, Any]:
    """The record ``human_verified`` with this person's decision; refuses a stale or invalid decision."""
    problems = decision.problems()
    if decision.record_id != record["record_id"]:
        problems.append(f"decision for {decision.record_id} applied to {record['record_id']}")
    if decision.payload_sha256 != payload_sha256(record):
        problems.append(f"{record['record_id']}: the decision was made on a different payload (stale review)")
    if record["tier"] == "human_verified":
        problems.append(f"{record['record_id']}: already human_verified; a second review is a new record version")
    if problems:
        raise RecordError("; ".join(problems))
    out = copy.deepcopy(dict(record))
    out["tier"], out["human"] = "human_verified", decision.to_json()
    return out


def effective_payload(record: Mapping[str, Any]) -> Any:
    """What a scorer compares against: the person's edit, ``None`` for a rejected record, else the payload."""
    human = record.get("human")
    if human and human["decision"] == "reject":
        return None
    if human and human["decision"] == "edit":
        return human["edited_payload"]
    return record["payload"]


def split_by_tier(records: Iterable[Mapping[str, Any]]) -> dict[str, list[Mapping[str, Any]]]:
    """Records grouped for reporting: ``primary`` (human accepted or edited), ``rejected``, and each AI tier."""
    out: dict[str, list[Mapping[str, Any]]] = {"primary": [], "rejected": [], "ai_validated": [], "ai_proposed": []}
    for record in records:
        if record["tier"] == "human_verified":
            out["rejected" if record["human"]["decision"] == "reject" else "primary"].append(record)
        else:
            out[record["tier"]].append(record)
    return out


def load_records(path: Path) -> list[dict[str, Any]]:
    """A JSONL reference file, every record validated and ids unique."""
    records = [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]
    problems = [p for record in records for p in validate_record(record)]
    ids = [record.get("record_id") for record in records]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        problems.append(f"duplicate record ids: {duplicates[:10]}")
    if problems:
        raise RecordError(f"{path}: " + "; ".join(problems[:20]))
    return records


def write_records(records: Sequence[Mapping[str, Any]], path: Path) -> str:
    """Write records as canonical JSONL to a private (gitignored) path; returns the file's SHA-256.

    A record carries reference text derived from the ground truth, so the path must be one Git ignores
    (:func:`evalv2.core.private.ensure_private`), and the hash is what the dataset registry pins.
    """
    problems = [p for record in records for p in validate_record(record)]
    if problems:
        raise RecordError("; ".join(problems[:20]))
    ensure_private(path)
    body = b"".join(canonical_json(dict(record)) + b"\n" for record in records)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(body)
    return sha256_bytes(body)
