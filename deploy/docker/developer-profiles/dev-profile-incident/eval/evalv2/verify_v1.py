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

"""``verify-v1``: V2's incident-and-object scoring reproduces V1's Stage 3 from the stored records, with no call.

For every video of a V1 results record, V2 rebuilds V1's store rows (the frozen ground-truth sheets and the stored
prediction, through V1's own writers), replays the stored assignment (V1 kept accepted pairs, not matrices) and
the stored judge scores in V1's call order, and scores the video with :func:`score_video`. Every score block must
equal the stored one, every stored judgement must be used, and V1's aggregates over the re-scored videos must
equal those over the stored ones and, given the report, its ``items`` and ``severity`` per config. Running it on
the real Stage 3 records is a separately approved check (RA0); nothing here calls a model, embedder or judge.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from evalv2.tasks.incident_objects import v1
from evalv2.tasks.incident_objects.aggregate import aggregate
from evalv2.tasks.incident_objects.scoring import SimilarityUnavailableError, score_video
from evalv2.tasks.judging import SequenceJudge

JUDGED = {"entities": ("description",), "instruments": ("name", "description"), "assets": ("name", "description")}
BLOCKS = ("counts", "matches", "unmatched_model", "unmatched_gt")
MAX_PROBLEMS = 50


def judgements(video: Mapping[str, Any]) -> list[tuple[str | None, str | None, float | None]]:
    """The judge calls V1 made for this video, in its call order, with the texts exactly as it sent them."""
    out: list[tuple[str | None, str | None, float | None]] = []
    description = (video.get("incident_field_scores") or {}).get("description") or {}
    if description.get("judged"):
        out.append((description.get("expected"), description.get("predicted"), description.get("score")))
    for kind in v1.KINDS:
        for match in (video.get(kind) or {}).get("matches", []):
            for name in JUDGED[kind]:
                score = (match.get("attribute_scores") or {}).get(name) or {}
                out.append((score.get("expected") or "", score.get("predicted") or "", score.get("score")))
    return out


def assignments(video: Mapping[str, Any]) -> dict[str, list[tuple[str, str, float]]]:
    out = {}
    for kind in v1.KINDS:
        id_field = v1.ID_FIELD[kind]
        out[kind] = [(m[id_field], m[f"gt_{id_field}"], m["similarity_score"])
                     for m in (video.get(kind) or {}).get("matches", [])]
    return out


def _no_similarity(kind: str, model_texts: list[str], gt_texts: list[str]) -> list[list[float]]:
    raise SimilarityUnavailableError("verify-v1 replays stored assignments; no similarity is computed")


def rescore(video: Mapping[str, Any], store: Any) -> tuple[dict[str, Any], int]:
    """V2's score blocks for one stored V1 video, and how many stored judgements went unused."""
    incident_id, run_id = video["incident_id"], video["run_id"]
    v1.put_prediction(store, incident_id, run_id, video.get("prediction") or v1.empty_prediction())
    judge = SequenceJudge(judgements(video))
    result = score_video(store, incident_id, run_id, similarity=_no_similarity, judge=judge,
                         out_of_taxonomy=v1.is_out_of_taxonomy(dict(video)), assignments=assignments(video))
    return result, judge.unused()


def differences(video: Mapping[str, Any], rescored: Mapping[str, Any]) -> list[str]:
    problems = []
    stored_fields, new_fields = video.get("incident_field_scores") or {}, rescored["incident_field_scores"]
    for name in sorted(set(stored_fields) | set(new_fields)):
        if stored_fields.get(name) != new_fields.get(name):
            problems.append(f"incident_field_scores.{name}: stored {stored_fields.get(name)!r} != V2 "
                            f"{new_fields.get(name)!r}")
    for kind in v1.KINDS:
        for block in BLOCKS:
            stored = (video.get(kind) or {}).get(block)
            if stored != rescored[kind][block]:
                problems.append(f"{kind}.{block} differs")
    return problems


def verify_record(record: Mapping[str, Any], sheets: Mapping[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """Re-score one results record (a config's run, or the original+recovery merge) and compare."""
    store = v1.gt_store(dict(sheets))
    problems: dict[str, list[str]] = {}
    stored_videos, rescored_videos = [], []
    for video in record["videos"]:
        label = f"{video.get('incident_id')}#{video.get('repeat', 1)}"
        if "incident_field_scores" not in video:
            continue  # an unscored (profile) record: nothing to reproduce
        rescored, unused = rescore(video, store)
        found = differences(video, rescored)
        if unused:
            found.append(f"{unused} stored judgements were not used")
        if found:
            problems[label] = found
        stored_videos.append(dict(video))
        rescored_videos.append({**video, "incident_field_scores": rescored["incident_field_scores"],
                                **{kind: {block: rescored[kind][block] for block in BLOCKS} for kind in v1.KINDS}})
    stored_aggregate = aggregate(stored_videos)["continuity_v1"]
    rescored_aggregate = aggregate(rescored_videos)["continuity_v1"]
    return {"experiment_id": record.get("experiment_id"), "protocol": record.get("protocol"),
            "videos": len(rescored_videos), "mismatched_videos": len(problems),
            "problems": dict(list(problems.items())[:MAX_PROBLEMS]),
            "continuity_equal": stored_aggregate == rescored_aggregate, "continuity": rescored_aggregate}


def compare_with_report(results: Iterable[Mapping[str, Any]], report: Mapping[str, Any]) -> list[str]:
    """Each verified config's ``items`` and ``severity`` against the Stage 3 report (matched by experiment id)."""
    by_experiment = {r["experiment_id"]: r for r in results}
    problems = []
    for name, config in (report.get("configs") or {}).items():
        result = by_experiment.get(config.get("experiment_id"))
        if result is None:
            problems.append(f"{name}: no verified record for {config.get('experiment_id')}")
            continue
        for block in ("items", "severity"):
            if config.get(block) != result["continuity"][block]:
                problems.append(f"{name}.{block}: the report differs from V2's re-scored aggregate")
    return problems


def verify(records: Iterable[Path], sheets: Mapping[str, list[dict[str, Any]]],
           report: Path | None = None) -> dict[str, Any]:
    results = [verify_record(json.loads(Path(path).read_text(encoding="utf-8")), sheets) for path in records]
    report_problems = compare_with_report(results, json.loads(Path(report).read_text(encoding="utf-8"))) if report \
        else []
    ok = not report_problems and all(r["mismatched_videos"] == 0 and r["continuity_equal"] for r in results)
    return {"ok": ok, "records": [{k: v for k, v in r.items() if k != "continuity"} for r in results],
            "report_problems": report_problems}
