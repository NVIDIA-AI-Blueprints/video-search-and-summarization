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

"""Track B runner: the production pipeline on the local experiment agent, one analysis per clip, subject and repeat.

``plan`` makes one work item per clip of the configured half, pipeline subject (``adapter: vss``) and repeat;
``budget_estimate`` is each profile's worst case, the agent's own retries included (``retries_included``: the
adapter never re-sends an analysis). ``execute`` sends one experimental ``/analyze`` request
(:mod:`evalv2.requests.vss`) through the subject's pipeline adapter and stores one artifact holding three
predictions of the same run, so the scorers can separate the effects (methodology v2.0 section 2):

- ``p1``: the raw P1 reply through V1's contract policy, exactly as Track A treats a reply. Against the same
  model's Track A predictions this is the caption effect once the experimental path runs the caption pass
  (blocker X16); until then it measures that the agent's P1 request and Track A's give the same results. The agent
  retries a runaway P1 output once and returns only the last reply, which Track A (never retrying a model output)
  would have scored as a miss: the artifact keeps the agent's ``p1_attempts`` and ``p1_retried``, and the
  caption-effect pairs leave retried runs out (:func:`evalv2.tasks.pipeline.scoring.caption_effect`).
- ``p1_agent``: the agent's validated P1 report before the zoomed second look: ``experiment.p1_report`` (P4), or
  without it the final report with every zoom correction undone (``name_before``, ``description_before``).
  Where both exist they must agree (``zoom_undo_consistent``).
- ``final``: the final report. ``final`` minus ``p1_agent`` is the zoom effect, with no extra calls.

The artifact also keeps the evidence entries (the crop scorers' input), the agent's experiment summaries (crop and
zoom calls against their caps), the object tracker's summary (the reply's top-level ``object_tracking``, whose
``detector`` is the tracker's call budget; ``experiment.object_tracking`` is only the request's setting), whether a
caption pass ran (with the run's ``chunking.enabled`` and the agent's caption skip reason), and whether V1's policy agreed with the agent's own validation. Executing is a live,
approval-gated run that needs the run's video-URL resolver.
"""

from __future__ import annotations

import copy
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from evalv2.adapters.base import CallContext, PipelineAdapter
from evalv2.adapters.vss import per_run_flags
from evalv2.core.retry import classify_http_status
from evalv2.requests.vss import VssProfile, build_analyze_request, profile_for
from evalv2.tasks.base import PredictionRecord, WorkItem
from evalv2.tasks.incident_objects import v1

TASK = "pipeline"
PREDICTION_KIND = "pipeline-prediction"
STAGES = ("p1", "p1_agent", "final")
PIPELINE_ADAPTER = "vss"
_REPORT_ITEMS = {"entity": ("entities", "entity_id"), "instrument": ("instruments", "instrument_id"),
                 "asset": ("assets", "asset_id")}


def p1_retried(attempts: Sequence[Mapping[str, Any]]) -> bool:
    """Whether the agent re-sent P1 after a rejected (runaway) output: its returned reply is then not the first."""
    return any(attempt.get("outcome") == "retried" for attempt in attempts) or len(attempts) > 1


