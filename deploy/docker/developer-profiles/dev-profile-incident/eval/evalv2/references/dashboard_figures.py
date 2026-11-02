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

"""Dashboard figures (methodology v2.0 §4 E, R4): a chart's reference numbers, recounted from a frozen snapshot.

A chart's figures are computed three independent ways, which must agree before they become a reference:

1. **SQL:** the production ``get_incident_dashboard`` on the snapshot in the SQL harness (:mod:`evalv2.sql.harness`).
2. **Recount** (:func:`dashboard`, this module): the same figures computed in Python from the snapshot's rows,
   following the rules the migration documents (``20261031090000_dashboard_official_else_newest.sql``):
   - each video is counted once, by its official revision, else its newest;
   - type, severity, weekday, hour and scope filters apply;
   - the period is ``[start, end)`` on the revision time;
   - local days, weekdays and hours use the request's time zone;
   - videos are counted all time, and model coverage counts every attempt in the period.
3. **Console replay:** the console's own ``generateChart`` run in Node on a recorded dashboard
   (:mod:`evalv2.references.dashboard_replay`). It is compared with :func:`chart`, which re-implements how the
   console turns one dashboard into chart rows: the period from the frozen clock, a trend coarsened past 31 bars,
   and a split bar chart's extra official and not-official queries.

Everything runs on a frozen clock and an explicit time zone.

- An all-time dashboard (open bounds) reads ``CURRENT_DATE`` only when nothing matches. The recount then uses the
  frozen clock's UTC date and :func:`clock_dependent` reports it.
- Timestamps are naive UTC, as stored; an offset in a snapshot string is dropped, as a cast to ``TIMESTAMP`` does.
- Labels sort in code-point order (the C collation). The SQL agreement test checks this on the harness's database.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from zoneinfo import ZoneInfo

FAILED = ("contract_failed", "request_failed")
TOP_RANKED = 8
TOP_PER_TYPE = 3
MAX_BUCKETS = 31  # generateChart's MAX_BUCKETS
WEEKDAYS = ("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat")
RPC_KEYS = ("p_period_start", "p_period_end", "p_type", "p_severity", "p_day_of_week", "p_hour", "p_timezone",
            "p_scope")
_SPACE = re.compile(r"[ \t\n\r\f\v]+")  # Postgres's \s
Fetch = Callable[[Mapping[str, Any]], Mapping[str, Any]]


# --- the SQL helpers, re-implemented -------------------------------------------------------------------------------

def _btrim(text: str) -> str:
    return text.strip(" ")


def canonical_incident_type(value: str | None) -> str | None:
    if value is None:
        return None
    text = _btrim(value).lower()
    return {"fighting": "assault", "animal": "animal attack"}.get(text, text or None)


def canonical_entity_type(value: Any) -> str:
    text = _btrim(_text(value) or "").lower()
    return {"": "unknown", "person": "human"}.get(text, text)


def normalized_name(value: Any) -> str | None:
    return _SPACE.sub(" ", _btrim(_text(value) or "").lower()) or None


def run_outcome(notes: str | None) -> str:
    """``run_outcome``: the analysis attempt's outcome from its immutable notes (``legacy`` without a contract)."""
    try:
        parsed = json.loads(notes) if notes is not None else None
    except ValueError:
        parsed = None
    record = parsed.get("incidentConsoleV2") if isinstance(parsed, dict) else None
    if not isinstance(record, dict) or record.get("contractVersion") is None:
        return "legacy"
    status = record.get("status")
    return status if status in ("valid_after_structural_repair", *FAILED) else "valid_first_pass"


