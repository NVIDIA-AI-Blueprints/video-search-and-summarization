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

"""``POST /api/v1/incidents/chat`` - Conversational Q&A over incidents and evidence crops.

Provides grounded conversational answers to user questions about surveillance incidents,
entities, instruments, and assets, accompanied by structured evidence citations
referencing exact timestamps and crop screenshots for UI inspection and jump-to-clip.

Follows a hexagonal architecture: calls evidence search capabilities through the
:class:`SearchClient` port. An in-memory adapter is provided for testing and offline
operation.

A request may name a report revision (``version_id``) and carry an inline ``draft_report``. The
official revision is answered through the search client (decision 2026-10-24). Any other revision,
and any draft, is answered from that revision's own report and saved ``evidence`` by
:mod:`vss_agents.utils.report_answers`, which composes prose for the kind of question asked (what
happened in order, who, which objects, when, severity, type). The prose names people and objects by
their descriptions, and keeps item and incident ids for the structured citations only. Revisions
are read through the :class:`RevisionStore` port.

The default search client (:class:`DisabledSearchClient`) searches nothing, and the answer says so.
Before it, the canned in-memory client's demo hits were returned as if they were real for library
questions.

TODO(MVP2 Phase 4 / search route owner): Wire SearchClient to live search endpoint /
pgvector RPC once merged.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any
from typing import Literal
from typing import Protocol
from typing import runtime_checkable
import uuid

from fastapi import APIRouter
from fastapi import FastAPI
from fastapi import HTTPException
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import model_validator

from vss_agents.utils import grounded_chat
from vss_agents.utils import incident_db
from vss_agents.utils import report_answers
from vss_agents.utils.crop_evidence import evidence_items
from vss_agents.utils.r2 import resolve_r2_url

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# SearchClient Port & In-Memory Adapter
# ---------------------------------------------------------------------------


@runtime_checkable
class SearchClient(Protocol):
    """Port for retrieving evidence hits matching a natural-language query."""

    async def search(
        self,
        query: str,
        top_k: int = 5,
        item_types: list[str] | None = None,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Retrieve verified evidence hits conforming to search-contract-v1 hit shape."""
        ...


