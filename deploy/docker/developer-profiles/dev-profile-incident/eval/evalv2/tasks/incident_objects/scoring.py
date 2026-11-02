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

"""One video's incident-field and object scores, in V1's record shape, plus the full similarity matrices.

The steps mirror V1's ``eval_gt.run_evaluation`` (and ``matching.match_kind``) and call V1's rules for every
decision (:mod:`evalv2.tasks.incident_objects.v1`); the differences are only where the inputs come from:

- **similarity** is a provider ``(kind, model_texts, gt_texts) -> matrix``. The embedder one (a run) computes it
  with V1's cosine over MiniLM vectors; the stored one (offline) reads matrices a run recorded. Every matrix is
  returned under ``similarity`` so it can be stored: V1 kept only the accepted pairs.
- **judge** is a :data:`evalv2.tasks.judging.Judge`; a missing judgement is unscored, never a fail.
- **assignments** (verify-v1 only) replace the matching step with stored pairs, since V1 kept no matrix.

An empty predicted description scores 0.0 without a judge call, attributes of an accepted pair are judged even
when empty (both as in V1), and a kind whose similarity is unavailable is unscored (``counts`` ``None``).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from evalv2.tasks.incident_objects import v1
from evalv2.tasks.judging import Judge

Similarity = Callable[[str, list[str], list[str]], list[list[float]]]
Assignments = Mapping[str, Sequence[tuple[str, str, float]]]


class SimilarityUnavailableError(RuntimeError):
    """The similarity provider could not score one kind (no embedder, or no stored matrix)."""


def embedder_similarity(embed: Callable[[list[str]], list[list[float]]]) -> Similarity:
    """Similarity from an embedder (texts -> vectors), with V1's cosine; ``embed`` raises when it fails."""

    def similarity(kind: str, model_texts: list[str], gt_texts: list[str]) -> list[list[float]]:
        vectors = embed(model_texts + gt_texts)
        matrix: list[list[float]] = v1.cosine_matrix(vectors[: len(model_texts)], vectors[len(model_texts):]).tolist()
        return matrix

    return similarity


def stored_similarity(matrices: Mapping[str, Mapping[str, Any]]) -> Similarity:
    """Similarity from matrices a run recorded; refuses texts that differ from the ones the matrix was built on."""

    def similarity(kind: str, model_texts: list[str], gt_texts: list[str]) -> list[list[float]]:
        stored = matrices.get(kind)
        if stored is None:
            raise SimilarityUnavailableError(f"{kind}: no stored similarity matrix")
        if stored["model_texts"] != model_texts or stored["gt_texts"] != gt_texts:
            raise SimilarityUnavailableError(f"{kind}: the stored matrix was built on other texts")
        return [list(row) for row in stored["matrix"]]

    return similarity


def _judged(judge: Judge, expected: Any, predicted: Any) -> dict[str, Any]:
    """V1's ``_judged_field_score`` with an injected judge: texts judged as given, ``or ""`` as in V1."""
    verdict = judge(expected or "", predicted or "")
    if verdict.ok:
        return {"expected": expected, "predicted": predicted, "score": verdict.score,
                "pass": verdict.score is not None and verdict.score >= v1.PASS_SCORE}
    return {"expected": expected, "predicted": predicted, "score": None, "pass": None, "error": verdict.error}


def score_pair(kind: str, model_row: Mapping[str, Any], gt_row: Mapping[str, Any], judge: Judge,
               entity_matches: list[dict[str, Any]]) -> dict[str, Any]:
    """V1's ``score_matched_pair`` with the judge injected; never changes whether the pair matched."""
    if kind == "entities":
        return {"type": v1.exact_match(gt_row.get("type"), model_row.get("type"), normalize_str=True),
                "description": _judged(judge, gt_row.get("description"), model_row.get("description"))}
    if kind == "instruments":
        return {"name": _judged(judge, gt_row.get("name"), model_row.get("name")),
                "description": _judged(judge, gt_row.get("description"), model_row.get("description")),
                "threat_level": v1.exact_match(gt_row.get("threat_level"), model_row.get("threat_level")),
                "holder": v1.resolve_holder(dict(model_row), dict(gt_row), entity_matches)}
    if kind == "assets":
        return {"name": _judged(judge, gt_row.get("name"), model_row.get("name")),
                "description": _judged(judge, gt_row.get("description"), model_row.get("description"))}
    raise ValueError(f"unknown kind {kind!r}")