def _text(value: Any) -> str | None:
    """``->>``: a JSON value as text (a string as itself, anything else as its JSON)."""
    if value is None or isinstance(value, str):
        return value
    return json.dumps(value)


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def _threat_level(value: Any) -> int | None:
    """``(x ->> 'threat_level')::NUMERIC::INTEGER`` for a JSON number: rounded half away from zero."""
    number = _number(value)
    if number is None:
        return None
    return int(Decimal(repr(value)).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    return parsed.replace(tzinfo=None)


def _local(moment: datetime, tz: str) -> datetime:
    return moment.replace(tzinfo=UTC).astimezone(ZoneInfo(tz)).replace(tzinfo=None)


def _dow(moment: datetime) -> int:
    return (moment.weekday() + 1) % 7  # EXTRACT(DOW): Sunday is 0


def _order(label: str | None) -> tuple[bool, str]:
    return (label is None, label or "")


# --- get_incident_dashboard ----------------------------------------------------------------------------------------

def _counted(snapshot: Mapping[str, Any], tz: str) -> list[dict[str, Any]]:
    """One row per video with a report: its official revision, else its newest (``searchable_revision_id``)."""
    tables = snapshot["tables"]
    by_video: dict[str, list[Mapping[str, Any]]] = {}
    for revision in tables.get("report_revisions", []):
        by_video.setdefault(str(revision["video_id"]), []).append(revision)
    rows = []
    for video in tables.get("videos", []):
        revisions = by_video.get(str(video["id"]), [])
        official = video.get("official_revision_id")
        if official is not None:
            chosen = next((r for r in revisions if r["id"] == official), None)
        else:
            chosen = max(revisions, key=lambda r: r["revision_no"], default=None)
        if chosen is None:
            continue
        created = _timestamp(chosen["created_at"])
        local = _local(created, tz)
        payload = chosen.get("payload") or {}
        rows.append({"incident_id": str(video["id"]), "kind": chosen["kind"], "created": created,
                     "is_official": official is not None, "type": chosen.get("incident_type"),
                     "severity": chosen.get("severity_level"),
                     "confidence": _number((payload.get("incident") or {}).get("confidence_score")),
                     "payload": payload, "dow": _dow(local), "hour": local.hour, "day": local.date()})
    return rows


def _severity_class(severity: int | None, band: str) -> bool:
    if severity is None:
        return False
    return {"low": severity <= 2, "medium": severity == 3, "high": severity >= 4}.get(band, False)


def _severity(rows: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    rows = list(rows)
    return {band: sum(1 for r in rows if _severity_class(r["severity"], band)) for band in ("low", "medium", "high")}


def _dimension_filter(row: Mapping[str, Any], params: Mapping[str, Any]) -> bool:
    if params.get("p_type") is not None:
        canonical = canonical_incident_type(params["p_type"])
        if not ((canonical is not None and row["type"] == canonical)
                or (canonical == "unclassified" and row["type"] is None)):
            return False
    if params.get("p_severity") is not None and not _severity_class(row["severity"], params["p_severity"]):
        return False
    if params.get("p_day_of_week") is not None and row["dow"] != params["p_day_of_week"]:
        return False
    if params.get("p_hour") is not None and row["hour"] != params["p_hour"]:
        return False
    scope = params.get("p_scope") if params.get("p_scope") is not None else "official"
    return not ((scope == "official" and not row["is_official"]) or (scope == "not_official" and row["is_official"]))


def _in_period(moment: datetime | None, start: datetime | None, end: datetime | None) -> bool:
    if start is not None and (moment is None or moment < start):
        return False
    return not (end is not None and (moment is None or moment >= end))


def _item_rows(rows: Sequence[Mapping[str, Any]], kind: str) -> list[tuple]:
    """``entity_rows``, ``instrument_rows`` and ``asset_rows``: one distinct row per incident and label."""
    out: set[tuple] = set()
    for row in rows:
        for item in row["payload"].get(kind) or []:
            if kind == "entities":  # a non-object element has no type (->> gives NULL): it counts as unknown
                kind_text = item.get("type") if isinstance(item, Mapping) else None
                out.add((row["incident_id"], row["type"], canonical_entity_type(kind_text), None, None))
                continue
            if not isinstance(item, Mapping):
                continue
            label = normalized_name(item.get("name"))
            if label is None:
                continue
            level = _threat_level(item.get("threat_level")) if kind == "instruments" else None
            official = row["is_official"] if kind == "assets" else None
            out.add((row["incident_id"], row["type"], label, level, official))
    return sorted(out, key=lambda t: (t[0], _order(t[1]), t[2], t[3] is None, t[3] or 0))


def _ranked(items: Iterable[tuple], limit: int | None = None, *, split: bool = False) -> list[dict[str, Any]]:
    incidents: dict[str, set[str]] = {}
    official: dict[str, set[str]] = {}
    for incident_id, _type, label, _level, is_official in items:
        incidents.setdefault(label, set()).add(incident_id)
        if is_official:
            official.setdefault(label, set()).add(incident_id)
    ranked = sorted(incidents, key=lambda label: (-len(incidents[label]), label))[:limit]
    out = []
    for label in ranked:
        row: dict[str, Any] = {"label": label, "count": len(incidents[label])}
        if split:
            row["official"] = len(official.get(label, ()))
            row["notOfficial"] = row["count"] - row["official"]
        out.append(row)
    return out


def _model_coverage(snapshot: Mapping[str, Any], start: datetime | None, end: datetime | None) -> list[dict]:
    tables = snapshot["tables"]
    runs = {str(run["id"]): run for run in tables.get("model_runs", [])}
    attempts = []
    for run in tables.get("model_runs", []):
        outcome = run_outcome(run.get("notes"))
        if outcome in FAILED and _in_period(_timestamp(run.get("run_datetime")), start, end):
            attempts.append((run["model_name"], outcome))
    for revision in tables.get("report_revisions", []):
        run = runs.get(str(revision.get("model_run_id")))
        if revision["kind"] == "ai" and run is not None and _in_period(_timestamp(revision["created_at"]), start,
                                                                          end):
            attempts.append((run["model_name"], run_outcome(run.get("notes"))))
    per_model: dict[str, Counter] = {}
    for model, outcome in attempts:
        per_model.setdefault(model if model is not None else "Unknown model", Counter())[outcome] += 1
    rows = [{"model": model, "withReport": sum(n for o, n in counts.items() if o not in FAILED),
             "validFirstPass": counts["valid_first_pass"], "repaired": counts["valid_after_structural_repair"],
             "legacy": counts["legacy"], "contractFailed": counts["contract_failed"],
             "requestFailed": counts["request_failed"]} for model, counts in per_model.items()]
    return sorted(rows, key=lambda r: (-r["withReport"], r["model"]))


def dashboard(snapshot: Mapping[str, Any], params: Mapping[str, Any], *, now: datetime | None = None
              ) -> dict[str, Any]:
    """``get_incident_dashboard(params)`` recounted from the snapshot; ``now`` replaces ``CURRENT_DATE``."""
    unknown = sorted(set(params) - set(RPC_KEYS))
    if unknown:
        raise ValueError(f"unknown dashboard parameters {unknown}")
    tz = params.get("p_timezone") or "UTC"
    start, end = _timestamp(params.get("p_period_start")), _timestamp(params.get("p_period_end"))
    clock = _timestamp(now) if now is not None else _timestamp(snapshot["taken_at"])
    counted = _counted(snapshot, tz)
    dimension = [r for r in counted if _dimension_filter(r, params)]
    rows = [r for r in dimension if _in_period(r["created"], start, end)]

    tables = snapshot["tables"]
    with_revisions = {str(r["video_id"]) for r in tables.get("report_revisions", [])}
    videos = tables.get("videos", [])
    video_counts = {
        "uploaded": len(videos),
        "withOfficial": sum(1 for v in videos if v.get("official_revision_id") is not None),
        "awaitingSelection": sum(1 for v in videos if v.get("official_revision_id") is None
                                 and str(v["id"]) in with_revisions),
        "withoutReport": sum(1 for v in videos if str(v["id"]) not in with_revisions),
    }

    days = [r["day"] for r in rows]
    first = _local(start, tz).date() if start is not None else (min(days) if days else clock.date())
    last = (_local(end - timedelta(microseconds=1), tz).date() if end is not None
            else (max(days) if days else clock.date()))
    trend = []
    day = first
    while day <= last:
        on_day = [r for r in rows if r["day"] == day]
        official = sum(1 for r in on_day if r["is_official"])
        trend.append({"date": day.isoformat(), "count": len(on_day), "official": official,
                      "notOfficial": len(on_day) - official})
        day += timedelta(days=1)

    entities, instruments, assets = (_item_rows(rows, kind) for kind in ("entities", "instruments", "assets"))
    types = []
    for kind in sorted({r["type"] for r in rows}, key=lambda t: (-sum(1 for r in rows if r["type"] == t),
                                                                   *_order(t))):
        members = [r for r in rows if r["type"] == kind]
        official = sum(1 for r in members if r["is_official"])
        types.append({"type": kind if kind is not None else "Unclassified", "count": len(members),
                      "official": official, "notOfficial": len(members) - official, "severity": _severity(members),
                      "entities": _ranked((t for t in entities if t[1] == kind), TOP_PER_TYPE),
                      "instruments": _ranked((t for t in instruments if t[1] == kind), TOP_PER_TYPE),
                      "assets": _ranked((t for t in assets if t[1] == kind), TOP_PER_TYPE)})

    previous = None
    if start is not None and end is not None:
        window = [r for r in dimension if start - (end - start) <= r["created"] < start]
        previous = {"total": len(window), "human": sum(1 for r in window if r["kind"] == "human"),
                    "ai": sum(1 for r in window if r["kind"] == "ai"),
                    "high": sum(1 for r in window if _severity_class(r["severity"], "high"))}

    confidences = [r["confidence"] for r in rows if r["confidence"] is not None]
    buckets = Counter(min(9, max(0, math.floor(c * 10))) for c in confidences)
    cells: dict[tuple[int, int], list[Mapping[str, Any]]] = {}
    for r in rows:
        cells.setdefault((r["dow"], r["hour"]), []).append(r)
    levels = Counter(t[3] for t in instruments if t[3] is not None)
    official_rows = [r for r in rows if r["is_official"]]
    return {
        "total": len(rows),
        "official": len(official_rows),
        "notOfficial": len(rows) - len(official_rows),
        "videos": video_counts,
        "previous": previous,
        "averageConfidence": sum(confidences) / len(confidences) if confidences else None,
        "confidenceNotProvided": len(rows) - len(confidences),
        "severity": _severity(rows),
        "severityByScope": {"official": _severity(official_rows),
                            "notOfficial": _severity(r for r in rows if not r["is_official"])},
        "revisions": {"ai": sum(1 for r in rows if r["kind"] == "ai"),
                      "human": sum(1 for r in rows if r["kind"] == "human")},
        "types": types,
        "trend": trend,
        "heatmap": [{"day": d, "hour": h, "count": len(members),
                     "official": sum(1 for r in members if r["is_official"]),
                     "notOfficial": sum(1 for r in members if not r["is_official"])}
                    for (d, h), members in sorted(cells.items())],
        "confidence": [{"bucket": b, "count": n} for b, n in sorted(buckets.items())],
        "entityTypes": _ranked(entities),
        "topInstruments": _ranked(instruments, TOP_RANKED),
        "topAssets": _ranked(assets, TOP_RANKED, split=True),
        "threatLevels": [{"level": level, "count": n} for level, n in sorted(levels.items())],
        "modelCoverage": _model_coverage(snapshot, start, end),
    }


def clock_dependent(params: Mapping[str, Any], result: Mapping[str, Any]) -> bool:
    """Whether the SQL's figures read ``CURRENT_DATE``: an open period bound with nothing counted."""
    return result.get("total") == 0 and (params.get("p_period_start") is None or params.get("p_period_end") is None)


def differences(expected: Any, actual: Any, path: str = "") -> list[str]:
    """Where two dashboards (or charts) differ; averages compare to a relative 1e-9 (summation order)."""
    if isinstance(expected, Mapping) and isinstance(actual, Mapping):
        out = []
        for key in sorted(set(expected) | set(actual)):
            if key not in expected or key not in actual:
                out.append(f"{path}.{key}: only in {'actual' if key not in expected else 'expected'}")
            else:
                out += differences(expected[key], actual[key], f"{path}.{key}")
        return out
    if isinstance(expected, list) and isinstance(actual, list):
        if len(expected) != len(actual):
            return [f"{path}: {len(expected)} items, {len(actual)} items"]
        return [d for i, (e, a) in enumerate(zip(expected, actual, strict=True))
                for d in differences(e, a, f"{path}[{i}]")]
    if isinstance(expected, float) and isinstance(actual, int | float) and not isinstance(actual, bool):
        return [] if math.isclose(expected, actual, rel_tol=1e-9) else [f"{path}: {expected} != {actual}"]
    return [] if expected == actual else [f"{path}: {expected!r} != {actual!r}"]


# --- generateChart's figures ---------------------------------------------------------------------------------------

def _iso_ms(moment: datetime) -> str:
    """``naiveUtc``: an instant as naive UTC ISO text with milliseconds, as the console sends it."""
    if moment.tzinfo is not None:
        moment = moment.astimezone(UTC).replace(tzinfo=None)
    return moment.isoformat(timespec="milliseconds")


def chart_query(spec: Mapping[str, Any], *, timezone: str, now: datetime, allowlist: Mapping[str, Any]
                ) -> dict[str, Any]:
    """The dashboard arguments ``generateChart`` sends for a spec (``dashboardQuery`` then ``dashboardRpcArgs``)."""
    days = allowlist["periods"][spec["period"]]
    return {"p_period_start": None if days is None else _iso_ms(now - timedelta(days=days)),
            "p_period_end": None if days is None else _iso_ms(now), "p_type": spec["incidentType"],
            "p_severity": spec["severity"], "p_day_of_week": None, "p_hour": None, "p_timezone": timezone,
            "p_scope": "all"}


def _capitalize(text: str) -> str:
    return text[:1].upper() + text[1:]


def _row(category: str, count: int) -> dict[str, Any]:
    return {"category": category, "values": {"count": count}}


def _bucket(day: str, dimension: str) -> str:
    if dimension == "report_month":
        return day[:7]
    if dimension != "report_week":
        return day
    moment = date.fromisoformat(day)
    return (moment - timedelta(days=moment.weekday())).isoformat()


def _trend_rows(dimension: str, trend: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, int] = {}
    for item in trend:
        key = _bucket(item["date"], dimension)
        buckets[key] = buckets.get(key, 0) + item["count"]
    return [_row(key, n) for key, n in buckets.items()]


def trend_dimension(dimension: str, trend: Sequence[Mapping[str, Any]]) -> str:
    if dimension == "report_day" and len(_trend_rows(dimension, trend)) > MAX_BUCKETS:
        return trend_dimension("report_week", trend)
    if dimension == "report_week" and len(_trend_rows(dimension, trend)) > MAX_BUCKETS:
        return "report_month"
    return dimension


def _heatmap_rows(dashboard_: Mapping[str, Any], field: str, labels: Sequence[str]) -> list[dict[str, Any]]:
    counts = [0] * len(labels)
    for cell in dashboard_["heatmap"]:
        index = cell[field]
        if isinstance(index, int) and 0 <= index < len(labels):
            counts[index] += cell["count"]
    return [_row(label, counts[i]) for i, label in enumerate(labels)]


def rows_for(dimension: str, dashboard_: Mapping[str, Any], reports: Mapping[str, int] | None
             ) -> list[dict[str, Any]]:
    """``rowsFor``: one dashboard as the rows of one chart dimension."""
    coverage = dashboard_["modelCoverage"]
    ranked = {"entity_type": "entityTypes", "instrument": "topInstruments", "asset": "topAssets"}
    if dimension == "incident_type":
        return [_row(_capitalize(t["type"]), t["count"]) for t in dashboard_["types"]]
    if dimension == "severity":
        return [_row(label, dashboard_["severity"][band]) for label, band in (("Low", "low"), ("Medium", "medium"),
                                                                              ("High", "high"))]
    if dimension == "report_status":
        return [_row("Official", (reports or {}).get("official", 0)),
                _row("Not official", (reports or {}).get("notOfficial", 0))]
    if dimension == "report_kind":
        return [_row("AI as generated", dashboard_["revisions"]["ai"]),
                _row("Human-edited", dashboard_["revisions"]["human"])]
    if dimension in ("report_day", "report_week", "report_month"):
        return _trend_rows(dimension, dashboard_["trend"])
    if dimension == "report_weekday":
        return _heatmap_rows(dashboard_, "day", WEEKDAYS)
    if dimension == "report_hour":
        return _heatmap_rows(dashboard_, "hour", [f"{h:02d}:00" for h in range(24)])
    if dimension in ranked:
        return [_row(_capitalize(item["label"]), item["count"]) for item in dashboard_[ranked[dimension]]]
    if dimension == "confidence":
        buckets = {item["bucket"]: item["count"] for item in dashboard_["confidence"]}
        return [_row(f"{b * 10}-{(b + 1) * 10}%", buckets.get(b, 0)) for b in range(10)]
    if dimension == "threat_level":
        levels = {item["level"]: item["count"] for item in dashboard_["threatLevels"]}
        return [_row(f"Threat {level}", levels.get(level, 0)) for level in range(1, 6)]
    if dimension == "model":
        return [_row(r["model"], r["withReport"] + r["contractFailed"] + r["requestFailed"]) for r in coverage]
    if dimension == "analysis_outcome":
        return [_row(label, sum(r[key] for r in coverage)) for label, key in (
            ("Valid", "validFirstPass"), ("IDs repaired", "repaired"), ("Earlier", "legacy"),
            ("Contract failed", "contractFailed"), ("Request failed", "requestFailed"))]
    raise ValueError(f"unknown chart dimension {dimension!r}")


def chart(spec: Mapping[str, Any] | None, *, chart_type: str, timezone: str, now: datetime,
          allowlist: Mapping[str, Any], fetch: Fetch) -> dict[str, Any]:
    """``generateChart``'s answer for an already parsed spec (``None``: unsupported), without the drawing.

    ``fetch`` answers one set of dashboard arguments: the recount, or the SQL harness.
    """
    if spec is None:
        return {"kind": "unsupported"}
    dimensions = allowlist["dimensions"]
    if chart_type == "pie" and not dimensions[spec["dimension"]]["pie"]:
        return {"kind": "needs_bar"}
    query = chart_query(spec, timezone=timezone, now=now, allowlist=allowlist)
    figures = fetch(query)
    reports = (None if spec["metric"] == "analyses" else
               {"total": figures["total"], "official": figures["official"], "notOfficial": figures["notOfficial"]})
    name = trend_dimension(spec["dimension"], figures["trend"])
    rows = rows_for(name, figures, reports)
    split = chart_type == "bar" and dimensions[name]["splits"]
    if split and name == "asset":
        rows = [{"category": _capitalize(item["label"]),
                 "values": {"official": item["official"], "not_official": item["notOfficial"]}}
                for item in figures["topAssets"]]
    elif split:
        scoped = {scope: {r["category"]: r["values"]["count"]
                          for r in rows_for(name, fetch({**query, "p_scope": scope}), None)}
                  for scope in ("official", "not_official")}
        rows = [{"category": r["category"], "values": {"official": scoped["official"].get(r["category"], 0),
                                                       "not_official": scoped["not_official"].get(r["category"], 0)}}
                for r in rows]
    return {"kind": "chart", "dimension": name, "series": ["official", "not_official"] if split else ["count"],
            "rows": rows, "reports": reports}


def from_console(answer: Mapping[str, Any]) -> dict[str, Any]:
    """A replayed ``generateChart`` answer in :func:`chart`'s shape (without the effective dimension)."""
    if answer.get("kind") != "chart":
        return {"kind": answer.get("kind")}
    drawn = answer["chart"]
    return {"kind": "chart", "series": [s["key"] for s in drawn["series"]], "rows": drawn["rows"],
            "reports": drawn["reports"]}


def comparable(result: Mapping[str, Any]) -> dict[str, Any]:
    """The parts of a chart answer two computations must agree on."""
    return {key: result[key] for key in ("kind", "series", "rows", "reports") if key in result}
