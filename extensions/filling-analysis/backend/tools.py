# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Deterministic tools over measured observations, shared by HTTP and MCP."""

from __future__ import annotations

import asyncio
import math
import re
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Annotated, Any, Callable

from pydantic import Field


def measurement_rows(result: dict, scene_id: str | None = None) -> list[dict]:
    rows = []
    provenance = result.get("provenance", {})
    for sample in result.get("samples", []):
        if scene_id is not None and sample.get("scene_id") != scene_id:
            continue
        for bottle in sample.get("bottles", []):
            row = {
                "t": sample["t"], "scene_id": sample.get("scene_id"),
                "phase": sample.get("phase", "unreadable"), "bottle_id": bottle["id"],
                "label": bottle.get("label", bottle["id"]), "level": bottle.get("level"),
                "confidence": bottle.get("confidence"),
                "level_kind": bottle.get("level_kind", "visible-height"),
            }
            if provenance.get("stream_id"):
                row.update(stream_id=provenance["stream_id"], source_clock_origin=provenance["actual_start_time"],
                           recorded_at=(datetime.fromisoformat(provenance["actual_start_time"].replace("Z", "+00:00")) + timedelta(seconds=sample["t"])).isoformat())
            rows.append(row)
    return rows


def _readable(rows: list[dict]) -> list[dict]:
    return [r for r in rows if isinstance(r["level"], (float, int))
            and not isinstance(r["level"], bool) and math.isfinite(r["level"])
            and 0 <= r["level"] <= 1]


