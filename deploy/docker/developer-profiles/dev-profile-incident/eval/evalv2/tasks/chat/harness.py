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

"""The chat harness (J2): the agent's real chat router, in-process, over a frozen corpus snapshot.

**Design.** The router (``vss_agents.api.incident_chat.create_incident_chat_router``) is built with ports that read
a snapshot (:mod:`evalv2.tasks.chat.corpus`), never the finals database:

- :class:`SnapshotRevisionStore` answers ``get_revision`` and ``get_official_revision_id`` from the snapshot;
- :class:`SnapshotLibraryIndex` lists the snapshot's reports as the router's ``LibraryReport``;
- the chat model is injected: :class:`FakeChatModel` (deterministic, no network) for tests and dry runs, or the
  agent's own ``grounded_chat.OpenAIChatModel`` for a live run, which the CLI builds only with ``--live``,
  ``EVALV2_ALLOW_LIVE=1`` and an approval marker that covers the config (:mod:`evalv2.approval`).

Every model call goes through :class:`MeteredChatModel`: it records the call (the context the model saw, usage and
latency) and, with a budget ledger, reserves the call's pessimistic tokens before sending it and settles the
measured tokens after, so a run stops (``BudgetExceeded``) before any call that would pass a cap. The route
function is called directly (no HTTP server, no socket); its ``HTTPException`` is recorded as the item's outcome.

**Runs and resume.** One JSON line per work item is appended to ``answers.jsonl`` as soon as it is answered; a
re-run skips every item already there (``--retry-errors`` asks the ``http_error`` ones again), and the budget log
(``budget.jsonl``) carries the counts across runs. ``manifest.json`` pins the config, the snapshots, the model and
whether the run was live; a resume with any of them changed is refused.

Only this module and the CLI's ``run`` and ``estimate`` import ``vss_agents``, lazily, so the rest of the chat task
(planning, scoring, readiness) runs in the eval environment.
"""

from __future__ import annotations

import asyncio
import copy
import json
import math
import re
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from evalv2.core.budget import BudgetExceeded, BudgetLedger
from evalv2.core.hashing import sha256_json
from evalv2.datasets.records import effective_payload
from evalv2.tasks.base import WorkItem
from evalv2.tasks.chat.corpus import subject_incident

HARNESS_VERSION = "evalv2-chat-harness-v1"
CHAT_PATH = "/api/v1/incidents/chat"
MAX_OUTPUT_TOKENS = 1024  # grounded_chat.OpenAIChatModel.request_body's max_tokens
CHARS_PER_TOKEN = 3.0  # pessimistic for English and JSON (about 4 is typical), so bounds overestimate
FINAL_OUTCOMES = frozenset({"answered", "no_report", "http_error"})
FAKE_MODEL = "fake-chat-v1"


class HarnessError(RuntimeError):
    """A run that cannot start or resume: a changed manifest, a missing corpus, no agent package."""


def prompt_tokens_bound(messages: Sequence[Mapping[str, Any]]) -> int:
    """A pessimistic prompt-token bound: characters / 3, plus a few tokens of framing per message."""
    return math.ceil(sum(len(str(m.get("content") or "")) for m in messages) / CHARS_PER_TOKEN) + 8 * len(messages)


def call_tokens_bound(messages: Sequence[Mapping[str, Any]]) -> int:
    return prompt_tokens_bound(messages) + MAX_OUTPUT_TOKENS


# ---------------------------------------------------------------------------
# Snapshot ports (the router's RevisionStore and LibraryIndex)
# ---------------------------------------------------------------------------


class SnapshotRevisionStore:
    """The router's ``RevisionStore`` over a snapshot: each report is its incident's official version."""

    def __init__(self, snapshot: Mapping[str, Any]) -> None:
        self._by_version = {int(r["version_id"]): (incident_id, r) for incident_id, r in snapshot["reports"].items()}
        self._official = {incident_id: int(r["version_id"]) for incident_id, r in snapshot["reports"].items()}

    async def get_revision(self, revision_id: int) -> dict[str, Any] | None:
        found = self._by_version.get(int(revision_id))
        if found is None:
            return None
        incident_id, report = found
        return {"id": int(revision_id), "video_id": incident_id, "kind": "ai",
                "payload": copy.deepcopy(report["payload"]), "evidence": copy.deepcopy(report["evidence"])}

    async def get_official_revision_id(self, video_id: str) -> int | None:
        return self._official.get(str(video_id))