def _pairs_from_assignment(kind: str, given: Sequence[tuple[str, str, float]], model_rows: list[dict[str, Any]],
                           gt_rows: list[dict[str, Any]]) -> list[tuple[str, str, float]]:
    id_field = v1.ID_FIELD[kind]
    model_ids, gt_ids = {r[id_field] for r in model_rows}, {r[id_field] for r in gt_rows}
    pairs = [(str(m), str(g), float(s)) for m, g, s in given]
    if len({m for m, _, _ in pairs}) != len(pairs) or len({g for _, g, _ in pairs}) != len(pairs):
        raise ValueError(f"{kind}: a stored assignment is not one-to-one")
    unknown = [p for p in pairs if p[0] not in model_ids or p[1] not in gt_ids]
    if unknown:
        raise ValueError(f"{kind}: stored pairs name unknown items {unknown}")
    return pairs


def score_video(store: Any, incident_id: str, run_id: str, *, similarity: Similarity, judge: Judge,
                out_of_taxonomy: bool = False, assignments: Assignments | None = None) -> dict[str, Any]:
    """``{"incident_field_scores", <kind>: {counts, matches, unmatched_model, unmatched_gt}, "scoring_errors",
    "similarity"}`` for one video whose prediction is already in ``store`` under ``run_id``."""
    gt_incident, model_incident = store.get_gt_incident(incident_id), store.get_incident(incident_id, run_id)
    fields = v1.compare_incident_fields(gt_incident, model_incident)
    errors: list[dict[str, Any]] = []
    predicted = model_incident.get("description")
    if not (predicted or "").strip():
        fields["description"].update(score=0.0, judged=False)
        fields["description"]["pass"] = False
    else:
        verdict = judge(gt_incident.get("description"), predicted)
        fields["description"]["judged"] = True
        if verdict.ok:
            fields["description"]["score"] = verdict.score
            fields["description"]["pass"] = verdict.score is not None and verdict.score >= v1.PASS_SCORE
        else:
            errors.append({"field": "incident.description", "error": verdict.error})
            fields["description"].update(score=None, error=verdict.error)
            fields["description"]["pass"] = None
    if out_of_taxonomy:
        fields.setdefault("type", {})["excluded"] = "out_of_taxonomy"

    out: dict[str, Any] = {"incident_field_scores": fields}
    matrices: dict[str, dict[str, Any]] = {}
    matches_by_kind: dict[str, list[dict[str, Any]]] = {}
    for kind in v1.KINDS:
        id_field, gt_id_field = v1.ID_FIELD[kind], f"gt_{v1.ID_FIELD[kind]}"
        model_rows = getattr(store, v1.MODEL_LIST[kind])(incident_id, run_id)
        gt_rows = getattr(store, v1.GT_LIST[kind])(incident_id)
        if assignments is not None:
            pairs = _pairs_from_assignment(kind, assignments.get(kind, ()), model_rows, gt_rows)
        elif model_rows and gt_rows:
            model_texts = [v1.TEXT[kind](r) for r in model_rows]
            gt_texts = [v1.TEXT[kind](r) for r in gt_rows]
            try:
                matrix = similarity(kind, model_texts, gt_texts)
            except SimilarityUnavailableError as exc:
                errors.append({"field": f"{kind}.matching", "error": str(exc)})
                out[kind] = {"counts": None, "matches": [], "unmatched_model": [], "unmatched_gt": []}
                matches_by_kind[kind] = []
                continue
            matrices[kind] = {"model_ids": [r[id_field] for r in model_rows],
                              "gt_ids": [r[id_field] for r in gt_rows], "model_texts": model_texts,
                              "gt_texts": gt_texts, "matrix": matrix}
            pairs = [(model_rows[a.index][id_field], gt_rows[a.gt_index][id_field], a.similarity)
                     for a in v1.solve_assignment(matrix, threshold=v1.MATCH_THRESHOLD)]
        else:
            pairs = []
        matches = [{id_field: m, gt_id_field: g, "similarity_score": s} for m, g, s in pairs]
        matches_by_kind[kind] = matches
        tp = len(matches)
        matched_model, matched_gt = {m for m, _, _ in pairs}, {g for _, g, _ in pairs}
        model_by_id, gt_by_id = {r[id_field]: r for r in model_rows}, {r[id_field]: r for r in gt_rows}
        for match in matches:
            entity_matches = matches_by_kind["entities"] if kind == "instruments" else []
            match["attribute_scores"] = score_pair(kind, model_by_id.get(match[id_field], {}),
                                                   gt_by_id.get(match[gt_id_field], {}), judge, entity_matches)
            for name, score in match["attribute_scores"].items():
                if isinstance(score, dict) and score.get("error"):
                    errors.append({"field": f"{kind}.{match[id_field]}.{name}", "error": score["error"]})
        out[kind] = {"counts": v1.prf1(tp, len(model_rows) - tp, len(gt_rows) - tp), "matches": matches,
                     "unmatched_model": [r for r in model_rows if r[id_field] not in matched_model],
                     "unmatched_gt": [r for r in gt_rows if r[id_field] not in matched_gt]}
    out["scoring_errors"] = errors
    out["similarity"] = matrices
    return out
