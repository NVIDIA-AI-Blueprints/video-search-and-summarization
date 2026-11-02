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

"""The two corpora the chatbot is asked about (methodology v2.0 §4 D), as frozen snapshots; never the finals DB.

A snapshot is plain JSON: ``{"corpus", "source", "reports": {incident_id: {"version_id", "payload", "evidence"}}}``,
one report version per incident, each the incident's official version. The harness serves it to the real chat
router through snapshot adapters (:mod:`evalv2.tasks.chat.harness`).

- **oracle:** the frozen ground truth's own reports (``p1_shaped``, the contract's shape) of one split half, with
  no evidence entries (gateway mode makes no crops). Answers on it measure the chatbot alone.
- **system:** the reports a sealed Track B run produced for the same half (one pipeline subject, the ``final``
  stage), with the run's evidence entries minus their crop images (no URL is resolved). Answers on it measure the
  whole application: an analysis error carries into the answer. An incident the run has no valid report for is
  absent from the corpus, so its questions are recorded as ``no_report``, never asked.

Incident ids are clip ids in both corpora, so a reference maps to a corpus report by its incident id alone
(:func:`subject_incident`). Item ids (``E1``) are local to each report: the expected answers never use them (a
holder is its ground-truth description), so no item mapping is needed. The two corpora are never pooled.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from evalv2.core.hashing import sha256_json

CORPORA = ("oracle", "system")
STAGES = ("p1", "p1_agent", "final")
_VERSION_BASE = {"oracle": 100_000, "system": 200_000}
_EVIDENCE_KEYS = ("item_id", "item_type", "box_seconds", "timestamp_seconds", "first_seen_seconds",
                  "last_seen_seconds", "label", "item_name", "description", "holder_entity_id", "threat_level")


class CorpusError(ValueError):
    """A corpus that cannot be built: an unknown corpus or stage, or a run without the expected subject."""


def _reports(corpus: str, by_incident: Mapping[str, tuple[Mapping[str, Any], Sequence[Mapping[str, Any]]]]
             ) -> dict[str, dict[str, Any]]:
    """Deterministic version ids (sorted incident ids), each report its incident's official version."""
    return {incident_id: {"version_id": _VERSION_BASE[corpus] + n, "payload": dict(payload),
                          "evidence": [dict(e) for e in evidence]}
            for n, (incident_id, (payload, evidence)) in enumerate(sorted(by_incident.items()), start=1)}


def oracle_snapshot(gt_index: Mapping[str, Any], clips: Sequence[str]) -> dict[str, Any]:
    """The ground truth's reports of ``clips`` (one split half)."""
    missing = sorted(set(clips) - set(gt_index["by_incident_id"]))
    if missing:
        raise CorpusError(f"no ground truth for {missing[:5]}")
    reports = _reports("oracle", {clip: (gt_index["by_incident_id"][clip]["p1_shaped"], []) for clip in clips})
    return {"corpus": "oracle", "source": {"ground_truth": "p1_shaped", "clips": len(clips)}, "reports": reports}


def _evidence(entries: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Evidence entries without crop images or frames: the chat reads items and seconds, never pixels."""
    return [{key: entry[key] for key in _EVIDENCE_KEYS if key in entry} | {"image": ""}
            for entry in entries if isinstance(entry, Mapping)]


def system_snapshot(predictions: Mapping[str, Mapping[str, Any] | None], clips: Sequence[str], *, stage: str = "final",
                    source: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """A Track B run's reports of ``clips``: ``predictions`` maps a clip to its stored pipeline prediction (``None``
    when the run has none). A clip without a valid ``stage`` report is left out and listed in ``source.missing``."""
    if stage not in STAGES:
        raise CorpusError(f"stage must be one of {STAGES}")
    chosen: dict[str, tuple[Mapping[str, Any], Sequence[Mapping[str, Any]]]] = {}
    missing = []
    for clip in sorted(clips):
        prediction = predictions.get(clip)
        report = ((prediction or {}).get("predictions") or {}).get(stage)
        if not isinstance(report, Mapping) or not isinstance(report.get("incident"), Mapping):
            missing.append(clip)
            continue
        chosen[clip] = (report, _evidence((prediction or {}).get("evidence") or []))
    return {"corpus": "system", "source": {**dict(source or {}), "stage": stage, "clips": len(clips),
                                           "missing": missing},
            "reports": _reports("system", chosen)}


def load_system_predictions(store_root: Any, run_id: str, subject: str, *, repeat: int = 1
                            ) -> dict[str, Mapping[str, Any] | None]:
    """The pipeline predictions of one subject and repeat in a sealed Track B run, by clip (``None`` for a failure)."""
    from evalv2.core.artifacts import ArtifactRef, ArtifactStore

    store = ArtifactStore(store_root)
    run = store.run(run_id)
    if not run.sealed:
        raise CorpusError(f"run {run_id} is not sealed: a system corpus is read from a finished run only")
    out: dict[str, Mapping[str, Any] | None] = {}
    for row in run.records():
        if row.get("subject") != subject or int(row.get("repeat") or 1) != repeat:
            continue
        record = store.get(ArtifactRef.parse(row["ref"]))
        ref = record.get("prediction_ref")
        out[str(row["item_id"])] = store.get(ArtifactRef.parse(ref)) if ref else None
    if not out:
        raise CorpusError(f"run {run_id} holds no prediction of subject {subject!r} (repeat {repeat})")
    return out


def snapshot_sha256(snapshot: Mapping[str, Any]) -> str:
    return sha256_json(dict(snapshot))


def subject_incident(payload: Mapping[str, Any], snapshot: Mapping[str, Any]) -> str | None:
    """The corpus incident an incident question is about (``None`` when the corpus has no report for it); a library
    question is about the whole corpus and returns ``"library"``."""
    if payload["scope"] == "library":
        return "library"
    incident_id = str(payload["params"]["incident_id"])
    return incident_id if incident_id in snapshot["reports"] else None
