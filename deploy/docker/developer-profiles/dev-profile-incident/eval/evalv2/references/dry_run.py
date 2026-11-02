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

"""Plumbing dry runs of RA1, RA2 and RB2: a synthetic detector and scripted passes. Never references.

A dry run drives the real run code end to end (plans, packets, blind checks, code checks, leakage, tiers, records,
summaries) with these stand-ins, so its counts are the real run's counts while its answers are scripted:

- :class:`SyntheticScene` places every RA1 item in a seeded scene (a visible span, a box that moves, or a static
  box with sub-unit jitter for an asset) and builds a :class:`~evalv2.references.detector.FakeDetector` from it:
  the item's box with detection noise where it is visible (missed on a share of frames), plus distractor boxes.
- :func:`box_pass` picks the mark that best covers the item's scene box, wrong on a seeded share of frames.
- :func:`chat_generator` and :func:`chat_validator` word questions from templates and re-derive answers from the
  validator packet alone, the way a careful reader copies values from the report (raw names, not normalised).
- :func:`dashboard_generator` and :func:`dashboard_validator` word chart requests from templates and parse them back.

Every pass here is a :class:`~evalv2.references.passes.ScriptedPass` naming the model ``scripted-dry-run``.
"""

from __future__ import annotations

import math
import random
import re
from collections.abc import Mapping
from typing import Any

from evalv2.references import boxes, chat
from evalv2.references.detector import FakeDetector
from evalv2.references.passes import DRY_RUN_MODEL, ScriptedPass
from evalv2.references.ra1 import frame_path


def _rng(*parts: Any) -> random.Random:
    return random.Random(":".join(str(p) for p in parts))


