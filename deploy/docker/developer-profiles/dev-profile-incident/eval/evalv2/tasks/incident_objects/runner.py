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

"""Track A runner for incident fields and objects: P1 alone on each subject, its output stored for offline scoring.

``plan`` makes one work item per clip of the configured half, subject and repeat. ``execute`` sends the P1 request
(:mod:`evalv2.requests.p1`, pinned to V1's body) through the subject's adapter, validates the reply with V1's
contract policy, and stores the raw reply, the policy result and the prediction (the report when it is
core-valid, else V1's empty prediction) as one artifact. Executing is a live model call: it is approval-gated
and needs the run's video-URL resolver; nothing here runs on its own.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Mapping
from typing import Any

from evalv2.adapters.base import CallContext, ModelAdapter
from evalv2.requests.p1 import build_p1_request, p1_provenance
from evalv2.tasks.base import PredictionRecord, WorkItem
from evalv2.tasks.incident_objects import v1

TASK = "incident_objects"
PREDICTION_KIND = "incident-objects-prediction"
LEDGER = "inference"
TOKENS_PER_ATTEMPT = v1.P1_TOKENS_CEILING  # what preflight assumes per attempt, and what every attempt reserves


class IncidentObjectsTask:
    task = TASK
    requires = frozenset({"video_input", "json_schema"})

    def __init__(self, *, video_url: Callable[[str], str] | None = None,
                 video_duration: Callable[[str], float | None] | None = None) -> None:
        self.video_url = video_url
        self.video_duration = video_duration or (lambda clip: None)

    def plan(self, config: Any, datasets: Mapping[str, Any]) -> list[WorkItem]:
        clips = datasets.get("clips")
        if clips is None:
            raise ValueError(f"{TASK} needs the split's clips (datasets['clips'] from the dataset loader)")
        prompt_id = config.tasks[TASK].options.get("prompt_id")
        return [WorkItem(TASK, subject.id, clip, repeat, {"prompt_id": prompt_id, "model": subject.model})
                for subject in config.task_subjects(TASK) for clip in clips
                for repeat in range(1, config.repeats + 1)]

    def budget_estimate(self, config: Any, items: list[WorkItem]) -> dict[str, dict[str, int]]:
        """One P1 attempt per item at V1's per-call token ceiling (retries are added by preflight)."""
        return {LEDGER: {"attempts": len(items), "tokens": len(items) * TOKENS_PER_ATTEMPT}}

    def execute(self, item: WorkItem, adapter: ModelAdapter, ctx: CallContext) -> PredictionRecord:
        if self.video_url is None:
            raise RuntimeError(f"{TASK}: no video-URL resolver; executing is a live, approval-gated run")
        prompt_id = item.spec.get("prompt_id")
        request = build_p1_request(adapter.model, self.video_url(item.item_id), prompt_id=prompt_id)
        provenance = {"stages": {"p1": {**p1_provenance(prompt_id), "model": adapter.model, "adapter": adapter.id}}}
        # Every attempt reserves the bound preflight assumed, on the ledger it checked, whatever the caller's context.
        result = adapter.complete(request, dataclasses.replace(ctx, budget_ledger=LEDGER,
                                                               tokens_bound=TOKENS_PER_ATTEMPT))
        if not result.ok:
            failure = result.failure
            p1_raw = {"ok": False, "failure_kind": failure.kind if failure else None,
                      "status_code": failure.http_status if failure else None,
                      "error": failure.detail if failure else ""}
            return PredictionRecord(item.key, TASK, item.subject, item.item_id, item.repeat,
                                    outcome=v1.outcome(p1_raw, {}), failure=result.to_dict().get("failure"),
                                    usage=result.usage.to_dict(), timing=result.to_dict().get("timing"),
                                    provenance=provenance)
        policy = v1.evaluate_policy(result.text, self.video_duration(item.item_id))
        prediction = policy["report"] if policy["core_ok"] else v1.empty_prediction()
        validation = {"core_violations": policy["core"], "enrichment_violations": policy["enrichment"],
                      "full_contract_ok": policy["full_contract_ok"], "core_ok": policy["core_ok"]}
        ref = ctx.store.put(PREDICTION_KIND, {"raw": result.text, "finish_reason": result.finish_reason,
                                              "validation": validation, "prediction": prediction})
        return PredictionRecord(item.key, TASK, item.subject, item.item_id, item.repeat,
                                outcome=v1.outcome({"ok": True, "core_ok": policy["core_ok"]}, validation),
                                prediction_ref=str(ref), usage=result.usage.to_dict(),
                                timing=result.to_dict().get("timing"), provenance=provenance)