def undo_zoom(report: Mapping[str, Any], evidence: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The report with every zoom correction undone, from the evidence entries' ``name_before``/``description_before``.

    A correction replaced an item's description (and, except for an entity, its name) in the report; the entry kept
    the earlier text. Undoing them restores the report the zoomed second look started from.
    """
    restored = copy.deepcopy(dict(report))
    for entry in evidence:
        if entry.get("name_before") is None and entry.get("description_before") is None:
            continue
        collection, id_field = _REPORT_ITEMS.get(str(entry.get("item_type")), (None, None))
        if collection is None:
            continue
        for item in restored.get(collection) or []:
            if str(item.get(id_field) or "") == str(entry.get("item_id") or ""):
                if entry.get("description_before") is not None:
                    item["description"] = entry["description_before"]
                if entry["item_type"] != "entity" and entry.get("name_before") is not None:
                    item["name"] = entry["name_before"]
                break
    return restored


class PipelineTask:
    task = TASK
    requires = frozenset({"video_input", "json_schema"})
    retries_included = True  # the worst case counts the agent's own retries; an analysis is never re-sent

    def __init__(self, *, video_url: Callable[[str], str] | None = None,
                 video_duration: Callable[[str], float | None] | None = None,
                 enabled_profiles: Sequence[str] = ()) -> None:
        self.video_url = video_url
        self.video_duration = video_duration or (lambda clip: None)
        self.enabled_profiles = tuple(enabled_profiles)

    @staticmethod
    def profile(spec: Mapping[str, Any]) -> VssProfile:
        return profile_for(spec.get("profile"), spec.get("overrides"))

    def plan(self, config: Any, datasets: Mapping[str, Any]) -> list[WorkItem]:
        clips = datasets.get("clips")
        if clips is None:
            raise ValueError(f"{TASK} needs the split's clips (datasets['clips'] from the dataset loader)")
        items = []
        for subject in config.task_subjects(TASK):
            if subject.adapter != PIPELINE_ADAPTER:
                raise ValueError(f"{TASK} runs pipeline subjects (adapter {PIPELINE_ADAPTER!r}); subject "
                                 f"{subject.id} uses {subject.adapter!r} (Track A belongs to incident_objects)")
            spec = {"model": subject.model, "profile": subject.profile, "overrides": dict(subject.request)}
            self.profile(spec)  # an unknown profile or override fails at plan time, before any call
            items += [WorkItem(TASK, subject.id, clip, repeat, spec)
                      for clip in clips for repeat in range(1, config.repeats + 1)]
        return items

    def budget_estimate(self, config: Any, items: list[WorkItem]) -> dict[str, dict[str, int]]:
        totals: dict[str, dict[str, int]] = {}
        for item in items:
            for ledger, (attempts, tokens) in self.profile(item.spec).worst_case().items():
                row = totals.setdefault(ledger, {"attempts": 0, "tokens": 0})
                row["attempts"] += attempts
                row["tokens"] += tokens
        return totals

    def execute(self, item: WorkItem, adapter: PipelineAdapter, ctx: CallContext) -> PredictionRecord:
        if self.video_url is None:
            raise RuntimeError(f"{TASK}: no video-URL resolver; executing is a live, approval-gated run")
        if adapter.model != item.spec["model"]:
            raise ValueError(f"work item for {item.spec['model']!r} given an adapter for {adapter.model!r}")
        profile = self.profile(item.spec)
        duration = self.video_duration(item.item_id)
        request = build_analyze_request(profile, video_url=self.video_url(item.item_id), model=adapter.model,
                                        duration_seconds=duration, enabled=self.enabled_profiles)
        result = adapter.analyze(item.item_id, request, ctx)
        provenance = {**dict(result.provenance or {}), "track": "B", "adapter": adapter.id,
                      "profile": profile.to_dict()}
        usage = {"ledgers": {ledger: dict(row) for ledger, row in result.charged.items()}}
        timing = {"elapsed_ms": round(result.elapsed_ms, 1)}
        if result.outcome == "request_failed":
            kind = "transport_error" if result.status == 0 else classify_http_status(result.status)
            failure = {"kind": kind, "detail": result.error or "", "http_status": result.status or None}
            p1_raw = {"ok": False, "failure_kind": kind, "status_code": result.status, "error": result.error or ""}
            return PredictionRecord(item.key, TASK, item.subject, item.item_id, item.repeat,
                                    outcome=v1.outcome(p1_raw, {}), failure=failure, usage=usage, timing=timing,
                                    provenance=provenance)
        raw = result.raw_output or ""
        policy = v1.evaluate_policy(raw, duration)
        validation = {"core_violations": policy["core"], "enrichment_violations": policy["enrichment"],
                      "full_contract_ok": policy["full_contract_ok"], "core_ok": policy["core_ok"]}
        answered = result.outcome == "answered"
        final = copy.deepcopy(dict(result.final_report)) if answered and result.final_report is not None else None
        undone = undo_zoom(final, result.evidence) if final is not None else None
        p1_report = copy.deepcopy(dict(result.p1_report)) if result.p1_report is not None else None
        predictions = {"p1": policy["report"] if policy["core_ok"] else v1.empty_prediction(),
                       "p1_agent": p1_report if p1_report is not None else undone, "final": final}
        caption = ((result.provenance or {}).get("stages") or {}).get("caption") or {}
        artifact = {
            "raw": result.raw_output, "finish_reason": result.finish_reason, "validation": validation,
            "agent_verdict": "valid" if answered else "contract_rejected",
            "policy_agrees": policy["core_ok"] == answered, "predictions": predictions,
            "p1_agent_source": None if final is None else ("experiment.p1_report" if p1_report is not None
                                                           else "zoom_undone"),
            "zoom_undo_consistent": (undone == p1_report) if p1_report is not None and undone is not None else None,
            "p1_attempts": result.p1_attempts, "p1_retried": p1_retried(result.p1_attempts),
            "evidence": [dict(entry) for entry in result.evidence],
            "experiment": {k: v for k, v in result.experiment.items() if k != "p1_report"},
            "object_tracking": dict(result.object_tracking) if result.object_tracking is not None else None,
            # Per run, never pinned (B3): whether the agent's caption pass ran (chunking.enabled) and why it skipped.
            "caption_pass": {"ran": bool(caption.get("calls")), "calls": int(caption.get("calls") or 0),
                             **per_run_flags(result.provenance, result.experiment)},
            "request_ref": result.request_ref, "response_ref": result.response_ref,
        }
        ref = ctx.store.put(PREDICTION_KIND, artifact)
        return PredictionRecord(item.key, TASK, item.subject, item.item_id, item.repeat,
                                outcome=v1.outcome({"ok": True, "core_ok": policy["core_ok"]}, validation),
                                prediction_ref=str(ref), usage=usage, timing=timing, provenance=provenance)
