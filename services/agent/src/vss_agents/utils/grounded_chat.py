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

"""Grounded chat about one incident report: context, a strict-JSON answer, and checked citations (chat-contract-v2).

The answer comes from a chat model that sees only one report version (the official one, a chosen one, or a reviewer's
unsaved draft): its incident fields, its entities, instruments and assets with their ids, and its timeline, sorted by
time with ``m:ss`` times.
- **Grounding:** the model must answer from that context alone, cite the item ids it relied on, and set
  ``insufficient_evidence`` when the report cannot answer (including a question that assumes something the report
  contradicts). Every cited id is checked against the context; an unknown id is dropped and counted, never shown.
- **Ids stay out of the prose:** the prompt has the model name people and objects by their descriptions and tell a
  what-happened question as the timeline in order. A known item id the model still writes in the prose (when the
  question did not ask for ids) is resolved to that item's description, as the template mode resolves the
  timeline's references (:func:`vss_agents.utils.report_answers.resolve_references`), and listed in
  ``ids_resolved``, so the leak is measured, not hidden.

Everything here is pure except :class:`OpenAIChatModel`, the default model port (an OpenAI-compatible endpoint, as
the rest of the agent uses), whose HTTP client and clock are injectable. The system prompt is the registered
``chat/incident-answer`` (``contracts/prompts/``).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from dataclasses import field
import json
import os
import re
import time
from typing import TYPE_CHECKING
from typing import Any
from typing import Protocol

import httpx

from vss_agents import incident_contract
from vss_agents.utils import report_answers

if TYPE_CHECKING:
    from collections.abc import Callable
    from collections.abc import Iterable
    from collections.abc import Mapping
    from collections.abc import Sequence

PROMPT_ID = "chat/incident-answer"
DEFAULT_MODEL = "nvidia/nemotron-3-ultra"
MODEL_ENV = "INCIDENT_CHAT_MODEL"
BASE_URL_ENVS = ("INCIDENT_CHAT_BASE_URL", "LLM_BASE_URL")
API_KEY_ENVS = ("INCIDENT_CHAT_API_KEY", "OPENAI_API_KEY")
MAX_HISTORY_MESSAGES = 8
ITEM_KINDS = {
    "entities": ("entity_id", "entity"),
    "instruments": ("instrument_id", "instrument"),
    "assets": ("asset_id", "asset"),
}
ANSWER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["answer", "citations", "insufficient_evidence"],
    "properties": {
        "answer": {"type": "string"},
        "citations": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["item_id"],
                "properties": {"item_id": {"type": "string"}},
            },
        },
        "insufficient_evidence": {"type": "boolean"},
    },
}


class ChatModelError(RuntimeError):
    """The chat model failed or answered outside the strict schema."""


class ChatModel(Protocol):
    model: str

    async def complete(self, messages: Sequence[Mapping[str, str]], schema: Mapping[str, Any]) -> dict[str, Any]: ...


@dataclass
class OpenAIChatModel:
    """The default chat model: ``<base>/v1/chat/completions`` with a strict JSON schema, temperature 0."""

    model: str = field(default_factory=lambda: os.environ.get(MODEL_ENV, "").strip() or DEFAULT_MODEL)
    base_url: str = field(default_factory=lambda: next((os.environ[e] for e in BASE_URL_ENVS if os.environ.get(e)), ""))
    api_key: str = field(default_factory=lambda: next((os.environ[e] for e in API_KEY_ENVS if os.environ.get(e)), ""))
    timeout_seconds: float = 60.0
    client_factory: Callable[..., httpx.AsyncClient] = httpx.AsyncClient
    clock: Callable[[], float] = time.perf_counter
    last_usage: dict[str, Any] = field(default_factory=dict)

    def request_body(self, messages: Sequence[Mapping[str, str]], schema: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "model": self.model,
            "messages": [dict(m) for m in messages],
            "stream": False,
            "temperature": 0,
            "max_tokens": 1024,
            "chat_template_kwargs": {"enable_thinking": False},
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "incident_chat_answer", "schema": dict(schema), "strict": True},
            },
        }

    async def complete(self, messages: Sequence[Mapping[str, str]], schema: Mapping[str, Any]) -> dict[str, Any]:
        if not self.base_url:
            raise ChatModelError(f"no chat model endpoint ({' or '.join(BASE_URL_ENVS)})")
        root = self.base_url.rstrip("/").removesuffix("/v1")
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        started = self.clock()
        try:
            async with self.client_factory(timeout=self.timeout_seconds) as client:
                response = await client.post(
                    f"{root}/v1/chat/completions", json=self.request_body(messages, schema), headers=headers
                )
        except httpx.HTTPError as exc:
            raise ChatModelError(f"chat model call failed: {type(exc).__name__}: {exc}") from exc
        if response.status_code >= 400:
            raise ChatModelError(f"chat model returned HTTP {response.status_code}: {response.text[:300]}")
        body = response.json()
        self.last_usage = {**(body.get("usage") or {}), "elapsed_ms": round((self.clock() - started) * 1000.0, 1)}
        try:
            content = body["choices"][0]["message"]["content"]
            parsed = json.loads(content)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise ChatModelError(f"chat model reply is not the strict JSON answer: {exc}") from exc
        if not isinstance(parsed, dict):
            raise ChatModelError("chat model reply is not a JSON object")
        return parsed


def report_context(report: Mapping[str, Any], evidence: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """What the model may know: the report's incident, items (with ids and evidence seconds) and timeline.

    The timeline is sorted by start second (stable for ties) and each event carries its start and end as ``m:ss``,
    so a chronological answer needs no arithmetic.
    """
    seconds = {str(e.get("item_id")): e.get("box_seconds", e.get("timestamp_seconds")) for e in evidence}
    items: list[dict[str, Any]] = []
    for kind, (id_field, item_type) in ITEM_KINDS.items():
        for item in report.get(kind) or []:
            item_id = str(item.get(id_field) or "")
            if item_id:
                fields = {k: v for k, v in item.items() if k != id_field and v not in (None, "", [])}
                items.append(
                    {"item_id": item_id, "item_type": item_type, **fields, "evidence_second": seconds.get(item_id)}
                )
    incident = {k: v for k, v in (report.get("incident") or {}).items() if v not in (None, "")}
    events = [e for e in report.get("timeline") or [] if isinstance(e, dict)]
    events.sort(key=lambda e: (_seconds(e.get("start_seconds")) is None, _seconds(e.get("start_seconds")) or 0.0))
    timeline = [
        {
            **e,
            **{
                f"{k}_clock": report_answers.clock(e.get(f"{k}_seconds"))
                for k in ("start", "end")
                if e.get(f"{k}_seconds") is not None
            },
        }
        for e in events
    ]
    return {"incident": incident, "items": items, "timeline": timeline}


def _seconds(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def item_mentions(context: Mapping[str, Any], retired: Iterable[str] = ()) -> report_answers.Mentions:
    """How a person reading the answer knows each item: its label or name, else its description's first clause.

    ``retired`` are ids a draft removed from the base version; like any other id the report does not hold, they read
    neutrally.
    """
    mentions = report_answers.Mentions()
    for item in context.get("items") or []:
        mentions[item["item_id"]] = report_answers.Item(
            kind=str(item.get("item_type") or ""),
            item_id=item["item_id"],
            name=str(item.get("name") or ""),
            description=str(item.get("description") or ""),
            label=str(item.get("label") or ""),
            entity_type=str(item.get("type") or "").lower(),
        ).mention()
    mentions.retired = frozenset(retired)
    return mentions


def chat_messages(context: Mapping[str, Any], history: Sequence[Mapping[str, str]]) -> list[dict[str, str]]:
    """The system prompt, the report context, then the latest turns of the conversation (system turns left out)."""
    turns = [{"role": m["role"], "content": m["content"]} for m in history if m["role"] in ("user", "assistant")]
    return [
        {"role": "system", "content": incident_contract.prompt(PROMPT_ID)},
        {"role": "user", "content": "REPORT:\n" + json.dumps(context, ensure_ascii=False, sort_keys=True)},
        *turns[-MAX_HISTORY_MESSAGES:],
    ]


@dataclass(frozen=True)
class GroundedAnswer:
    content: str
    item_ids: tuple[str, ...]
    insufficient_evidence: bool
    dropped_citations: tuple[str, ...] = ()
    ids_resolved: tuple[str, ...] = ()


def check_answer(
    reply: Mapping[str, Any], context: Mapping[str, Any], *, question: str = "", retired: Iterable[str] = ()
) -> GroundedAnswer:
    """The model's answer with its citations checked against the context; unknown ids are dropped and counted.

    Unless the question asks for ids, an id written in the prose is resolved: a known item id to the item's
    description, any other id-like token ("E7", a draft's "new-E3") to a neutral "a person" or "an object". Each is
    listed in ``ids_resolved``. An empty answer is a model failure (a chat history cannot carry an empty turn).
    """
    if not isinstance(reply.get("answer"), str) or not isinstance(reply.get("insufficient_evidence"), bool):
        raise ChatModelError("chat model reply lacks the answer or the insufficient_evidence flag")
    if not reply["answer"].strip():
        raise ChatModelError("chat model returned an empty answer")
    known = {item["item_id"] for item in context["items"]}
    cited: list[str] = []
    dropped: list[str] = []
    for citation in reply.get("citations") or []:
        item_id = str((citation or {}).get("item_id") or "") if isinstance(citation, dict) else ""
        if item_id in known and item_id not in cited:
            cited.append(item_id)
        elif item_id and item_id not in known:
            dropped.append(item_id)
    insufficient = bool(reply["insufficient_evidence"])
    content = reply["answer"].strip()
    mentions = item_mentions(context, retired)
    leaked: list[str] = [] if report_answers.asks_for_ids(question) else report_answers.replaced_ids(content, mentions)
    if leaked:
        content = report_answers.resolve_references(content, mentions)
    return GroundedAnswer(
        content=content,
        item_ids=() if insufficient else tuple(cited),
        insufficient_evidence=insufficient,
        dropped_citations=tuple(dropped),
        ids_resolved=tuple(leaked),
    )


# ---------------------------------------------------------------------------
# Library scope: a planned search over every incident's report, then an answer
# ---------------------------------------------------------------------------
#
# The model only plans (turns the question into filters) and, for a question that is not a count, writes the answer
# from the best matching reports. Code applies the filters, counts, and composes every count and breakdown answer, so
# a number in a library answer is always a code count. A count whose question the filters do not capture whole (a
# condition left in the search phrase, or a filter value the library does not use) is refused, never estimated.

PLAN_PROMPT_ID = "chat/library-plan"
LIBRARY_PROMPT_ID = "chat/library-answer"
MAX_LIBRARY_REPORTS = 8
MAX_LISTED = 5
SEVERITY_BANDS = {"low": (1, 2), "medium": (3, 3), "high": (4, 5)}
SEVERITY_RANGES = {"high": "levels 4-5", "medium": "level 3", "low": "levels 1-2"}
GROUPINGS = ("incident_type", "severity")
PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["incident_type", "severity", "instrument", "asset", "query", "wants_count", "group_by"],
    "properties": {
        "incident_type": {"type": ["string", "null"]},
        "severity": {"type": ["string", "null"], "enum": ["low", "medium", "high", None]},
        "instrument": {"type": ["string", "null"]},
        "asset": {"type": ["string", "null"]},
        "query": {"type": "string"},
        "wants_count": {"type": "boolean"},
        "group_by": {"type": ["string", "null"], "enum": [*GROUPINGS, None]},
    },
}
LIBRARY_ANSWER_SCHEMA: dict[str, Any] = {
    **ANSWER_SCHEMA,
    "properties": {
        **ANSWER_SCHEMA["properties"],
        "citations": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["incident_id", "item_id"],
                "properties": {"incident_id": {"type": "string"}, "item_id": {"type": "string"}},
            },
        },
    },
}
# Words a search phrase may hold without narrowing the reports it is about ("incidents", "how many", "recorded").
_GENERIC_TEXT = (
    "a an the of in on at for to and or with by from as is are was were be been there that which what how many much "
    "number count counts total all any every each incident incidents report reports library case cases video videos "
    "clip clips event events happen happened happens occur occurred occurs record records recorded"
)
_GENERIC_WORDS = frozenset(_GENERIC_TEXT.split())
# Words a breakdown question uses to ask for the breakdown itself ("which type happened most often").
_GROUPING_TEXT = (
    "type types kind kinds category categories severity severities level levels most least common often frequent "
    "frequently breakdown per"
)
_GROUPING_WORDS = frozenset(_GROUPING_TEXT.split())
_DROPPED_AS = {"incident_type": "an incident type", "severity": "a severity", "group_by": "a breakdown"}


@dataclass(frozen=True)
class LibraryReport:
    """One incident's searchable report version: the official one, else the newest."""

    incident_id: str
    version_id: int
    payload: Mapping[str, Any]
    evidence: Sequence[Mapping[str, Any]] = ()


class LibraryIndex(Protocol):
    async def reports(self) -> list[LibraryReport]: ...


def _incident_types() -> list[str]:
    return list(incident_contract.load_schema()["properties"]["incident"]["properties"]["type"]["enum"])


def _words(text: Any) -> set[str]:
    return {w for w in "".join(c if c.isalnum() else " " for c in str(text or "").lower()).split() if len(w) > 2}


def _join(parts: Sequence[str]) -> str:
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1] if parts else ""


def _number(n: int) -> str:
    return report_answers.NUMBER_WORDS[n] if 0 < n < len(report_answers.NUMBER_WORDS) else str(n)


def plan_messages(question: str, history: Sequence[Mapping[str, str]]) -> list[dict[str, str]]:
    turns = [{"role": m["role"], "content": m["content"]} for m in history if m["role"] in ("user", "assistant")]
    return [
        {
            "role": "system",
            "content": incident_contract.prompt(PLAN_PROMPT_ID) + f"\nIncident types: {', '.join(_incident_types())}.",
        },
        *turns[-MAX_HISTORY_MESSAGES:-1],
        {"role": "user", "content": question},
    ]


def _phrase(value: Any) -> str | None:
    """A planner's object phrase in plain lower case, or None when it holds no word to match."""
    text = " ".join(str(value).lower().split()) if isinstance(value, str) else ""
    return text if report_answers.term_sets(text) else None


def check_plan(plan: Mapping[str, Any]) -> dict[str, Any]:
    """The planner's filters, normalised. An incident type, severity or breakdown the library does not use is not
    applied, and is listed in ``dropped`` (a count that depends on it cannot be exact)."""
    if not isinstance(plan.get("query"), str) or not isinstance(plan.get("wants_count"), bool):
        raise ChatModelError("chat model plan lacks the query or the wants_count flag")
    dropped: dict[str, str] = {}

    def keep(key: str, allowed: Iterable[str]) -> str | None:
        value = plan.get(key)
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        text = " ".join(str(value).replace("_", " ").lower().split())
        known = {" ".join(option.replace("_", " ").lower().split()): option for option in allowed}
        if text in known:
            return known[text]
        dropped[key] = str(value).strip()
        return None

    return {
        "incident_type": keep("incident_type", _incident_types()),
        "severity": keep("severity", SEVERITY_BANDS),
        "instrument": _phrase(plan.get("instrument")),
        "asset": _phrase(plan.get("asset")),
        "query": plan["query"].strip(),
        "wants_count": plan["wants_count"],
        "group_by": keep("group_by", GROUPINGS),
        "dropped": dropped,
    }


def count_exact(plan: Mapping[str, Any]) -> bool:
    """Whether the filters capture the whole question, so the number of matching reports is its exact answer: no
    filter value was dropped, and the search phrase holds nothing but generic words and the filters' own words."""
    if plan["dropped"]:
        return False
    allowed = set(_GENERIC_WORDS) | (_GROUPING_WORDS if plan["group_by"] else set())
    for key in ("incident_type", "instrument", "asset"):
        allowed |= {word for term in report_answers.term_sets(plan[key]) for word in term}
    if plan["severity"]:
        allowed |= {plan["severity"], "severity", "severe", "level", "levels"}
    return all(term & allowed for term in report_answers.term_sets(plan["query"]))


def _object_words(item: Mapping[str, Any]) -> set[str]:
    text = " ".join(str(item.get(key) or "") for key in ("name", "label", "description"))
    return {word for term in report_answers.term_sets(text) for word in term}


def _has_term(term: frozenset[str], words: set[str]) -> bool:
    """A whole word, or the head of a compound ("gun" in "handgun", "knife" in "pocketknife"); never a word's start or
    middle ("bat" is not in "bathroom")."""
    return bool(term & words) or any(
        len(part) >= 3 and len(word) - len(part) >= 3 and word.endswith(part) for part in term for word in words
    )


def involves(report: LibraryReport, phrase: str) -> bool:
    """Whether one instrument or asset of the report is named by every word of ``phrase``.

    Instruments and assets are searched alike: a planner cannot know how a report filed an object (a thrown chair is
    an instrument, a broken one an asset), and a wrong guess must not turn into a wrong count of zero.
    """
    terms = report_answers.term_sets(phrase)
    objects = [*(report.payload.get("instruments") or []), *(report.payload.get("assets") or [])]
    return bool(terms) and any(
        all(_has_term(term, _object_words(item)) for term in terms) for item in objects if isinstance(item, dict)
    )


def _band(level: Any) -> str | None:
    if not isinstance(level, int) or isinstance(level, bool):
        return None
    return next((band for band, (low, high) in SEVERITY_BANDS.items() if low <= level <= high), None)


def matches(report: LibraryReport, plan: Mapping[str, Any]) -> bool:
    incident = report.payload.get("incident") or {}
    if plan["incident_type"] and incident.get("type") != plan["incident_type"]:
        return False
    if plan["severity"] and _band(incident.get("severity_level")) != plan["severity"]:
        return False
    return all(involves(report, plan[key]) for key in ("instrument", "asset") if plan[key])


def rank(reports: Sequence[LibraryReport], query: str) -> list[LibraryReport]:
    """Reports by word overlap of ``query`` with their title, description and item names (ties by incident id)."""
    terms = _words(query)

    def score(report: LibraryReport) -> int:
        incident = report.payload.get("incident") or {}
        names = " ".join(
            str(i.get("name") or i.get("label") or i.get("description") or "")
            for kind in ("entities", "instruments", "assets")
            for i in report.payload.get(kind) or []
        )
        return len(terms & _words(f"{incident.get('title')} {incident.get('description')} {names}"))

    return sorted(reports, key=lambda r: (-score(r), r.incident_id))


