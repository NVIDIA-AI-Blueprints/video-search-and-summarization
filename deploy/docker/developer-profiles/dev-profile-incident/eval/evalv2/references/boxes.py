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

"""Box references for ground-truth items (methodology v2.0 §3, R2): tooling only, nothing here runs a model.

How a reference is made (each run is approval-gated):

1. **Frames.** Native-resolution frames at every second of the clip (:func:`frame_plan`); the extraction itself
   is ``scripts/v2_box_frames.py`` in a throwaway environment.
2. **Proposals.** An offline open-vocabulary detector (never the production grounding model, never a subject)
   proposes boxes for the item's words on each frame (:mod:`evalv2.references.detector`).
3. **First pass.** A generator (Claude in Claude Code) sees the item's ground-truth text and the frames with the
   proposals numbered, and picks one number per second, or ``none`` (:func:`generator_packet`).
4. **Blind validation.** A second pass sees the same item on frames offset by half a second, with its own
   proposals under a different numbering, and never the first pass's answer (:func:`validator_packet`,
   :func:`assert_blind`).
5. **Record.** Agreeing seconds become the item's per-second boxes (:func:`build_record`): a scorer can then
   compare a predicted box with the reference at the same second. Code checks plus agreement make a record
   ``ai_validated``; only a person makes it ``human_verified``.

Boxes use the agent's convention: ``[ymin, xmin, ymax, xmax]`` normalised to 0-1000.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from evalv2.datasets.records import new_record, promote_validated

SCALE = 1000
AGREEMENT_IOU = 0.5
MIN_SIDE = 4  # units of 1000: a box thinner than this is not a box
VALIDATOR_OFFSET_S = 0.5
NONE = "none"
KINDS = ("entity", "instrument", "asset")
_ANSWER_KEYS = frozenset({"second", "choice", "confidence"})
ANSWER_FORMAT = {"answers": [{"second": "<frame time t>", "choice": "<mark number, or none>",
                               "confidence": "<0 to 1>"}]}


class BoxReferenceError(ValueError):
    """A malformed proposal, answer or packet, or a validator packet that is not blind."""


def valid_box(box: Sequence[float]) -> bool:
    if len(box) != 4 or not all(isinstance(v, int | float) and not isinstance(v, bool) for v in box):
        return False
    ymin, xmin, ymax, xmax = box
    return 0 <= ymin < ymax <= SCALE and 0 <= xmin < xmax <= SCALE and min(ymax - ymin, xmax - xmin) >= MIN_SIDE


def iou(a: Sequence[float], b: Sequence[float]) -> float:
    """Intersection over union of two ``[ymin, xmin, ymax, xmax]`` boxes."""
    inter_h = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    inter_w = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    intersection = inter_h * inter_w
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - intersection
    return intersection / union if union > 0 else 0.0


def frame_plan(duration_s: float, *, step_s: float = 1.0, offset_s: float = 0.0) -> list[float]:
    """The frame times of one pass: every ``step_s`` from ``offset_s``, strictly inside the clip."""
    if duration_s <= 0 or step_s <= 0:
        raise BoxReferenceError("duration and step must be positive")
    count = math.floor((duration_s - offset_s) / step_s + 1e-9)
    times = [round(offset_s + i * step_s, 3) for i in range(count + 1)]
    return [t for t in times if t < duration_s]


@dataclass(frozen=True)
class Proposal:
    """One detector box on one frame, before numbering."""

    box: tuple[float, float, float, float]
    score: float
    label: str = ""

    def __post_init__(self) -> None:
        if not valid_box(self.box):
            raise BoxReferenceError(f"invalid proposal box {self.box}")


def number_proposals(proposals: Sequence[Proposal], *, seed: str) -> dict[int, Proposal]:
    """Numbers 1..n for one frame's proposals, in an order shuffled by ``seed`` (never by score or position)."""
    ordered = sorted(proposals, key=lambda p: (p.box, p.score, p.label))
    random.Random(hashlib.sha256(seed.encode()).hexdigest()).shuffle(ordered)
    return {index: proposal for index, proposal in enumerate(ordered, start=1)}


