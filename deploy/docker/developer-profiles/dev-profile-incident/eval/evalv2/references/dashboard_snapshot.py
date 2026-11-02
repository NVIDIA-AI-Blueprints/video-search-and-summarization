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

"""Dashboard snapshots (methodology v2.0 §4 E): frozen rows for the SQL harness, the recount and the console replay.

- :func:`seeded_snapshot` is a synthetic snapshot made from a seed. It exercises the dashboard's rules:
  - videos without a report, with one report, or with several;
  - official revisions that are not the newest;
  - human edits and AI re-generations;
  - failed attempts with no revision;
  - legacy, repaired and unparseable run notes;
  - an unclassified type and missing severities and confidences;
  - entity types needing canonicalisation, item names needing normalisation, fractional and non-numeric threat
    levels, and duplicate items in one report;
  - revision times older than a year, after the clock, around midnight UTC and across the US daylight-saving change.

  It holds no real data and can be committed.
- :func:`corpus_snapshot` is the evaluation corpus as a dashboard: one video and one AI revision per report, spread
  evenly over a window before the frozen clock. Its facet columns are filled as the agent's writer fills them.
  - Building it from real reports belongs to the approved run RB2 (dashboard references).
  - Its rows quote report text, so it is written only to private paths.

Every timestamp is explicit: a missing ``created_at`` or ``run_datetime`` would take the database's ``now()``.
"""

from __future__ import annotations

import json
import math
import random
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from evalv2.sql.harness import SNAPSHOT_VERSION

TYPES = ("road accident", "burglary", "explosion", "assault", "animal attack", "robbery", None)
MODELS = ("nvidia/cosmos-3-super-reasoner", "nvidia/cosmos-3-nano")
ENTITY_TYPES = ("human", "person", "Human ", "vehicle", "animal", "", None)
INSTRUMENTS = ("knife", "Knife ", "baseball  bat", "crowbar", "gun", "stick\n", "")
ASSETS = ("car", "shop door", "Shop  Door", "window", "bicycle")
THREATS = (1, 2, 3, 4, 5, 2.5, 3.5, None, "high")
CONFIDENCES = (0.05, 0.42, 0.7, 0.95, 1.0, None, "0.8")
CONTRACT = "incident-contract-v4"


def _iso(moment: datetime) -> str:
    return moment.replace(microsecond=0).isoformat()


def _clock(taken_at: str) -> datetime:
    return datetime.fromisoformat(taken_at).astimezone(UTC).replace(tzinfo=None)


def _notes(rng: random.Random, status: str | None = None) -> str | None:
    if status is not None:
        return json.dumps({"incidentConsoleV2": {"contractVersion": CONTRACT, "status": status}})
    return rng.choice((
        json.dumps({"incidentConsoleV2": {"contractVersion": CONTRACT, "status": "valid_first_pass"}}),
        json.dumps({"incidentConsoleV2": {"contractVersion": CONTRACT, "status": "valid_first_pass"}}),
        json.dumps({"incidentConsoleV2": {"contractVersion": CONTRACT, "status": "valid_after_structural_repair"}}),
        json.dumps({"source": "midterm import"}), "free-text note", None))


def _moment(rng: random.Random, clock: datetime, n: int) -> datetime:
    case = n % 8
    if case == 0:
        return clock - timedelta(days=rng.randint(370, 700), seconds=rng.randint(0, 86399))
    if case == 1:
        return clock - timedelta(seconds=rng.randint(60, 2 * 86400))
    if case == 2:  # within half an hour of midnight UTC
        day = (clock - timedelta(days=rng.randint(1, 60))).replace(hour=0, minute=0, second=0)
        return day + timedelta(minutes=rng.choice((-1, 1)) * rng.randint(1, 30))
    if case == 3:  # around a US daylight-saving change (2026-03-08 07:00 UTC, 2026-11-01 06:00 UTC)
        change = rng.choice((datetime(2026, 3, 8, 7), datetime(2026, 11, 1, 6)))
        return min(change + timedelta(minutes=rng.randint(-90, 90)), clock - timedelta(minutes=1))
    if n == 4:  # one report after the clock: never inside a period that ends now
        return clock + timedelta(hours=rng.randint(1, 48))
    return clock - timedelta(days=rng.randint(0, 120), seconds=rng.randint(0, 86399))


def _payload(rng: random.Random, kind: str | None, severity: int | None) -> dict[str, Any]:
    entities = []
    for i in range(rng.randint(0, 3)):
        entity: dict[str, Any] = {"entity_id": f"E{i + 1}", "description": "a person near the door"}
        entity_type = rng.choice(ENTITY_TYPES)
        if entity_type is not None:
            entity["type"] = entity_type
        entities.append(entity)
    instruments = [{"instrument_id": f"I{i + 1}", "entity_id": "E1", "name": rng.choice(INSTRUMENTS),
                    "description": "held in the right hand", "threat_level": rng.choice(THREATS)}
                   for i in range(rng.randint(0, 3))]
    assets = [{"asset_id": f"A{i + 1}", "name": rng.choice(ASSETS), "description": "near the entrance"}
              for i in range(rng.randint(0, 2))]
    incident: dict[str, Any] = {"type": kind, "title": f"{kind or 'unclassified'} report", "start_timestamp": 1,
                                "end_timestamp": 9, "description": "Synthetic report for the dashboard checks.",
                                "severity_level": severity, "severity_reason": "synthetic", "location": "street"}
    confidence = rng.choice(CONFIDENCES)
    if confidence is not None:
        incident["confidence_score"] = confidence
    return {"incident": incident, "entities": entities, "instruments": instruments, "assets": assets,
            "timeline": [], "uncertainties": []}