_DEFAULT_SEED_HITS: list[dict[str, Any]] = [
    {
        "result_id": "res_assault_machete",
        "rank": 1,
        "similarity_score": 0.89,
        "item": {
            "item_type": "instrument",
            "item_id": "I1",
            "name": "machete",
            "label": "machete",
            "description": "Long curved blade held by assailant during storefront altercation.",
            "threat_level": 4,
            "holder_entity_id": "E1",
            "crop_image_url": "https://pub-r2.example.com/evidence/Assault007/m86a5c319e26659804b0/instruments/I1.jpg",
            "timestamp_seconds": 14,
            "bounding_box": [320, 450, 510, 600],
        },
        "incident": {
            "incident_id": "Assault007",
            "model_run_id": "m86a5c319e26659804b0",
            "incident_type": "assault",
            "severity_level": 4,
            "title": "Physical altercation with edged weapon in ATM lobby",
            "description": "An altercation ensues where a perpetrator brandishes a large blade against a victim.",
            "playback_url": "https://pub-r2.example.com/videos/anomaly/assault/Assault007.mp4",
            "incident_window": [10.0, 28.0],
        },
        "critic": {
            "verdict": "confirmed",
            "confidence": 0.96,
            "rationale": "The cropped screenshot clearly shows a machete held in the assailant's hand.",
        },
    },
    {
        "result_id": "res_assault_perpetrator",
        "rank": 2,
        "similarity_score": 0.81,
        "item": {
            "item_type": "entity",
            "item_id": "E1",
            "name": "perpetrator",
            "label": "person",
            "description": "Adult wearing dark hooded jacket lunging toward victim.",
            "threat_level": 5,
            "holder_entity_id": None,
            "crop_image_url": "https://pub-r2.example.com/evidence/Assault007/m86a5c319e26659804b0/entities/E1.jpg",
            "timestamp_seconds": 12,
            "bounding_box": [210, 390, 820, 640],
        },
        "incident": {
            "incident_id": "Assault007",
            "model_run_id": "m86a5c319e26659804b0",
            "incident_type": "assault",
            "severity_level": 4,
            "title": "Physical altercation with edged weapon in ATM lobby",
            "description": "An altercation ensues where a perpetrator brandishes a large blade against a victim.",
            "playback_url": "https://pub-r2.example.com/videos/anomaly/assault/Assault007.mp4",
            "incident_window": [10.0, 28.0],
        },
        "critic": {
            "verdict": "confirmed",
            "confidence": 0.94,
            "rationale": "The crop depicts the primary aggressor in active motion.",
        },
    },
    {
        "result_id": "res_burglary_crowbar",
        "rank": 3,
        "similarity_score": 0.78,
        "item": {
            "item_type": "instrument",
            "item_id": "I1",
            "name": "crowbar",
            "label": "crowbar",
            "description": "Heavy metal pry bar used to force open the storefront entrance door.",
            "threat_level": 3,
            "holder_entity_id": "E1",
            "crop_image_url": "https://pub-r2.example.com/evidence/Burglary005/m41d7e220f18831902a2/instruments/I1.jpg",
            "timestamp_seconds": 8,
            "bounding_box": [400, 280, 520, 410],
        },
        "incident": {
            "incident_id": "Burglary005",
            "model_run_id": "m41d7e220f18831902a2",
            "incident_type": "burglary",
            "severity_level": 3,
            "title": "Commercial door breach after hours",
            "description": "Suspect uses a tool to lever open the security door.",
            "playback_url": "https://pub-r2.example.com/videos/anomaly/burglary/Burglary005.mp4",
            "incident_window": [5.0, 20.0],
        },
        "critic": {
            "verdict": "confirmed",
            "confidence": 0.92,
            "rationale": "The crop clearly shows a metal pry bar engaged with the door frame.",
        },
    },
    {
        "result_id": "res_burglary_safe",
        "rank": 4,
        "similarity_score": 0.72,
        "item": {
            "item_type": "asset",
            "item_id": "A1",
            "name": "cash safe",
            "label": "safe",
            "description": "Anchored metal safe box targeted by intruder.",
            "threat_level": None,
            "holder_entity_id": None,
            "crop_image_url": "https://pub-r2.example.com/evidence/Burglary005/m41d7e220f18831902a2/assets/A1.jpg",
            "timestamp_seconds": 15,
            "bounding_box": [500, 600, 750, 850],
        },
        "incident": {
            "incident_id": "Burglary005",
            "model_run_id": "m41d7e220f18831902a2",
            "incident_type": "burglary",
            "severity_level": 3,
            "title": "Commercial door breach after hours",
            "description": "Suspect uses a tool to lever open the security door.",
            "playback_url": "https://pub-r2.example.com/videos/anomaly/burglary/Burglary005.mp4",
            "incident_window": [5.0, 20.0],
        },
        "critic": {
            "verdict": "confirmed",
            "confidence": 0.90,
            "rationale": "The crop displays the targeted commercial safe fixture.",
        },
    },
]