def _cycle_answer(result: dict, question: str, reference_level: float | None,
                  scene_id: str | None, response: dict) -> dict | None:
    """Query measured completed cycles; no manual anomaly labels enter this path."""
    if scene_id not in (None, "single-station"):
        return {**response, "supported": False, "answer": "Select the single-station scene for completed bottle-cycle measurements."}
    if re.search(r"\b(litres?|liters?|milliliters?|volume|cause|pressure|temperature)\b", question):
        return {**response, "supported": False, "answer": "Cycle tools report relative visible liquid height and observed exterior overflow. They cannot establish volume, causes, or machine settings."}
    cycles = list(result.get("cycles", []))
    under = bool(re.search(r"under[ -]?fill|\bshortfall\b|\blow fills?\b", question))
    overflow = bool(re.search(r"\b(overflow(?:s|ing|ed)?|spill(?:s|ing|ed)?|leak(?:s|ing|ed)?)\b", question))
    final_comparison = bool(re.search(r"\b(final|finished|completed|completion)\b", question) and re.search(r"\b(below|above|reference|lowest|highest)\b", question))
    largest = bool(re.search(r"\b(largest|greatest|biggest|most)\b.*\bshortfall\b|\blowest\b.*\b(final|finished)\b", question))
    summary = bool(re.search(r"\b(summary|summarize|overview|status|count|many|bottles|cycles|measurements)\b", question))
    if not (under or overflow or final_comparison or largest):
        if re.search(r"\b(reach|reached|cross|crossed|fastest|rate|speed|rising|maximum|minimum|highest|lowest|reference|target)\b", question):
            return None  # Preserve sampled-height and reference-crossing operations.
        if not summary:
            return None
    readable = [c for c in cycles if c.get("status") != "uncertain" and c.get("measurement_time") is not None and isinstance(c.get("final_level"), (int, float))]
    criterion = None
    if overflow:
        selected = [c for c in cycles if c["status"] == "overflow"]
        explanation = "cycles have observed exterior overflow"
    elif largest:
        deficits = [(c, (reference_level if reference_level is not None else c["reference_level"]) - c["final_level"]) for c in readable]
        largest_deficit = max((deficit for _, deficit in deficits), default=0.0)
        selected = [c for c, deficit in deficits if largest_deficit > 0 and abs(deficit - largest_deficit) < 1e-9]
        explanation = "cycle has the largest measured final-height shortfall" if len(selected) == 1 else "cycles tie for the largest measured final-height shortfall"
    elif final_comparison and not under:
        below = bool(re.search(r"\bbelow\b", question))
        references = {c["id"]: reference_level if reference_level is not None else c["reference_level"] for c in readable}
        selected = [c for c in readable if (c["final_level"] < references[c["id"]] if below else c["final_level"] >= references[c["id"]])]
        relation = "below" if below else "at or above"
        reference_description = f"the supplied {reference_level:.2%} reference" if reference_level is not None else "each cycle's calibrated demo reference"
        explanation = f"completed cycles have readable final height {relation} {reference_description}"
        unique_references = set(references.values())
        criterion = {"measurement": "final-relative-visible-height", "operator": "<" if below else ">=",
                     "reference_level": next(iter(unique_references)) if len(unique_references) == 1 else reference_level,
                     "reference_source": "supplied" if reference_level is not None else "calibrated-per-cycle",
                     "references_by_cycle": references}
    elif under:
        if reference_level is None:
            selected = [c for c in cycles if c["status"] == "underfill"]
            explanation = "cycles finished below the calibrated demo reference minus its tolerance"
        else:
            selected = [c for c in readable if c["final_level"] < reference_level - c["tolerance"]]
            explanation = f"cycles finished below the {reference_level:.1%} reference minus the calibrated tolerance"
    else:
        selected = cycles
        counts = result["summary"]
        explanation = (f"measured bottle cycles: {counts['normal']} normal, {counts['underfill']} underfill, "
                       f"{counts['overflow']} overflow, {counts['uncertain']} uncertain")
    records, evidence = [], []
    provenance = result.get("provenance", {})
    for cycle in selected:
        reference = reference_level if reference_level is not None else cycle["reference_level"]
        records.append({**cycle, "comparison_reference_level": reference,
                        "shortfall_percentage_points": max(0.0, (reference - cycle["final_level"]) * 100) if cycle["final_level"] is not None else None})
        t = cycle.get("overflow_time") if overflow else cycle["measurement_time"]
        if t is None:
            t = cycle["measurement_time"] if cycle["measurement_time"] is not None else cycle.get("overflow_time")
        if t is None:
            continue
        entry = {"t": t, "label": f"{cycle['label']}: {cycle['status']}", "bottle_id": cycle["id"],
                 "evidence_start": cycle["evidence_start"], "evidence_end": cycle["evidence_end"]}
        if provenance.get("stream_id"):
            entry.update(stream_id=provenance["stream_id"], source_clock_origin=provenance["actual_start_time"],
                         recorded_at=(datetime.fromisoformat(provenance["actual_start_time"].replace("Z", "+00:00")) + timedelta(seconds=t)).isoformat())
        evidence.append(entry)
    details = "; ".join(f"{c['label']} " + (f"at {c['measurement_time']:.2f}s: " if c.get("measurement_time") is not None else "(completion unreadable): ")
                        + (f"{c['final_level']:.1%} visible height" if c["final_level"] is not None else "height unreadable") for c in selected[:16])
    if result.get("measurement", {}).get("engine") == "rfdetr":
        method = (" Final visible heights come from RF-DETR bottle/liquid masks. Completion is inferred from "
                  "sampled height rise, stability and station departure; this does not verify nozzle flow. "
                  "Overflow flags use the separately calibrated exterior-color signal, not the liquid-mask model.")
    else:
        method = " This is the legacy calibrated pixel measurement method, not RF-DETR-derived fill height."
    response.update(answer=f"{len(selected)} {explanation}. " + (details + ". " if details else "") +
                    "The reference is a demo visible-height comparison, not a factory specification or volume. Uncertain cycles are not certified normal." + method,
                    cycles=records, summary=result["summary"], evidence=evidence, analysis_kind="bottle-cycles")
    if criterion:
        response["reference_criterion"] = criterion
    return response


