# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""File-backed human interaction broker shared by NemoClaw and the VSS UI."""

import asyncio
import json
import os
from pathlib import Path
import re
import time
from typing import Annotated
from typing import Any
from typing import Literal
from uuid import UUID
from uuid import uuid4

from pydantic import BaseModel
from pydantic import Field
from pydantic import field_validator

_POLL_INTERVAL_SECONDS = 0.1
_ACK_RETENTION_SECONDS = 24 * 60 * 60
_UUID_PATTERN = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")


class InteractionOption(BaseModel):
    """One selectable answer."""

    label: str = Field(min_length=1, max_length=256)
    description: str = Field(default="", max_length=2_000)

    @field_validator("label")
    @classmethod
    def _normalize_label(cls, label: str) -> str:
        normalized = label.strip()
        if not normalized:
            raise ValueError("option labels must not be blank")
        return normalized

    @field_validator("description")
    @classmethod
    def _normalize_description(cls, description: str) -> str:
        return description.strip()


class InteractionQuestion(BaseModel):
    """One question rendered by the VSS chat sidebar."""

    question_id: str = Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=64)
    header: str = Field(default="", max_length=12)
    prompt: str = Field(min_length=1, max_length=10_000)
    options: list[InteractionOption] = Field(default_factory=list, max_length=4)
    multi_select: bool = False
    allow_other: bool = True

    @field_validator("header", "prompt")
    @classmethod
    def _normalize_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized and value:
            raise ValueError("question text must not be blank")
        return normalized

    @field_validator("options")
    @classmethod
    def _validate_options(cls, options: list[InteractionOption]) -> list[InteractionOption]:
        if len(options) == 1:
            raise ValueError("options must be empty or contain at least two choices")
        labels = [option.label.strip().casefold() for option in options]
        if len(labels) != len(set(labels)):
            raise ValueError("option labels must be unique")
        return options


class AskUserQuestionInput(BaseModel):
    """Input for a structured, blocking human question."""

    interaction_id: UUID = Field(
        description="A newly generated UUID for this interaction; never reuse an earlier value."
    )
    questions: Annotated[list[InteractionQuestion], Field(min_length=1, max_length=3)]
    timeout_seconds: int = Field(default=900, ge=30, le=3_600)

    @field_validator("interaction_id")
    @classmethod
    def _validate_interaction_id(cls, interaction_id: UUID) -> UUID:
        if not _UUID_PATTERN.fullmatch(str(interaction_id)):
            raise ValueError("interaction_id must be an RFC 4122 UUID")
        return interaction_id

    @field_validator("questions")
    @classmethod
    def _validate_question_ids(cls, questions: list[InteractionQuestion]) -> list[InteractionQuestion]:
        ids = [question.question_id for question in questions]
        if len(ids) != len(set(ids)):
            raise ValueError("question_id values must be unique")
        return questions


class InteractionResponse(BaseModel):
    """Answer envelope written by the VSS UI adapter."""

    version: Literal[1]
    interaction_id: UUID
    answers: dict[str, list[str]]


class InteractionAcknowledgement(BaseModel):
    """Terminal receipt written after the MCP tool consumes a response."""

    version: Literal[1]
    interaction_id: UUID
    status: Literal["answered", "expired", "rejected", "cancelled"]
    answers: dict[str, list[str]] | None = None
    error: str | None = None


def _broker_path(directory: Path, interaction_id: UUID, suffix: str) -> Path:
    return directory / f"{interaction_id}.{suffix}.json"