def _item_view(item: Mapping[str, Any]) -> dict[str, Any]:
    """What a pass may know about the item: its kind, id and ground-truth words, nothing a model produced."""
    view = {"clip": item["clip"], "kind": item["kind"], "item_id": item["item_id"], "text": item["text"]}
    if item["kind"] not in KINDS:
        raise BoxReferenceError(f"unknown item kind {item['kind']!r}")
    return view


def _frames(item: Mapping[str, Any], times: Sequence[float], proposals: Mapping[float, Sequence[Proposal]],
            *, role: str) -> list[dict[str, Any]]:
    frames = []
    for t in times:
        numbered = number_proposals(proposals.get(t, ()), seed=f"{role}:{item['clip']}:{item['item_id']}:{t}")
        frames.append({"t": t, "image": f"{item['clip']}/{role}/t{t:08.3f}.jpg",
                       "marks": {str(n): list(p.box) for n, p in numbered.items()}})
    return frames


def generator_packet(item: Mapping[str, Any], times: Sequence[float],
                     proposals: Mapping[float, Sequence[Proposal]], *, prompt_id: str) -> dict[str, Any]:
    """The first pass: the item's words and, per second, a frame with its proposals numbered."""
    return {"role": "generator", "prompt": prompt_id, "item": _item_view(item),
            "frames": _frames(item, times, proposals, role="generator"), "answer_format": ANSWER_FORMAT}


def validator_packet(item: Mapping[str, Any], times: Sequence[float],
                     proposals: Mapping[float, Sequence[Proposal]], *, prompt_id: str) -> dict[str, Any]:
    """The blind pass: the same item on other frames (offset times) with its own proposals, numbered anew."""
    return {"role": "validator", "prompt": prompt_id, "item": _item_view(item),
            "frames": _frames(item, times, proposals, role="validator"), "answer_format": ANSWER_FORMAT}


def assert_blind(validator: Mapping[str, Any], generator: Mapping[str, Any], answers: Sequence[Mapping[str, Any]]
                 ) -> None:
    """Refuse a validator packet that could reveal the first pass.

    It must name the validator role, carry no answer object or field, show no frame time the generator saw, and
    contain none of the boxes the generator chose; the item it shows must be the same item.
    """
    if validator.get("role") != "validator" or generator.get("role") != "generator":
        raise BoxReferenceError("packets must be a generator and a validator packet")
    if validator["item"] != generator["item"]:
        raise BoxReferenceError("the validator packet shows another item")
    if validator.get("answer_format") != ANSWER_FORMAT:
        raise BoxReferenceError("the validator packet's answer format is not the fixed template")
    text = json.dumps({k: v for k, v in validator.items() if k != "answer_format"}, sort_keys=True)
    if re.search(r'"(answers|choice|chosen|confidence|first_pass)"\s*:', text):
        raise BoxReferenceError("the validator packet holds an answer field")
    seen = {frame["t"] for frame in generator["frames"]}
    if any(frame["t"] in seen for frame in validator["frames"]):
        raise BoxReferenceError("the validator packet reuses a frame the generator saw")
    chosen = _chosen_boxes(generator, answers)
    for frame in validator["frames"]:
        for box in frame["marks"].values():
            if tuple(box) in chosen:
                raise BoxReferenceError("the validator packet holds a box the generator chose")


