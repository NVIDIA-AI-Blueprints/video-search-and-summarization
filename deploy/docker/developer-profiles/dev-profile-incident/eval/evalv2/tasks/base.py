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

"""What every evaluation dimension implements: a task runner and scorers.

A **task runner** turns a config into work items (``plan``, pure, which drives preflight and dry runs) and
executes one item against one subject (``execute``), writing immutable artifacts. A **scorer** reads artifacts
and a ground-truth record and returns a score; it never calls the network (scoring runs under
``core.netguard.no_network``), and a missing cached judge verdict or embedding leaves a field *unscored*,
never 0. A subject without the capabilities a task requires is reported as **unsupported**.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from evalv2.core.checkpoint import idempotency_key


@dataclass(frozen=True)
class WorkItem:
    task: str
    subject: str
    item_id: str
    repeat: int
    spec: Mapping[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return idempotency_key({"task": self.task, "subject": self.subject, "item_id": self.item_id,
                                "repeat": self.repeat, "spec": dict(self.spec)})


@dataclass(frozen=True)
class PredictionRecord:
    work_key: str
    task: str
    subject: str
    item_id: str
    repeat: int
    outcome: str  # ok | invalid_output | unsupported | an infrastructure failure kind
    prediction_ref: str | None = None
    failure: Mapping[str, Any] | None = None
    usage: Mapping[str, Any] | None = None
    timing: Mapping[str, Any] | None = None
    provenance: Mapping[str, Any] = field(default_factory=dict)  # model and prompt hash of every stage

    @property
    def failure_kind(self) -> str | None:
        """The adapter's failure kind. A checkpoint needs it beside ``outcome``, which may be V1's lossy label."""
        return None if self.failure is None else self.failure.get("kind")


@dataclass(frozen=True)
class ScoreRecord:
    scorer_id: str
    scorer_version: str
    work_key: str
    values: Mapping[str, Any]
    unscored: tuple[str, ...] = ()


@runtime_checkable
class TaskRunner(Protocol):
    task: str
    requires: frozenset[str]

    def plan(self, config: Any, dataset: Any) -> list[WorkItem]: ...

    def execute(self, item: WorkItem, subject: Any, ctx: Any) -> PredictionRecord: ...


@runtime_checkable
class Scorer(Protocol):
    id: str
    version: str
    reads: frozenset[str]

    def score(self, prediction: PredictionRecord, ground_truth: Mapping[str, Any], ctx: Any) -> ScoreRecord: ...


TASKS: dict[str, Callable[[], TaskRunner]] = {}


def register_task(name: str) -> Callable[[Callable[[], TaskRunner]], Callable[[], TaskRunner]]:
    def decorator(factory: Callable[[], TaskRunner]) -> Callable[[], TaskRunner]:
        if name in TASKS:
            raise ValueError(f"task {name!r} is already registered")
        TASKS[name] = factory
        return factory
    return decorator