def identity(prompt: str, *, validator: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {"model": DRY_RUN_MODEL, "prompt": prompt}
    if validator:
        out["id"] = f"{DRY_RUN_MODEL}:{prompt}"
    return out


# --- RA1 ----------------------------------------------------------------------------------------------------------


class SyntheticScene:
    """A seeded scene for every planned RA1 item, and the detector proposals it implies."""

    def __init__(self, planned: Mapping[str, Any], *, seed: int, miss_rate: float = 0.1, distractors: int = 1,
                 noise: float = 3.0, full_visibility: bool = False) -> None:
        self.planned, self.seed = planned, seed
        self.miss_rate, self.distractors, self.noise = miss_rate, distractors, noise
        self.items = {(item["clip"], item["item_id"]): item for item in planned["items"]}
        self._paths = {}
        for (clip, item_id), item in self.items.items():
            rng = _rng(seed, "scene", clip, item_id)
            duration = planned["durations"][clip]
            start = 0.0 if full_visibility else rng.uniform(0, duration * 0.3)
            end = duration if full_visibility else min(duration, start + rng.uniform(duration * 0.4, duration))
            tall = item["kind"] == "entity"
            height, width = (rng.uniform(220, 480), rng.uniform(90, 200)) if tall else (rng.uniform(60, 260),
                                                                                         rng.uniform(60, 260))
            centre = (rng.uniform(height / 2 + 10, 990 - height / 2), rng.uniform(width / 2 + 10, 990 - width / 2))
            velocity = (0.0, 0.0) if item["kind"] == "asset" else (rng.uniform(-4, 4), rng.uniform(-6, 6))
            self._paths[(clip, item_id)] = (start, end, height, width, centre, velocity, rng.uniform(0, 6.28))

    def truth(self, clip: str, item_id: str, t: float) -> tuple[float, float, float, float] | None:
        """The item's box at ``t``, or ``None`` when it is not visible."""
        start, end, height, width, (cy, cx), (vy, vx), phase = self._paths[(clip, item_id)]
        if not start <= t <= end:
            return None
        jitter = 0.3 * math.sin(7.1 * t + phase)  # a static asset still moves a fraction of a unit between frames
        cy = min(max(cy + vy * t + jitter, height / 2 + 1), 999 - height / 2)
        cx = min(max(cx + vx * t + jitter, width / 2 + 1), 999 - width / 2)
        return (round(cy - height / 2, 2), round(cx - width / 2, 2), round(cy + height / 2, 2),
                round(cx + width / 2, 2))

    def detector(self, frames_root: Any) -> FakeDetector:
        proposals: dict[str, list[boxes.Proposal]] = {}
        for clip in self.planned["clips"]:
            times = self.planned["times"][clip]
            clip_items = [item for item in self.planned["items"] if item["clip"] == clip]
            for t in sorted(set(times["generator"]) | set(times["validator"])):
                rows = []
                for item in clip_items:
                    box = self.truth(clip, item["item_id"], t)
                    rng = _rng(self.seed, "detect", clip, item["item_id"], t)
                    if box is not None and rng.random() >= self.miss_rate:
                        noisy = tuple(min(1000.0, max(0.0, round(v + rng.uniform(-self.noise, self.noise), 2)))
                                      for v in box)
                        if boxes.valid_box(noisy):
                            rows.append(boxes.Proposal(noisy, round(rng.uniform(0.4, 0.95), 3), item["query"]))
                for query in self.planned["queries"][clip]:
                    for k in range(self.distractors):
                        rng = _rng(self.seed, "distractor", clip, query, t, k)
                        y, x = rng.uniform(0, 800), rng.uniform(0, 800)
                        rows.append(boxes.Proposal((round(y, 2), round(x, 2), round(y + rng.uniform(40, 190), 2),
                                                    round(x + rng.uniform(40, 190), 2)),
                                                   round(rng.uniform(0.1, 0.5), 3), query))
                proposals[frame_path(frames_root, clip, t)] = rows
        return FakeDetector(proposals, identity={"name": "synthetic-dry-run", "version": "1",
                                                 "weights_sha256": "0" * 64})


def box_pass(scene: SyntheticScene, *, role: str, prompt: str, frame_error: float) -> ScriptedPass:
    """Per frame, the mark that best covers the scene box (IoU >= 0.5), else ``none``; wrong on ``frame_error``."""

    def script(packet_id: str, packet: Mapping[str, Any]) -> dict[str, Any]:
        item = packet["item"]
        answers = []
        for frame in packet["frames"]:
            truth = scene.truth(item["clip"], item["item_id"], frame["t"])
            scored = sorted(((boxes.iou(box, truth) if truth else 0.0, int(n)) for n, box in frame["marks"].items()),
                            reverse=True)
            choice: Any = scored[0][1] if scored and scored[0][0] >= boxes.AGREEMENT_IOU else "none"
            rng = _rng(scene.seed, role, item["clip"], item["item_id"], frame["t"])
            if rng.random() < frame_error:
                others = [n for _, n in scored if n != choice]
                choice = rng.choice(others) if others else "none"
            answers.append({"second": frame["t"], "choice": choice, "confidence": 0.9})
        return {"answers": answers}

    return ScriptedPass(script, identity(prompt, validator=role == "validator"))


# --- RA2 ----------------------------------------------------------------------------------------------------------

_QUESTIONS = {
    "incident.type": ("What kind of incident does this clip show?", "How would you classify what happens here?",
                      "Which incident category fits this footage?"),
    "incident.severity": ("On a scale of 1 to 5, how severe is this incident?",
                          "How serious was the incident, from 1 to 5?", "What severity level would you give it?"),
    "incident.window": ("When does the incident start and end in the video?",
                        "Between which seconds does the event happen?", "At what time does it begin and end?"),
    "incident.people_count": ("How many people are involved?", "How many persons take part in the incident?",
                              "What is the number of people in this clip?"),
    "incident.instrument_list": ("Which weapons or tools were used?", "What objects were used as weapons?",
                                 "Was anything used to cause harm, and if so what?"),
    "incident.asset_list": ("What property was damaged or carried off?", "Which objects or property were affected?",
                            "What things were harmed or taken?"),
    "incident.facts": ("What happened in this clip?", "Describe the incident.",
                       "Can you summarise the events in the video?"),
    "incident.unanswerable": ("What is the licence plate of the vehicle shown?",
                              "What was the name of the person involved?", "At which street address did it happen?"),
    "incident.false_premise": ("Why did the firefighters spray water on the crowd?",
                               "What made the firefighters hose down the people?",
                               "Why were the people sprayed by firefighters?"),
    "library.most_common_type": ("What is the most common type of incident on file?",
                                 "Which incident type occurs most often?", "What kind of incident do we see most?"),
}


def _sentences(text: str | None) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text or "") if len(chat.words(s)) >= 2][:3]


def _distinctive(description: str | None, corpus: Mapping[str, Any], n: int = 3) -> list[str]:
    """The ``n`` content words of a description that the fewest reports share (a searcher's clue)."""
    counts: dict[str, int] = {}
    for report in corpus.values():
        for word in set(chat.words(report["incident"].get("description"))):
            counts[word] = counts.get(word, 0) + 1
    ranked = sorted(set(chat.words(description)), key=lambda w: (counts.get(w, 0), w))
    return ranked[:n]


