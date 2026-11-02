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

"""RA1, the box-reference run (methodology v2.0 §3, R2): ground-truth items to per-second box records.

The run makes no model call itself (:mod:`evalv2.references.passes`) and resumes where it stopped:

1. **Plan** (:func:`plan`): one item per ground-truth entity, instrument and asset of the chosen clips, each with
   its detector query (:func:`detector_query`); per clip, the generator's frame times (every second) and the
   validator's (offset by half a second); and the frame-extraction plan for ``scripts/v2_box_frames.py extract``.
2. **Proposals:** the offline detector runs once per clip on every planned frame, for the clip's queries. Its reply
   is cached with its identity (``proposals/<clip>.json``), so a resumed run never runs it again. A proposal's
   ``label`` must be the query that produced it: an item's packets show only the proposals labelled with its query.
3. **Passes**, per item: the generator packet; once answered, the blind validator packet on the offset frames,
   checked with ``assert_blind`` against the generator packet and its answers; once that is answered, the record.
   Only seconds both passes were shown count. The generator's last frame can fall on a second the offset validator
   never sees (a clip whose length ends in .0 to .5 s): such a second is reported as single-pass, never as a
   disagreement (the contract of :func:`evalv2.references.boxes.build_record`).
4. **Output:** the records (private JSONL; every record valid, none ``human_verified``) and a summary of counts.
   An item whose validator packet fails ``assert_blind``, or whose answers do not parse, gets no record and is
   listed in the summary.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from evalv2.core.hashing import canonical_json
from evalv2.core.private import ensure_private
from evalv2.datasets.records import split_by_tier, validate_record, write_records
from evalv2.references import boxes
from evalv2.references.detector import Detector
from evalv2.references.passes import Pass, packet_sha256

GENERATOR_PROMPT = "gen-box-select-v1"
VALIDATOR_PROMPT = "gen-box-validate-v1"
COLLECTIONS = (("entity", "entities", "entity_id"), ("instrument", "instruments", "instrument_id"),
               ("asset", "assets", "asset_id"))
# An entity is proposed by its type: an open-vocabulary detector finds "person" far better than "human".
ENTITY_QUERIES = {"human": "person", "person": "person"}


def detector_query(kind: str, row: Mapping[str, Any]) -> str:
    """The detector's text query for one ground-truth item: an entity's type, an instrument's or asset's name."""
    if kind == "entity":
        entity_type = " ".join(str(row.get("type") or "").split()).lower()
        return ENTITY_QUERIES.get(entity_type, entity_type or "object")
    return " ".join(str(row.get("name") or "").split()).lower() or kind


def items_for(gt_index: Mapping[str, Any], clips: Sequence[str]) -> list[dict[str, Any]]:
    """One item per ground-truth entity, instrument and asset, in clip and id order."""
    out = []
    for clip in clips:
        entry = gt_index["by_incident_id"][clip]
        for kind, collection, id_field in COLLECTIONS:
            for row in entry["p1_shaped"][collection]:
                label = row.get("type") if kind == "entity" else row.get("name")
                out.append({"clip": clip, "kind": kind, "item_id": str(row[id_field]),
                            "text": f"{str(label or '').strip()}: {str(row.get('description') or '').strip()}",
                            "category": entry["category"], "query": detector_query(kind, row)})
    return out


def frame_path(frames_root: Path | str, clip: str, t: float) -> str:
    """Where ``scripts/v2_box_frames.py extract`` writes the native frame of ``clip`` at ``t``."""
    return str(Path(frames_root) / clip / f"t{float(t):08.3f}.jpg")


def plan(gt_index: Mapping[str, Any], clips: Sequence[str], durations: Mapping[str, float], *,
         videos: Mapping[str, Path | str]) -> dict[str, Any]:
    """Items, frame times, queries and the extraction plan for ``clips`` (only clips with at least one item)."""
    items = items_for(gt_index, clips)
    with_items = [clip for clip in clips if any(item["clip"] == clip for item in items)]
    times = {clip: {"generator": boxes.frame_plan(durations[clip]),
                    "validator": boxes.frame_plan(durations[clip], offset_s=boxes.VALIDATOR_OFFSET_S)}
             for clip in with_items}
    queries = {clip: sorted({item["query"] for item in items if item["clip"] == clip}) for clip in with_items}
    extraction = {"clips": [{"clip": clip, "video": str(videos[clip]),
                             "times": sorted(set(times[clip]["generator"]) | set(times[clip]["validator"]))}
                            for clip in with_items]}
    return {"items": items, "clips": with_items, "times": times, "queries": queries, "extraction": extraction,
            "durations": {clip: durations[clip] for clip in with_items}}


def _proposals(clip: str, times: Mapping[str, Sequence[float]], queries: Sequence[str], detector: Detector | None,
               frames_root: Path, cache: Path) -> tuple[dict[float, list[boxes.Proposal]], dict[str, Any]]:
    """The detector's proposals per frame time of one clip (cached), and the detector's identity."""
    all_times = sorted(set(times["generator"]) | set(times["validator"]))
    path = cache / f"{clip}.json"
    if path.exists():
        cached = json.loads(path.read_text(encoding="utf-8"))
        if cached["queries"] == list(queries) and sorted(float(t) for t in cached["frames"]) == all_times:
            return ({float(t): [boxes.Proposal(tuple(r["box"]), float(r["score"]), str(r.get("label", "")))
                                for r in rows] for t, rows in cached["frames"].items()}, cached["detector"])
        raise ValueError(f"{path} holds proposals for other frames or queries; a changed plan is a new run directory")
    if detector is None:
        raise ValueError(f"no cached proposals for {clip} and no detector: RA1 needs the offline detector (X5)")
    by_path = {frame_path(frames_root, clip, t): t for t in all_times}
    found = detector.propose(list(by_path), list(queries))
    proposals = {by_path[p]: list(rows) for p, rows in found.items() if p in by_path}
    identity = dict(detector.identity)
    ensure_private(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json({"detector": identity, "queries": list(queries),
                                     "frames": {f"{t:.3f}": [{"box": list(p.box), "score": p.score, "label": p.label}
                                                             for p in proposals.get(t, [])] for t in all_times}}))
    return proposals, identity


def _for_item(proposals: Mapping[float, Sequence[boxes.Proposal]], times: Sequence[float],
              query: str) -> dict[float, list[boxes.Proposal]]:
    return {t: [p for p in proposals.get(t, ()) if p.label.strip().lower() == query] for t in times}


def _answers(answer: Any) -> list[Mapping[str, Any]]:
    """The answer list of a pass's reply: ``{"answers": [...]}`` as the prompt asks, or the bare list."""
    rows = answer.get("answers") if isinstance(answer, Mapping) else answer
    if not isinstance(rows, list):
        raise boxes.BoxReferenceError("an answer is {\"answers\": [...]} with one entry per frame")
    return rows


