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

from dataclasses import dataclass
from dataclasses import field
import json
import os
import time
from typing import TYPE_CHECKING
from typing import Any
from typing import Protocol

import httpx

from vss_agents import incident_contract
from vss_agents.utils import report_answers

if TYPE_CHECKING:
    from collections.abc import Callable
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


def item_mentions(context: Mapping[str, Any]) -> dict[str, str]:
    """How a person reading the answer knows each item: its label or name, else its description's first clause."""
    mentions = {}
    for item in context.get("items") or []:
        mentions[item["item_id"]] = report_answers.Item(
            kind=str(item.get("item_type") or ""),
            item_id=item["item_id"],
            name=str(item.get("name") or ""),
            description=str(item.get("description") or ""),
            label=str(item.get("label") or ""),
            entity_type=str(item.get("type") or "").lower(),
        ).mention()
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


def check_answer(reply: Mapping[str, Any], context: Mapping[str, Any], *, question: str = "") -> GroundedAnswer:
    """The model's answer with its citations checked against the context; unknown ids are dropped and counted.

    Unless the question asks for ids, a known item id written in the prose is resolved to the item's description and
    listed in ``ids_resolved``.
    """
    if not isinstance(reply.get("answer"), str) or not isinstance(reply.get("insufficient_evidence"), bool):
        raise ChatModelError("chat model reply lacks the answer or the insufficient_evidence flag")
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
    leaked: list[str] = [] if report_answers.asks_for_ids(question) else report_answers.referenced_ids(content, known)
    if leaked:
        content = report_answers.resolve_references(content, item_mentions(context))
    return GroundedAnswer(
        content=content,
        item_ids=() if insufficient else tuple(cited),
        insufficient_evidence=insufficient,
        dropped_citations=tuple(dropped),
        ids_resolved=tuple(leaked),
    )