def _incident_fields(report: LibraryReport) -> dict[str, Any]:
    incident = report.payload.get("incident") or {}
    return {"type": incident.get("type"), "title": incident.get("title")}


def library_mention(report: LibraryReport) -> str:
    """How an answer names a library report: :func:`incident_mention` of its incident."""
    return incident_mention(_incident_fields(report))


def _scope(plan: Mapping[str, Any]) -> tuple[str, str]:
    """The plan's filters as words: those before "report" ("high-severity assault") and the condition after it
    ("involving a knife")."""
    before = " ".join(
        word
        for word in (f"{plan['severity']}-severity" if plan["severity"] else "", plan["incident_type"] or "")
        if word
    )
    things = []
    for key in ("instrument", "asset"):
        phrase = plan[key]
        if phrase:
            first, last = phrase.split()[0], phrase.split()[-1]
            plural = last.endswith("s") and not last.endswith("ss")
            things.append(
                phrase
                if plural or first.isdigit() or first in ("a", "an", "the", "some", "any")
                else f"{'an' if phrase[0] in 'aeiou' else 'a'} {phrase}"
            )
    return before, (f" involving {_join(things)}" if things else "")


def count_answer(plan: Mapping[str, Any], ranked: Sequence[LibraryReport]) -> str:
    """How many reports match the plan's filters (a code count), naming them (up to :data:`MAX_LISTED`)."""
    before, condition = _scope(plan)
    noun = f"{before} report" if before else "report"
    n = len(ranked)
    if n == 0:
        return f"The library holds no {noun}s{condition}."
    head = f"The library holds {_number(n)} {noun}{'' if n == 1 else 's'}{condition}"
    names = [library_mention(r) for r in ranked]
    if n <= MAX_LISTED:
        return f"{head}: {_join(names)}."
    return f"{head}, including {_join(names[:3])}."