def run(planned: Mapping[str, Any], *, detector: Detector | None, generator: Pass, validator: Pass, out: Path,
        frames_root: Path, dataset: str) -> dict[str, Any]:
    """Advance every item as far as the passes' answers allow; write the records and the summary under ``out``."""
    out = Path(out)
    ensure_private(out)
    records: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    seconds: Counter[str] = Counter()
    problems: list[dict[str, str]] = []
    images: set[str] = set()
    detectors: dict[str, dict[str, Any]] = {}
    for clip in planned["clips"]:
        clip_items = [item for item in planned["items"] if item["clip"] == clip]
        times = planned["times"][clip]
        proposals, identity = _proposals(clip, times, planned["queries"][clip], detector, frames_root,
                                         out / "proposals")
        detectors[json.dumps(identity, sort_keys=True)] = identity
        counts["detector_frames"] += len(set(times["generator"]) | set(times["validator"]))
        counts["proposals"] += sum(len(rows) for rows in proposals.values())
        for item in clip_items:
            gen_packet = boxes.generator_packet(item, times["generator"],
                                                _for_item(proposals, times["generator"], item["query"]),
                                                prompt_id=GENERATOR_PROMPT)
            images.update(frame["image"] for frame in gen_packet["frames"])
            counts["generator_frames"] += len(gen_packet["frames"])
            counts["generator_frames_with_marks"] += sum(1 for frame in gen_packet["frames"] if frame["marks"])
            key = f"{clip}-{item['item_id']}"
            gen_reply = generator.answer(f"ra1-gen-{key}", gen_packet)
            if gen_reply is None:
                counts["pending_generator"] += 1
                continue
            try:
                gen_answers = _answers(gen_reply)
                generated = boxes.boxes_by_second(gen_packet, gen_answers)
            except (boxes.BoxReferenceError, ValueError, TypeError) as exc:
                problems.append({"item": key, "stage": "generator_answer", "problem": str(exc)[:200]})
                continue
            val_packet = boxes.validator_packet(item, times["validator"],
                                                _for_item(proposals, times["validator"], item["query"]),
                                                prompt_id=VALIDATOR_PROMPT)
            try:
                boxes.assert_blind(val_packet, gen_packet, gen_answers)
            except boxes.BoxReferenceError as exc:
                problems.append({"item": key, "stage": "assert_blind", "problem": str(exc)[:200]})
                continue
            counts["blind_checks_passed"] += 1
            images.update(frame["image"] for frame in val_packet["frames"])
            counts["validator_frames"] += len(val_packet["frames"])
            counts["validator_frames_with_marks"] += sum(1 for frame in val_packet["frames"] if frame["marks"])
            val_reply = validator.answer(f"ra1-val-{key}", val_packet)
            if val_reply is None:
                counts["pending_validator"] += 1
                continue
            try:
                validated = boxes.boxes_by_second(val_packet, _answers(val_reply), offset_s=boxes.VALIDATOR_OFFSET_S)
            except (boxes.BoxReferenceError, ValueError, TypeError) as exc:
                problems.append({"item": key, "stage": "validator_answer", "problem": str(exc)[:200]})
                continue
            common = {second: box for second, box in generated.items() if second in validated}
            single = sorted(second for second, box in generated.items() if second not in validated and box is not None)
            record = boxes.build_record(
                item, common, validated, duration_s=planned["durations"][clip], dataset=dataset,
                generator={**dict(generator.identity), "detector": identity,
                           "packet_sha256": packet_sha256(gen_packet)},
                validator={**dict(validator.identity), "packet_sha256": packet_sha256(val_packet),
                           "single_pass_seconds": single})
            records.append(record)
            seconds["agreed"] += len(record["payload"]["boxes"])
            seconds["disagreed"] += len(record["payload"]["disagreed_seconds"])
            seconds["single_pass"] += len(single)
    invalid = [p for record in records for p in validate_record(record)]
    if invalid:
        raise ValueError("; ".join(invalid[:10]))
    digest = write_records(records, out / "records.jsonl") if records else None
    tiers = Counter(record["tier"] for record in records)
    summary = {
        "run": "RA1", "dataset": dataset, "records_sha256": digest,
        "clips": len(planned["clips"]),
        "items": len(planned["items"]),
        "items_by_kind": dict(Counter(item["kind"] for item in planned["items"])),
        "items_by_category": dict(Counter(item["category"] for item in planned["items"])),
        "queries": {"distinct": len({q for qs in planned["queries"].values() for q in qs}),
                    "clip_query_pairs": sum(len(qs) for qs in planned["queries"].values())},
        "frames": {"extract": sum(len(c["times"]) for c in planned["extraction"]["clips"]),
                   "generator": sum(len(t["generator"]) for t in planned["times"].values()),
                   "validator": sum(len(t["validator"]) for t in planned["times"].values())},
        "drawn_images": {"paths": len(images), "frames_in_packets": counts["generator_frames"]
                         + counts["validator_frames"]},
        "detectors": list(detectors.values()),
        "counts": dict(counts),
        "records": len(records),
        "tiers": {tier: tiers.get(tier, 0) for tier in ("ai_proposed", "ai_validated", "human_verified")},
        "report_groups": {group: len(rows) for group, rows in split_by_tier(records).items()},
        "flags": dict(Counter(flag for record in records for flag in record["flags"])),
        "seconds": dict(seconds),
        "problems": problems,
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary
