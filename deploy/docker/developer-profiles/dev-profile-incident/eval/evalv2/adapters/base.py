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

"""The adapter protocol: provider specifics only, never scoring.

An adapter sends one canonical request to one model (or one deployed pipeline) and returns what came back, with
usage, timing, every attempt, and references to the stored request and response artifacts. It charges every
HTTP attempt to the budget before sending it and applies the run's retry policy. It never judges the content:
validation and scoring belong to tasks.
"""

from __future__ import annotations

import copy
import re
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlsplit, urlunsplit

from evalv2.core.artifacts import ArtifactRef, ArtifactStore
from evalv2.core.budget import BudgetLedger
from evalv2.core.instrument import Timing, Usage
from evalv2.core.retry import Failure, RetryPolicy

CAPABILITY_NAMES = ("video_input", "image_input", "text_input", "json_schema", "bbox")
# The most one P1 attempt can use, as V1 charges it (``experiment_runner.P1_TOKENS_CEILING``, D11): the largest
# Cosmos 3 Super prompt, rounded up, plus the request's max_tokens (16,384). Adapters may not import V1, so the value
# is repeated here and a test keeps the two equal. Preflight assumes the same bound per attempt.
P1_TOKENS_CEILING = 35000


class AdapterConfigError(RuntimeError):
    """The adapter is not configured (for example a missing endpoint or key); nothing was sent."""


_URL_IN_TEXT = re.compile(r"https?://[^\s'\"<>]+", re.IGNORECASE)


def redact_url(url: str) -> str:
    """An http(s) URL without its query string, fragment or user info: a signed URL's signature and a
    ``user:password@`` are credentials, never stored. The host and port are kept."""
    parts = urlsplit(url)
    if parts.scheme.lower() not in ("http", "https") or not (parts.query or parts.fragment or "@" in parts.netloc):
        return url
    host_and_port = parts.netloc.rpartition("@")[2]  # the user info, if any, ends at the last "@"
    return urlunsplit((parts.scheme, host_and_port, parts.path, "", ""))


def redact_text(text: str) -> str:
    """Free text (an error body, an exception message) with every http(s) URL in it redacted by :func:`redact_url`.

    A provider's 400 can echo the request's signed video URL (``url='https://...?X-Amz-Signature=...'``); a failure's
    detail is stored and reported, so it never keeps the signature.
    """
    return _URL_IN_TEXT.sub(lambda match: redact_url(match.group(0)), text or "")