def breakdown_answer(plan: Mapping[str, Any], ranked: Sequence[LibraryReport]) -> str:
    """How the matching reports divide by incident type or severity band (code counts)."""
    before, condition = _scope(plan)
    n = len(ranked)
    if n <= 1:
        answer = count_answer(plan, ranked)
        level = (ranked[0].payload.get("incident") or {}).get("severity_level") if ranked else None
        band = _band(level)
        if band and plan["group_by"] == "severity":
            answer = f"{answer[:-1]}, which is {band} severity (level {level})."
        return answer
    lead = f"Of the {_number(n)} {before + ' ' if before else ''}reports in the library{condition}, "
    if plan["group_by"] == "severity":
        bands = Counter(_band((r.payload.get("incident") or {}).get("severity_level")) for r in ranked)
        parts: list[str] = []
        for band, span in SEVERITY_RANGES.items():
            if bands[band]:
                verb = ("is " if bands[band] == 1 else "are ") if not parts else ""
                parts.append(f"{_number(bands[band])} {verb}{band} severity ({span})")
        if bands[None]:
            parts.append(f"{_number(bands[None])} {'has' if bands[None] == 1 else 'have'} no severity level")
        return f"{lead}{_join(parts)}."
    types = Counter(str((r.payload.get("incident") or {}).get("type") or "") for r in ranked)
    untyped = types.pop("", 0)
    ordered = sorted(types.items(), key=lambda kv: (-kv[1], kv[0]))
    if not ordered:
        return f"{lead}none has an incident type."
    if len(ordered) == 1 and not untyped:
        return f"{lead}all are {ordered[0][0]} reports."
    top_count = ordered[0][1]
    top = [kind for kind, count in ordered if count == top_count]
    reports = "report" if top_count == 1 else "reports"
    if len(top) == 1:
        text = f"{top[0]} is the most common type, with {_number(top_count)} {reports}"
    else:
        text = f"{_join(top)} are the most common types, with {_number(top_count)} {reports} each"
    rest = ordered[len(top) :]
    if rest:
        text += f", followed by {_join([f'{kind} ({count})' for kind, count in rest])}"
    if untyped:
        text += f"; {_number(untyped)} {'has' if untyped == 1 else 'have'} no type"
    return f"{lead}{text}."


def uncountable_answer(plan: Mapping[str, Any]) -> str:
    """Why a count or breakdown question has no exact answer: what the filters could not capture."""
    parts = [f'"{value}" as {_DROPPED_AS[key]}' for key, value in plan["dropped"].items()]
    if plan["query"] and not count_exact({**plan, "dropped": {}}):
        parts.append(f'"{plan["query"]}"')
    reason = f", not by {_join(parts)}" if parts else ""
    answer = (
        f"I can count incidents only by type, severity (low, medium or high), instrument or asset{reason}, so I can't "
        "give an exact number."
    )
    if "incident_type" in plan["dropped"]:
        answer += f" The incident types are {_join(_incident_types())}."
    return answer


def library_context(
    candidates: Sequence[LibraryReport], matching: Sequence[LibraryReport], plan: Mapping[str, Any]
) -> dict[str, Any]:
    """What the model may know: the code counts over every matching report, and the best matching reports."""
    reports = []
    for report in candidates:
        incident = report.payload.get("incident") or {}
        items = [
            {
                "item_id": str(i.get(id_field)),
                "item_type": item_type,
                "name": i.get("name") or i.get("label") or i.get("description") or i.get("type"),
            }
            for kind, (id_field, item_type) in ITEM_KINDS.items()
            for i in report.payload.get(kind) or []
            if i.get(id_field)
        ]
        reports.append(
            {
                "incident_id": report.incident_id,
                "type": incident.get("type"),
                "severity_level": incident.get("severity_level"),
                "title": incident.get("title"),
                "description": incident.get("description"),
                "items": items,
            }
        )
    by_type = Counter(str((r.payload.get("incident") or {}).get("type") or "none") for r in matching)
    by_severity = Counter(_band((r.payload.get("incident") or {}).get("severity_level")) or "none" for r in matching)
    return {
        "total_matching": len(matching),
        "count_exact": count_exact(plan),
        "counts_by_type": dict(sorted(by_type.items())),
        "counts_by_severity": dict(sorted(by_severity.items())),
        "dropped_filters": dict(plan["dropped"]),
        "reports": reports,
    }


def library_messages(context: Mapping[str, Any], history: Sequence[Mapping[str, str]]) -> list[dict[str, str]]:
    turns = [{"role": m["role"], "content": m["content"]} for m in history if m["role"] in ("user", "assistant")]
    return [
        {"role": "system", "content": incident_contract.prompt(LIBRARY_PROMPT_ID)},
        {"role": "user", "content": "LIBRARY:\n" + json.dumps(context, ensure_ascii=False, sort_keys=True)},
        *turns[-MAX_HISTORY_MESSAGES:],
    ]


@dataclass(frozen=True)
class LibraryAnswer:
    content: str
    citations: tuple[tuple[str, str], ...]
    insufficient_evidence: bool
    dropped_citations: tuple[str, ...] = ()
    ids_resolved: tuple[str, ...] = ()


