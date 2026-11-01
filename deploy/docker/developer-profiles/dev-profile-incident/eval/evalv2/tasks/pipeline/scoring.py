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

"""Track B scoring: the Track A incident-and-object scorers on each stage of a pipeline run, and paired effects.

- :func:`score_stage` scores one stored stage prediction exactly as Track A scores a reply (A6's ``score_video``
  over V1's store), under a stage-specific run id so the stages of one run never mix. A stage without a report
  (the agent rejected P1, so there is no ``final``) is unscored, never counted as a zoom miss.
- :func:`run_metrics` reads one scored stage's comparable figures: item F1 per kind, the judged name and
  description pass rates of matched items, and the incident description.
- :func:`paired_effect` compares two stages run by run: the zoom effect (``p1_agent`` against ``final`` of the
  same run) or the caption effect (the same model's Track A prediction against Track B's ``p1``, paired by clip
  and repeat). Per metric it gives the pairs, the means before and after, the mean difference and how many runs
  improved, worsened or stayed the same. A run where either side is unscored for a metric is left out of that
  metric. Intervals come with the report.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

from evalv2.tasks.incident_objects import v1
from evalv2.tasks.incident_objects.scoring import Similarity, score_video
from evalv2.tasks.judging import Judge
from evalv2.tasks.pipeline.runner import STAGES

JUDGED = {"entities": ("description",), "instruments": ("name", "description"), "assets": ("name", "description")}


def score_stage(gt_store: Any, artifact: Mapping[str, Any], stage: str, *, incident_id: str, run_id: str,
                similarity: Similarity, judge: Judge, out_of_taxonomy: bool = False) -> dict[str, Any] | None:
    """One stage of one stored Track B run, scored like a Track A reply; ``None`` when the stage has no report."""
    if stage not in STAGES:
        raise ValueError(f"unknown stage {stage!r}; expected one of {STAGES}")
    prediction = (artifact.get("predictions") or {}).get(stage)
    if prediction is None:
        return None
    stage_run = f"{run_id}:{stage}"
    v1.put_prediction(gt_store, incident_id, stage_run, copy.deepcopy(dict(prediction)))
    return score_video(gt_store, incident_id, stage_run, similarity=similarity, judge=judge,
                       out_of_taxonomy=out_of_taxonomy)


def _rate(values: list[bool | None]) -> float | None:
    judged = [v for v in values if v is not None]
    return sum(judged) / len(judged) if judged else None


def run_metrics(scored: Mapping[str, Any]) -> dict[str, float | None]:
    out: dict[str, float | None] = {}
    for kind in v1.KINDS:
        block = scored.get(kind) or {}
        counts = block.get("counts")
        out[f"{kind}.f1"] = counts["f1"] if counts else None
        for attribute in JUDGED[kind]:
            out[f"{kind}.{attribute}_pass"] = _rate([(m.get("attribute_scores") or {}).get(attribute, {}).get("pass")
                                                     for m in block.get("matches") or []])
    passed = ((scored.get("incident_field_scores") or {}).get("description") or {}).get("pass")
    out["incident.description_pass"] = None if passed is None else float(passed)
    return out


def paired_effect(before: Mapping[str, Mapping[str, Any]], after: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Per metric, the change from ``before`` to ``after`` over the runs (keys) both sides scored."""
    keys = sorted(set(before) & set(after))
    rows = [(run_metrics(before[key]), run_metrics(after[key])) for key in keys]
    names = sorted({name for b, a in rows for name in (*b, *a)})
    effects = {}
    for name in names:
        pairs = [(b[name], a[name]) for b, a in rows if b.get(name) is not None and a.get(name) is not None]
        if not pairs:
            effects[name] = {"pairs": 0}
            continue
        n = len(pairs)
        effects[name] = {"pairs": n, "mean_before": round(sum(b for b, _ in pairs) / n, 4),
                         "mean_after": round(sum(a for _, a in pairs) / n, 4),
                         "mean_delta": round(sum(a - b for b, a in pairs) / n, 4),
                         "improved": sum(1 for b, a in pairs if a > b), "worsened": sum(1 for b, a in pairs if a < b),
                         "unchanged": sum(1 for b, a in pairs if a == b)}
    return {"runs_paired": len(keys), "only_before": len(set(before) - set(after)),
            "only_after": len(set(after) - set(before)), "metrics": effects}
