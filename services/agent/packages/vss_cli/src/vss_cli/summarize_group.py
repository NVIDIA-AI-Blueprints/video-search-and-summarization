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
"""``vss summarize`` on the fixed command-group grammar.

The group is a thin client over the existing LVS ``POST /v1/summarize`` API.
It surrounds that call with the unified-memory lifecycle supplied by
``vss_core.memory``; video inference remains an LVS responsibility.
"""

from __future__ import annotations

from collections.abc import Callable
import json
from pathlib import PurePosixPath
import secrets
import time
from typing import Any
from typing import ClassVar
from typing import Protocol
from typing import Self

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import model_validator

from . import config as config_mod
from . import memory as memory_mod
from .exits import Exit
from .group import CommandGroup
from .group import Context
from .group import InvalidInput
from .group import Result

_SUMMARIZE_PATH = PurePosixPath("/v1/summarize")
_REQUEST_TIMEOUT_SECONDS = 3600.0
_CROCKFORD32 = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


class SummarizeInput(BaseModel):
    """Summarize one video through the configured LVS deployment.

    Exactly one source is required: ``--id`` names an uploaded asset and
    ``--url`` supplies an HTTP/HTTPS/S3 video URL. ``scenario`` and ``events``
    mirror the LVS request contract. Repeat ``--event`` to request multiple
    event types; omitting it sends the required empty event list.
    """

    model_config = ConfigDict(extra="forbid")

    id: str | None = Field(None, description="ID of an uploaded video asset.")
    url: str | None = Field(None, description="HTTP/HTTPS/S3 URL of the video to summarize.")
    scenario: str = Field(..., min_length=1, description="Use-case context, such as warehouse or security.")
    events: list[str] = Field(
        default_factory=list,
        description="Event type to detect; repeatable.",
        json_schema_extra={"cli_flag": "--event"},
    )
    chunk_duration: int = Field(
        10,
        ge=0,
        le=3600,
        description="Video chunk duration in seconds; 0 disables chunking.",
    )
    prompt: str | None = Field(None, description="Additional summarization instruction.")
    model: str | None = Field(
        None,
        description="VLM model. Defaults to the model recorded by `vss configure`.",
    )

    @model_validator(mode="after")
    def validate_source(self) -> Self:
        if bool(self.id) == bool(self.url):
            raise ValueError("exactly one of id or url is required")
        return self


class SummaryRunner(Protocol):
    """Execution boundary injected into :class:`SummarizeGroup`."""

    def summarize(self, request: SummarizeInput) -> dict[str, Any]: ...


class LvsSummaryRunner:
    """HTTP adapter for the configured LVS route."""

    def __init__(
        self,
        deployment: config_mod.Deployment,
        *,
        timeout: float = _REQUEST_TIMEOUT_SECONDS,
    ) -> None:
        self._deployment = deployment
        self._timeout = timeout

    def summarize(self, request: SummarizeInput) -> dict[str, Any]:
        import httpx

        from vss_core._foundation.errors import BackendUnreachableError

        payload = request.model_dump(exclude_none=True)
        if request.model is None:
            payload["model"] = self._default_model()

        endpoint = self._deployment.endpoint("agent").rstrip("/")
        url = f"{endpoint}{_SUMMARIZE_PATH}"
        try:
            response = httpx.post(url, json=payload, timeout=self._timeout)
        except httpx.HTTPError as error:
            raise BackendUnreachableError("lvs", f"unreachable at {url}: {error}", error) from error

        if response.status_code >= 500:
            raise BackendUnreachableError("lvs", f"backend error HTTP {response.status_code}")
        if response.status_code >= 400:
            raise InvalidInput(f"summarization rejected HTTP {response.status_code}: {response.text[:500]}")

        try:
            completion = response.json()
        except ValueError as error:
            raise BackendUnreachableError("lvs", "response was not valid JSON", error) from error
        if not isinstance(completion, dict):
            raise BackendUnreachableError("lvs", "response was not a JSON object")
        return completion

    def _default_model(self) -> str:
        service = self._deployment.services.get("rt_vlm")
        if service and service.models:
            return service.models[0]
        raise config_mod.ConfigError(
            f"deployment at {self._deployment.base_url} reports no RT-VLM model. "
            "Pass --model or re-run `vss configure` after the model is ready."
        )


RunnerFactory = Callable[[Context], SummaryRunner]


def _runner_from_context(ctx: Context) -> SummaryRunner:
    deployment = ctx.deployment or config_mod.load()
    return LvsSummaryRunner(deployment)


def _mint_job_id() -> str:
    """Mint a lexicographically sortable ``summarize-<ULID>`` identifier."""
    value = (int(time.time() * 1000) & ((1 << 48) - 1)) << 80 | secrets.randbits(80)
    ulid = "".join(_CROCKFORD32[(value >> shift) & 0x1F] for shift in range(125, -1, -5))
    return f"summarize-{ulid}"