def parse_answers(answers: Sequence[Mapping[str, Any]], packet: Mapping[str, Any]) -> dict[float, int | None]:
    """Per frame time, the chosen mark number or ``None``; every shown frame needs exactly one valid answer."""
    marks = {frame["t"]: frame["marks"] for frame in packet["frames"]}
    out: dict[float, int | None] = {}
    for answer in answers:
        if set(answer) - _ANSWER_KEYS or "second" not in answer or "choice" not in answer:
            raise BoxReferenceError(f"answer keys must be {sorted(_ANSWER_KEYS)}: {answer}")
        t = float(answer["second"])
        if t not in marks:
            raise BoxReferenceError(f"answer for a frame not shown: {t}")
        if t in out:
            raise BoxReferenceError(f"two answers for frame {t}")
        choice = answer["choice"]
        if choice == NONE:
            out[t] = None
        elif str(choice) in marks[t]:
            out[t] = int(choice)
        else:
            raise BoxReferenceError(f"frame {t}: mark {choice!r} was not shown")
    missing = sorted(set(marks) - set(out))
    if missing:
        raise BoxReferenceError(f"no answer for frames {missing}")
    return out


def _chosen_boxes(packet: Mapping[str, Any], answers: Sequence[Mapping[str, Any]]) -> set[tuple[float, ...]]:
    chosen = parse_answers(answers, packet)
    marks = {frame["t"]: frame["marks"] for frame in packet["frames"]}
    return {tuple(marks[t][str(n)]) for t, n in chosen.items() if n is not None}


def boxes_by_second(packet: Mapping[str, Any], answers: Sequence[Mapping[str, Any]],
                    *, offset_s: float = 0.0) -> dict[int, list[float] | None]:
    """Per whole second, the chosen box (or ``None``): a frame at ``t`` stands for second ``round(t - offset)``."""
    chosen = parse_answers(answers, packet)
    marks = {frame["t"]: frame["marks"] for frame in packet["frames"]}
    return {round(t - offset_s): (list(marks[t][str(n)]) if n is not None else None) for t, n in chosen.items()}


def spans(seconds: Sequence[int], *, max_gap: int = 1) -> list[list[int]]:
    """Visible seconds as ``[first, last]`` spans, joining gaps of up to ``max_gap`` seconds."""
    out: list[list[int]] = []
    for second in sorted(seconds):
        if out and second - out[-1][1] <= max_gap + 1:
            out[-1][1] = second
        else:
            out.append([second, second])
    return out


def build_record(item: Mapping[str, Any], generated: Mapping[int, list[float] | None],
                 validated: Mapping[int, list[float] | None], *, duration_s: float, dataset: str,
                 generator: Mapping[str, Any], validator: Mapping[str, Any]) -> dict[str, Any]:
    """One item's box reference: per-second boxes where both passes saw it and agree (IoU >= 0.5).

    Disagreeing seconds are dropped and flagged; the record is ``ai_validated`` only when every code check passes
    and the passes agree on every second either saw the item.
    """
    agreed: dict[str, list[float]] = {}
    disagreed: list[int] = []
    for second in sorted(set(generated) | set(validated)):
        a, b = generated.get(second), validated.get(second)
        if a is None and b is None:
            continue
        if a is not None and b is not None and iou(a, b) >= AGREEMENT_IOU:
            agreed[str(second)] = a
        else:
            disagreed.append(second)
    payload = {"clip": item["clip"], "kind": item["kind"], "item_id": item["item_id"], "boxes": agreed,
               "visible_spans": spans([int(s) for s in agreed]), "disagreed_seconds": disagreed}
    record = new_record(record_id=f"box:{item['clip']}:{item['item_id']}", dataset=dataset, kind="box",
                        payload=payload, clip=item["clip"], strata={"category": item.get("category"),
                                                                    "item_kind": item["kind"]},
                        generator=dict(generator), flags=["no_candidate"] if not agreed else [])
    checks = [{"name": "boxes_valid", "passed": all(valid_box(b) for b in agreed.values())},
              {"name": "seconds_inside_clip", "passed": all(0 <= int(s) < duration_s for s in agreed)},
              {"name": "item_seen", "passed": bool(agreed)}]
    return promote_validated(record, checks=checks, validator={**dict(validator), "agrees": not disagreed,
                                                               "disagreed_seconds": disagreed})