def _write_json_exclusive(path: Path, payload: dict[str, Any]) -> None:
    """Publish one complete JSON envelope without exposing a partial file."""

    temporary_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        descriptor = os.open(temporary_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o660)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(payload, output, separators=(",", ":"))
            output.flush()
            os.fsync(output.fileno())
        temporary_path.chmod(0o660)
        os.link(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _write_acknowledgement(
    acknowledgement_path: Path,
    interaction_id: UUID,
    status: Literal["answered", "expired", "rejected", "cancelled"],
    *,
    answers: dict[str, list[str]] | None = None,
    error: str | None = None,
) -> None:
    acknowledgement = InteractionAcknowledgement(
        version=1,
        interaction_id=interaction_id,
        status=status,
        answers=answers,
        error=error,
    ).model_dump(mode="json", exclude_none=True)
    try:
        _write_json_exclusive(acknowledgement_path, acknowledgement)
    except FileExistsError:
        try:
            existing = InteractionAcknowledgement.model_validate_json(acknowledgement_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RuntimeError("interaction acknowledgement is unreadable") from exc
        if existing.model_dump(mode="json", exclude_none=True) != acknowledgement:
            raise RuntimeError("interaction acknowledgement conflicts with the terminal result") from None


def _ensure_interaction_broker(directory: Path) -> int:
    directory.mkdir(parents=True, exist_ok=True, mode=0o2770)
    directory.chmod(0o2770)
    if not os.access(directory, os.R_OK | os.W_OK | os.X_OK):
        raise RuntimeError(f"interaction broker directory is not accessible: '{directory}'")
    return directory.stat().st_gid


def prepare_interaction_broker(directory: Path, *, now_ms: int | None = None) -> int:
    """Create the private shared spool and remove envelopes that cannot be resumed."""

    broker_gid = _ensure_interaction_broker(directory)

    current_ms = int(time.time() * 1_000) if now_ms is None else now_ms
    for request_path in directory.glob("*.request.json"):
        interaction_id: UUID | None = None
        try:
            request = json.loads(request_path.read_text(encoding="utf-8"))
            interaction_id = UUID(str(request["interaction_id"]))
        except (OSError, ValueError, KeyError, TypeError):
            pass
        stem = request_path.name.removesuffix(".request.json")
        if interaction_id is not None and str(interaction_id) == stem:
            _write_acknowledgement(
                _broker_path(directory, interaction_id, "ack"),
                interaction_id,
                "cancelled",
                error="The orchestrator restarted while waiting for user input.",
            )
        request_path.unlink(missing_ok=True)
        (directory / f"{stem}.response.json").unlink(missing_ok=True)

    retention_cutoff = current_ms / 1_000 - _ACK_RETENTION_SECONDS
    for acknowledgement_path in directory.glob("*.ack.json"):
        try:
            stale = acknowledgement_path.stat().st_mtime < retention_cutoff
        except FileNotFoundError:
            continue
        if stale:
            acknowledgement_path.unlink(missing_ok=True)

    for orphan_path in [*directory.glob("*.response.json"), *directory.glob(".*.tmp")]:
        orphan_path.unlink(missing_ok=True)
    return broker_gid


def _validate_answers(request: AskUserQuestionInput, response: InteractionResponse) -> dict[str, list[str]]:
    if response.interaction_id != request.interaction_id:
        raise ValueError("interaction response id does not match the pending request")
    expected = {question.question_id: question for question in request.questions}
    if set(response.answers) != set(expected):
        raise ValueError("interaction response must answer every pending question exactly once")
    normalized: dict[str, list[str]] = {}
    for question_id, question in expected.items():
        answers = [answer.strip() for answer in response.answers[question_id]]
        if not answers or any(not answer for answer in answers) or len(answers) != len(set(answers)):
            raise ValueError(f"{question_id} contains an empty or duplicate answer")
        if not question.multi_select and len(answers) != 1:
            raise ValueError(f"{question_id} accepts exactly one answer")
        labels = {option.label for option in question.options}
        if labels and not question.allow_other and any(answer not in labels for answer in answers):
            raise ValueError(f"{question_id} requires a declared option")
        normalized[question_id] = answers
    return normalized


async def ask_user_question(directory: Path, request: AskUserQuestionInput) -> dict[str, Any]:
    """Publish a question and block until the UI answers or the request expires."""

    _ensure_interaction_broker(directory)
    request_path = _broker_path(directory, request.interaction_id, "request")
    response_path = _broker_path(directory, request.interaction_id, "response")
    acknowledgement_path = _broker_path(directory, request.interaction_id, "ack")
    created_at_ms = int(time.time() * 1_000)
    expires_at_ms = created_at_ms + request.timeout_seconds * 1_000
    payload: dict[str, Any] = {
        "version": 1,
        "interaction_id": str(request.interaction_id),
        "questions": [question.model_dump() for question in request.questions],
        "created_at_ms": created_at_ms,
        "expires_at_ms": expires_at_ms,
    }
    if acknowledgement_path.exists():
        raise RuntimeError("interaction_id was previously used")
    try:
        _write_json_exclusive(request_path, payload)
    except FileExistsError as exc:
        raise RuntimeError("interaction_id is already pending") from exc

    terminal = False
    try:
        deadline = time.monotonic() + max(0, (expires_at_ms - int(time.time() * 1_000)) / 1_000)
        while time.monotonic() < deadline:
            try:
                raw_response = await asyncio.to_thread(response_path.read_text, encoding="utf-8")
            except FileNotFoundError:
                await asyncio.sleep(_POLL_INTERVAL_SECONDS)
                continue
            try:
                response = InteractionResponse.model_validate_json(raw_response)
                answers = _validate_answers(request, response)
            except (ValueError, json.JSONDecodeError) as exc:
                terminal = True
                _write_acknowledgement(
                    acknowledgement_path,
                    request.interaction_id,
                    "rejected",
                    error=str(exc),
                )
                raise RuntimeError(f"invalid interaction response: {exc}") from exc
            terminal = True
            _write_acknowledgement(
                acknowledgement_path,
                request.interaction_id,
                "answered",
                answers=answers,
            )
            return {
                "status": "answered",
                "interaction_id": str(request.interaction_id),
                "answers": answers,
            }
        terminal = True
        expiry_error = "The user did not answer before the interaction expired."
        _write_acknowledgement(
            acknowledgement_path,
            request.interaction_id,
            "expired",
            error=expiry_error,
        )
        return {
            "status": "expired",
            "interaction_id": str(request.interaction_id),
            "error": expiry_error,
        }
    except asyncio.CancelledError:
        terminal = True
        _write_acknowledgement(
            acknowledgement_path,
            request.interaction_id,
            "cancelled",
            error="The agent run was cancelled while waiting for user input.",
        )
        raise
    finally:
        if terminal:
            request_path.unlink(missing_ok=True)
            response_path.unlink(missing_ok=True)