@dataclass(frozen=True)
class SnapshotReport:
    """Stands in for ``grounded_chat.LibraryReport`` where the agent package is absent (tests of the ports)."""

    incident_id: str
    version_id: int
    payload: Mapping[str, Any]
    evidence: Sequence[Mapping[str, Any]] = ()


class SnapshotLibraryIndex:
    """The router's ``LibraryIndex`` over a snapshot (the whole corpus is one half's library)."""

    def __init__(self, snapshot: Mapping[str, Any], report_type: Callable[..., Any] | None = None) -> None:
        self._snapshot = snapshot
        self._report_type = report_type or SnapshotReport

    async def reports(self) -> list[Any]:
        return [self._report_type(incident_id=incident_id, version_id=int(r["version_id"]),
                                  payload=copy.deepcopy(r["payload"]), evidence=copy.deepcopy(r["evidence"]))
                for incident_id, r in sorted(self._snapshot["reports"].items())]


# ---------------------------------------------------------------------------
# Chat models: the fake, and the metering wrapper every model call goes through
# ---------------------------------------------------------------------------


def _last_user(messages: Sequence[Mapping[str, Any]]) -> str:
    return next((str(m["content"]) for m in reversed(messages) if m.get("role") == "user"), "")


def _context(messages: Sequence[Mapping[str, Any]], prefix: str) -> dict[str, Any] | None:
    for message in messages:
        content = str(message.get("content") or "")
        if message.get("role") == "user" and content.startswith(prefix):
            parsed: dict[str, Any] = json.loads(content[len(prefix):])
            return parsed
    return None


