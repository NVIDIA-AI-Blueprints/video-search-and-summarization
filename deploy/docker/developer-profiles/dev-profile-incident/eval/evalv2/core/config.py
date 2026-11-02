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

"""Run configuration (JSON, like V1's experiment configs), validated strictly.

One config can run one model on one task or several models on several tasks. The rules that protect the
methodology are enforced here, not left to the operator:

- a run on the **test** half must name the approved freeze manifest (methodology v2.0 §8);
- every task names subjects the config defines, and scorer ids carry a version (``incident.type@1``);
- the retry policy is one of V1's named policies, and every cap names a known ledger;
- a **pilot** (``purpose: "pilot"``) runs on the dev half only, never the held-out test half.

``budget_mode`` says what the caps are. ``worst_case`` (the default): every cap covers the preflight worst case, so a
run can never stop at a cap. ``hard_stop``: the caps are the owner's approved budget, which may sit below the
pessimistic worst case (every attempt at the token ceiling, every retry taken); preflight then lists the gap as
``hard_stop`` risks instead of problems, and the ledger refuses, before sending, any attempt that would pass a cap.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from evalv2.core.budget import LEDGERS, Cap
from evalv2.core.retry import POLICIES

CONFIG_VERSION = "evalv2-config-v1"
_ID = r"^[a-z0-9][a-z0-9_.-]{0,63}$"
_SCORER_ID = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+@[0-9A-Za-z.-]+$")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SubjectConfig(_Strict):
    id: str = Field(pattern=_ID)
    adapter: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    model: str = Field(min_length=1)
    endpoint: str | None = None
    request: dict[str, Any] = Field(default_factory=dict)
    profile: str | None = None


class TaskConfig(_Strict):
    subjects: list[str] | Literal["all"] = "all"
    scorers: list[str] = Field(default_factory=list)
    reuse: str | None = Field(default=None, description="Run id whose artifacts this task reuses (no new calls).")
    options: dict[str, Any] = Field(default_factory=dict)

    @field_validator("scorers")
    @classmethod
    def _versioned_scorers(cls, scorers: list[str]) -> list[str]:
        bad = [s for s in scorers if not _SCORER_ID.match(s)]
        if bad:
            raise ValueError(f"scorer ids must look like 'task.metric@version': {bad}")
        return scorers


class CapConfig(_Strict):
    ledger: Literal["inference", "caption", "grounding", "zoom", "object_tracking", "embedding", "judge",
                    "generation"]
    max_attempts: int | None = Field(default=None, ge=0)
    max_tokens: int | None = Field(default=None, ge=0)
    subject: str = "*"
    task: str = "*"

    def to_cap(self) -> Cap:
        return Cap(self.ledger, self.max_attempts, self.max_tokens, self.subject, self.task)


class SplitConfig(_Strict):
    version: str = "split-v2"
    half: Literal["dev", "test"]


class ItemSelector(_Strict):
    """One work item a recovery run re-sends: a subject's clip at one repeat."""

    subject: str = Field(pattern=_ID)
    item_id: str = Field(min_length=1)
    repeat: int = Field(ge=1, le=10)


class RecoveryConfig(_Strict):
    """A recovery run re-sends only named open items of an earlier run, under its own config, marker and --out.

    ``of_experiment`` and ``of_manifest`` name the earlier run (its sealed manifest ref); ``items`` are the open
    items to try again. The earlier run's final items are never re-sent, so the two runs together still hold one
    outcome per work item.
    """

    of_experiment: str = Field(pattern=_ID)
    of_manifest: str = Field(min_length=1)
    items: list[ItemSelector] = Field(min_length=1)


class RunConfig(_Strict):

    config_version: Literal["evalv2-config-v1"] = CONFIG_VERSION
    experiment: str = Field(pattern=_ID)
    split: SplitConfig
    datasets: dict[str, str] = Field(default_factory=dict)
    subjects: list[SubjectConfig]
    tasks: dict[str, TaskConfig]
    repeats: int = Field(default=1, ge=1, le=10)
    seed: int
    retry_policy: str = "retry-v2"
    caps: list[CapConfig] = Field(default_factory=list)
    freeze_manifest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    purpose: Literal["pilot", "final"] | None = None
    budget_mode: Literal["worst_case", "hard_stop"] = "worst_case"
    recovery: RecoveryConfig | None = None
    notes: str = ""

    @field_validator("retry_policy")
    @classmethod
    def _known_policy(cls, value: str) -> str:
        if value not in POLICIES:
            raise ValueError(f"unknown retry policy {value!r}; expected one of {sorted(POLICIES)}")
        return value

    @model_validator(mode="after")
    def _consistent(self) -> RunConfig:
        ids = [s.id for s in self.subjects]
        if len(ids) != len(set(ids)):
            raise ValueError(f"duplicate subject ids: {ids}")
        if not self.tasks:
            raise ValueError("a run needs at least one task")
        for name, task in self.tasks.items():
            if not re.match(_ID, name):
                raise ValueError(f"invalid task name {name!r}")
            if task.subjects != "all":
                unknown = sorted(set(task.subjects) - set(ids))
                if unknown:
                    raise ValueError(f"task {name!r} names undefined subjects {unknown}")
        if self.split.half == "test" and self.freeze_manifest is None:
            raise ValueError("a run on the test half must name the approved freeze manifest (freeze_manifest)")
        if self.purpose == "pilot" and self.split.half != "dev":
            raise ValueError("a pilot runs on the dev half only: the test half is held out for the final runs")
        if self.recovery is not None:
            keys = [(i.subject, i.item_id, i.repeat) for i in self.recovery.items]
            if len(keys) != len(set(keys)):
                raise ValueError("recovery.items names an item twice")
            unknown = sorted({i.subject for i in self.recovery.items} - set(ids))
            if unknown:
                raise ValueError(f"recovery.items names undefined subjects {unknown}")
            if any(i.repeat > self.repeats for i in self.recovery.items):
                raise ValueError("recovery.items names a repeat above the config's repeats")
        return self

    def task_subjects(self, task: str) -> list[SubjectConfig]:
        chosen = self.tasks[task].subjects
        return list(self.subjects) if chosen == "all" else [s for s in self.subjects if s.id in chosen]

    def budget_caps(self) -> list[Cap]:
        return [cap.to_cap() for cap in self.caps]


if set(get_args(CapConfig.model_fields["ledger"].annotation)) != set(LEDGERS):  # keep the two lists in step
    raise RuntimeError("CapConfig.ledger and budget.LEDGERS disagree")


def load_config(path: Path) -> RunConfig:
    return RunConfig.model_validate(json.loads(Path(path).read_text()))