def chat_generator(gt_indexes: Mapping[str, Mapping[str, Any]]) -> ScriptedPass:
    """Template wordings per class; facts are the report's first sentences; a retrieval clue is rare words."""

    def script(packet_id: str, packet: Mapping[str, Any]) -> dict[str, Any]:
        cls, params = packet["class"], packet["params"]
        if cls in _QUESTIONS:
            question, *paraphrases = _QUESTIONS[cls]
        elif cls == "incident.instrument_holder":
            name = next(i["name"] for i in packet["context"]["instruments"]
                        if i["instrument_id"] == params["instrument_id"])
            question, *paraphrases = (f"Who was holding the {name}?", f"Which person had the {name}?",
                                      f"Who used the {name}?")
        elif cls == "library.type_count":
            question, *paraphrases = (f"How many {params['type']} incidents are on file?",
                                      f"Count the {params['type']} reports.",
                                      f"What number of {params['type']} cases do we have?")
        elif cls == "library.severity_at_least_count":
            question, *paraphrases = (f"How many incidents are rated {params['level']} or higher?",
                                      f"Count the reports with severity of at least {params['level']}.",
                                      f"How many cases reach severity {params['level']} or above?")
        elif cls == "library.instrument_incidents":
            question, *paraphrases = (f"Which incidents involve a {params['instrument']}?",
                                      f"List the reports where a {params['instrument']} appears.",
                                      f"In which clips is a {params['instrument']} used?")
        else:  # library.retrieval
            corpus = chat.oracle_corpus(gt_indexes[params["corpus"]])
            target = packet["context"]["target_reports"][params["targets"][0]]
            clue = " ".join(_distinctive(target["incident"].get("description"), corpus))
            question, *paraphrases = (f"Find the incident with {clue}.", f"Which clip shows {clue}?",
                                      f"Show me the report about {clue}.")
        out: dict[str, Any] = {"question": question, "paraphrases": paraphrases}
        if cls == "incident.facts":
            out["facts"] = _sentences(packet["context"]["incident"].get("description"))
        return out

    return ScriptedPass(script, identity("gen-chat-wording-v1"))


def chat_validator() -> ScriptedPass:
    """Answers from the validator packet alone, copying values as the report writes them (raw names and labels)."""

    def script(packet_id: str, packet: Mapping[str, Any]) -> dict[str, Any]:
        cls, context, question = packet["class"], packet["context"], packet["question"]
        if cls in chat.REFUSAL_CLASSES:
            return {"kind": "refusal", "value": None}
        if chat.CLASSES[cls] == "incident":
            incident = context["incident"]
            if cls == "incident.type":
                return {"kind": "value", "value": incident.get("type")}
            if cls == "incident.severity":
                return {"kind": "value", "value": incident.get("severity_level")}
            if cls == "incident.window":
                return {"kind": "window", "value": [incident.get("start_timestamp"), incident.get("end_timestamp")]}
            if cls == "incident.people_count":
                return {"kind": "count", "value": sum(1 for e in context["entities"] if e.get("type") == "human")}
            if cls == "incident.instrument_list":
                return {"kind": "list", "value": [i.get("name") for i in context["instruments"]]}
            if cls == "incident.asset_list":
                return {"kind": "list", "value": [a.get("name") for a in context["assets"]]}
            if cls == "incident.instrument_holder":
                named = [i for i in context["instruments"] if chat.has_phrase(chat.words(question),
                                                                            chat.words(i.get("name")))]
                holder = next((e for e in context["entities"] if named and e["entity_id"] == named[0].get("entity_id")),
                              None)
                return {"kind": "value", "value": holder.get("description") if holder else None}
            return {"kind": "facts", "facts": _sentences(incident.get("description"))}
        reports = context["reports"]
        asked = chat.words(question)
        if cls == "library.type_count":
            kind = next(t for t in sorted({r["incident"]["type"] for r in reports.values()}, key=len, reverse=True)
                        if chat.has_phrase(asked, chat.words(t)))
            return {"kind": "count", "value": sum(1 for r in reports.values() if r["incident"]["type"] == kind)}
        if cls == "library.severity_at_least_count":
            level = next(int(w) for w in asked if w.isdigit())
            return {"kind": "count",
                    "value": sum(1 for r in reports.values() if (r["incident"].get("severity_level") or 0) >= level)}
        if cls == "library.instrument_incidents":
            names = {chat.normalise(i.get("name")) for r in reports.values() for i in r["instruments"]}
            phrase = max((n for n in names if n and chat.has_phrase(asked, n.split())), key=len)
            return {"kind": "list", "value": sorted(i for i, r in reports.items()
                                                    if any(chat.has_phrase(chat.words(x.get("name")), phrase.split())
                                                           for x in r["instruments"]))}
        if cls == "library.most_common_type":
            counts: dict[str, int] = {}
            for r in reports.values():
                counts[r["incident"]["type"]] = counts.get(r["incident"]["type"], 0) + 1
            return {"kind": "list", "value": sorted(t for t, n in counts.items() if n == max(counts.values()))}
        best = max(reports, key=lambda i: (len(set(asked) & set(chat.words(reports[i]["incident"].get("description")))),
                                           i))
        return {"kind": "list", "value": [best]}

    return ScriptedPass(script, identity("gen-chat-rederive-v1", validator=True))