def answer_question(result: dict, question: str, reference_level: float | None = None,
                    scene_id: str | None = None) -> dict:
    """Select supported operations; never generate ungrounded process explanations."""
    question = question.lower().strip()
    response: dict[str, Any] = {"answer": "", "evidence": [], "method": "measurement-tools", "supported": True,
                              "provenance": result.get("provenance", {}), "source_sha256": result.get("source_sha256")}
    # Preserve the actual analysis identity; never relabel a legacy cache as RF measurements.
    response.update({key: result[key] for key in ("algorithm", "version", "measurement", "models", "quality",
                    "source_id", "stream_id", "source_clock_origin") if key in result})
    if result.get("analysis_kind") == "bottle-cycles":
        cycle_response = _cycle_answer(result, question, reference_level, scene_id, response)
        if cycle_response is not None:
            return cycle_response
    rows = measurement_rows(result, scene_id)
    named_ids = {r["bottle_id"] for r in rows
                 if any(len(str(value)) >= 3 and re.search(r"\b" + re.escape(str(value).lower()) + r"\b", question)
                        for value in (r["label"], r["bottle_id"]))}
    if named_ids:
        rows = [r for r in rows if r["bottle_id"] in named_ids]
    valid = _readable(rows)
    if re.search(r"\b(spill|leak|litres?|liters?|milliliters?|volume|cause|defect|pressure|temperature)\b", question):
        response.update(supported=False, answer="These tools measure visible liquid height in this recorded video. "
                        "They cannot establish volume, spills, leaks, causes or machine settings. "
                        "Reference chapters are human-reviewed navigation, not detector results.")
        return response
    if not valid:
        response["answer"] = "No readable liquid-height measurements are available for this selection. Unreadable does not mean empty."
        return response
    scenes = {r["scene_id"] for r in rows}
    comparative = re.search(r"\b(fastest|rate|speed|increase|change|rising|reference|target|above|below|reach|cross|threshold|lowest|minimum|least|highest|maximum|fullest|max)\b|most full", question)
    if comparative and scene_id is None and len(scenes) > 1:
        response.update(supported=False, answer="Choose one camera shot before comparing visible heights, reference times or rates. "
                        "Different shots use different measurement windows and are not directly comparable.")
        return response

    def evidence(row: dict, label: str | None = None) -> dict:
        return {"t": row["t"], "label": label or f"{row['label']}: {row['level']:.1%} visible height",
                **{k: row[k] for k in ("stream_id", "source_clock_origin", "recorded_at") if k in row}}

    suffix = " These are relative visible-height measurements, not volume; identities apply only within each camera shot."
    if re.search(r"\b(fastest|rate|speed|increase|change|rising)\b", question):
        grouped: dict[str, list] = defaultdict(list)
        for row in rows:
            grouped[row["bottle_id"]].append(row)
        changes = []
        for group in grouped.values():
            group.sort(key=lambda r: r["t"])
            segments, segment = [], []
            max_gap = 1.5 / max(float(result.get("sample_fps", 1)), 0.001)
            for row in group:
                if not _readable([row]) or (segment and row["t"] - segment[-1]["t"] > max_gap):
                    segments.append(segment)
                    segment = []
                if _readable([row]):
                    segment.append(row)
            segments.append(segment)
            for segment in segments:
                if len(segment) >= 3 and segment[-1]["t"] > segment[0]["t"]:
                    first, last = segment[0], segment[-1]
                    changes.append(((last["level"] - first["level"]) / (last["t"] - first["t"]), first, last))
        if not changes:
            response["answer"] = "There are not enough time-separated readable observations of one tracked bottle to calculate a change."
            return response
        rate, first, last = max(changes, key=lambda c: c[0])
        response["answer"] = (f"The largest observed first-to-last height change rate is {rate * 100:.2f} percentage points/s "
                              f"for {last['label']}, from {first['t']:.2f}s to {last['t']:.2f}s. "
                              "This describes the sampled measurements, not calibrated flow rate." + suffix)
        response["evidence"] = [evidence(first), evidence(last)]
    elif re.search(r"\b(reference|target|above|below|reach|cross|threshold)\b", question):
        if reference_level is None:
            response.update(supported=False, answer="Set a reference level first, then ask which measured bottles are above or below it.")
            return response
        below = bool(re.search(r"\b(below|under)\b", question))
        selected = [r for r in valid if (r["level"] < reference_level if below else r["level"] >= reference_level)]
        first_by_bottle = {}
        for row in sorted(selected, key=lambda r: r["t"]):
            first_by_bottle.setdefault(row["bottle_id"], row)
        relation = "below" if below else "at or above"
        ordered = sorted(first_by_bottle.values(), key=lambda r: r["t"])
        criterion = {"operator": "<" if below else ">=", "reference_level": reference_level,
                     "measurement": "relative-visible-height", "selection": "earliest qualifying readable sample, ordered by source time"}
        records = []
        details = []
        for row in ordered:
            track = sorted([r for r in rows if r["bottle_id"] == row["bottle_id"]], key=lambda r: r["t"])
            readable_track = _readable(track)
            qualifiers = []
            already_qualifying = row["t"] == readable_track[0]["t"]
            earlier_readable = [r for r in readable_track if r["t"] < row["t"]]
            previous = earlier_readable[-1] if earlier_readable else None
            gap = bool(previous and (any(r["level"] is None for r in track if previous["t"] < r["t"] < row["t"])
                       or row["t"] - previous["t"] > 1.5 / max(float(result.get("sample_fps", 1)), 0.001)))
            if already_qualifying:
                qualifiers.append("already " + relation + " at its first readable sample; crossing unknown")
            elif gap:
                qualifiers.append("after an unreadable gap; crossing unknown")
            record = {"bottle_id": row["bottle_id"], "label": row["label"], "scene_id": row["scene_id"],
                      "t": row["t"], "level": row["level"], "criterion": criterion["operator"], "reference_level": reference_level,
                      "exact_crossing_time_known": False, "already_qualifying_at_first_readable_sample": already_qualifying,
                      "unreadable_gap_before": gap,
                      "previous_readable_observation": {"t": previous["t"], "level": previous["level"]} if previous else None,
                      "sampling_qualification": "; ".join(qualifiers) or "First qualifying sampled observation; exact crossing time unknown",
                      **{k: row[k] for k in ("stream_id", "source_clock_origin", "recorded_at") if k in row}}
            records.append(record)
            detail = f"{row['label']} at {row['t']:.2f}s"
            if qualifiers:
                detail += " (" + "; ".join(qualifiers) + ")"
            details.append(detail)
        response["reference_criterion"] = criterion
        response["first_observed"] = records[0] if records else None
        response["first_observed_ties"] = [r for r in records if r["t"] == records[0]["t"]] if records else []
        response["ordered_reference_observations"] = records
        response["answer"] = (f"{len(first_by_bottle)} distinct within-shot tracks have at least one readable sample "
                              f"{relation} the {reference_level:.0%} reference. "
                              + ("First observed, in time order: " + "; ".join(details[:12]) + ". " if details else "")
                              + ("Multiple tracks tie at the first observed sample time. " if len(response["first_observed_ties"]) > 1 else "")
                              + "These are sampled observations, not exact crossing times." + suffix)
        response["evidence"] = [evidence(r) for r in ordered[:12]]
    elif re.search(r"\b(lowest|minimum|least)\b", question):
        row = min(valid, key=lambda r: r["level"])
        response["answer"] = f"The lowest readable height is {row['level']:.1%} for {row['label']} at {row['t']:.2f}s." + suffix
        response["evidence"] = [evidence(row)]
    elif re.search(r"\b(highest|maximum|fullest|max)\b|most full", question):
        row = max(valid, key=lambda r: r["level"])
        response["answer"] = f"The highest readable height is {row['level']:.1%} for {row['label']} at {row['t']:.2f}s." + suffix
        response["evidence"] = [evidence(row)]
    elif re.search(r"\b(summary|summarize|overview|status|count|many|bottles|measurements)\b", question):
        if len(scenes) > 1:
            pieces = []
            for scene in sorted(scenes, key=str):
                own = [r for r in rows if r["scene_id"] == scene]
                pieces.append(f"{scene or 'unassigned shot'}: {len(_readable(own))}/{len(own)} readable observations, "
                              f"{len({r['bottle_id'] for r in own})} within-shot tracks")
            response["answer"] = "; ".join(pieces) + ". Choose a single shot to compare heights or rates; measurement windows differ between shots."
            return response
        row = max(valid, key=lambda r: r["level"])
        response["answer"] = (f"This selection contains {len(rows)} bottle observations, of which {len(valid)} have readable "
                              f"heights, across {len({r['bottle_id'] for r in rows})} distinct within-shot tracks. "
                              f"The highest readable height is {row['level']:.1%} at {row['t']:.2f}s." + suffix)
        response["evidence"] = [evidence(row)]
    else:
        response.update(supported=False, answer="Ask for a measurement summary, the highest or lowest visible height, "
                        "height change rate, or bottles above/below a chosen reference. This interface uses deterministic measurement tools, not an LLM.")
    return response