def _memory_input(request: SummarizeInput) -> Any:
    """Map the CLI request into #1583's generic memory input envelope."""
    from vss_core.memory import SummaryAdapter

    media_ref: dict[str, Any] = {"source": "lvs"}
    if request.url:
        media_ref["url"] = request.url
    return SummaryAdapter.build_input(
        prompt=request.prompt,
        video_id=request.id,
        media_ref=media_ref,
        params=request.model_dump(exclude_none=True),
        intent="summarize",
    )


def _completion_output(completion: dict[str, Any], *, job_id: str) -> tuple[Any, str | None]:
    """Parse one LVS completion into the versioned summary extension."""
    from vss_core.memory import SummaryAdapter
    from vss_core.memory import SummaryEvent
    from vss_core.memory import SummaryExtension

    try:
        content = completion["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as error:
        raise ValueError("summarization response has no choices[0].message.content") from error

    parsed: Any = None
    if isinstance(content, str):
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            parsed = None

    if isinstance(parsed, dict):
        answer_value = parsed.get("video_summary")
        answer = answer_value if isinstance(answer_value, str) else str(answer_value or "")
        raw_events = parsed.get("events") or []
        if not isinstance(raw_events, list) or not all(isinstance(event, dict) for event in raw_events):
            raise ValueError("summarization response events must be a list of objects")
        events = [SummaryEvent.model_validate(event) for event in raw_events]
        reported_total = parsed.get("total_events", len(events))
        if not isinstance(reported_total, int):
            raise ValueError("summarization response total_events must be an integer")
    else:
        answer = content if isinstance(content, str) else json.dumps(content)
        events = []
        reported_total = 0

    completion_id = completion.get("id")
    summary_id = str(completion_id or job_id)
    metadata = {
        key: value
        for key, value in {
            "completion_id": completion_id,
            "video_id": completion.get("video_id"),
            "model": completion.get("model"),
            "created": completion.get("created"),
            "usage": completion.get("usage"),
        }.items()
        if value is not None
    }
    extension = SummaryExtension(
        summary_id=summary_id,
        events=events,
        total_events=reported_total,
        metadata=metadata,
    )
    return SummaryAdapter.build_output(answer=answer, summary=extension), str(completion_id) if completion_id else None


def _failed_record(
    *,
    adapter: Any,
    job_id: str,
    created_at: str,
    input_data: Any,
    error: Exception,
    backend_ref: str | None,
) -> Any:
    """Build a failed terminal record without changing the original error."""
    from vss_core.memory.models import MemoryError

    return adapter.terminal_record(
        job_id=job_id,
        created_at=created_at,
        status="failed",
        input_data=input_data,
        error=MemoryError(
            code=type(error).__name__,
            message=str(error) or type(error).__name__,
        ),
        backend_ref=backend_ref,
    )


class SummarizeGroup(CommandGroup):
    """Summarize video through LVS and persist its lifecycle."""

    name: ClassVar[str] = "summarize"
    summary: ClassVar[str] = "Summarize video"
    Input: ClassVar[type[BaseModel] | None] = SummarizeInput
    extra_params: ClassVar[tuple[Any, ...]] = (memory_mod.index_option(),)

    def __init__(self, runner_factory: RunnerFactory = _runner_from_context) -> None:
        self._runner_factory = runner_factory

    def run(self, action: str, inputs: BaseModel, ctx: Context) -> Result:  # noqa: ARG002 - fixed verb signature
        if not isinstance(inputs, SummarizeInput):  # pragma: no cover - framework constructs this
            raise TypeError(f"expected SummarizeInput, got {type(inputs).__name__}")

        from vss_core.memory import SummaryAdapter
        from vss_core.memory.adapters import utc_now_iso

        job_id = _mint_job_id()
        created_at = utc_now_iso()
        input_data = _memory_input(inputs)
        memory = self.memory(ctx)
        adapter = SummaryAdapter()
        memory.service.upsert(
            adapter.running_record(
                job_id=job_id,
                created_at=created_at,
                input_data=input_data,
            )
        )

        backend_ref: str | None = None
        try:
            completion = self._runner_factory(ctx).summarize(inputs)
            completion_ref = completion.get("id")
            backend_ref = str(completion_ref) if completion_ref else None
            output, backend_ref = _completion_output(completion, job_id=job_id)
        except Exception as error:
            memory.service.upsert(
                _failed_record(
                    adapter=adapter,
                    job_id=job_id,
                    created_at=created_at,
                    input_data=input_data,
                    error=error,
                    backend_ref=backend_ref,
                )
            )
            raise

        memory.service.upsert(
            adapter.terminal_record(
                job_id=job_id,
                created_at=created_at,
                status="completed",
                input_data=input_data,
                output=output,
                backend_ref=backend_ref,
            )
        )
        return Result(
            body={"job_id": job_id, "summary": completion},
            exit=Exit.SUCCESS,
            job_id=job_id,
        )


SUMMARIZE = SummarizeGroup()

__all__ = [
    "SUMMARIZE",
    "LvsSummaryRunner",
    "SummarizeGroup",
    "SummarizeInput",
    "SummaryRunner",
]