def redact_strings(value: Any) -> Any:
    """``value`` with :func:`redact_text` applied to every string inside it (dicts and lists are copied)."""
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Mapping):
        return {key: redact_strings(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [redact_strings(item) for item in value]
    return value


def stored_request(request: Mapping[str, Any]) -> dict[str, Any]:
    """The request as stored: every ``url`` in its message content parts (a video or image) without a signature.

    The request sent is unchanged; only the stored copy is redacted, so a re-signed URL makes the same artifact.
    """
    stored = copy.deepcopy(dict(request))
    for message in stored.get("messages") or []:
        content = message.get("content") if isinstance(message, dict) else None
        for part in content if isinstance(content, list) else []:
            for value in part.values() if isinstance(part, dict) else []:
                if isinstance(value, dict) and isinstance(value.get("url"), str):
                    value["url"] = redact_url(value["url"])
    return stored


@dataclass(frozen=True)
class Capabilities:
    video_input: bool = False
    image_input: bool = False
    text_input: bool = True
    json_schema: bool = False
    bbox: bool = False
    frame_policy: str = "unknown"
    verified: bool = False
    notes: str = ""
    listed_on: tuple[str, ...] = ()  # endpoints whose model listing named this id (RB1, read-only)

    def missing(self, requires: Iterable[str]) -> list[str]:
        """Required capabilities this subject lacks; an empty list means the task is supported."""
        unknown = sorted(set(requires) - set(CAPABILITY_NAMES))
        if unknown:
            raise ValueError(f"unknown capabilities {unknown}")
        return sorted(name for name in requires if not getattr(self, name))


@dataclass
class CallContext:
    store: ArtifactStore
    ledger: BudgetLedger
    policy: RetryPolicy
    subject: str
    task: str
    budget_ledger: str = "inference"
    tokens_bound: int = P1_TOKENS_CEILING  # pessimistic tokens per attempt, charged until usage is measured
    sleep: Callable[[float], None] = time.sleep


@dataclass(frozen=True)
class CallResult:
    request_ref: ArtifactRef
    response_ref: ArtifactRef | None
    text: str | None
    finish_reason: str | None
    usage: Usage
    timing: Timing | None
    failure: Failure | None
    attempts: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return self.failure is None

    def to_dict(self) -> dict[str, Any]:
        return {"request_ref": str(self.request_ref),
                "response_ref": str(self.response_ref) if self.response_ref else None,
                "finish_reason": self.finish_reason, "usage": self.usage.to_dict(),
                "timing": self.timing.to_dict() if self.timing else None,
                "failure": self.failure.to_dict() if self.failure else None, "attempts": list(self.attempts)}


@runtime_checkable
class ModelAdapter(Protocol):
    id: str
    model: str
    capabilities: Capabilities

    def complete(self, request: Mapping[str, Any], ctx: CallContext) -> CallResult: ...


@dataclass(frozen=True)
class PipelineResult:
    """One whole-pipeline run (Track B): the deployed pipeline's reply and what it cost, ledger by ledger."""

    status: int  # the HTTP status; 0 when no reply arrived
    outcome: str  # "answered" | "contract_rejected" | "request_failed"
    body: Mapping[str, Any]
    request_ref: str
    response_ref: str | None
    provenance: Mapping[str, Any] | None
    elapsed_ms: float
    charged: Mapping[str, Mapping[str, int]] = field(default_factory=dict)
    error: str | None = None

    @property
    def _reply(self) -> Mapping[str, Any]:
        """The body of a 200, the detail of a 422 (where a contract rejection carries the raw output)."""
        if self.status == 422:
            detail = self.body.get("detail")
            return detail if isinstance(detail, Mapping) else {}
        return self.body

    @property
    def raw_output(self) -> str | None:
        return self._reply.get("raw_output")

    @property
    def finish_reason(self) -> str | None:
        return self._reply.get("finish_reason")

    @property
    def p1_attempts(self) -> list[Mapping[str, Any]]:
        """Every P1 call the agent made, in order (``attempt``, ``outcome``: valid, retried or failed, ...)."""
        return [dict(a) for a in self._reply.get("p1_attempts") or [] if isinstance(a, Mapping)]

    @property
    def experiment(self) -> Mapping[str, Any]:
        return self._reply.get("experiment") or {}

    @property
    def p1_report(self) -> Mapping[str, Any] | None:
        return self.experiment.get("p1_report") if self.outcome == "answered" else None

    @property
    def final_report(self) -> Mapping[str, Any] | None:
        return self.body.get("report") if self.outcome == "answered" else None

    @property
    def evidence(self) -> list[Mapping[str, Any]]:
        return list(self.body.get("evidence") or []) if self.outcome == "answered" else []

    @property
    def object_tracking(self) -> Mapping[str, Any] | None:
        """The object tracker's summary: the reply's top-level ``object_tracking`` (``experiment.object_tracking`` is
        only the request's setting), with ``detector`` the tracker's call budget (``cap``, ``calls``, ...)."""
        summary = self.body.get("object_tracking") if self.outcome == "answered" else None
        return summary if isinstance(summary, Mapping) else None


@runtime_checkable
class PipelineAdapter(Protocol):
    """A deployed pipeline run as one subject (Track B): it sends one analysis and accounts for every call."""

    id: str
    model: str
    capabilities: Capabilities

    def analyze(self, incident_id: str, request: Mapping[str, Any], ctx: CallContext) -> PipelineResult: ...