async def _cycle_video_evidence(answer: dict, result: dict, source_provider: Callable,
                                measurement_evidence_provider: Callable | None) -> dict:
    """Fetch each selected cycle's own clip; never substitute another bottle's link."""
    selected = answer.get("cycles", [])
    observations = {item["bottle_id"]: item for item in answer.get("evidence", []) if item.get("bottle_id")}
    provenance = result.get("provenance", {})
    semaphore = asyncio.Semaphore(3)
    try:
        source = source_provider()
    except Exception:
        source = {}

    def source_matches(current: dict) -> bool:
        return bool(provenance.get("stream_id") and current.get("id") == source.get("id")
                    and current.get("stream_id") == provenance["stream_id"]
                    and current.get("sha256") == result.get("source_sha256")
                    and current.get("actual_start_time") == provenance.get("actual_start_time"))

    async def fetch(cycle: dict) -> dict:
        entry = {"bottle_id": cycle["id"], "label": cycle["label"], "available": False}
        observation = observations.get(cycle["id"])
        if observation is None:
            return {**entry, "reason": "This cycle has no measured event timestamp for evidence"}
        entry["t"] = observation["t"]
        if measurement_evidence_provider is None:
            return {**entry, "reason": "Video evidence integration is not configured"}
        async with semaphore:
            try:
                if not source_matches(source_provider()):
                    return {**entry, "reason": "The selected source does not match the analyzed recording"}
                t, duration = float(observation["t"]), float(source["duration"])
                start, end = float(observation["evidence_start"]), float(observation["evidence_end"])
                if not all(math.isfinite(value) for value in (start, end, t, duration)) or not 0 <= start <= t <= end <= duration or end <= start:
                    return {**entry, "reason": "The measured cycle evidence interval is invalid"}
                entry["source_offsets"] = {"start": start, "end": end}
                clip = await measurement_evidence_provider(start, end)
                if not clip.get("available"):
                    return {**entry, "reason": clip.get("reason", "VIOS evidence is unavailable for this bottle")}
                if (not source_matches(source_provider()) or clip.get("source_id") != source.get("id")
                        or clip.get("stream_id") != provenance["stream_id"] or clip.get("source_clock_origin") != provenance["actual_start_time"]
                        or clip.get("requested", {}).get("streamId") != provenance["stream_id"]
                        or clip.get("actual", {}).get("streamId", provenance["stream_id"]) != provenance["stream_id"]):
                    return {**entry, "reason": "The returned clip does not match this bottle's analyzed source and clock"}
                clock = datetime.fromisoformat(provenance["actual_start_time"].replace("Z", "+00:00"))
                for field, offset in (("startTime", start), ("endTime", end)):
                    requested = datetime.fromisoformat(clip["requested"][field].replace("Z", "+00:00"))
                    if abs((requested - clock).total_seconds() - offset) > 0.000001:
                        return {**entry, "reason": "The returned clip was requested for a different bottle interval"}
                offsets = clip.get("source_offsets", {})
                if offsets.get("start") != start or offsets.get("end") != end:
                    return {**entry, "reason": "The returned clip offsets do not match this measured bottle interval"}
                link = clip.get("public_url") or clip.get("local_clip_url")
                if not isinstance(link, str) or not link.startswith(("http://", "https://", "/")):
                    return {**entry, "reason": "VIOS returned no usable clip link for this bottle"}
                return {**clip, **entry, "available": True}
            except Exception as exc:
                return {**entry, "reason": "Evidence retrieval failed for this bottle; no other bottle's clip was substituted", "error_type": type(exc).__name__}

    clips = await asyncio.gather(*(fetch(cycle) for cycle in selected[:16]))
    try:
        still_selected = source_matches(source_provider())
    except Exception:
        still_selected = False
    if not still_selected:
        clips = [{"bottle_id": item["bottle_id"], "label": item["label"], "available": False,
                  "reason": "The selected source changed during evidence retrieval"} for item in clips]
    # Keep complete legacy metadata for the first clip, but avoid repeating each
    # VIOS response across cycles, observation records and the per-bottle list.
    compact_fields = {"bottle_id", "label", "available", "t", "source_offsets", "public_url", "local_clip_url", "reason", "error_type"}
    compact = [{key: value for key, value in item.items() if key in compact_fields} for item in clips]
    for item, full in zip(compact, clips):
        if item["available"]:
            item["requested"] = {key: full["requested"][key] for key in ("startTime", "endTime")}
            if full.get("actual", {}).get("startTime"):
                item["actual_start_time"] = full["actual"]["startTime"]
    by_bottle = {item["bottle_id"]: item for item in compact}
    for cycle in selected:
        evidence = by_bottle.get(cycle["id"], {"bottle_id": cycle["id"], "label": cycle["label"], "available": False,
                                             "reason": "Evidence fetch limit reached; request this bottle separately"})
        cycle["video_evidence"] = evidence
        if cycle["id"] in observations:
            observations[cycle["id"]]["video_evidence"] = evidence
    answer["video_evidences"] = compact
    answer["video_evidence"] = clips[0] if clips else {"available": False, "reason": "No selected measured cycles have video evidence"}
    answer["video_evidence_source"] = {"source_id": source.get("id"), "stream_id": provenance.get("stream_id"),
                                       "source_sha256": result.get("source_sha256"), "source_clock_origin": provenance.get("actual_start_time")}
    answer["video_evidence_limit"] = {"maximum": 16, "selected": len(selected), "fetched": len(clips), "truncated": len(selected) > 16}
    links = []
    for item in clips:
        if item["available"]:
            link = item.get("public_url") or item["local_clip_url"]
            links.append(f"[{item['label']} clip]({link})")
        else:
            links.append(f"{item['label']}: evidence unavailable")
    if links:
        answer["answer"] += " Per-bottle video evidence: " + "; ".join(links) + "."
    return answer


