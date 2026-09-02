# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Extract versioned VSS UI artifacts from an agent's streamed text."""

from __future__ import annotations

import hashlib
import json
import re
from collections import OrderedDict
from dataclasses import dataclass

from .contract import ConnectorEvent
from .json_codec import strict_json_loads

ARTIFACT_OPEN = "<vss-ui-artifact>"
ARTIFACT_CLOSE = "</vss-ui-artifact>"
ARTIFACT_PROTOCOL_VERSION = "1.0"
MAX_ARTIFACT_LENGTH = 1_000_000
MAX_TRACKED_ARTIFACTS = 10_000
MAX_JSON_DOCUMENTS = 100
_KIND_PATTERN = re.compile(r"^vss\.[a-z0-9]+(?:[._-][a-z0-9]+)*$")


@dataclass(frozen=True, slots=True)
class VssUiArtifact:
    """One validated, connector-independent UI artifact."""

    artifact_id: str
    kind: str
    payload: dict[str, object]

    def event(self) -> ConnectorEvent:
        return ConnectorEvent(
            "artifact.created",
            {
                "artifact_id": self.artifact_id,
                "version": ARTIFACT_PROTOCOL_VERSION,
                "kind": self.kind,
                "payload": self.payload,
            },
        )


def parse_artifact(value: str) -> VssUiArtifact | None:
    """Validate an artifact JSON payload without interpreting its domain data."""

    if not value or len(value) > MAX_ARTIFACT_LENGTH:
        return None
    try:
        decoded = strict_json_loads(value)
    except ValueError:
        return None
    if not isinstance(decoded, dict):
        return None
    if decoded.get("version") != ARTIFACT_PROTOCOL_VERSION:
        return None
    kind = decoded.get("kind")
    payload = decoded.get("payload")
    if not isinstance(kind, str) or not _KIND_PATTERN.fullmatch(kind):
        return None
    if not isinstance(payload, dict):
        return None

    try:
        canonical = json.dumps(
            {"version": ARTIFACT_PROTOCOL_VERSION, "kind": kind, "payload": payload},
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError, RecursionError):
        return None
    digest = hashlib.sha256(canonical.encode()).hexdigest()[:24]
    return VssUiArtifact(
        artifact_id=f"artifact_{digest}",
        kind=kind,
        payload=payload,
    )


def _retained_marker_prefix(value: str) -> int:
    """Return how much of a possible opening marker suffix must be buffered."""

    maximum = min(len(value), len(ARTIFACT_OPEN) - 1)
    for length in range(maximum, 0, -1):
        if ARTIFACT_OPEN.startswith(value[-length:]):
            return length
    return 0


class ArtifactStreamParser:
    """Remove artifact envelopes from text and emit their structured events.

    The parser is intentionally connector-neutral. An agent can therefore use any
    upstream protocol as long as its final text carries the same small envelope.
    Malformed or unsupported envelopes are returned as ordinary text so a gateway
    bug can never silently eat an agent response.
    """

    def __init__(self, *, suppress_invalid_after_artifact: bool = False) -> None:
        self._buffer = ""
        self._seen: OrderedDict[str, None] = OrderedDict()
        self._suppress_invalid_after_artifact = suppress_invalid_after_artifact

    def _artifact_event(self, artifact: VssUiArtifact) -> ConnectorEvent | None:
        if artifact.artifact_id in self._seen:
            self._seen.move_to_end(artifact.artifact_id)
            return None
        self._seen[artifact.artifact_id] = None
        if len(self._seen) > MAX_TRACKED_ARTIFACTS:
            self._seen.popitem(last=False)
        return artifact.event()

    @staticmethod
    def _json_documents(value: str) -> list[object]:
        """Decode bounded JSON documents embedded in CLI stdout.

        ``JSONDecoder`` locates each document, while ``strict_json_loads``
        re-validates its exact bytes to reject duplicate keys and non-finite
        numbers before any value can become a UI artifact.
        """

        decoder = json.JSONDecoder()
        documents: list[object] = []
        cursor = 0
        while cursor < len(value) and len(documents) < MAX_JSON_DOCUMENTS:
            start = value.find("{", cursor)
            if start < 0:
                break
            try:
                _, end = decoder.raw_decode(value, start)
            except (json.JSONDecodeError, RecursionError):
                cursor = start + 1
                continue
            try:
                documents.append(strict_json_loads(value[start:end]))
            except ValueError:
                cursor = start + 1
                continue
            cursor = end
        return documents

    def _inspect_vss_cli_search(self, value: str) -> list[ConnectorEvent]:
        """Recognize an exact completed VSS CLI search transaction.

        The completion marker binds the candidate result to a successful
        search job. This avoids asking a model to reconstruct JSON while still
        requiring the same evidence the search skill validates.
        """

        documents = self._json_documents(value)
        completed_jobs = {
            document.get("job_id")
            for document in documents
            if isinstance(document, dict)
            and document.get("event") == "vss_job_completed"
            and document.get("group") == "search"
            and document.get("status") == "completed"
            and document.get("exit_hint") == 0
            and isinstance(document.get("job_id"), str)
            and 0 < len(document["job_id"]) <= 256
        }
        events: list[ConnectorEvent] = []
        for document in documents:
            if (
                not isinstance(document, dict)
                or document.get("job_id") not in completed_jobs
                or not isinstance(document.get("data"), list)
                or not isinstance(document.get("search_messages"), list)
            ):
                continue
            try:
                encoded = json.dumps(
                    {
                        "version": ARTIFACT_PROTOCOL_VERSION,
                        "kind": "vss.search.results",
                        "payload": document,
                    },
                    allow_nan=False,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            except (TypeError, ValueError, RecursionError):
                continue
            artifact = parse_artifact(encoded)
            if artifact is None:
                continue
            event = self._artifact_event(artifact)
            if event is not None:
                events.append(event)
        return events

    def inspect_complete(self, value: object) -> list[ConnectorEvent]:
        """Find complete envelopes in a tool result without changing its content.

        Responses-compatible harnesses differ in how much of their internal tool
        trajectory they expose. Inspecting complete tool outputs lets a harness
        publish the same artifact directly from a skill, while streamed final text
        remains the portable fallback. Both paths share the deduplication set.
        """

        events: list[ConnectorEvent] = []
        stack: list[tuple[object, int]] = [(value, 0)]
        visited = 0
        while stack and visited < 1_000:
            candidate, depth = stack.pop()
            visited += 1
            if isinstance(candidate, str):
                if len(candidate) > MAX_ARTIFACT_LENGTH * 2:
                    continue
                events.extend(self._inspect_vss_cli_search(candidate))
                cursor = 0
                while cursor < len(candidate):
                    opening = candidate.find(ARTIFACT_OPEN, cursor)
                    if opening < 0:
                        break
                    payload_start = opening + len(ARTIFACT_OPEN)
                    closing = candidate.find(ARTIFACT_CLOSE, payload_start)
                    if closing < 0:
                        break
                    artifact = parse_artifact(candidate[payload_start:closing].strip())
                    if artifact is not None:
                        event = self._artifact_event(artifact)
                        if event is not None:
                            events.append(event)
                    cursor = closing + len(ARTIFACT_CLOSE)
            elif depth < 4 and isinstance(candidate, dict):
                stack.extend((nested, depth + 1) for nested in candidate.values())
            elif depth < 4 and isinstance(candidate, (list, tuple)):
                stack.extend((nested, depth + 1) for nested in candidate)
        return events

    def feed(self, delta: str) -> list[ConnectorEvent]:
        if not delta:
            return []
        self._buffer += delta
        events: list[ConnectorEvent] = []

        while self._buffer:
            opening = self._buffer.find(ARTIFACT_OPEN)
            if opening < 0:
                retained = _retained_marker_prefix(self._buffer)
                visible = self._buffer[:-retained] if retained else self._buffer
                if visible:
                    events.append(ConnectorEvent("message.delta", {"delta": visible}))
                self._buffer = self._buffer[-retained:] if retained else ""
                break

            if opening:
                events.append(
                    ConnectorEvent("message.delta", {"delta": self._buffer[:opening]})
                )
                self._buffer = self._buffer[opening:]

            closing = self._buffer.find(ARTIFACT_CLOSE, len(ARTIFACT_OPEN))
            if closing < 0:
                if len(self._buffer) > MAX_ARTIFACT_LENGTH + len(ARTIFACT_OPEN):
                    # This cannot be a valid envelope. Release the opening marker and
                    # resume scanning the remainder instead of buffering indefinitely.
                    events.append(
                        ConnectorEvent("message.delta", {"delta": ARTIFACT_OPEN})
                    )
                    self._buffer = self._buffer[len(ARTIFACT_OPEN) :]
                    continue
                break

            end = closing + len(ARTIFACT_CLOSE)
            raw_envelope = self._buffer[:end]
            raw_payload = self._buffer[len(ARTIFACT_OPEN) : closing].strip()
            self._buffer = self._buffer[end:]
            artifact = parse_artifact(raw_payload)
            if artifact is None:
                # Once this run has already produced a validated artifact from
                # a tool result, a later artifact-shaped block is transport
                # noise even if the model damaged its JSON while copying it.
                # Preserve malformed markup when no valid artifact exists so
                # ordinary assistant content is never silently discarded.
                if not self._seen or not self._suppress_invalid_after_artifact:
                    events.append(
                        ConnectorEvent("message.delta", {"delta": raw_envelope})
                    )
                continue
            event = self._artifact_event(artifact)
            if event is not None:
                events.append(event)

        return events

    def finish(self) -> list[ConnectorEvent]:
        if not self._buffer:
            return []
        buffered = self._buffer
        self._buffer = ""
        return [ConnectorEvent("message.delta", {"delta": buffered})]


def strip_artifact_envelopes(value: str) -> str:
    """Remove valid VSS envelopes from text used for continuity checks.

    UI presentation markers are not conversation content and must not enter a
    harness's recovered history. Invalid or incomplete markers remain visible.
    """

    parser = ArtifactStreamParser()
    events = parser.feed(value) + parser.finish()
    return "".join(
        str(event.data.get("delta", ""))
        for event in events
        if event.type == "message.delta"
    )


def strip_artifacts_from_value(value: object, *, depth: int = 0) -> object:
    """Remove valid envelopes from a bounded nested tool-output structure."""

    if isinstance(value, str):
        return strip_artifact_envelopes(value)
    if depth >= 4:
        return value
    if isinstance(value, dict):
        return {
            key: strip_artifacts_from_value(item, depth=depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [strip_artifacts_from_value(item, depth=depth + 1) for item in value]
    return value
