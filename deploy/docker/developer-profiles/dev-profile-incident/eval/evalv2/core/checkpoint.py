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

"""Resumable work: an append-only checkpoint of finished work items, keyed by idempotency key.

A work item's key is the SHA-256 of its full specification (task, subject, item, repeat, settings), so the same
item always has the same key and a changed setting never reuses an old result. An item is *final* once it has
a model output, valid or not, is unsupported, or failed in a way V1's ``recovery-v1`` never retries (an HTTP
error status, or a 2xx that is not a completion): a resumed run skips it and never repeats the call. Only an
infrastructure failure, a budget refusal or an attempt that was never sent stays open: recovery may retry it
later, and that attempt is recorded too.

Every outcome name a task records is classified explicitly (``FINAL_OUTCOMES``, ``OPEN_OUTCOMES``); an unknown
name is refused rather than guessed. V1's outcome labels (``classify_outcome``) fold a transport error, a
non-completion, a budget refusal and an unsent attempt into ``http_error``, so a task that records V1's label
passes the adapter's ``failure_kind`` too, and the failure kind decides.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evalv2.core.hashing import canonical_json, sha256_json
from evalv2.core.retry import FAILURE_KINDS, INFRASTRUCTURE_FAILURES

# Open: no model output for a reason outside the model, or the attempt was never sent. Recovery may retry these.
OPEN_OUTCOMES = INFRASTRUCTURE_FAILURES | frozenset({"budget_exceeded", "not_sent"})
FINAL_OUTCOMES = frozenset({
    "ok", "invalid_output", "unsupported",          # the generic names (``PredictionRecord.outcome``)
    "scored", "contract_invalid", "parse_failure",  # V1's labels for a model output (incident_objects, pipeline)
    "http_error", "invalid_response",               # not infrastructure: V1's recovery-v1 never retries them
})


def is_final_outcome(outcome: str, failure_kind: str | None = None) -> bool:
    """Whether a recorded outcome ends its work item; ``failure_kind`` (the adapter's ``Failure.kind``) decides.

    Raises ``CheckpointError`` for a name that is not classified here, so a new task outcome must be added to one
    of the two sets before a run can record it.
    """
    if outcome not in FINAL_OUTCOMES | OPEN_OUTCOMES:
        raise CheckpointError(f"unclassified outcome {outcome!r}: add it to FINAL_OUTCOMES or OPEN_OUTCOMES")
    if failure_kind is None:
        return outcome in FINAL_OUTCOMES
    if failure_kind not in FAILURE_KINDS:
        raise CheckpointError(f"unknown failure kind {failure_kind!r}")
    return failure_kind not in OPEN_OUTCOMES


def idempotency_key(spec: Mapping[str, Any]) -> str:
    return sha256_json(dict(spec))


class CheckpointError(RuntimeError):
    """A second final result for the same work item, or a malformed checkpoint."""


class Checkpoint:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._final: dict[str, dict[str, Any]] = {}
        self._attempts: dict[str, int] = {}
        if self.path.exists():
            for number, line in enumerate(self.path.read_text().splitlines(), start=1):
                if not line.strip():
                    continue
                try:
                    self._apply(json.loads(line))
                except (json.JSONDecodeError, KeyError) as exc:
                    raise CheckpointError(f"{self.path}:{number}: malformed checkpoint line") from exc

    def _apply(self, entry: dict[str, Any]) -> None:
        key = entry["key"]
        final = is_final_outcome(entry["outcome"], entry.get("failure_kind"))
        self._attempts[key] = self._attempts.get(key, 0) + 1
        if final:
            if key in self._final:
                raise CheckpointError(f"work item {key[:12]} already has a final result")
            self._final[key] = entry

    def is_final(self, key: str) -> bool:
        return key in self._final

    def final_result(self, key: str) -> dict[str, Any] | None:
        return self._final.get(key)

    def attempts(self, key: str) -> int:
        return self._attempts.get(key, 0)

    def record(self, key: str, outcome: str, *, ref: str | None = None, failure_kind: str | None = None,
               **fields: Any) -> None:
        """Append one result; a second final result for the same key, or an unclassified outcome, is refused.

        ``failure_kind`` is the adapter's ``Failure.kind`` when the attempt failed; pass it whenever ``outcome`` is
        V1's label, which folds open and final failures together into ``http_error``.
        """
        if is_final_outcome(outcome, failure_kind) and key in self._final:
            raise CheckpointError(f"work item {key[:12]} already has a final result")
        entry = {"key": key, "outcome": outcome, "ref": ref, "at": datetime.now(UTC).isoformat(), **fields}
        if failure_kind is not None:
            entry["failure_kind"] = failure_kind
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("ab") as handle:
            handle.write(canonical_json(entry) + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        self._apply(entry)

    def final_keys(self) -> set[str]:
        return set(self._final)