# --- RB2 ----------------------------------------------------------------------------------------------------------

_PERIOD_WORDS = {"last_7_days": "in the last week", "last_30_days": "over the last month",
                 "last_90_days": "over the last three months", "last_365_days": "over the last year",
                 "all_time": ""}
_UNSUPPORTED = {  # one template request per hint of evalv2.references.rb2.UNSUPPORTED_HINTS
    "another period: a named month": "Chart incidents by type for March 2026.",
    "another period: a specific year": "Chart incidents by severity for 2025.",
    "another period: an exact date range": "Chart reports per day from 3 to 9 October.",
    "a location or site filter": "Chart incidents at the north car park by type.",
    "a camera filter": "Chart incidents seen by camera 4.",
    "a filter on a described person": "Chart the incidents involving the man in the red jacket.",
    "a field the allowlist does not list, such as the weather": "Chart incidents by weather condition.",
    "a field the allowlist does not list, such as a vehicle's colour": "Chart road accidents by car colour.",
    "when incidents happened: their time of day": "Chart what time of day incidents actually happened.",
    "when incidents happened: their own date": "Chart incidents by the date they occurred.",
    "a request to change the figures": "Chart assaults, but double the counts.",
    "a request to invent figures": "Chart next month's predicted incidents.",
    "not a chart request: a written summary": "Write a summary of last night's incidents.",
    "not a chart request: an action on reports": "Delete every report older than a year.",
    "a filter on model confidence": "Chart reports with confidence above 0.8 by type.",
}


def _dimension_words(dimension: str) -> str:
    return dimension.replace("_", " ")


def dashboard_generator() -> ScriptedPass:
    """A request naming every non-default part of the spec, in plain words; unsupported requests from templates."""

    def script(packet_id: str, packet: Mapping[str, Any]) -> dict[str, Any]:
        expected = packet["expected"]
        if expected == {"unsupported": True}:
            text = _UNSUPPORTED[packet["unsupported_kind"]]
            return {"request": text, "paraphrases": [f"Please: {text}", f"Could you {text[0].lower()}{text[1:]}"]}
        parts = [f"{expected['metric']} by {_dimension_words(expected['dimension'])}"]
        if expected["incidentType"]:
            parts.append(f"for {expected['incidentType']} incidents")
        if expected["severity"]:
            parts.append(f"with {expected['severity']} severity")
        if _PERIOD_WORDS[expected["period"]]:
            parts.append(_PERIOD_WORDS[expected["period"]])
        body = " ".join(parts)
        return {"request": f"Show {body}", "paraphrases": [f"Chart {body}", f"Can I see {body}?"]}

    return ScriptedPass(script, identity("gen-dashboard-request-v1"))


def dashboard_validator(*, seed: int, error: float = 0.0) -> ScriptedPass:
    """Parses a template request back to a spec against the packet's allowlist; wrong period on ``error``."""

    def script(packet_id: str, packet: Mapping[str, Any]) -> dict[str, Any]:
        request, allowlist = packet["request"].lower(), packet["allowlist"]
        if request.startswith("show ") is False or " by " not in request:
            return {"unsupported": True}
        dimension = next((d for d in sorted(allowlist["dimensions"], key=len, reverse=True)
                          if f" by {_dimension_words(d)}" in request), None)
        if dimension is None:
            return {"unsupported": True}
        period = next((p for p, w in _PERIOD_WORDS.items() if w and w in request), "all_time")
        if _rng(seed, "respec", packet_id).random() < error:
            period = next(p for p in allowlist["periods"] if p != period)
        kind = next((t for t in allowlist["incidentTypes"] if f"for {t} incidents" in request), None)
        severity = next((s for s in allowlist["severities"] if f"with {s} severity" in request), None)
        return {"metric": allowlist["dimensions"][dimension]["metric"], "dimension": dimension, "period": period,
                "incidentType": kind, "severity": severity}

    return ScriptedPass(script, identity("gen-dashboard-respec-v1", validator=True))
