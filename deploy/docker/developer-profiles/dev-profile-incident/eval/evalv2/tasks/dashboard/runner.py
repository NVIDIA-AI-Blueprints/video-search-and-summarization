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

"""C1c runner for "Ask for a chart": each dashboard reference request sent as the console sends it, the reply stored.

``plan`` makes one work item per reference record (RB2, ``datasets["dashboard_references"]``), subject and repeat;
a record a reviewer rejected is left out. ``execute`` builds the console's chart request
(:mod:`evalv2.requests.chart`, byte-identical to the golden), sends it through the subject's adapter on the
``inference`` ledger, and stores the raw reply, the console's parse of it (:func:`parse_reply`) and how it reads
(``reply_kind``) as one artifact. It decides nothing about right or wrong: scoring
(:mod:`evalv2.tasks.dashboard.report`) compares the stored reply with the reference later, offline.

Executing is a live model call: it is approval-gated by the executor, and nothing here runs on its own.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from typing import Any

from evalv2.core.checkpoint import FINAL_OUTCOMES, OPEN_OUTCOMES
from evalv2.datasets.records import effective_payload
from evalv2.references.dashboard import is_refusal, load_allowlist, parse_spec
from evalv2.requests.chart import (
    CONSOLE_TIMEOUT_S,
    DEFAULT_SHAPE,
    REQUEST_SHAPES,
    build_chart_request,
    chart_provenance,
    parse_reply,
    request_text,
)
from evalv2.tasks.base import PredictionRecord, WorkItem

TASK = "dashboard"
DATASET_ROLE = "dashboard_references"
PREDICTION_KIND = "dashboard-prediction"
LEDGER = "inference"
# Per attempt: the prompt (about 1,000 tokens: 3,883 characters of system prompt, 986 WordPiece tokens, plus a
# request of at most 24) and the console's max_tokens of 200, with headroom for another tokenizer and the chat
# template. What preflight assumes and what every attempt reserves until its usage is measured.
TOKENS_PER_ATTEMPT = 1_500
REPLY_KINDS = ("spec", "refusal", "invalid_object", "no_json", "empty")


def reply_kind(text: str | None, reply: Any, allowlist: Mapping[str, Any]) -> str:
    """How the console reads a reply: a spec, a refusal, an object that is neither, no JSON object, or no text."""
    if not text:
        return "empty"
    if reply is None:
        return "no_json"
    if is_refusal(reply):
        return "refusal"
    return "spec" if parse_spec(reply, allowlist) is not None else "invalid_object"


def references(datasets: Mapping[str, Any]) -> list[dict[str, Any]]:
    block = datasets.get(DATASET_ROLE)
    if block is None:
        raise ValueError(f"{TASK} needs datasets['{DATASET_ROLE}'] (a dashboard_references dataset, RB2)")
    return [dict(record) for record in block["records"]]


class DashboardTask:
    task = TASK
    requires = frozenset({"text_input"})

    def __init__(self, *, allowlist: Mapping[str, Any] | None = None) -> None:
        self._allowlist = dict(allowlist) if allowlist is not None else None

    @property
    def allowlist(self) -> dict[str, Any]:
        if self._allowlist is None:
            self._allowlist = load_allowlist()
        return self._allowlist

    def plan(self, config: Any, datasets: Mapping[str, Any]) -> list[WorkItem]:
        prompt_sha = chart_provenance()["prompt_sha256"]
        shape = config.tasks[TASK].options.get("request_shape", DEFAULT_SHAPE)
        if shape not in REQUEST_SHAPES:
            raise ValueError(f"tasks.{TASK}.options.request_shape {shape!r} is not one of {sorted(REQUEST_SHAPES)}")
        extra = {} if shape == DEFAULT_SHAPE else {"request_shape": shape}  # the sealed pilot's plan keys stay as they were
        items = []
        for record in sorted(references(datasets), key=lambda r: r["record_id"]):
            payload = effective_payload(record)
            if payload is None:  # rejected by a reviewer: no reference to score against
                continue
            request_text(payload["request"])  # a request the console would refuse is refused here, before any call
            for subject in config.task_subjects(TASK):
                for repeat in range(1, config.repeats + 1):
                    items.append(WorkItem(TASK, subject.id, record["record_id"], repeat,
                                          {"model": subject.model, "prompt_sha256": prompt_sha,
                                           "request": payload["request"], **extra}))
        return items

    def budget_estimate(self, config: Any, items: list[WorkItem]) -> dict[str, dict[str, int]]:
        """One chart call per item at the per-attempt bound (retries are added by preflight)."""
        return {LEDGER: {"attempts": len(items), "tokens": len(items) * TOKENS_PER_ATTEMPT}}

    def execute(self, item: WorkItem, adapter: Any, ctx: Any) -> PredictionRecord:
        """``adapter`` is a ``ModelAdapter`` and ``ctx`` a ``CallContext`` (untyped here: this package imports no
        adapter module, so its scoring stays offline by construction, ``tests/v2/test_v2_guards.py``)."""
        shape = str(item.spec.get("request_shape", DEFAULT_SHAPE))
        request = build_chart_request(adapter.model, str(item.spec["request"]), shape)
        provenance = {"stages": {"chart": {**chart_provenance(shape), "model": adapter.model, "adapter": adapter.id}}}
        # Every attempt reserves the bound preflight assumed, on the ledger it checked, whatever the caller's context.
        result = adapter.complete(request, dataclasses.replace(ctx, budget_ledger=LEDGER,
                                                               tokens_bound=TOKENS_PER_ATTEMPT))
        timing = result.to_dict().get("timing")
        if not result.ok:
            kind = result.failure.kind if result.failure else "http_error"
            outcome = kind if kind in FINAL_OUTCOMES | OPEN_OUTCOMES else "http_error"
            return PredictionRecord(item.key, TASK, item.subject, item.item_id, item.repeat, outcome=outcome,
                                    failure=result.to_dict().get("failure"), usage=result.usage.to_dict(),
                                    timing=timing, provenance=provenance)
        reply = parse_reply(result.text)
        kind = reply_kind(result.text, reply, self.allowlist)
        wall_ms = (timing or {}).get("wall_ms")
        ref = ctx.store.put(PREDICTION_KIND, {
            "raw": result.text, "finish_reason": result.finish_reason, "reply": reply, "reply_kind": kind,
            "over_console_timeout": wall_ms is not None and wall_ms > CONSOLE_TIMEOUT_S * 1000})
        # An empty or unparseable reply is a model output that failed (final, never retried); scoring counts it.
        outcome = "invalid_output" if kind in ("empty", "no_json") else "ok"
        return PredictionRecord(item.key, TASK, item.subject, item.item_id, item.repeat, outcome=outcome,
                                prediction_ref=str(ref), usage=result.usage.to_dict(), timing=timing,
                                provenance=provenance)