def facets(payload: Mapping[str, Any]) -> dict[str, Any]:
    """The revision's facet columns as the agent's writer fills them (``incident_db.revision_facets``)."""
    incident = payload.get("incident") if isinstance(payload.get("incident"), Mapping) else {}
    kind, severity = incident.get("type"), incident.get("severity_level")

    def whole(value: Any) -> int | None:
        ok = isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)
        return max(0, round(value)) if ok else None

    return {"incident_type": kind if isinstance(kind, str) and kind else None,
            "severity_level": severity if isinstance(severity, int) and not isinstance(severity, bool) else None,
            "start_seconds": whole(incident.get("start_timestamp")),
            "end_seconds": whole(incident.get("end_timestamp"))}


def seeded_snapshot(seed: int = 0, *, videos: int = 48, taken_at: str = "2026-11-01T12:00:00+00:00"
                    ) -> dict[str, Any]:
    rng = random.Random(seed)
    clock = _clock(taken_at)
    tables: dict[str, list[dict[str, Any]]] = {"videos": [], "model_runs": [], "report_revisions": []}
    revision_id = 0
    for n in range(videos):
        video_id = f"s{seed % 1000:03d}v{n:04d}"
        first = _moment(rng, clock, n)
        revisions: list[dict[str, Any]] = []
        moment = first
        for number in range(1, rng.choice((0, 1, 1, 1, 2, 2, 3)) + 1):
            revision_id += 1
            if number > 1:
                moment += timedelta(minutes=rng.randint(5, 3 * 24 * 60))
            kind = "ai" if number == 1 or rng.random() < 0.4 else "human"
            incident_type = rng.choice(TYPES)
            severity = rng.choice((1, 2, 3, 4, 5, None))
            payload = _payload(rng, incident_type, severity)
            row = {"id": revision_id, "video_id": video_id, "revision_no": number, "kind": kind,
                   "payload": payload, "evidence": [], **facets(payload), "created_at": _iso(moment)}
            if kind == "ai":
                run_id = f"r{seed % 1000:03d}m{revision_id:06d}"
                tables["model_runs"].append({"id": run_id, "model_name": rng.choice(MODELS),
                                             "notes": _notes(rng),
                                             "run_datetime": _iso(moment - timedelta(seconds=rng.randint(5, 300)))})
                row["model_run_id"] = run_id
            else:
                row["created_by"] = "reviewer@example.org"
                row["parent_id"] = revisions[-1]["id"]
            revisions.append(row)
        tables["report_revisions"] += revisions
        video: dict[str, Any] = {"id": video_id, "filepath": f"uploads/{video_id}/clip.mp4", "source": "seeded",
                                 "uploaded_datetime": _iso(first - timedelta(minutes=2))}
        if revisions and rng.random() < 0.45:
            video["official_revision_id"] = rng.choice(revisions)["id"]
        tables["videos"].append(video)
        if rng.random() < 0.2:  # a failed attempt: a model_runs row and no revision
            tables["model_runs"].append({
                "id": f"r{seed % 1000:03d}f{n:06d}", "model_name": rng.choice(MODELS),
                "notes": _notes(rng, rng.choice(("contract_failed", "request_failed"))),
                "run_datetime": _iso(first - timedelta(minutes=rng.randint(1, 600)))})
    return {"version": SNAPSHOT_VERSION, "taken_at": taken_at, "seed": seed, "tables": tables}


def corpus_snapshot(reports: Mapping[str, Mapping[str, Any]], *, taken_at: str, model_name: str,
                    window_days: int = 90, official: bool = True) -> dict[str, Any]:
    """One video (the incident id) and one AI revision per report, spread evenly over ``window_days`` before the
    clock, with a valid first-pass attempt each; ``official`` marks every revision official."""
    clock = _clock(taken_at)
    ids = sorted(reports)
    tables: dict[str, list[dict[str, Any]]] = {"videos": [], "model_runs": [], "report_revisions": []}
    step = timedelta(days=window_days) / max(len(ids), 1)
    for index, incident_id in enumerate(ids):
        if len(incident_id) > 20:
            raise ValueError(f"incident id {incident_id!r} is longer than videos.id allows (20)")
        created = clock - timedelta(days=window_days) + step * index + step / 2
        run_id = f"corpus-{index:05d}"
        payload = dict(reports[incident_id])
        tables["model_runs"].append({"id": run_id, "model_name": model_name,
                                     "notes": _notes(random.Random(0), "valid_first_pass"),
                                     "run_datetime": _iso(created - timedelta(minutes=1))})
        tables["report_revisions"].append({"id": index + 1, "video_id": incident_id, "revision_no": 1, "kind": "ai",
                                           "model_run_id": run_id, "payload": payload, "evidence": [],
                                           **facets(payload), "created_at": _iso(created)})
        video: dict[str, Any] = {"id": incident_id, "filepath": f"corpus/{incident_id}.mp4", "source": "eval-corpus",
                                 "uploaded_datetime": _iso(created - timedelta(minutes=5))}
        if official:
            video["official_revision_id"] = index + 1
        tables["videos"].append(video)
    return {"version": SNAPSHOT_VERSION, "taken_at": taken_at, "tables": tables}