def incident_mention(report: Mapping[str, Any]) -> str:
    """How an answer names an incident of the library: its type and title (``the burglary ("Shop door forced")``)."""
    kind = str(report.get("type") or "").strip()
    title = str(report.get("title") or "").strip().rstrip(".")
    phrase = f"the {kind}" if kind else "the incident"
    return f'{phrase} ("{title}")' if title else phrase


def _library_item_mentions(context: Mapping[str, Any], cited: Sequence[tuple[str, str]]) -> report_answers.Mentions:
    """How a library answer names each item id. The same id (E1) recurs across incidents, so an id names the item of
    the one incident it was cited with, else of the one incident shown that holds it; an id that could be either of
    two different items reads neutrally ("a person", "an object") rather than as the wrong one."""
    by_pair: dict[tuple[str, str], str] = {}
    holders: dict[str, list[str]] = {}
    for report in context["reports"]:
        for item in report["items"]:
            entity = item.get("item_type") == "entity"
            by_pair[(report["incident_id"], item["item_id"])] = report_answers.Item(
                kind=str(item.get("item_type") or ""),
                item_id=item["item_id"],
                name="" if entity else str(item.get("name") or ""),
                description=str(item.get("name") or "") if entity else "",
            ).mention()
            holders.setdefault(item["item_id"], []).append(report["incident_id"])
    cited_with: dict[str, list[str]] = {}
    for incident_id, item_id in cited:
        cited_with.setdefault(item_id, []).append(incident_id)
    mentions = report_answers.Mentions()
    for item_id, incidents in holders.items():
        candidates = {by_pair[(incident_id, item_id)] for incident_id in cited_with.get(item_id) or incidents}
        mentions[item_id] = (
            candidates.pop()
            if len(candidates) == 1
            else report_answers.NEUTRAL.get(item_id.removeprefix("new-")[:1], "an item")
        )
    return mentions


def check_library_answer(reply: Mapping[str, Any], context: Mapping[str, Any], *, question: str = "") -> LibraryAnswer:
    """The answer with each (incident, item) citation checked against the reports shown; unknown pairs dropped.

    Unless the question asks for ids, an incident id written in the prose is replaced by the incident's type and title,
    and an item id by the item's name (or a neutral phrase when it is ambiguous); all are listed in ``ids_resolved``.
    An empty answer is a model failure.
    """
    if not isinstance(reply.get("answer"), str) or not isinstance(reply.get("insufficient_evidence"), bool):
        raise ChatModelError("chat model reply lacks the answer or the insufficient_evidence flag")
    if not reply["answer"].strip():
        raise ChatModelError("chat model returned an empty answer")
    known = {(r["incident_id"], i["item_id"]) for r in context["reports"] for i in r["items"]}
    cited: list[tuple[str, str]] = []
    dropped: list[str] = []
    for citation in reply.get("citations") or []:
        pair = (
            (str(citation.get("incident_id") or ""), str(citation.get("item_id") or ""))
            if isinstance(citation, dict)
            else ("", "")
        )
        if pair in known and pair not in cited:
            cited.append(pair)
        elif any(pair) and pair not in known:
            dropped.append(f"{pair[0]}/{pair[1]}")
    insufficient = bool(reply["insufficient_evidence"])
    content = reply["answer"].strip()
    resolved: list[str] = []
    if not report_answers.asks_for_ids(question):
        for report in sorted(context["reports"], key=lambda r: len(r["incident_id"]), reverse=True):
            pattern = re.compile(rf"(?<![\w-]){re.escape(report['incident_id'])}(?![\w-])")
            if pattern.search(content):
                # A title may hold a backslash: escaped, it is literal text, never a group reference.
                content = pattern.sub(incident_mention(report).replace("\\", "\\\\"), content)
                resolved.append(report["incident_id"])
        mentions = _library_item_mentions(context, cited)
        leaked = report_answers.replaced_ids(content, mentions)
        if leaked or resolved:
            content = report_answers.resolve_references(content, mentions)
            resolved += leaked
    return LibraryAnswer(
        content=content,
        citations=() if insufficient else tuple(cited),
        insufficient_evidence=insufficient,
        dropped_citations=tuple(dropped),
        ids_resolved=tuple(resolved),
    )