@dataclass
class FakeChatModel:
    """A deterministic chat model for tests and dry runs; it never touches the network.

    - **plan** (the library planner): an incident type the question names (from the planner prompt's type list), a
      count when it asks "how many" or "number of", severity ``high`` for "severity 4 or higher"-like wording, a
      type breakdown for "most common", and the question as the search phrase;
    - **incident answer:** the report's type and severity, citing its first item; ``insufficient_evidence`` when the
      question names a word in ``refuse_words``;
    - **library answer:** the shown reports' ids, citing the first item of each (at most three).

    ``responder(kind, messages)`` overrides any reply; ``fail`` makes every call raise the agent's
    ``ChatModelError`` (passed in, since the agent package is imported lazily).
    """

    model: str = FAKE_MODEL
    refuse_words: tuple[str, ...] = ("license plate", "licence plate", "weather", "name of")
    responder: Callable[[str, Sequence[Mapping[str, Any]]], Mapping[str, Any] | None] | None = None
    fail: type[Exception] | None = None
    last_usage: dict[str, Any] = field(default_factory=dict)
    calls: int = 0

    @staticmethod
    def kind(messages: Sequence[Mapping[str, Any]], schema: Mapping[str, Any]) -> str:
        if "wants_count" in (schema.get("required") or []):
            return "library_plan"
        return "library_answer" if _context(messages, "LIBRARY:\n") is not None else "incident"

    async def complete(self, messages: Sequence[Mapping[str, Any]], schema: Mapping[str, Any]) -> dict[str, Any]:
        self.calls += 1
        kind = self.kind(messages, schema)
        prompt_chars = sum(len(str(m.get("content") or "")) for m in messages)
        self.last_usage = {"prompt_tokens": prompt_chars // 4, "completion_tokens": 40,
                           "total_tokens": prompt_chars // 4 + 40, "elapsed_ms": 0.0}
        if self.fail is not None:
            raise self.fail("fake chat model failure")
        if self.responder is not None:
            reply = self.responder(kind, messages)
            if reply is not None:
                return dict(reply)
        question = _last_user(messages).lower()
        if kind == "library_plan":
            return self._plan(messages, question)
        if kind == "library_answer":
            library = _context(messages, "LIBRARY:\n") or {}
            shown = library.get("reports") or []
            cited = [{"incident_id": r["incident_id"], "item_id": r["items"][0]["item_id"]}
                     for r in shown[:3] if r.get("items")]
            return {"answer": "Matching reports: " + ", ".join(r["incident_id"] for r in shown[:3]) + ".",
                    "citations": cited, "insufficient_evidence": not shown}
        report = _context(messages, "REPORT:\n") or {}
        incident = report.get("incident") or {}
        items = report.get("items") or []
        if any(word in question for word in self.refuse_words):
            return {"answer": "The report does not say.", "citations": [], "insufficient_evidence": True}
        return {"answer": f"This is a {incident.get('type')} incident at severity {incident.get('severity_level')}.",
                "citations": [{"item_id": items[0]["item_id"]}] if items else [], "insufficient_evidence": False}

    @staticmethod
    def _plan(messages: Sequence[Mapping[str, Any]], question: str) -> dict[str, Any]:
        system = str(messages[0].get("content") or "") if messages else ""
        listed = re.search(r"Incident types: (.*)\.", system)
        types = [t.strip() for t in listed.group(1).split(",")] if listed else []
        named = next((t for t in sorted(types, key=len, reverse=True) if t.replace("_", " ") in question), None)
        wants_count = bool(re.search(r"\bhow many\b|\bnumber of\b|\bcount\b", question))
        severe = re.search(r"\b(?:severity|level)\s*(?:of\s*)?(?:4|four)\b|\bhigh[- ]severity\b", question)
        group = "incident_type" if re.search(r"\bmost (?:common|frequent)\b", question) else None
        return {"incident_type": named, "severity": "high" if severe else None, "instrument": None, "asset": None,
                "query": "" if (wants_count or group) else question, "wants_count": wants_count, "group_by": group}


@dataclass
class CallRecord:
    kind: str
    context: str
    usage: dict[str, Any]
    elapsed_ms: float
    tokens_bound: int
    error: str | None = None


class MeteredChatModel:
    """Wraps a chat model: records each call and, with a ledger, reserves before the call and settles after."""

    def __init__(self, inner: Any, *, ledger: BudgetLedger | None = None, subject: str = "", task: str = "chat",
                 clock: Callable[[], float] = time.perf_counter) -> None:
        self.inner = inner
        self.ledger = ledger
        self.subject = subject
        self.task = task
        self.clock = clock
        self.records: list[CallRecord] = []

    @property
    def model(self) -> str:
        return str(self.inner.model)

    @property
    def last_usage(self) -> dict[str, Any]:
        return dict(getattr(self.inner, "last_usage", {}) or {})

    def begin(self) -> None:
        self.records = []

    async def complete(self, messages: Sequence[Mapping[str, Any]], schema: Mapping[str, Any]) -> dict[str, Any]:
        kind = FakeChatModel.kind(messages, schema)
        bound = call_tokens_bound(messages)
        reservation = (self.ledger.reserve("inference", subject=self.subject, task=self.task, tokens_bound=bound)
                       if self.ledger is not None else None)
        context = next((str(m["content"]) for m in messages if m.get("role") == "user"
                        and str(m.get("content") or "").startswith(("REPORT:\n", "LIBRARY:\n"))), "")
        started = self.clock()
        try:
            reply: dict[str, Any] = await self.inner.complete(messages, schema)
        except BaseException as exc:
            if reservation is not None and self.ledger is not None:
                self.ledger.settle(reservation, tokens_used=_tokens(self.last_usage))
            self.records.append(CallRecord(kind, context, self.last_usage, (self.clock() - started) * 1000, bound,
                                           f"{type(exc).__name__}: {exc}"[:300]))
            raise
        if reservation is not None and self.ledger is not None:
            self.ledger.settle(reservation, tokens_used=_tokens(self.last_usage))
        self.records.append(CallRecord(kind, context, self.last_usage, (self.clock() - started) * 1000, bound))
        return reply


def _tokens(usage: Mapping[str, Any]) -> int | None:
    """Measured tokens of a call, ``None`` when it reported none (the ledger then keeps the bound)."""
    total = usage.get("total_tokens")
    if isinstance(total, int) and not isinstance(total, bool):
        return total
    parts = [usage.get(k) for k in ("prompt_tokens", "completion_tokens")]
    return sum(parts) if all(isinstance(p, int) and not isinstance(p, bool) for p in parts) else None  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# The router, in-process
# ---------------------------------------------------------------------------


@dataclass
class CorpusRouter:
    """One corpus's chat route: the endpoint function and the index it searches."""

    corpus: str
    snapshot: Mapping[str, Any]
    endpoint: Callable[..., Any]
    index: SnapshotLibraryIndex
    request_type: Any
    http_exception: type[Exception]
    grounded_chat: Any


def build_router(corpus: str, snapshot: Mapping[str, Any], model: Any) -> CorpusRouter:
    """The agent's real chat router in grounded mode over ``snapshot`` (imports the agent package)."""
    try:
        from fastapi import HTTPException
        from vss_agents.api import incident_chat
        from vss_agents.utils import grounded_chat
    except ImportError as exc:  # pragma: no cover - depends on the interpreter
        raise HarnessError("the chat harness runs with the agent's Python (services/agent/.venv): "
                           f"vss_agents is not importable ({exc})") from exc
    index = SnapshotLibraryIndex(snapshot, grounded_chat.LibraryReport)
    router = incident_chat.create_incident_chat_router(
        search_client=incident_chat.DisabledSearchClient(), revision_store=SnapshotRevisionStore(snapshot),
        chat_model=model, mode="grounded", library_index=index)
    endpoint = next(r.endpoint for r in router.routes if getattr(r, "path", None) == CHAT_PATH)
    return CorpusRouter(corpus, snapshot, endpoint, index, incident_chat.ChatRequest, HTTPException, grounded_chat)


def request_body(payload: Mapping[str, Any], incident_id: str | None, session_id: str) -> dict[str, Any]:
    """The console's request: the question alone; an incident question carries the incident's scope."""
    body: dict[str, Any] = {"session_id": session_id, "messages": [{"role": "user", "content": payload["question"]}]}
    if payload["scope"] == "incident":
        body["context"] = {"scope": "incident", "incident_id": incident_id}
    return body


async def library_lists(router: CorpusRouter, grounding: Mapping[str, Any], answer: Mapping[str, Any]
                        ) -> dict[str, list[str]]:
    """What a library answer retrieved and named, recomputed by the router's own functions from its plan.

    ``ranked``: every report matching the plan's filters, in the router's rank order (the model saw the first
    ``MAX_LIBRARY_REPORTS``). ``named``: the incidents a code-composed answer names in its prose (it has no
    structured citations): all of them up to ``MAX_LISTED``, else the first three; none for a breakdown of several.
    """
    gc = router.grounded_chat
    plan = grounding.get("plan")
    if not isinstance(plan, Mapping):
        return {"ranked": [], "named": []}
    reports = await router.index.reports()
    ranked = [r.incident_id for r in gc.rank([r for r in reports if gc.matches(r, plan)], plan["query"])]
    named: list[str] = []
    if grounding.get("answered_by") == "code" and not answer.get("insufficient_evidence"):
        n = len(ranked)
        if not (plan.get("group_by") and n > 1):
            named = ranked[:n] if n <= gc.MAX_LISTED else ranked[:3]
    return {"ranked": ranked, "named": named}


def _usage_total(records: Iterable[CallRecord]) -> dict[str, Any]:
    out: dict[str, Any] = {"calls": 0}
    for record in records:
        out["calls"] += 1
        for key, value in record.usage.items():
            if isinstance(value, int | float) and not isinstance(value, bool):
                out[key] = out.get(key, 0) + value
    return out


async def ask(item: WorkItem, record: Mapping[str, Any], router: CorpusRouter, model: MeteredChatModel
              ) -> dict[str, Any]:
    """One work item through the router: the answer row (``BudgetExceeded`` propagates: the run stops)."""
    payload = effective_payload(record)
    assert payload is not None  # rejected references are never planned
    base = {"key": item.key, "task": item.task, "subject": item.subject, "item_id": item.item_id,
            "repeat": item.repeat, "corpus": router.corpus, "record_id": record["record_id"],
            "scope": payload["scope"], "class": payload["class"], "tier": record["tier"]}
    target = subject_incident(payload, router.snapshot)
    if target is None:
        return {**base, "incident_id": payload["params"].get("incident_id"), "outcome": "no_report",
                "detail": "the corpus holds no report for this incident", "calls": [], "usage": {"calls": 0}}
    incident_id = None if target == "library" else target
    body = request_body(payload, incident_id, f"eval-{item.key[:16]}")
    model.begin()
    started = time.perf_counter()
    try:
        response = await router.endpoint(router.request_type.model_validate(body))
    except BudgetExceeded:
        raise
    except router.http_exception as exc:
        return {**base, "incident_id": incident_id, "outcome": "http_error",
                "status": getattr(exc, "status_code", None), "detail": str(getattr(exc, "detail", exc))[:500],
                "calls": [vars(r) for r in model.records], "usage": _usage_total(model.records),
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 1)}
    elapsed = round((time.perf_counter() - started) * 1000, 1)
    dumped = response.model_dump(exclude_none=True)
    message, grounding = dumped["message"], dumped.get("grounding") or {}
    row = {**base, "incident_id": incident_id, "outcome": "answered",
           "answer": {"content": message["content"], "citations": message.get("citations") or [],
                      "insufficient_evidence": bool(message.get("insufficient_evidence"))},
           "grounding": grounding, "calls": [vars(r) for r in model.records], "usage": _usage_total(model.records),
           "elapsed_ms": elapsed}
    if payload["scope"] == "library":
        row["library"] = await library_lists(router, grounding, row["answer"])
    return row


