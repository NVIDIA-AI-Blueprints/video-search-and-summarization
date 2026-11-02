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
"""The pieces of a live judge stage: a judge's adapter, its own budget ledger and the ``ask`` factory.

A judge stage is separate and budgeted (methodology v2.0 §5). It reads stored predictions and asks one judge
through the cache (:func:`evalv2.tasks.judges.task_judges`), with that judge's hard caps on its own ``judge``
ledger. Nothing here runs by itself: a stage is started only with the owner's approval of its budget. Tasks cannot
build adapters (they see only ``adapters.base``), which is why this glue lives above them.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

from evalv2.adapters.base import CallContext, ModelAdapter
from evalv2.adapters.registry import build_adapter
from evalv2.core.artifacts import ArtifactStore
from evalv2.core.budget import BudgetLedger, Cap
from evalv2.core.retry import RetryPolicy
from evalv2.tasks.judges import LEDGER, JudgeSpec, LiveJudge


def judge_adapter(spec: JudgeSpec, **kwargs: Any) -> ModelAdapter:
    """The judge's OpenAI-compatible adapter: its endpoint names the base-URL and key variables (never stored)."""
    return build_adapter(spec, **kwargs)


def judge_ledger(spec: JudgeSpec, run_caps: Iterable[Cap] = (), log_path: Path | None = None) -> BudgetLedger:
    """One judge stage's ledger: the judge's hard ceilings, plus any (tighter) judge caps from the run config.

    One stage asks one judge, so the ceilings are that judge's alone; its log makes a resumed stage start from the
    attempts and tokens already charged.
    """
    caps = [*spec.caps(), *(cap for cap in run_caps if cap.ledger == LEDGER)]
    return BudgetLedger(caps, log_path)


def live_factory(*, store: ArtifactStore, ledger: BudgetLedger, policy: RetryPolicy, subject: str, task: str,
                 transport: Any = None, environ: Mapping[str, str] | None = None,
                 sleep: Callable[[float], None] = time.sleep) -> Callable[[JudgeSpec, str], LiveJudge]:
    """``ask(spec, kind)`` for :func:`evalv2.tasks.judges.task_judges`: live judges charged to ``subject`` and
    ``task`` on the stage's ledger (``transport`` and ``environ`` exist for tests)."""
    adapters: dict[str, ModelAdapter] = {}

    def ask(spec: JudgeSpec, kind: str) -> LiveJudge:
        if spec.id not in adapters:
            adapters[spec.id] = judge_adapter(spec, transport=transport, environ=environ)
        ctx = CallContext(store=store, ledger=ledger, policy=policy, subject=subject, task=task,
                          budget_ledger=LEDGER, tokens_bound=0, sleep=sleep)
        return LiveJudge(spec, kind, adapters[spec.id], ctx)

    return ask