class DisabledSearchClient:
    """The default chat search client: it searches nothing, and the chat answer says so.

    Decision 2026-10-24 kept official-version chat on the in-memory canned client for MVP2, so a finals
    video gets no evidence. That client's demo hits (``Assault007``, ``Burglary005``) were also returned,
    as if they were real, for every library question. The default now searches nothing instead, and the
    answer says that this chat mode does not search that scope. Tests and local tools inject
    :class:`InMemorySearchClient` explicitly.
    """

    enabled = False

    async def search(
        self,
        query: str,
        top_k: int = 5,
        item_types: list[str] | None = None,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        del query, top_k, item_types, filters  # the SearchClient signature; nothing is searched
        return []


class InMemorySearchClient:
    """In-memory SearchClient adapter with demo hits, for tests and local development only."""

    def __init__(self, seed_hits: list[dict[str, Any]] | None = None) -> None:
        self.hits = list(seed_hits if seed_hits is not None else _DEFAULT_SEED_HITS)

    async def search(
        self,
        query: str,
        top_k: int = 5,
        item_types: list[str] | None = None,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        query_lower = query.lower().strip()
        query_tokens = [tok for tok in query_lower.replace("?", " ").replace(",", " ").split() if len(tok) > 2]

        incident_id_filter = filters.get("incident_id") if filters else None
        incident_types_filter = filters.get("incident_types") if filters else None

        matched_hits: list[tuple[float, dict[str, Any]]] = []
        for hit in self.hits:
            item = hit.get("item", {})
            incident = hit.get("incident", {})

            # Filter by incident_id if specified
            if incident_id_filter and incident.get("incident_id") != incident_id_filter:
                continue

            # Filter by item_type if specified
            if item_types and item.get("item_type") not in item_types:
                continue

            # Filter by incident_types if specified
            if incident_types_filter and incident.get("incident_type") not in incident_types_filter:
                continue

            # Keyword matching against searchable text fields
            searchable_text = " ".join(
                [
                    str(item.get("item_type", "")),
                    str(item.get("name", "")),
                    str(item.get("label", "")),
                    str(item.get("description", "")),
                    str(incident.get("incident_id", "")),
                    str(incident.get("title", "")),
                    str(incident.get("incident_type", "")),
                    str(incident.get("description", "")),
                ]
            ).lower()

            # If query has tokens, at least one token should match; if query has no tokens, match all
            if query_tokens:
                score = sum(
                    1
                    for tok in query_tokens
                    if tok in searchable_text or (len(tok) > 3 and tok.rstrip("s") in searchable_text)
                )
                if score > 0:
                    matched_hits.append((score, hit))
                elif incident_id_filter:
                    matched_hits.append((0.1, hit))
            else:
                matched_hits.append((1, hit))

        # Sort by match score descending
        matched_hits.sort(key=lambda pair: pair[0], reverse=True)
        return [hit for _, hit in matched_hits[:top_k]]


# ---------------------------------------------------------------------------
# RevisionStore Port & incident_db Adapter
# ---------------------------------------------------------------------------


class RevisionStoreUnavailableError(RuntimeError):
    """The revision store cannot be reached or is not configured."""


@runtime_checkable
class RevisionStore(Protocol):
    """Port for reading saved report revisions."""

    async def get_revision(self, revision_id: int) -> dict[str, Any] | None:
        """The ``report_revisions`` row, or ``None`` when it does not exist."""
        ...

    async def get_official_revision_id(self, video_id: str) -> int | None:
        """The video's ``official_revision_id``, or ``None`` when it has none."""
        ...


class IncidentDBRevisionStore:
    """RevisionStore adapter over the incident Supabase database (``utils/incident_db``)."""

    async def _db(self) -> incident_db.IncidentDB:
        db = await incident_db.get_db()
        if db is None:
            raise RevisionStoreUnavailableError("incident database is not configured or unreachable")
        return db

    async def get_revision(self, revision_id: int) -> dict[str, Any] | None:
        return await (await self._db()).get_revision(revision_id)

    async def get_official_revision_id(self, video_id: str) -> int | None:
        video = await (await self._db()).get_video(video_id)
        official = video.get("official_revision_id") if video else None
        return int(official) if official is not None else None


class IncidentDBLibraryIndex:
    """Every incident's searchable report version (official, else newest), read through ``incident_db``.

    Cached for ``ttl_seconds`` so a conversation does not reread the library on every turn.
    """

    def __init__(self, ttl_seconds: float = 60.0, clock: Any = time.monotonic) -> None:
        self.ttl_seconds = ttl_seconds
        self.clock = clock
        self._cached: tuple[float, list[grounded_chat.LibraryReport]] | None = None

    async def reports(self) -> list[grounded_chat.LibraryReport]:
        if self._cached is not None and self.clock() - self._cached[0] < self.ttl_seconds:
            return self._cached[1]
        db = await incident_db.get_db()
        if db is None:
            raise RevisionStoreUnavailableError("the incident database is not configured")
        out: list[grounded_chat.LibraryReport] = []
        for video in await db.list_videos():
            version_id = video.get("official_revision_id")
            revision = await db.get_revision(int(version_id)) if version_id else None
            if revision is None:
                revisions = await db.list_revisions(str(video["id"]))
                revision = max(revisions, key=lambda r: (r.get("revision_no") or 0, r.get("id") or 0), default=None)
            if revision is not None:
                out.append(
                    grounded_chat.LibraryReport(
                        incident_id=str(video["id"]),
                        version_id=int(revision["id"]),
                        payload=revision.get("payload") or {},
                        evidence=revision.get("evidence") or [],
                    )
                )
        self._cached = (self.clock(), out)
        return out


def _draft_evidence(draft_report: dict[str, Any], saved: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The draft's items, each with the saved crop of the same item in the base revision (if any)."""
    crops = {(e.get("item_type"), e.get("item_id")): e for e in saved}
    merged = []
    for item in evidence_items(draft_report):
        base = crops.get((item["item_type"], item["item_id"]))
        merged.append({**item, "image": base.get("image") if base else None})
    return merged


# ---------------------------------------------------------------------------
# Request & Response Data Models (chat-contract-v1)
# ---------------------------------------------------------------------------


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant", "system"] = Field(
        ..., description="Role of message author ('user', 'assistant', 'system')"
    )
    content: str = Field(..., min_length=1, description="Text content of message")


class ChatContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: Literal["global", "incident"] = Field(
        default="global", description="Scope of conversation: 'global' or 'incident'"
    )
    incident_id: str | None = Field(default=None, description="Optional incident_id when scope is 'incident'")
    evidence_crop_ids: list[str] | None = Field(default=None, description="Optional evidence item IDs")


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str | None = Field(default=None, min_length=1, max_length=128, description="Optional session ID")
    messages: list[ChatMessage] = Field(..., min_length=1, description="Message history")
    context: ChatContext | None = Field(default=None, description="Optional context")
    version_id: int | None = Field(
        default=None, ge=1, description="Optional report revision (report_revisions.id) the question is about"
    )
    draft_report: dict[str, Any] | None = Field(
        default=None,
        description="Optional unsaved incident report edited from version_id; answers are grounded in it",
    )

    @model_validator(mode="after")
    def _draft_needs_version(self) -> ChatRequest:
        if self.draft_report is not None and self.version_id is None:
            raise ValueError("draft_report requires version_id")
        return self


class EvidenceCitation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    incident_id: str = Field(..., min_length=1)
    item_id: str = Field(..., min_length=1)
    item_type: Literal["entity", "instrument", "asset"]
    timestamp_seconds: float = Field(..., ge=0)
    crop_image_url: str


class AssistantMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["assistant"] = "assistant"
    content: str
    citations: list[EvidenceCitation]
    # chat-contract-v2 only (grounded mode); absent from a template answer, which stays chat-contract-v1.
    insufficient_evidence: bool | None = None


class ChatResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(..., min_length=1)
    message: AssistantMessage
    grounding: dict[str, Any] | None = None  # chat-contract-v2 only (grounded mode)


CHAT_MODE_ENV = "INCIDENT_CHAT_MODE"
CHAT_MODES = ("template", "grounded")


def chat_mode() -> str:
    """``template`` (the default: keyword-ranked evidence, no model call) or ``grounded`` (a chat model answers)."""
    mode = (os.environ.get(CHAT_MODE_ENV) or "template").strip().lower()
    if mode not in CHAT_MODES:
        raise ValueError(f"{CHAT_MODE_ENV} must be one of {CHAT_MODES}, got {mode!r}")
    return mode


# ---------------------------------------------------------------------------
# Router & Registration
# ---------------------------------------------------------------------------


def create_incident_chat_router(
    search_client: SearchClient | None = None,
    revision_store: RevisionStore | None = None,
    chat_model: grounded_chat.ChatModel | None = None,
    mode: str | None = None,
    library_index: grounded_chat.LibraryIndex | None = None,
) -> APIRouter:
    """Build the ``POST /api/v1/incidents/chat`` router with an injected or default SearchClient.

    ``mode`` (default ``INCIDENT_CHAT_MODE``, else ``template``) chooses how an incident question is answered: the
    template answer from keyword-ranked evidence (chat-contract-v1), or a grounded answer from ``chat_model``
    (default :class:`grounded_chat.OpenAIChatModel`) over one report version (chat-contract-v2). A question with
    no incident (library scope) is answered in grounded mode by a planned search over every incident's report
    (``library_index``, default :class:`IncidentDBLibraryIndex`): the model turns the question into filters, code
    filters and counts the reports exactly, and the model answers from the best matches with incident citations.
    """
    router = APIRouter()
    client: SearchClient = search_client if search_client is not None else DisabledSearchClient()
    revisions: RevisionStore = revision_store if revision_store is not None else IncidentDBRevisionStore()
    selected_mode = mode if mode is not None else chat_mode()
    if selected_mode not in CHAT_MODES:
        raise ValueError(f"unknown chat mode {selected_mode!r}")
    models: dict[str, grounded_chat.ChatModel] = {"default": chat_model} if chat_model is not None else {}

    indexes: dict[str, grounded_chat.LibraryIndex] = {"default": library_index} if library_index is not None else {}

    def _index() -> grounded_chat.LibraryIndex:
        if "default" not in indexes:
            indexes["default"] = IncidentDBLibraryIndex()
        return indexes["default"]

    def _model() -> grounded_chat.ChatModel:
        if "default" not in models:
            models["default"] = grounded_chat.OpenAIChatModel()
        return models["default"]

    async def _grounded(body: ChatRequest, session_id: str, incident_id: str | None) -> ChatResponse:
        """A chat model's answer over one report version (the asked one, else the incident's official one)."""
        try:
            version_id = body.version_id
            if version_id is None:
                assert incident_id is not None
                version_id = await revisions.get_official_revision_id(incident_id)
                if version_id is None:
                    raise HTTPException(
                        status_code=409,
                        detail=f"Incident {incident_id} has no official report version; send version_id",
                    )
            revision = await revisions.get_revision(version_id)
            if revision is None:
                raise HTTPException(status_code=404, detail=f"Report version {version_id} not found")
            video_id = str(revision["video_id"])
            if incident_id is not None and incident_id != video_id:
                raise HTTPException(
                    status_code=400,
                    detail=f"Report version {version_id} belongs to incident {video_id}, not {incident_id}",
                )
            official = await revisions.get_official_revision_id(video_id) == version_id
        except RevisionStoreUnavailableError as exc:
            raise HTTPException(status_code=503, detail=f"Report revisions unavailable: {exc}") from exc
        except HTTPException:
            raise
        except Exception as exc:
            logger.error("RevisionStore failed during grounded chat: %s", exc, exc_info=True)
            raise HTTPException(status_code=502, detail=f"Report revision error: {exc}") from exc

        saved = revision.get("evidence") or []
        raw = revision.get("payload")
        payload: dict[str, Any] = raw if isinstance(raw, dict) else {}
        report, evidence, retired = payload, saved, set[str]()
        if body.draft_report is not None:
            # The console's draft holds only the edited fields; the title, timeline and the rest come from the base.
            report = report_answers.overlay_draft(payload, body.draft_report)
            evidence = _draft_evidence(body.draft_report, saved)
            retired = report_answers.item_ids(payload) - report_answers.item_ids(report)
        context = grounded_chat.report_context(report, evidence)
        model = _model()
        history = [message.model_dump() for message in body.messages]
        try:
            reply = await model.complete(grounded_chat.chat_messages(context, history), grounded_chat.ANSWER_SCHEMA)
            question = next((m.content for m in reversed(body.messages) if m.role == "user"), "")
            answer = grounded_chat.check_answer(reply, context, question=question, retired=retired)
        except grounded_chat.ChatModelError as exc:
            logger.error("Chat model failed for version %s: %s", version_id, exc)
            raise HTTPException(status_code=502, detail=f"Chat model error: {exc}") from exc
        by_id = {str(entry.get("item_id")): entry for entry in evidence}
        items = {item["item_id"]: item for item in context["items"]}
        citations = []
        for item_id in answer.item_ids:
            entry = by_id.get(item_id, {})
            seconds = entry.get("box_seconds", entry.get("timestamp_seconds"))
            if seconds is None:
                seconds = items[item_id].get("timestamp_seconds") or 0
            citations.append(
                EvidenceCitation(
                    incident_id=video_id,
                    item_id=item_id,
                    item_type=items[item_id]["item_type"],
                    timestamp_seconds=max(0.0, float(seconds)),
                    crop_image_url=resolve_r2_url(str(entry.get("image") or "")),
                )
            )
        grounding = {
            "mode": "grounded",
            "scope": "incident",
            "model": model.model,
            "version_id": version_id,
            "official": official,
            "draft": body.draft_report is not None,
            "dropped_citations": list(answer.dropped_citations),
            "ids_resolved": list(answer.ids_resolved),
            "prompt_sha256": grounded_chat.incident_contract.prompt_sha256(grounded_chat.PROMPT_ID),
            "usage": dict(getattr(model, "last_usage", {}) or {}),
        }
        return ChatResponse(
            session_id=session_id,
            message=AssistantMessage(
                content=answer.content, citations=citations, insufficient_evidence=answer.insufficient_evidence
            ),
            grounding=grounding,
        )

    async def _load_version(
        body: ChatRequest, filters: dict[str, Any], store: RevisionStore
    ) -> tuple[dict[str, Any], str, bool]:
        """The revision a question is about (``body.version_id``), its incident, and whether it is official."""
        assert body.version_id is not None
        try:
            revision = await store.get_revision(body.version_id)
            if revision is None:
                raise HTTPException(status_code=404, detail=f"Report version {body.version_id} not found")
            video_id = str(revision["video_id"])
            if filters.get("incident_id", video_id) != video_id:
                raise HTTPException(
                    status_code=400,
                    detail=f"Report version {body.version_id} belongs to incident {video_id}, "
                    f"not {filters['incident_id']}",
                )
            official = await store.get_official_revision_id(video_id) == body.version_id
        except RevisionStoreUnavailableError as exc:
            raise HTTPException(status_code=503, detail=f"Report revisions unavailable: {exc}") from exc
        except HTTPException:
            raise
        except Exception as exc:
            logger.error("RevisionStore failed during incident chat: %s", exc, exc_info=True)
            raise HTTPException(status_code=502, detail=f"Report revision error: {exc}") from exc
        return revision, video_id, official

    async def _library(body: ChatRequest, session_id: str) -> ChatResponse:
        """A planned search over every incident's report, an exact count, and an answer with incident citations."""
        model = _model()
        history = [message.model_dump() for message in body.messages]
        question = next(m.content for m in reversed(body.messages) if m.role == "user").strip()
        try:
            plan = grounded_chat.check_plan(
                await model.complete(grounded_chat.plan_messages(question, history), grounded_chat.PLAN_SCHEMA)
            )
        except grounded_chat.ChatModelError as exc:
            raise HTTPException(status_code=502, detail=f"Chat model error: {exc}") from exc
        try:
            reports = await _index().reports()
        except Exception as exc:
            logger.error("Library index failed during library chat: %s", exc, exc_info=True)
            raise HTTPException(status_code=502, detail=f"Library read error: {exc}") from exc
        matching = [report for report in reports if grounded_chat.matches(report, plan)]
        candidates = grounded_chat.rank(matching, plan["query"])[: grounded_chat.MAX_LIBRARY_REPORTS]
        grounding: dict[str, Any] = {
            "mode": "grounded",
            "scope": "library",
            "model": model.model,
            "version_id": None,
            "official": None,
            "draft": False,
            "plan": plan,
            "matching": len(matching),
            "candidates": [report.incident_id for report in candidates],
            "dropped_citations": [],
            "prompt_sha256": grounded_chat.incident_contract.prompt_sha256(grounded_chat.LIBRARY_PROMPT_ID),
        }
        if not candidates:
            return ChatResponse(
                session_id=session_id,
                message=AssistantMessage(
                    content="No report in the library matches that question.", citations=[], insufficient_evidence=True
                ),
                grounding=grounding,
            )
        context = grounded_chat.library_context(candidates, len(matching))
        try:
            reply = await model.complete(
                grounded_chat.library_messages(context, history), grounded_chat.LIBRARY_ANSWER_SCHEMA
            )
            answer = grounded_chat.check_library_answer(reply, context, question=question)
        except grounded_chat.ChatModelError as exc:
            raise HTTPException(status_code=502, detail=f"Chat model error: {exc}") from exc
        by_report = {report.incident_id: report for report in candidates}
        citations = []
        for incident_id, item_id in answer.citations:
            report = by_report[incident_id]
            entry = next((e for e in report.evidence if str(e.get("item_id")) == item_id), {})
            item = next(i for i in context["reports"] if i["incident_id"] == incident_id)["items"]
            item_type = next(i["item_type"] for i in item if i["item_id"] == item_id)
            seconds = entry.get("box_seconds", entry.get("timestamp_seconds")) or 0
            citations.append(
                EvidenceCitation(
                    incident_id=incident_id,
                    item_id=item_id,
                    item_type=item_type,
                    timestamp_seconds=max(0.0, float(seconds)),
                    crop_image_url=resolve_r2_url(str(entry.get("image") or "")),
                )
            )
        grounding["dropped_citations"] = list(answer.dropped_citations)
        grounding["ids_resolved"] = list(answer.ids_resolved)
        # How each incident shown is named in prose (type and title), so a client can label citations without ids.
        grounding["incident_titles"] = {r["incident_id"]: grounded_chat.incident_mention(r) for r in context["reports"]}
        grounding["usage"] = dict(getattr(model, "last_usage", {}) or {})
        return ChatResponse(
            session_id=session_id,
            message=AssistantMessage(
                content=answer.content, citations=citations, insufficient_evidence=answer.insufficient_evidence
            ),
            grounding=grounding,
        )

    @router.post(
        "/api/v1/incidents/chat",
        response_model=ChatResponse,
        response_model_exclude_none=True,  # a template answer keeps exactly the chat-contract-v1 keys
        summary="Conversational Q&A over incidents and evidence crops with grounded citations",
        tags=["Incident Chat"],
    )
    async def chat_incidents(body: ChatRequest) -> ChatResponse:
        session_id = body.session_id or f"session_{uuid.uuid4().hex[:12]}"

        # Extract latest user message
        user_message: ChatMessage | None = None
        for msg in reversed(body.messages):
            if msg.role == "user":
                user_message = msg
                break

        if not user_message:
            raise HTTPException(status_code=400, detail="At least one message with role 'user' is required")

        query_text = user_message.content.strip()
        if not query_text:
            raise HTTPException(status_code=400, detail="User message content must not be empty")

        # Prepare search filters
        filters: dict[str, Any] = {}
        if body.context and body.context.scope == "incident" and body.context.incident_id:
            filters["incident_id"] = body.context.incident_id

        if selected_mode == "grounded" and (body.version_id is not None or "incident_id" in filters):
            return await _grounded(body, session_id, filters.get("incident_id"))
        if selected_mode == "grounded":
            return await _library(body, session_id)

        scope = "incident" if filters else "library"
        if body.version_id is not None:
            revision, video_id, official = await _load_version(body, filters, revisions)
            if not official or body.draft_report is not None:
                return _composed_answer(session_id, query_text, video_id, revision, body.draft_report)
            # The official revision goes through the search client (decision 2026-10-24).
            filters, scope = {"incident_id": video_id}, "official"

        if not getattr(client, "enabled", True):
            return ChatResponse(
                session_id=session_id,
                message=AssistantMessage(role="assistant", content=NOT_SEARCHED[scope], citations=[]),
            )
        try:
            hits = await client.search(query=query_text, top_k=5, filters=filters)
        except Exception as exc:
            logger.error("SearchClient failed during incident chat: %s", exc, exc_info=True)
            raise HTTPException(status_code=502, detail=f"Evidence search error: {exc}") from exc

        # Handle empty results cleanly
        if not hits:
            scope_desc = "" if scope == "library" else " in this incident"
            return ChatResponse(
                session_id=session_id,
                message=AssistantMessage(
                    role="assistant",
                    content=f"No matching incidents or evidence items were found for '{query_text}'{scope_desc}.",
                    citations=[],
                ),
            )

        # Ground answer with citations
        citations: list[EvidenceCitation] = []
        seen_items: set[tuple[str, str]] = set()
        cited_hits: list[dict[str, Any]] = []

        for hit in hits:
            item = hit.get("item", {})
            incident = hit.get("incident", {})
            inc_id = incident.get("incident_id", "unknown")
            item_id = item.get("item_id", "unknown")
            item_type = item.get("item_type", "entity")
            ts = float(item.get("timestamp_seconds", 0.0))
            crop_url = item.get("crop_image_url", "")

            key = (inc_id, item_id)
            if key not in seen_items:
                seen_items.add(key)
                citations.append(
                    EvidenceCitation(
                        incident_id=inc_id,
                        item_id=item_id,
                        item_type=item_type,
                        timestamp_seconds=ts,
                        crop_image_url=crop_url,
                    )
                )
                cited_hits.append(hit)

        # Prose names incidents by type and title and items by name; the ids stay in the citations.
        content = report_answers.compose_hits(cited_hits)

        return ChatResponse(
            session_id=session_id,
            message=AssistantMessage(
                role="assistant",
                content=content,
                citations=citations,
            ),
        )

    return router


NOT_SEARCHED = {
    "official": (
        "This chat mode doesn't answer questions about the official version yet, so no evidence was searched. "
        "You can ask about any other version of this report, or about an unsaved edit."
    ),
    "incident": (
        "This chat mode only answers questions about a report version, so no evidence was searched. "
        "Ask from the incident's report page."
    ),
    "library": (
        "This chat mode doesn't answer questions across the whole library yet, so no incidents were searched. "
        "Open an incident's report to ask about it."
    ),
}


def _composed_answer(
    session_id: str,
    query: str,
    video_id: str,
    revision: dict[str, Any],
    draft_report: dict[str, Any] | None,
) -> ChatResponse:
    """The answer to a question about one revision (or a draft of it), composed from its report.

    The citations are the items the answer relies on, each with the crop and second of its evidence entry
    (a draft item takes the saved crop of the same item in the base revision).
    """
    saved = revision.get("evidence") or []
    payload = revision.get("payload") if isinstance(revision.get("payload"), dict) else None
    report: dict[str, Any] | None = payload
    evidence, retired = saved, set()
    if draft_report is not None:
        # The console's draft holds only the edited fields; the title, timeline and the rest come from the base.
        report = report_answers.overlay_draft(payload, draft_report)
        evidence = _draft_evidence(draft_report, saved)
        retired = report_answers.item_ids(payload) - report_answers.item_ids(report)
    view = report_answers.report_view(report, evidence, retired=retired)
    answer = report_answers.compose(query, view)
    entries = {(str(e.get("item_type")), str(e.get("item_id"))): e for e in evidence if isinstance(e, dict)}
    citations: list[EvidenceCitation] = []
    for item_id in answer.item_ids:
        item = view.item(item_id)
        if item is None:
            continue
        entry = entries.get((item.kind, item_id), {})
        seconds = entry.get("timestamp_seconds")
        if not isinstance(seconds, int | float) or isinstance(seconds, bool):
            seconds = item.seconds or 0
        citations.append(
            EvidenceCitation(
                incident_id=video_id,
                item_id=item_id,
                item_type=item.kind,  # type: ignore[arg-type]
                timestamp_seconds=max(0.0, float(seconds)),
                crop_image_url=resolve_r2_url(str(entry.get("image") or "")),
            )
        )
    return ChatResponse(
        session_id=session_id,
        message=AssistantMessage(role="assistant", content=answer.text, citations=citations),
    )


def register_incident_chat_routes(
    app: FastAPI,
    search_client: SearchClient | None = None,
    revision_store: RevisionStore | None = None,
    chat_model: grounded_chat.ChatModel | None = None,
) -> None:
    """Register ``POST /api/v1/incidents/chat`` onto the FastAPI app (mode from ``INCIDENT_CHAT_MODE``)."""
    try:
        app.include_router(create_incident_chat_router(search_client, revision_store, chat_model))
        logger.info("Registered POST /api/v1/incidents/chat")
    except Exception as exc:
        logger.error("Failed to register incident chat routes: %s", exc, exc_info=True)
        raise
