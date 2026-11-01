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

"""Answers composed from one incident report, without a model: the chat route's template mode.

The template mode used to answer every question with the evidence items whose words matched it, ids in the prose
("5 relevant item(s) were identified: human (E1) in vc83... (incident at 134s); ..."). A question about what happened
shares no words with any item, so it got the report's first five items. This module answers from the report itself
(its incident fields, items and timeline), by the kind of question:

- **what happened, or the order of events:** the timeline in time order, one sentence per event with its time; the
  incident description when there is no timeline;
- **who:** the people, with how they look and when they are seen;
- **objects** (weapons, tools, what was used or taken, or a named object): the instruments, with who held them, and
  the assets. A named object the report does not record is said to be absent;
- **when, or how long:** the incident window, or when a named item is seen;
- **severity** and **type**;
- **anything else:** the timeline events that share the question's words; otherwise a plain statement that the report
  does not say, with no citations.

Item ids are references inside the report: the P1 prompt has timeline sentences say "E1 strikes E2 with I1". Before
any report text reaches an answer, each id is resolved to its item's own short description
(:func:`resolve_references`). The ids an answer relies on are returned separately, for the structured citations. An id
is written in the prose only when the question asks for ids. Every sentence comes from a report field; nothing is
inferred beyond it.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import TYPE_CHECKING
from typing import Any

if TYPE_CHECKING:
    from collections.abc import Iterable
    from collections.abc import Mapping
    from collections.abc import Sequence

ITEM_KINDS = (
    ("entities", "entity_id", "entity"),
    ("instruments", "instrument_id", "instrument"),
    ("assets", "asset_id", "asset"),
)
MAX_EVENTS = 8
HUMAN_TYPES = frozenset({"human", "person", "people", "man", "woman", "child", "pedestrian"})
NUMBER_WORDS = ("no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten")

_ID_TOKEN = r"(?:new-)?[EIA][0-9]{1,3}"  # the contract's ids, and the console's ids for items added in a draft
_ID = re.compile(rf"(?<![\w-])({_ID_TOKEN})(?![\w-])")
NEUTRAL = {"E": "a person", "I": "an object", "A": "an item"}
_ASKS_IDS = re.compile(r"\b(?:ids?|identifiers?|item[_ ]ids?)\b")
_CHRONOLOGY = re.compile(
    r"\b(?:what (?:happen\w*|went on|occurred)|happen(?:ed|s|ing)?|in order|order of|sequence|timeline|"
    r"chronolog\w*|step by step|walk me through|events?|describe|summar\w*|recap|overview|tell me about)\b"
)
_PEOPLE = re.compile(
    r"\b(?:who|whom|people|persons?|men|women|suspects?|victims?|attackers?|assailants?|offenders?|individuals?|"
    r"humans?|anyone|someone|involved)\b"
)
_OBJECTS = re.compile(
    r"\b(?:objects?|items?|weapons?|tools?|instruments?|assets?|property|taken|stolen|steal\w*|damaged?|"
    r"carr(?:y|ied|ying)|hold(?:ing)?|held|used|using|harm\w*|things?)\b"
)
_WHEN = re.compile(
    r"\b(?:when|what time|how long|duration|start(?:ed|s)?|end(?:ed|s)?|begin\w*|at what (?:second|point|time|moment))\b"
)
_SEVERITY = re.compile(r"\b(?:severity|severe|serious\w*|danger\w*|risk\w*)\b")
_TYPE = re.compile(
    r"\b(?:(?:what|which) (?:kind|type|sort|category) of|type of incident|kind of incident|classif\w*|category)\b"
)
_HOW_MANY = re.compile(r"\bhow many\b")
_PRESENCE = re.compile(r"^\s*(?:is|are|was|were|did|does|do|has|have|had)\b|\bany\b")
# A bare existence question ("is there a crowbar?", "any knives?"): the only yes/no question a list of items can answer.
_EXISTENCE = re.compile(r"^\s*(?:(?:is|are|was|were)\s+there\b|any\b)")
_WORD = re.compile(r"[a-z0-9]+")
_STOP = frozenset(
    [
        "a",
        "an",
        "the",
        "and",
        "or",
        "but",
        "if",
        "of",
        "to",
        "in",
        "on",
        "at",
        "by",
        "for",
        "with",
        "from",
        "into",
        "onto",
        "over",
        "under",
        "about",
        "as",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "do",
        "does",
        "did",
        "done",
        "has",
        "have",
        "had",
        "there",
        "here",
        "this",
        "that",
        "these",
        "those",
        "it",
        "its",
        "they",
        "them",
        "their",
        "he",
        "him",
        "his",
        "she",
        "her",
        "we",
        "you",
        "your",
        "i",
        "me",
        "my",
        "what",
        "which",
        "who",
        "whom",
        "whose",
        "when",
        "where",
        "why",
        "how",
        "any",
        "some",
        "all",
        "each",
        "every",
        "no",
        "not",
        "yes",
        "can",
        "could",
        "would",
        "should",
        "will",
        "may",
        "might",
        "must",
        "see",
        "seen",
        "seem",
        "show",
        "shows",
        "shown",
        "tell",
        "told",
        "say",
        "says",
        "said",
        "report",
        "video",
        "clip",
        "incident",
        "incidents",
        "happen",
        "happened",
        "happens",
        "happening",
        "order",
        "please",
        "also",
        "just",
        "only",
        "very",
        "much",
        "many",
        "more",
        "most",
        "other",
        "another",
        "one",
        "two",
        "three",
        "first",
        "then",
        "next",
        "last",
        "before",
        "after",
        "time",
        "times",
        "involved",
        "kind",
        "type",
        "sort",
        "category",
        "describe",
        "description",
        "summary",
        "summarise",
        "summarize",
        "events",
        "event",
        "timeline",
        "sequence",
        "people",
        "person",
        "persons",
        "who",
        "objects",
        "object",
        "items",
        "item",
        "things",
        "thing",
        "like",
        "look",
        "looks",
        "looking",
        "wear",
        "wears",
        "wearing",
        "cause",
        "caused",
        "causes",
        "causing",
        "could",
        "doing",
        "going",
        "get",
        "got",
        "make",
        "made",
        "take",
        "took",
        "give",
        "gave",
        "know",
        "think",
        "need",
        "want",
        "way",
        "ever",
        "still",
        "really",
    ]
)
_TIME_FIRST = re.compile(r"^\s*(?:when|what time|how long|at what (?:second|point|time|moment))\b")


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        return None
    return float(value)


def clock(seconds: Any) -> str | None:
    """A video time as the console shows it: ``m:ss``, or ``h:mm:ss`` from an hour."""
    value = _num(seconds)
    if value is None:
        return None
    whole = max(0, math.floor(value))
    hours, minutes, rest = whole // 3600, whole % 3600 // 60, whole % 60
    return f"{hours}:{minutes:02d}:{rest:02d}" if hours else f"{minutes}:{rest:02d}"


def _tokens(text: str) -> list[str]:
    """The content words of a text, in order, as written (lower case)."""
    return [w for w in _WORD.findall(text.lower()) if len(w) > 2 and w not in _STOP]


def _variants(word: str) -> set[str]:
    """A word and its likely singulars, for matching only ("knives" meets "knife", "witnesses" meets "witness")."""
    out = {word}
    if len(word) > 4 and word.endswith("ies"):
        out.add(word[:-3] + "y")
    elif len(word) > 4 and word.endswith("ves"):
        out |= {word[:-3] + "f", word[:-3] + "fe"}
    elif len(word) > 3 and word.endswith("es"):
        out |= {word[:-2], word[:-1]}
    elif len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        out.add(word[:-1])
    return out


def _words(text: str) -> set[str]:
    return {variant for word in _tokens(text) for variant in _variants(word)}


def _lower_first(text: str) -> str:
    first = text.split(" ", 1)[0]
    return text if len(first) > 1 and first.isupper() else text[:1].lower() + text[1:]


def _capitalize(text: str) -> str:
    return text[:1].upper() + text[1:]


def _sentence_case(text: str) -> str:
    text = re.sub(r"(^|[.!?]\s+)([a-z])", lambda m: m.group(1) + m.group(2).upper(), text.strip())
    return text


def _first_clause(text: str) -> str:
    return re.split(r"[,;.]", text.strip(), maxsplit=1)[0].strip()


def _article(text: str, *, definite: bool) -> str:
    text = text.strip().rstrip(".")
    if re.match(r"(?i)^(?:the|a|an|this|that|his|her|their|its)\b", text):
        return _lower_first(text)
    if definite:
        return f"the {_lower_first(text)}"
    return f"{'an' if text[:1].lower() in 'aeiou' else 'a'} {_lower_first(text)}"


def _count(n: int, singular: str, plural: str) -> str:
    return f"{NUMBER_WORDS[n] if n < len(NUMBER_WORDS) else n} {singular if n == 1 else plural}"


def _join(parts: Sequence[str]) -> str:
    parts = [p for p in parts if p]
    if len(parts) <= 1:
        return "".join(parts)
    return ", ".join(parts[:-1]) + " and " + parts[-1]


@dataclass(frozen=True)
class Item:
    """One entity, instrument or asset of the report, as the answers need it."""

    kind: str
    item_id: str
    name: str = ""
    description: str = ""
    label: str = ""
    entity_type: str = ""
    seconds: float | None = None
    first_seen: float | None = None
    last_seen: float | None = None
    holder: str | None = None
    threat_level: int | None = None

    @property
    def is_person(self) -> bool:
        return self.kind == "entity" and self.entity_type in HUMAN_TYPES

    @property
    def words(self) -> set[str]:
        return _words(" ".join((self.name, self.description, self.label, self.entity_type)))

    @property
    def name_words(self) -> set[str]:
        """The words that name the item (its name or label, or a person's type): what an event about it would say."""
        return _words(" ".join((self.name, self.label, self.entity_type if self.kind == "entity" else "")))

    def mention(self) -> str:
        """How an answer names the item: its short visual label or name, else its description's first clause."""
        if self.kind == "entity":
            text = self.label or _first_clause(self.description) or self.entity_type or "person"
        else:
            text = self.name or self.label or _first_clause(self.description) or self.kind
        return _article(text, definite=True)

    def introduce(self, mentions: Mapping[str, str]) -> str:
        """The item named for the first time: how it looks (an entity) or what it is (an object)."""
        if self.kind == "entity":
            text = self.description or self.label or self.entity_type or "person"
            return _article(_resolve_plain(text, mentions), definite=False)
        text = _article(
            _resolve_plain(self.name or self.label or self.description or self.kind, mentions), definite=False
        )
        detail = _first_clause(self.description)
        if detail and self.name and _words(detail) - _words(self.name):
            text += f" ({_lower_first(_resolve_plain(detail, mentions))})"
        return text

    def seen(self) -> str:
        first, last = clock(self.first_seen), clock(self.last_seen)
        if first and last and first != last:
            return f"seen from {first} to {last}"
        moment = first or clock(self.seconds)
        return f"seen at {moment}" if moment else ""


class Mentions(dict[str, str]):
    """Item id to how the answer names it; ``retired`` holds the ids of the base version that a draft removed."""

    retired: frozenset[str] = frozenset()


@dataclass(frozen=True)
class ReportView:
    """One report version (or an unsaved draft): incident fields, items and the timeline in time order.

    ``retired`` are the ids of base-version items the draft removed: report text written before the edit may still
    refer to them, and they read as a neutral "a person" or "an object".
    """

    incident: Mapping[str, Any]
    items: tuple[Item, ...]
    timeline: tuple[Mapping[str, Any], ...]
    retired: frozenset[str] = frozenset()

    def item(self, item_id: str | None) -> Item | None:
        return next((i for i in self.items if i.item_id == item_id), None) if item_id else None

    def mentions(self) -> Mentions:
        """Each item's mention, any id inside one (a label quoting another item) resolved in a single pass."""
        base = Mentions({i.item_id: i.mention() for i in self.items})
        base.retired = self.retired
        resolved = {item_id: _resolve_plain(text, base) for item_id, text in base.items()}
        # Labels that name each other ("man beside E2", "man beside E1") still hold an id after one pass: it reads
        # neutrally rather than recursing.
        neutral = Mentions()
        neutral.retired = frozenset(base) | self.retired
        final = Mentions({item_id: _resolve_plain(text, neutral) for item_id, text in resolved.items()})
        final.retired = self.retired
        return final


def item_ids(report: Mapping[str, Any] | None) -> set[str]:
    """Every entity, instrument and asset id a report holds."""
    report = report if isinstance(report, dict) else {}
    return {
        str(raw[id_field]).strip()
        for key, id_field, _ in ITEM_KINDS
        for raw in report.get(key) or []
        if isinstance(raw, dict) and str(raw.get(id_field) or "").strip()
    }


def overlay_draft(base: Mapping[str, Any] | None, draft: Mapping[str, Any]) -> dict[str, Any]:
    """The report an unsaved draft stands for: the base version with the reviewer's incident fields and items.

    The console sends a draft with only the fields a reviewer edits (incident type, times, description, severity and the
    items). As a human save does (the console's ``humanRevisionContent``), the model-only fields (title, severity reason,
    confidence, location, timeline) come from the base version.
    """
    out: dict[str, Any] = dict(base or {})
    raw_base, raw_draft = out.get("incident"), draft.get("incident")
    base_incident: dict[str, Any] = raw_base if isinstance(raw_base, dict) else {}
    draft_incident: dict[str, Any] = raw_draft if isinstance(raw_draft, dict) else {}
    out["incident"] = {**base_incident, **draft_incident}
    for key, _, _ in ITEM_KINDS:
        if key in draft:
            out[key] = draft[key]
    return out


def report_view(
    report: Mapping[str, Any] | None, evidence: Sequence[Mapping[str, Any]] = (), *, retired: Iterable[str] = ()
) -> ReportView:
    """The answerable view of a report; a revision saved without its report falls back to its evidence entries."""
    report = report if isinstance(report, dict) else {}
    seen = {
        (str(e.get("item_type")), str(e.get("item_id"))): _num(e.get("box_seconds", e.get("timestamp_seconds")))
        for e in evidence
        if isinstance(e, dict)
    }
    items: list[Item] = []
    for key, id_field, kind in ITEM_KINDS:
        for raw in report.get(key) or []:
            if not isinstance(raw, dict) or not str(raw.get(id_field) or "").strip():
                continue
            item_id = str(raw[id_field]).strip()
            threat = _num(raw.get("threat_level"))
            items.append(
                Item(
                    kind=kind,
                    item_id=item_id,
                    name=str(raw.get("name") or "").strip(),
                    description=str(raw.get("description") or "").strip(),
                    label=str(raw.get("label") or "").strip(),
                    entity_type=str(raw.get("type") or "").strip().lower(),
                    seconds=_num(raw.get("timestamp_seconds"))
                    if _num(raw.get("timestamp_seconds")) is not None
                    else seen.get((kind, item_id)),
                    first_seen=_num(raw.get("first_seen_seconds")),
                    last_seen=_num(raw.get("last_seen_seconds")),
                    holder=str(raw.get("entity_id") or "").strip() or None if kind == "instrument" else None,
                    threat_level=round(threat) if threat is not None else None,
                )
            )
    if not items:
        for entry in evidence:
            if not isinstance(entry, dict) or entry.get("item_type") not in ("entity", "instrument", "asset"):
                continue
            if not str(entry.get("item_id") or "").strip():
                continue
            kind = str(entry["item_type"])
            name = str(entry.get("item_name") or "").strip()
            items.append(
                Item(
                    kind=kind,
                    item_id=str(entry["item_id"]).strip(),
                    name="" if kind == "entity" else name,
                    description=str(entry.get("description") or "").strip(),
                    label=str(entry.get("label") or "").strip(),
                    entity_type=(name or "person").lower() if kind == "entity" else "",
                    seconds=_num(entry.get("box_seconds", entry.get("timestamp_seconds"))),
                )
            )
    events = [
        e for e in report.get("timeline") or [] if isinstance(e, dict) and str(e.get("description") or "").strip()
    ]
    events.sort(key=lambda e: (_num(e.get("start_seconds")) is None, _num(e.get("start_seconds")) or 0.0))
    raw_incident = report.get("incident")
    incident: dict[str, Any] = raw_incident if isinstance(raw_incident, dict) else {}
    held = {i.item_id for i in items}
    return ReportView(
        incident=incident,
        items=tuple(items),
        timeline=tuple(events),
        retired=frozenset(str(i) for i in retired if str(i) not in held),
    )


def _id_pattern(known: Iterable[str]) -> tuple[re.Pattern[str], re.Pattern[str]]:
    """Each known id (longest first) and any other id-like token, as a whole token (never inside ``new-E2`` or
    ``E12``), and a parenthesised list of them."""
    alternatives = sorted({re.escape(i) for i in known if i}, key=len, reverse=True)
    core = "|".join([*alternatives, _ID_TOKEN])
    token = re.compile(rf"(?<![\w-])({core})(?![\w-])")
    group = re.compile(rf"\s*\(\s*((?:{core})(?:\s*(?:,|and|&)\s*(?:{core}))*)\s*\)")
    return token, group


def _replacement(token: str, mentions: Mapping[str, str], retired: frozenset[str]) -> str:
    """What an id-like token reads as: its item's mention; a neutral phrase for an id the report no longer holds
    (a draft's own ``new-`` id, an ``E``/``I`` id, or a removed item); an ``A``-number the report never used
    ("A4 paper", "the A1") is not a reference and stays as written."""
    if token in mentions:
        return mentions[token]
    bare = token.removeprefix("new-")
    if token.startswith("new-") or bare[:1] in "EI" or token in retired:
        return NEUTRAL.get(bare[:1], "an item")
    return token


def _resolve_plain(text: str, mentions: Mapping[str, str]) -> str:
    retired: frozenset[str] = getattr(mentions, "retired", frozenset())
    token, group = _id_pattern([*mentions, *retired])

    def drop(match: re.Match[str]) -> str:
        ids = token.findall(match.group(1))
        return "" if ids and all(i in mentions for i in ids) else match.group(0)

    return token.sub(lambda m: _replacement(m.group(1), mentions, retired), group.sub(drop, text))


def resolve_references(text: str, mentions: Mapping[str, str]) -> str:
    """Report text with each item-id reference resolved to that item's mention.

    ``"E1 strikes E2 with I1."`` becomes ``"The man in black strikes the cashier with the crowbar."``; a parenthesised id
    after a description (``"a man (E1)"``) is dropped, since the description already names it. An id the report no
    longer holds reads as a neutral "a person", "an object" or "an item" (:func:`_replacement`).
    """

    return _sentence_case(_resolve_plain(text, mentions))


def referenced_ids(text: str, known: Iterable[str]) -> list[str]:
    """The known item ids a piece of report text refers to, in order of first reference."""
    known = set(known)
    token, _ = _id_pattern(known)
    out: list[str] = []
    for item_id in token.findall(text):
        if item_id in known and item_id not in out:
            out.append(item_id)
    return out


def replaced_ids(text: str, mentions: Mapping[str, str]) -> list[str]:
    """The id-like tokens :func:`resolve_references` would rewrite in ``text`` (known ids and ids no longer held)."""
    retired: frozenset[str] = getattr(mentions, "retired", frozenset())
    token, _ = _id_pattern([*mentions, *retired])
    out: list[str] = []
    for found in token.findall(text):
        if _replacement(found, mentions, retired) != found and found not in out:
            out.append(found)
    return out


@dataclass(frozen=True)
class ComposedAnswer:
    """A composed answer: the prose, the item ids it relies on (for citations), and whether the report could answer."""

    text: str
    item_ids: tuple[str, ...]
    insufficient_evidence: bool
    kind: str


def _event_sentence(event: Mapping[str, Any], mentions: Mapping[str, str], *, last: bool) -> str:
    body = resolve_references(str(event.get("description") or "").strip().rstrip("."), mentions)
    start, end = clock(event.get("start_seconds")), clock(event.get("end_seconds"))
    if not start:
        return f"{_capitalize(body)}."
    when = f"from {start} to {end}" if end and end != start else f"at {start}"
    return f"{'Finally, ' + when if last else _capitalize(when)}, {_lower_first(body)}."


def _window(view: ReportView) -> str:
    start, end = clock(view.incident.get("start_timestamp")), clock(view.incident.get("end_timestamp"))
    if start and end and start != end:
        return f"from {start} to {end}"
    return f"at {start}" if start else ""


def _chronology(view: ReportView, targets: Sequence[Item]) -> ComposedAnswer:
    mentions = view.mentions()
    known = [i.item_id for i in view.items]
    events = list(view.timeline)
    lead_subject = ""
    if targets:
        target_ids = {t.item_id for t in targets}
        target_words = set().union(*(t.name_words for t in targets))
        about = [
            e
            for e in events
            if target_ids & set(referenced_ids(str(e.get("description") or ""), known))
            or target_words & _words(str(e.get("description") or ""))
        ]
        if about:
            events = about
            lead_subject = _join(list(dict.fromkeys(mentions[t.item_id] for t in targets)))
    kind = str(view.incident.get("type") or "").strip()
    if events:
        shown = events[:MAX_EVENTS]
        sentences = [
            _event_sentence(e, mentions, last=(n == len(shown) - 1 and len(shown) > 1 and len(events) <= MAX_EVENTS))
            for n, e in enumerate(shown)
        ]
        if len(events) > MAX_EVENTS:
            sentences.append(f"The timeline records {_count(len(events) - MAX_EVENTS, 'more event', 'more events')}.")
        if lead_subject:
            lead = f"Here is what the report records about {lead_subject}, in order:"
        elif kind:
            window = _window(view)
            lead = f"The report describes {_article(kind, definite=False)}{' ' + window if window else ''}. In order:"
        else:
            lead = "Here is what the report records, in order:"
        cited = []
        for event in shown:
            cited += [i for i in referenced_ids(str(event.get("description") or ""), known) if i not in cited]
        cited = cited or [i.item_id for i in view.items if i.is_person]
        return ComposedAnswer(" ".join([lead, *sentences]), tuple(cited), False, "chronology")
    description = str(view.incident.get("description") or "").strip()
    if description:
        window = _window(view)
        text = (
            "The report has no event-by-event timeline. It describes the incident as follows: "
            f"{resolve_references(description, mentions).rstrip('.')}."
            + (f" It runs {window} in the video." if window else "")
        )
        cited = referenced_ids(description, known) or [i.item_id for i in view.items if i.is_person]
        return ComposedAnswer(text, tuple(cited), False, "chronology")
    return ComposedAnswer("This report doesn't describe what happened.", (), True, "chronology")


def _people(view: ReportView, targets: Sequence[Item], *, how_many: bool) -> ComposedAnswer:
    mentions = view.mentions()
    people = [i for i in view.items if i.is_person]
    chosen = [t for t in targets if t.is_person]
    if chosen:
        details = [f"{t.introduce(mentions)}{', ' + t.seen() if t.seen() else ''}" for t in chosen]
        return ComposedAnswer(
            f"The report describes {_join(details)}.", tuple(t.item_id for t in chosen), False, "people"
        )
    if not people:
        return ComposedAnswer("This report records no people.", (), False, "people")
    details = [f"{p.introduce(mentions)}{' (' + p.seen() + ')' if p.seen() else ''}" for p in people]
    lead = _capitalize(_count(len(people), "person is", "people are"))
    text = f"{lead} involved: {_join(details)}."
    others = [i for i in view.items if i.kind == "entity" and not i.is_person]
    if others and not how_many:
        text += f" The report also records {_join([o.introduce(mentions) for o in others])}."
    cited = [p.item_id for p in people] + ([o.item_id for o in others] if others and not how_many else [])
    return ComposedAnswer(text, tuple(cited), False, "people")


def _object_phrase(view: ReportView, item: Item) -> str:
    mentions = view.mentions()
    text = item.introduce(mentions)
    holder = view.item(item.holder)
    if holder is not None:
        text += f", held by {mentions[holder.item_id]}"
    if item.threat_level is not None:
        text += f" (threat level {item.threat_level} of 5)"
    moment = item.seen()
    return f"{text}, {moment}" if moment else text


def _about(view: ReportView, targets: Sequence[Item]) -> ComposedAnswer:
    """A yes/no question about what a named item did or underwent ("did the hooded person steal anything?", "was the
    door broken?"): the report's events about it, in order, without a yes or no it does not state. With no such event
    the report does not answer it."""
    answer = _chronology(view, targets)
    if answer.kind == "chronology" and answer.text.startswith("Here is what the report records about"):
        return answer
    mentions = view.mentions()
    named = _join(list(dict.fromkeys(mentions[t.item_id] for t in targets)))
    return ComposedAnswer(
        f"The report records {named} but says nothing more about {'them' if len(targets) > 1 else 'it'} that answers "
        "this.",
        (),
        True,
        "about",
    )


def _absent(view: ReportView, unmatched: Sequence[str]) -> ComposedAnswer:
    """A question about something the report never names ("is there a dog?"): it says so, and what it does record."""
    people = [i for i in view.items if i.is_person]
    mentions = view.mentions()
    objects = list(dict.fromkeys(mentions[i.item_id] for i in view.items if i.kind != "entity"))
    text = f"This report doesn't mention {_join([chr(34) + w + chr(34) for w in unmatched])}."
    if view.items:
        text += (
            f" It records {_count(len(people), 'person', 'people')} and "
            f"{_count(len(objects), 'object', 'objects')}" + (f": {_join(objects)}." if objects else ".")
        )
    return ComposedAnswer(text, (), True, "absent")


def _holders(view: ReportView, targets: Sequence[Item]) -> ComposedAnswer | None:
    """Who held the named instruments ("who held the crowbar?"), when the report says."""
    held: list[tuple[Item, Item]] = []
    for target in targets:
        holder = view.item(target.holder) if target.kind == "instrument" else None
        if holder is not None:
            held.append((target, holder))
    if not held:
        return None
    mentions = view.mentions()
    sentences = [f"{_capitalize(mentions[t.item_id])} is held by {h.introduce(mentions)}." for t, h in held]
    cited = list(dict.fromkeys(i for t, h in held for i in (t.item_id, h.item_id)))
    return ComposedAnswer(" ".join(sentences), tuple(cited), False, "people")


def _objects(
    view: ReportView, targets: Sequence[Item], unmatched: Sequence[str], *, presence: bool, how_many: bool
) -> ComposedAnswer:
    instruments = [i for i in view.items if i.kind == "instrument"]
    assets = [i for i in view.items if i.kind == "asset"]
    chosen = [t for t in targets if t.kind != "entity"] or list(targets)
    if chosen:
        phrases = [_object_phrase(view, t) for t in chosen]
        opener = "Yes: the report records" if presence else "The report records"
        return ComposedAnswer(f"{opener} {_join(phrases)}.", tuple(t.item_id for t in chosen), False, "objects")
    sentences = []
    if unmatched and presence:
        sentences.append(f"This report doesn't mention {_join([chr(34) + w + chr(34) for w in unmatched])}.")
    if how_many:
        sentences.append(
            f"It records {_count(len(instruments), 'instrument', 'instruments')} and "
            f"{_count(len(assets), 'asset', 'assets')}."
        )
    if instruments:
        sentences.append(f"Objects used: {_join([_object_phrase(view, i) for i in instruments])}.")
    if assets:
        sentences.append(f"Property involved: {_join([_object_phrase(view, a) for a in assets])}.")
    if not instruments and not assets:
        sentences.append("It records no objects." if unmatched and presence else "This report records no objects.")
    cited = [i.item_id for i in instruments + assets]
    return ComposedAnswer(_sentence_case(" ".join(sentences)), tuple(cited), False, "objects")


def _when(view: ReportView, targets: Sequence[Item]) -> ComposedAnswer:
    timed = [t for t in targets if t.seen()]
    if timed:
        mentions = view.mentions()
        by_mention: dict[str, list[str]] = {}
        for t in timed:  # one sentence per thing named (a till listed as instrument and asset is one thing)
            by_mention.setdefault(mentions[t.item_id], []).append(t.seen().removeprefix("seen "))
        text = " ".join(
            f"{_capitalize(m)} is seen {_join(list(dict.fromkeys(times)))}." for m, times in by_mention.items()
        )
        return ComposedAnswer(text, tuple(t.item_id for t in timed), False, "time")
    start, end = _num(view.incident.get("start_timestamp")), _num(view.incident.get("end_timestamp"))
    if start is None:
        return ComposedAnswer("This report doesn't record when the incident happens.", (), True, "time")
    if end is None or end <= start:
        return ComposedAnswer(f"The incident starts at {clock(start)} in the video.", (), False, "time")
    length = round(end - start)
    duration = f"{length} seconds" if length < 120 else f"{length // 60} minutes {length % 60} seconds"
    return ComposedAnswer(
        f"The incident runs from {clock(start)} to {clock(end)} in the video ({duration}).", (), False, "time"
    )


def _severity(view: ReportView) -> ComposedAnswer:
    level = _num(view.incident.get("severity_level"))
    if level is None:
        return ComposedAnswer("This report doesn't rate the severity.", (), True, "severity")
    band = "low" if level <= 2 else "medium" if level == 3 else "high"
    reason = str(view.incident.get("severity_reason") or "").strip().rstrip(".")
    text = f"The report rates the severity {round(level)} of 5 ({band})"
    if reason:
        text += f": {_lower_first(resolve_references(reason, view.mentions()))}"
    return ComposedAnswer(text + ".", tuple(referenced_ids(reason, [i.item_id for i in view.items])), False, "severity")


def _incident_type(view: ReportView) -> ComposedAnswer:
    kind = str(view.incident.get("type") or "").strip()
    if not kind:
        return ComposedAnswer("This report doesn't classify the incident.", (), True, "type")
    title = str(view.incident.get("title") or "").strip().rstrip(".")
    text = f"It is {_article(kind, definite=False)}" + (
        f": {resolve_references(title, view.mentions())}." if title else "."
    )
    return ComposedAnswer(text, (), False, "type")


def compose(question: str, view: ReportView) -> ComposedAnswer:
    """The answer to one question about one report version, or a statement that the report does not say."""
    asks_ids = bool(_ASKS_IDS.search(question.lower()))
    q = _ASKS_IDS.sub(" ", question.lower())  # "the item ids of the people" asks about the people
    words = _words(q)
    known_words = set().union(
        *(i.words for i in view.items), *(_words(str(e.get("description") or "")) for e in view.timeline)
    )
    known_words |= _words(
        " ".join(str(view.incident.get(k) or "") for k in ("type", "title", "description", "location"))
    )
    intent_words = {
        w
        for pattern in (_CHRONOLOGY, _PEOPLE, _OBJECTS, _WHEN, _SEVERITY, _TYPE)
        for match in pattern.finditer(q)
        for w in _words(match.group(0))
    }
    # Category words ("tools", "weapons") ask for every item of a kind; only other words name a particular item, and
    # an item whose name or label matches beats one whose description merely mentions the word.
    naming = words - intent_words
    targets = [i for i in view.items if naming & i.name_words] or [i for i in view.items if naming & i.words]
    # The words the report never uses, as the question wrote them (a singular form that matches counts as used).
    unmatched = [
        t for t in dict.fromkeys(_tokens(q)) if not _variants(t) & known_words and not _variants(t) & intent_words
    ]
    how_many = bool(_HOW_MANY.search(q))
    if _TIME_FIRST.search(q):
        answer = _when(view, targets)
    elif _CHRONOLOGY.search(q):
        answer = _chronology(view, targets)
    elif _TYPE.search(q):
        answer = _incident_type(view)
    elif _SEVERITY.search(q) and not targets:
        answer = _severity(view)
    elif _WHEN.search(q):
        answer = _when(view, targets)
    elif _PRESENCE.search(q) and unmatched and not targets:
        answer = _absent(view, unmatched)
    elif _PRESENCE.search(q) and not _EXISTENCE.search(q) and targets:
        answer = _about(view, targets)
    elif (
        _PEOPLE.search(q) and targets and any(not t.is_person for t in targets) and (holders := _holders(view, targets))
    ):
        answer = holders
    elif _PEOPLE.search(q) and not any(not t.is_person for t in targets) and not (_OBJECTS.search(q) and unmatched):
        answer = _people(view, targets, how_many=how_many)
    elif _OBJECTS.search(q) or targets or (_PRESENCE.search(q) and unmatched):
        answer = _objects(
            view, targets, unmatched if not targets else [], presence=bool(_EXISTENCE.search(q)), how_many=how_many
        )
    else:
        about = [e for e in view.timeline if words & _words(str(e.get("description") or ""))]
        if about:
            answer = _chronology(ReportView(view.incident, view.items, tuple(about)), [])
        else:
            missing = (
                f" doesn't mention {_join([chr(34) + w + chr(34) for w in unmatched])}" if unmatched else " doesn't say"
            )
            answer = ComposedAnswer(
                f"This report{missing}. You can ask what happened, who was involved, which objects were involved, or "
                "when it happened.",
                (),
                True,
                "unanswerable",
            )
    if asks_ids and answer.item_ids:
        mentions = view.mentions()
        listed = _join([f"{i} is {mentions[i]}" for i in answer.item_ids if i in mentions])
        answer = ComposedAnswer(
            f"{answer.text} Item ids: {listed}.", answer.item_ids, answer.insufficient_evidence, answer.kind
        )
    return answer


def _incident_phrase(incident: Mapping[str, Any]) -> str:
    kind = str(incident.get("incident_type") or "").strip()
    title = str(incident.get("title") or "").strip().rstrip(".")
    phrase = _article(kind, definite=False) if kind else "an incident"
    return f'{phrase} ("{title}")' if title else phrase


def compose_hits(hits: Sequence[Mapping[str, Any]]) -> str:
    """Search hits (incident evidence items) as prose, grouped by incident, with no ids."""
    groups: dict[str, list[Mapping[str, Any]]] = {}
    incidents: dict[str, Mapping[str, Any]] = {}
    for hit in hits:
        incident = hit.get("incident") or {}
        key = str(incident.get("incident_id") or "")
        groups.setdefault(key, []).append(hit.get("item") or {})
        incidents.setdefault(key, incident)

    def item_phrase(item: Mapping[str, Any]) -> str:
        fields = (
            ("label", "name") if item.get("item_type") == "entity" else ("name", "label")
        )  # a person's type is no name
        name = next((str(item[f]) for f in fields if item.get(f)), "") or str(item.get("description") or "item")
        moment = clock(item.get("timestamp_seconds"))
        return _article(name, definite=False) + (f" (seen at {moment})" if moment else "")

    if len(groups) == 1:
        key, items = next(iter(groups.items()))
        return (
            f"I found {_count(len(items), 'matching item', 'matching items')} in "
            f"{_incident_phrase(incidents[key])}: {_join([item_phrase(i) for i in items])}."
        )
    parts = [
        f"In {_incident_phrase(incidents[k])}: {_join([item_phrase(i) for i in items])}." for k, items in groups.items()
    ]
    return " ".join([f"I found matching evidence in {_count(len(groups), 'incident', 'incidents')}.", *parts])