def create_mcp_server(source_provider: Callable[[], dict], result_provider: Callable[[], dict],
                      vss_evidence_provider: Callable | None = None, sources_provider: Callable | None = None,
                      selection_provider: Callable | None = None, analysis_provider: Callable | None = None,
                      status_provider: Callable | None = None, measurement_evidence_provider: Callable | None = None):
    """Actual MCP tools for VIOS-backed source selection, analysis and measurements."""
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError:
        return None
    server = FastMCP("Filling replay measurements", json_response=True, streamable_http_path="/")

    @server.tool()
    def source_metadata() -> dict:
        """Return the real recorded source identity and human-reviewed reference chapters."""
        return source_provider()

    @server.tool()
    def measured_samples(scene_id: str | None = None, start: float = 0, end: float | None = None,
                         limit: int = 200) -> dict:
        """Read measured bottle observations; visible height is not volume."""
        if not math.isfinite(start) or start < 0 or (end is not None and (not math.isfinite(end) or end < start)):
            raise ValueError("Invalid source time range")
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        result = result_provider()
        rows = [r for r in measurement_rows(result, scene_id)
                if r["t"] >= start and (end is None or r["t"] <= end)]
        return {"rows": rows[:limit], "total": len(rows), "truncated": len(rows) > limit, "method": "measurement-tools", "provenance": result.get("provenance", {})}

    @server.tool()
    async def query_measurements(question: str,
                           reference_percent: Annotated[float | None, Field(ge=0, le=100, allow_inf_nan=False,
                               description="Visible-height reference in percent, from 0 to 100. Use 75 for a 75% reference; do not pass a 0..1 fraction.")] = None,
                           scene_id: str | None = None,
                           include_evidence: Annotated[bool, Field(description="Fetch real video evidence. Cycle answers return a separate clip for each bottle (maximum16); original-shot answers return one clip. Defaults to true.")] = True) -> dict:
        """Answer measurements with real video evidence. The bottle-cycles profile supports completed underfills, exterior overflows and largest final-height shortfall; original-shot profiles support visible-height comparisons only. The reference is a demo visible-height threshold, not volume or a factory specification. Set reference_percent=75 for75% (0..100); reference_level is not accepted. Preserve sampling qualifications. Cycle answers have video_evidences and cycles[].video_evidence keyed by bottle_id: cite each bottle's own available public_url/local_clip_url; never reuse another bottle's clip when unavailable. Legacy video_evidence is only the first clip, not evidence for every bottle."""
        reference_level = reference_percent / 100 if reference_percent is not None else None
        result = result_provider()
        answer = answer_question(result, question, reference_level, scene_id)
        if not include_evidence:
            return answer
        if answer.get("analysis_kind") == "bottle-cycles" and answer["supported"]:
            return await _cycle_video_evidence(answer, result, source_provider, measurement_evidence_provider)
        answer["video_evidence"] = {"available": False, "reason": "No supported measured observation is available for video evidence"}
        observations = answer.get("evidence", [])
        observation = answer.get("first_observed") or (observations[0] if observations else None)
        if not answer["supported"] or observation is None:
            return answer
        if measurement_evidence_provider is None:
            answer["video_evidence"]["reason"] = "Video evidence integration is not configured"
            return answer
        try:
            source, provenance = source_provider(), result.get("provenance", {})
            if not provenance.get("stream_id") or source.get("stream_id") != provenance["stream_id"] or source.get("sha256") != result["source_sha256"] or source.get("actual_start_time") != provenance.get("actual_start_time"):
                answer["video_evidence"]["reason"] = "The selected source does not match this analyzed stream and recording clock"
                return answer
            t, duration = float(observation["t"]), float(source["duration"])
            if not math.isfinite(t) or not math.isfinite(duration) or not 0 <= t <= duration or duration <= 0:
                answer["video_evidence"]["reason"] = "The observation timestamp is outside the recorded source"
                return answer
            start = max(0.0, float(observation.get("evidence_start", t - 1.0)))
            end = min(duration, float(observation.get("evidence_end", t + 2.0)))
            if not math.isfinite(start) or not math.isfinite(end) or not 0 <= start <= t <= end <= duration or end <= start:
                answer["video_evidence"]["reason"] = "The measured evidence interval is invalid for this recording"
                return answer
            clip = await measurement_evidence_provider(start, end)
            if not clip.get("available"):
                answer["video_evidence"] = {"available": False, "reason": clip.get("reason", "VIOS evidence is unavailable for this recorded interval")}
                return answer
            if clip.get("source_id") != source["id"] or clip.get("stream_id") != provenance["stream_id"] or clip.get("source_clock_origin") != provenance["actual_start_time"] or clip.get("requested", {}).get("streamId") != provenance["stream_id"] or clip.get("actual", {}).get("streamId", provenance["stream_id"]) != provenance["stream_id"]:
                answer["video_evidence"]["reason"] = "Evidence source identity changed or does not match the analyzed recording"
                return answer
            link = clip.get("public_url") or clip.get("local_clip_url")
            if not isinstance(link, str) or not link.startswith(("http://", "https://", "/")):
                answer["video_evidence"]["reason"] = "VIOS returned no usable evidence link"
                return answer
            answer["video_evidence"] = clip
            answer["answer"] += f" Video evidence: [Open clip]({link})."
        except Exception as exc:
            answer["video_evidence"] = {"available": False, "reason": "Video evidence retrieval failed; the measured result remains available", "error_type": type(exc).__name__}
        return answer

    # Pinned MCP SDK 1.16 otherwise ignores unknown function arguments. Tighten
    # this tool's model/schema so old reference_level inputs fail explicitly.
    reference_tool = server._tool_manager.get_tool("query_measurements")
    argument_model = reference_tool.fn_metadata.arg_model
    argument_model.model_config = {**argument_model.model_config, "extra": "forbid"}
    argument_model.model_rebuild(force=True)
    reference_tool.parameters = argument_model.model_json_schema(by_alias=True)

    if vss_evidence_provider is not None:
        @server.tool()
        async def vss_evidence(stream_id: str, start_time: str, end_time: str) -> dict:
            """Request real VSS evidence for a known stream UUID and explicit UTC interval. No source identity is inferred."""
            return await vss_evidence_provider(stream_id, start_time, end_time)

    if sources_provider is not None:
        @server.tool()
        async def vss_sources() -> dict:
            """Discover actual VIOS registered sensors and streams before selecting a recorded source."""
            return await sources_provider()

    if selection_provider is not None:
        @server.tool()
        async def select_vss_source(sensor_id: str | None = None, stream_id: str | None = None, refresh: bool = False) -> dict:
            """Select an actual VIOS recorded stream, download it and verify full-frame calibration identity. May take minutes. Does not register or modify VSS footage."""
            return await selection_provider(sensor_id, stream_id, refresh)

    if analysis_provider is not None:
        @server.tool()
        def analyze_fill_progress(force: bool = False) -> dict:
            """Start pixel analysis of the selected verified VIOS recording; reuses only matching source/binding/calibration/algorithm cache. Poll fill_analysis_status for completion."""
            return analysis_provider(force)

    if status_provider is not None:
        @server.tool()
        def fill_analysis_status() -> dict:
            """Read the selected source's actual analysis state; saved results are analyzed replay."""
            return status_provider()

    if measurement_evidence_provider is not None:
        @server.tool()
        async def measurement_evidence(start: float, end: float) -> dict:
            """Get VIOS evidence for source-second offsets in the selected verified recording, preserving its stream UUID and actual UTC recording origin."""
            return await measurement_evidence_provider(start, end)

    return server
