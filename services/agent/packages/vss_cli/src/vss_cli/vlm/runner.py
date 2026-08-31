# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Reusable, bounded VLM job runner shared by CLI and introspection."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
import secrets
import time
from typing import TYPE_CHECKING
from typing import Any
from typing import Literal
from typing import Self

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import field_validator
from pydantic import model_validator

from vss_cli import config as config_mod
from vss_cli.persistence import mark_terminal

from .memory_adapter import VLMAdapter

if TYPE_CHECKING:
    from collections.abc import Awaitable
    from collections.abc import Callable

    from vss_core.introspection.models import VLMEvidence
    from vss_core.vios import SensorRef
    from vss_core.vlm import VLMAnalyzer

_CROCKFORD32 = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_TERMINAL_WRITE_ATTEMPTS = 3
_TERMINAL_WRITE_BACKOFF_SECONDS = 0.5


def _ulid() -> str:
    value = (int(time.time() * 1000) & ((1 << 48) - 1)) << 80 | secrets.randbits(80)
    return "".join(_CROCKFORD32[(value >> shift) & 0x1F] for shift in range(125, -1, -5))


def mint_job_id() -> str:
    """Mint the public ``vlm-<ULID>`` job handle."""
    return f"vlm-{_ulid()}"


class VLMJobRequest(BaseModel):
    """Strict shared request accepted by CLI and introspection callers."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    sensor: str = Field(min_length=1)
    start_time: str = Field(min_length=1)
    end_time: str = Field(min_length=1)
    prompt: str = Field(min_length=1, max_length=512_000)
    intent: Literal["video-qa", "introspection"] = "video-qa"

    @field_validator("sensor", "prompt")
    @classmethod
    def _nonempty(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must be non-empty")
        return stripped

    @field_validator("start_time", "end_time")
    @classmethod
    def _utc_iso(cls, value: str) -> str:
        stripped = value.strip()
        try:
            parsed = datetime.fromisoformat(stripped.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("must be an ISO-8601 UTC instant") from error
        if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
            raise ValueError("must include the UTC timezone (Z or +00:00)")
        return parsed.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")

    @model_validator(mode="after")
    def _ordered_window(self) -> Self:
        start = datetime.fromisoformat(self.start_time.replace("Z", "+00:00"))
        end = datetime.fromisoformat(self.end_time.replace("Z", "+00:00"))
        if start >= end:
            raise ValueError("start_time must be before end_time")
        return self


@dataclass(frozen=True)
class VLMJobResult:
    job_id: str
    status: Literal["completed", "failed", "partial", "timeout"]
    answer: str | None
    sensor_name: str
    start_time: str
    end_time: str
    model: str
    persisted: bool
    record: Literal["absent", "closed", "stale"]
    error: str | None = None
    persistence_error: str | None = None


class VLMJobError(Exception):
    """A terminal inference failure that still carries its persisted handle."""

    def __init__(self, result: VLMJobResult, cause: BaseException) -> None:
        super().__init__(str(cause))
        self.result = result
        self.cause = cause


class IntrospectionVLMJobRunner:
    """Adapt persisted VLM jobs to the core introspection runner protocol."""

    def __init__(
        self,
        deployment: config_mod.Deployment,
        *,
        memory: Any | None,
        timeout_seconds: int = 180,
        analyzer: VLMAnalyzer | None = None,
        analyzer_model: str | None = None,
        resolve_sensor_fn: Callable[..., Awaitable[SensorRef]] | None = None,
        recorded_segments_fn: Callable[..., Awaitable[list[tuple[str, str]]]] | None = None,
    ) -> None:
        self._deployment = deployment
        self._memory = memory
        self._timeout_seconds = timeout_seconds
        self._analyzer = analyzer
        self._analyzer_model = analyzer_model
        self._resolve_sensor_fn = resolve_sensor_fn
        self._recorded_segments_fn = recorded_segments_fn
        self.persistence_errors: list[str] = []
        self.backend_errors: list[str] = []
        self.timed_out = False

    async def run(
        self,
        *,
        sensor: str,
        start_time: str,
        end_time: str,
        prompt: str,
        intent: str,
    ) -> VLMEvidence:
        from vss_core.introspection import VLMEvidence

        if intent != "introspection":
            raise ValueError("introspection VLM jobs require intent='introspection'")
        request = VLMJobRequest(
            sensor=sensor,
            start_time=start_time,
            end_time=end_time,
            prompt=prompt,
            intent="introspection",
        )
        try:
            result = await run_vlm_job(
                request,
                self._deployment,
                analyzer=self._analyzer,
                analyzer_model=self._analyzer_model,
                memory=self._memory,
                timeout_seconds=self._timeout_seconds,
                resolve_sensor_fn=self._resolve_sensor_fn,
                recorded_segments_fn=self._recorded_segments_fn,
            )
        except VLMJobError as error:
            self.timed_out = self.timed_out or error.result.status == "timeout"
            if _is_backend_error(error.cause):
                self.backend_errors.append(str(error.cause))
            raise
        except Exception as error:
            if _is_backend_error(error):
                self.backend_errors.append(str(error))
            raise
        if result.persistence_error is not None:
            self.persistence_errors.append(result.persistence_error)
        if result.answer is None:  # pragma: no cover - successful runner results always carry an answer
            raise RuntimeError("VLM job completed without an answer")
        return VLMEvidence(
            job_id=result.job_id,
            persisted=result.persisted,
            sensor=result.sensor_name,
            start_time=result.start_time,
            end_time=result.end_time,
            answer=result.answer,
        )


def _is_backend_error(error: BaseException) -> bool:
    return any(
        klass.__name__ in {"BackendUnreachableError", "ConnectError", "ConnectTimeout", "VSTError", "VIOSTimeoutError"}
        for klass in type(error).__mro__
    )


def _production_analyzer(deployment: config_mod.Deployment, timeout_seconds: int) -> tuple[VLMAnalyzer, str]:
    from vss_core.vios import VSTClient
    from vss_core.vlm import OpenAIVLMAnalyzer

    service = deployment.services.get("rt_vlm")
    if service is None or not service.models:
        raise config_mod.ConfigError("the configured RT-VLM service reports no model")
    model = service.models[0]
    vst = VSTClient(
        internal_url=str(deployment.base_url),
        external_url=str(deployment.base_url),
        timeout_seconds=float(timeout_seconds),
    )
    return (
        OpenAIVLMAnalyzer(
            base_url=f"{service.url.rstrip('/')}/v1",
            model=model,
            vst=vst,
            timeout_seconds=timeout_seconds,
            media_mode="video_url",
            # RT-VLM fetches the clip itself. Use VST's in-cluster videoUrl
            # (for example http://vst-ingress:30888/...) so SSRF policy does
            # not reject the host-side localhost origin.
            video_url_scope="internal",
            cosmos_nim_runtime_options=False,
        ),
        model,
    )


async def run_vlm_job(
    request: VLMJobRequest,
    deployment: config_mod.Deployment,
    *,
    analyzer: VLMAnalyzer | None = None,
    analyzer_model: str | None = None,
    memory: Any | None = None,
    timeout_seconds: int = 180,
    resolve_sensor_fn: Callable[..., Awaitable[SensorRef]] | None = None,
    recorded_segments_fn: Callable[..., Awaitable[list[tuple[str, str]]]] | None = None,
) -> VLMJobResult:
    """Validate one VIOS interval and perform exactly one bounded inference."""
    from vss_core.memory.adapters import utc_now_iso
    from vss_core.vios import recorded_segments
    from vss_core.vios import resolve_sensor
    from vss_core.vios import resolve_window

    if timeout_seconds < 1:
        raise ValueError("timeout_seconds must be >= 1")
    resolver = resolve_sensor_fn or resolve_sensor
    segments_reader = recorded_segments_fn or recorded_segments
    vst_url = str(deployment.base_url).rstrip("/")
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds

    def remaining_seconds() -> float:
        left = deadline - loop.time()
        if left <= 0:
            raise TimeoutError()
        return left

    owns_analyzer = analyzer is None
    sensor = None
    start_time: str | None = None
    end_time: str | None = None
    job_id: str | None = None
    created_at: str | None = None
    input_data: Any = None
    model = analyzer_model or (type(analyzer).__name__ if analyzer is not None else "")
    adapter = VLMAdapter()
    persistence_error: str | None = None

    def close(status: Literal["failed", "partial", "timeout"], detail: str) -> Literal["absent", "closed", "stale"]:
        if memory is None or job_id is None or created_at is None or input_data is None:
            return "absent"
        closed = mark_terminal(
            memory,
            adapter,
            job_id=job_id,
            created_at=created_at,
            input_data=input_data,
            status=status,
            message=detail,
            attempts=_TERMINAL_WRITE_ATTEMPTS,
            backoff_seconds=_TERMINAL_WRITE_BACKOFF_SECONDS,
        )
        return "closed" if closed else "stale"

    def timeout_result(*, record: Literal["absent", "closed", "stale"]) -> VLMJobResult:
        return VLMJobResult(
            job_id=job_id or mint_job_id(),
            status="timeout",
            answer=None,
            sensor_name=sensor.name if sensor is not None else request.sensor,
            start_time=start_time or request.start_time,
            end_time=end_time or request.end_time,
            model=model or "vlm",
            persisted=record == "closed",
            record=record,
            error=f"VLM job timed out after {timeout_seconds}s",
            persistence_error=persistence_error,
        )

    try:
        try:
            async with asyncio.timeout(timeout_seconds):
                sensor = await resolver(vst_url, request.sensor, timeout_seconds=remaining_seconds())
                segments = await segments_reader(vst_url, sensor.stream_id, timeout_seconds=remaining_seconds())
                if not segments:
                    from vss_core.vios import VIOSInvalidInputError

                    raise VIOSInvalidInputError(f"nothing is recorded for sensor {sensor.name!r}")
                start_time, end_time = resolve_window(segments, request.start_time, request.end_time, sensor.kind)

                if analyzer is None:
                    analyzer, model = _production_analyzer(deployment, max(1, int(remaining_seconds())))
                elif not model:
                    model = type(analyzer).__name__

                job_id = mint_job_id()
                created_at = utc_now_iso()
                params = {
                    "model": model,
                    "time_format": "iso",
                    "timeout_seconds": timeout_seconds,
                }
                input_data = adapter.build_input(
                    prompt=request.prompt,
                    intent=request.intent,
                    sensor_name=sensor.name,
                    sensor_type=sensor.kind,
                    sensor_id=sensor.sensor_id,
                    stream_id=sensor.stream_id,
                    start_time=start_time,
                    end_time=end_time,
                    params=params,
                )
                if memory is not None:
                    try:
                        memory.service.upsert(
                            adapter.submitted_record(job_id=job_id, created_at=created_at, input_data=input_data)
                        )
                    except Exception as error:
                        persistence_error = str(error)
                        memory = None

                answer = await analyzer.analyze(
                    sensor_id=sensor.sensor_id,
                    start_timestamp=start_time,
                    end_timestamp=end_time,
                    prompt=request.prompt,
                    time_format="iso",
                )

                success_record: Literal["absent", "closed", "stale"] = "absent"
                persisted = False
                if memory is not None:
                    try:
                        memory.service.upsert(
                            adapter.terminal_record(
                                job_id=job_id,
                                created_at=created_at,
                                status="completed",
                                input_data=input_data,
                                output=adapter.build_output(answer=answer, model=model),
                            )
                        )
                        success_record = "closed"
                        persisted = True
                    except Exception as error:
                        persistence_error = str(error)
                        success_record = close("partial", f"completion persistence failed: {error}")
                final_status: Literal["completed", "partial"] = "partial" if persistence_error else "completed"
                return VLMJobResult(
                    job_id=job_id,
                    status=final_status,
                    answer=answer,
                    sensor_name=sensor.name,
                    start_time=start_time,
                    end_time=end_time,
                    model=model,
                    persisted=persisted,
                    record=success_record,
                    persistence_error=persistence_error,
                )
        except TimeoutError as error:
            record = close("timeout", f"VLM job timed out after {timeout_seconds}s")
            raise VLMJobError(timeout_result(record=record), error) from error
        except Exception as error:
            if job_id is None or sensor is None or start_time is None or end_time is None:
                raise
            record = close("failed", str(error))
            result = VLMJobResult(
                job_id=job_id,
                status="failed",
                answer=None,
                sensor_name=sensor.name,
                start_time=start_time,
                end_time=end_time,
                model=model,
                persisted=record == "closed",
                record=record,
                error=str(error),
                persistence_error=persistence_error,
            )
            raise VLMJobError(result, error) from error
    finally:
        if owns_analyzer and analyzer is not None:
            close_analyzer = getattr(analyzer, "aclose", None)
            if close_analyzer is not None:
                await close_analyzer()


__all__ = [
    "IntrospectionVLMJobRunner",
    "VLMJobError",
    "VLMJobRequest",
    "VLMJobResult",
    "mint_job_id",
    "run_vlm_job",
]
