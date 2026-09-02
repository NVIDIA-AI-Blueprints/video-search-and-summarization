# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Versioned types shared by the gateway HTTP surface and its connectors."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime

PROTOCOL_VERSION = "1.0"
MAX_IDENTIFIER_LENGTH = 256
MAX_MESSAGE_CONTENT_LENGTH = 1_000_000
MAX_TRANSCRIPT_LENGTH = 5_000_000
ALLOWED_ROLES = frozenset({"system", "developer", "user", "assistant"})
TERMINAL_EVENT_TYPES = frozenset({"run.completed", "run.failed", "run.cancelled"})


class ContractError(ValueError):
    """The client supplied an invalid gateway contract value."""


def _required_identifier(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{name} must be a non-empty string")
    value = value.strip()
    if len(value) > MAX_IDENTIFIER_LENGTH:
        raise ContractError(
            f"{name} must be at most {MAX_IDENTIFIER_LENGTH} characters"
        )
    if any(ord(character) < 32 for character in value):
        raise ContractError(f"{name} must not contain control characters")
    return value


@dataclass(frozen=True, slots=True)
class Message:
    """One text message in a run input or recovery transcript."""

    role: str
    content: str

    @classmethod
    def from_dict(cls, value: object, name: str) -> Message:
        if not isinstance(value, dict):
            raise ContractError(f"{name} must be an object")
        role = value.get("role")
        content = value.get("content")
        if role not in ALLOWED_ROLES:
            allowed = ", ".join(sorted(ALLOWED_ROLES))
            raise ContractError(f"{name}.role must be one of: {allowed}")
        if not isinstance(content, str):
            raise ContractError(f"{name}.content must be a string")
        if len(content) > MAX_MESSAGE_CONTENT_LENGTH:
            raise ContractError(
                f"{name}.content must be at most {MAX_MESSAGE_CONTENT_LENGTH} characters",
            )
        return cls(role=role, content=content)

    def to_responses_item(self) -> dict[str, object]:
        return {
            "type": "message",
            "role": self.role,
            "content": [{"type": "input_text", "text": self.content}],
        }

    def to_chat_message(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass(frozen=True, slots=True)
class CreateRunRequest:
    """A new turn plus an optional transcript used to recover gateway state."""

    thread_id: str
    input: tuple[Message, ...]
    history: tuple[Message, ...] = ()
    surface: str = "vss-ui"
    instructions: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, value: object) -> CreateRunRequest:
        if not isinstance(value, dict):
            raise ContractError("request body must be an object")

        thread_id = _required_identifier(value.get("thread_id"), "thread_id")
        input_value = value.get("input")
        if not isinstance(input_value, list) or not input_value:
            raise ContractError("input must be a non-empty array")
        messages = tuple(
            Message.from_dict(message, f"input[{index}]")
            for index, message in enumerate(input_value)
        )

        history_value = value.get("history", [])
        if not isinstance(history_value, list):
            raise ContractError("history must be an array")
        history = tuple(
            Message.from_dict(message, f"history[{index}]")
            for index, message in enumerate(history_value)
        )

        surface_value = value.get("surface", "vss-ui")
        surface = _required_identifier(surface_value, "surface")
        instructions = value.get("instructions")
        if instructions is not None and not isinstance(instructions, str):
            raise ContractError("instructions must be a string")
        metadata = value.get("metadata", {})
        if not isinstance(metadata, dict):
            raise ContractError("metadata must be an object")

        transcript_length = sum(len(message.content) for message in messages + history)
        if transcript_length > MAX_TRANSCRIPT_LENGTH:
            raise ContractError(
                f"input and history must total at most {MAX_TRANSCRIPT_LENGTH} characters"
            )

        return cls(
            thread_id=thread_id,
            input=messages,
            history=history,
            surface=surface,
            instructions=instructions,
            metadata=metadata,
        )

    def canonical_dict(self) -> dict[str, object]:
        return {
            "thread_id": self.thread_id,
            "input": [message.to_chat_message() for message in self.input],
            "history": [message.to_chat_message() for message in self.history],
            "surface": self.surface,
            "instructions": self.instructions,
            "metadata": self.metadata,
        }

    def history_prefix(self) -> tuple[Message, ...]:
        """Return recovery history without a duplicate copy of the new input."""

        input_length = len(self.input)
        if (
            input_length <= len(self.history)
            and self.history[-input_length:] == self.input
        ):
            return self.history[:-input_length]
        return self.history

    def full_transcript(self) -> tuple[Message, ...]:
        """Accept history both with and without the current input appended."""

        return self.history_prefix() + self.input


@dataclass(frozen=True, slots=True)
class ConnectorEvent:
    """A connector-neutral event produced while an upstream turn runs."""

    type: str
    data: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RunEvent:
    """An immutable, replayable event in one gateway run."""

    sequence: int
    type: str
    run_id: str
    thread_id: str
    data: dict[str, object]
    created_at: str

    @classmethod
    def create(
        cls,
        *,
        sequence: int,
        type: str,
        run_id: str,
        thread_id: str,
        data: dict[str, object] | None = None,
    ) -> RunEvent:
        return cls(
            sequence=sequence,
            type=type,
            run_id=run_id,
            thread_id=thread_id,
            data=data or {},
            created_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "protocol_version": PROTOCOL_VERSION,
            "id": str(self.sequence),
            "type": self.type,
            "run_id": self.run_id,
            "thread_id": self.thread_id,
            "created_at": self.created_at,
            "data": self.data,
        }

    def to_sse(self) -> bytes:
        payload = json.dumps(self.to_dict(), separators=(",", ":"), ensure_ascii=False)
        return f"id: {self.sequence}\nevent: {self.type}\ndata: {payload}\n\n".encode()


def transcript_digest(messages: tuple[Message, ...] | list[Message]) -> str:
    """Return a stable digest for continuity checks without retaining client IDs."""

    serialized = json.dumps(
        [message.to_chat_message() for message in messages],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(serialized.encode()).hexdigest()


def request_digest(request: CreateRunRequest) -> str:
    serialized = json.dumps(
        request.canonical_dict(),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(serialized.encode()).hexdigest()