# ---------------------------------------------------------------------------
# Runs: manifest, resume, answers
# ---------------------------------------------------------------------------


def read_rows(path: Path) -> list[dict[str, Any]]:
    path = Path(path)
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def latest_rows(rows: Iterable[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    """The last row per work key (a ``--retry-errors`` pass appends a newer one)."""
    out: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        out[str(row["key"])] = row
    return out


def check_manifest(out: Path, manifest: Mapping[str, Any]) -> None:
    """Write the run's manifest, or refuse a resume whose config, snapshots, model or liveness changed."""
    path = Path(out) / "manifest.json"
    if path.is_file():
        stored = json.loads(path.read_text(encoding="utf-8"))
        changed = sorted(k for k in set(stored) | set(manifest) if k != "started_at" and stored.get(k) != manifest.get(k))
        if changed:
            raise HarnessError(f"{path}: a resume must keep the run's {changed}; use a new --out for a changed run")
        return
    path.write_text(json.dumps(dict(manifest), indent=2, sort_keys=True) + "\n", encoding="utf-8")


@dataclass
class RunSummary:
    planned: int
    already_done: int
    asked: int = 0
    outcomes: dict[str, int] = field(default_factory=dict)
    stopped: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {"planned": self.planned, "already_done": self.already_done, "asked": self.asked,
                "outcomes": dict(sorted(self.outcomes.items())), "stopped": self.stopped,
                "open": self.planned - self.already_done - sum(self.outcomes.values())}


async def run_items(items: Sequence[WorkItem], records: Mapping[str, Mapping[str, Any]],
                    routers: Mapping[str, CorpusRouter], model: MeteredChatModel, answers: Path, *,
                    retry_errors: bool = False, limit: int | None = None) -> RunSummary:
    """Ask every open item, appending each answer row as soon as it exists; stop at the first budget refusal."""
    done = latest_rows(read_rows(answers))
    open_items = [i for i in items if i.key not in done
                  or (retry_errors and done[i.key].get("outcome") == "http_error")]
    summary = RunSummary(planned=len(items), already_done=len(items) - len(open_items))
    answers.parent.mkdir(parents=True, exist_ok=True)
    for item in open_items[:limit] if limit is not None else open_items:
        try:
            row = await ask(item, records[item.spec["record_id"]], routers[item.spec["corpus"]], model)
        except BudgetExceeded as exc:
            summary.stopped = f"budget: {exc}"
            break
        with answers.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")
        summary.asked += 1
        summary.outcomes[row["outcome"]] = summary.outcomes.get(row["outcome"], 0) + 1
    return summary


def run(items: Sequence[WorkItem], records: Mapping[str, Mapping[str, Any]], snapshots: Mapping[str, Mapping[str, Any]],
        model: MeteredChatModel, out: Path, *, manifest: Mapping[str, Any], retry_errors: bool = False,
        limit: int | None = None) -> dict[str, Any]:
    """Build one router per corpus, check the manifest, and ask every open item."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    check_manifest(out, {**dict(manifest), "harness": HARNESS_VERSION,
                         "snapshots": {name: sha256_json(dict(s)) for name, s in sorted(snapshots.items())}})
    routers = {name: build_router(name, snapshot, model) for name, snapshot in snapshots.items()}
    summary = asyncio.run(run_items(items, records, routers, model, out / "answers.jsonl",
                                    retry_errors=retry_errors, limit=limit))
    result = summary.to_json()
    if model.ledger is not None:
        result["budget"] = model.ledger.snapshot()
    return result
