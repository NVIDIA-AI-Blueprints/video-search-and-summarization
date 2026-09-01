# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""``vss vlm`` on the fixed verb grammar.

Visual question-answering (VQA) over video in a single bounded synchronous
call: ask a question, get an answer, persist the result to unified memory.

Unlike ``vss summarize``, ``vss vlm run`` is a **point call** (SDD §3.3). The
lifecycle record is written once with a terminal status; there is no
``submitted`` intermediate. One call produces one answer -- never routes to the
summarize pipeline and never re-enters a long-running job.

Media reaches the VLM one of two ways (VLM-1 / VLM-2):

* **Path B** (default): ``--sensor <name> [--start-time T --end-time T]``.
  VIOS resolves the sensor to a clip URL and the VLM receives that URL. No
  video bytes cross the CLI; the VLM fetches the clip from VIOS directly.
* **Path A** (escape hatch): ``--media-url <url>``. A pre-resolved handle --
  any HTTP/HTTPS URL -- is sent directly as ``video_url``. Add ``--use-base64``
  to read a local file and send it as base64-encoded bytes instead.

The VLM endpoint is the deployment's ``rt_vlm`` service (discovered by
``vss configure``), called via the OpenAI-compatible ``/v1/chat/completions``
API. The model defaults to whatever ``vss configure`` recorded for ``rt_vlm``.

Intent (VLM-6) classifies what this call is for: ``qa`` (default), ``critic``,
``report``, or ``introspection``. Stored in ``output.ext.intent`` and available
to harness routing (NFR-5) without the CLI implementing the routing itself.

Persistence (VLM-5) stores ``output.answer``, ``output.ext`` (model, intent,
completion_id) and ``output.handles.media_urls``. Opt out with ``--no-persist``.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json as _json_mod
import os
import secrets
import tempfile
import time
from typing import TYPE_CHECKING
from typing import Any
from typing import ClassVar
from typing import Literal
import urllib.parse

import click
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import model_validator

from vss_cli import config as config_mod
from vss_cli import memory as memory_mod
from vss_cli import params as params_mod
from vss_cli.exits import Exit
from vss_cli.group import CommandGroup
from vss_cli.group import Context
from vss_cli.group import InvalidInput
from vss_cli.group import Result

if TYPE_CHECKING:
    from collections.abc import Sequence

    from vss_core.memory.models import MemoryInput

_JOB_DOMAIN = "vlm"
_CROCKFORD32 = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_COMPLETIONS_PATH = "/v1/chat/completions"


def _ulid() -> str:
    value = (int(time.time() * 1000) & ((1 << 48) - 1)) << 80 | secrets.randbits(80)
    return "".join(_CROCKFORD32[(value >> shift) & 0x1F] for shift in range(125, -1, -5))


def _mint_job_id() -> str:
    return f"{_JOB_DOMAIN}-{_ulid()}"


def _default_model(deployment: config_mod.Deployment) -> str:
    """The model the deployment's RT-VLM reports serving, or a ConfigError."""
    service = deployment.services.get("rt_vlm")
    if service and service.models:
        return service.models[0]
    raise config_mod.ConfigError(
        f"deployment at {deployment.base_url} reports no RT-VLM model, so --model cannot be defaulted. "
        f"Pass --model explicitly, or re-run `vss configure --base-url {deployment.base_url}`."
    )


def _is_loopback_url(url: str) -> bool:
    """True when the URL's host is a loopback address that a remote service cannot reach.

    VIOS on Docker resolves clip URLs using the HAProxy-facing hostname, which is
    often ``localhost`` or ``127.0.0.1`` from the CLI's perspective.  That URL is
    reachable from the CLI host but not from inside the rt_vlm container; sending it
    as ``video_url`` triggers SSRF protection (or a silent fetch failure), returning
    an error or an empty answer.  Detecting this early lets the CLI fall back to
    fetching the clip itself and inlining it as base64 before the VLM call.
    """
    try:
        host = urllib.parse.urlparse(url).hostname or ""
    except Exception:
        return False
    return host in ("localhost", "127.0.0.1", "::1") or host.startswith("127.")


def _extract_answer(completion: dict[str, Any]) -> str:
    """Pull the text answer out of an OpenAI-style completion."""
    try:
        content = completion["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("VLM response has no choices[0].message.content") from exc
    if not isinstance(content, str):
        raise ValueError(f"VLM response content is not a string: {type(content).__name__}")
    return content


class VlmInput(BaseModel):
    """Ask a visual question about video and persist the answer to unified memory.

    Exactly one media source is required:

    * ``--sensor <name>`` to pull a clip from VIOS (optionally windowed with
      ``--start-time`` / ``--end-time``).
    * ``--media-url <url>`` to send an already-resolved HTTP/HTTPS handle.

    ``--prompt`` is the only other required field. Everything else defaults.
    """

    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(..., max_length=512_000, description="Visual question to answer.")
    sensor: str | None = Field(
        None,
        description="VIOS sensor name. Resolves to a clip URL that the VLM fetches directly (Path B).",
    )
    start_time: str | None = Field(
        None,
        description="Clip window start, ISO-8601 UTC. Only valid with --sensor.",
    )
    end_time: str | None = Field(
        None,
        description="Clip window end, ISO-8601 UTC. Only valid with --sensor.",
    )
    media_url: str | None = Field(
        None,
        description="Pre-resolved HTTP/HTTPS video URL (Path A). Mutually exclusive with --sensor and --file.",
    )
    file: str | None = Field(
        None,
        description=(
            "Path to a local video file (Path A). The file is read and sent as base64-encoded bytes. "
            "Mutually exclusive with --sensor and --media-url."
        ),
    )
    intent: Literal["critic", "report", "qa", "introspection"] = Field(
        "qa",
        description="Semantic intent of this call. Stored in memory; used by harness routing (NFR-5).",
    )
    model: str | None = Field(
        None,
        max_length=1024,
        description="VLM model name. Defaults to whatever the deployment's RT-VLM reports.",
    )
    timeout: int = Field(
        30,
        ge=1,
        le=3600,
        description="HTTP timeout for the VLM call, in seconds.",
    )
    max_tokens: int | None = Field(None, ge=1, le=1_000_000, description="Maximum tokens to generate.")
    temperature: float | None = Field(None, ge=0.0, le=1.0, description="Sampling temperature.")
    num_frames: int = Field(
        8,
        ge=1,
        le=256,
        description=(
            "Frame-sampling budget sent to RT-VLM as num_frames_per_second_or_fixed_frames_chunk. "
            "RT-VLM defaults this to 0 (opening frame only) when absent, so the CLI always includes it."
        ),
    )

    @model_validator(mode="after")
    def _validate_media_source(self) -> VlmInput:
        has_sensor = bool(self.sensor)
        has_url = bool(self.media_url)
        has_file = bool(self.file)
        sources_count = sum([has_sensor, has_url, has_file])
        if sources_count != 1:
            raise ValueError("exactly one of --sensor, --media-url, or --file is required")
        if not has_sensor and (self.start_time or self.end_time):
            raise ValueError("--start-time / --end-time require --sensor")
        return self


class VlmOptions(BaseModel):
    """Job and transport options. Not sent to the VLM backend."""

    model_config = ConfigDict(extra="forbid")

    no_persist: bool = Field(False, description="Skip writing the answer to unified memory.")
    use_base64: bool = Field(
        False,
        description=(
            "Read the --media-url value as a local file path and send its bytes as base64. "
            "Path A escape hatch when VIOS is not available."
        ),
    )


def _resolve_vios_clip(
    deployment: config_mod.Deployment,
    sensor: str,
    start_time: str | None,
    end_time: str | None,
) -> tuple[str, str | None, str | None]:
    """Resolve a VIOS sensor to a clip URL and the effective window bounds.

    Returns ``(media_url, resolved_start, resolved_end)`` where the bounds reflect
    the actual window VIOS served, which may differ from the inputs when only one
    bound was supplied or neither was supplied.
    """
    from vss_core import vios

    origin = deployment.base_url.rstrip("/")

    async def _fetch() -> tuple[str, str | None, str | None]:
        ref = await vios.resolve_sensor(origin, sensor)
        segments = await vios.recorded_segments(origin, ref.stream_id)
        start, end = vios.resolve_window(segments, start_time, end_time, ref.kind)
        url = await vios.get_video_clip_url(
            stream_id=ref.stream_id,
            start_time=start,
            end_time=end,
            vst_internal_url=origin,
        )
        url = vios.normalise_media_url(url, origin)
        await vios.warm_media_url(url)
        return url, start, end

    return asyncio.run(_fetch())


def _build_vlm_request(
    *,
    prompt: str,
    media_url: str,
    model: str,
    max_tokens: int | None,
    temperature: float | None,
    num_frames: int,
) -> dict[str, Any]:
    """Build an OpenAI-compatible /v1/chat/completions payload for a URL source."""
    request: dict[str, Any] = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "video_url", "video_url": {"url": media_url}},
                    {"type": "text", "text": prompt},
                ],
            }
        ],
        "num_frames_per_second_or_fixed_frames_chunk": num_frames,
    }
    if max_tokens is not None:
        request["max_tokens"] = max_tokens
    if temperature is not None:
        request["temperature"] = temperature
    return request


def _iter_base64_json(
    *,
    prompt: str,
    file_path: str,
    model: str,
    max_tokens: int | None,
    temperature: float | None,
    num_frames: int,
) -> Any:
    """Yield the VLM request body as a JSON byte stream, reading the file in 192 KB chunks.

    At most one raw chunk (~192 KB) and its base64 encoding (~256 KB) live in memory
    at a time.  The previous list-then-join approach kept the entire encoded payload in
    memory simultaneously with the joined string, the data-URI f-string, and the
    json.dumps output -- typically 4-5x the encoded file size.
    """
    sentinel = f"__b64_{secrets.token_hex(8)}__"
    payload: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": [
            {"type": "video_url", "video_url": {"url": sentinel}},
            {"type": "text", "text": prompt},
        ]}],
        "num_frames_per_second_or_fixed_frames_chunk": num_frames,
    }
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    if temperature is not None:
        payload["temperature"] = temperature

    raw = _json_mod.dumps(payload)
    # json.dumps quotes the sentinel; partition on the quoted form.
    sentinel_quoted = _json_mod.dumps(sentinel)  # e.g. '"__b64_abc123__"'
    pre, _, post = raw.partition(sentinel_quoted)

    try:
        with open(file_path, "rb") as fh:
            yield (pre + '"data:video/mp4;base64,').encode()
            while chunk := fh.read(3 * 65536):
                yield base64.b64encode(chunk)
            yield ('"' + post).encode()
    except OSError as exc:
        raise InvalidInput(f"cannot read local file {file_path!r}: {exc}") from exc


class VlmGroup(CommandGroup):
    """Ask a visual question about video and persist the answer to memory."""

    name: ClassVar[str] = "vlm"
    summary: ClassVar[str] = "Ask a visual question about video"

    Input: ClassVar[type[BaseModel] | None] = VlmInput
    requires: ClassVar[frozenset[str]] = frozenset({"rt_vlm"})
    extra_params: ClassVar[Sequence[click.Parameter]] = tuple(params_mod.options_from_model(VlmOptions))

    def run(self, action: str, inputs: BaseModel, ctx: Context) -> Result:  # noqa: ARG002
        import httpx

        if not isinstance(inputs, VlmInput):  # pragma: no cover
            raise TypeError(f"expected VlmInput, got {type(inputs).__name__}")

        deployment = ctx.deployment or config_mod.load()
        options = VlmOptions(**{k: v for k, v in ctx.extra.items() if k in VlmOptions.model_fields})

        if options.use_base64 and inputs.sensor:
            raise InvalidInput("--use-base64 cannot be combined with --sensor")

        model = inputs.model or _default_model(deployment)
        job_id = _mint_job_id()

        from vss_core.memory.adapters import utc_now_iso

        from .memory_adapter import VlmAdapter

        adapter = VlmAdapter()
        created_at = utc_now_iso()

        model_params: dict[str, Any] = {"model": model, "timeout": inputs.timeout, "num_frames": inputs.num_frames}
        if inputs.max_tokens is not None:
            model_params["max_tokens"] = inputs.max_tokens
        if inputs.temperature is not None:
            model_params["temperature"] = inputs.temperature

        # Initialise memory before media resolution so any failure path (including
        # the loopback clip-fetch timeout below) can write a terminal record.
        memory = self.persist_memory(ctx, no_persist=options.no_persist)

        # Resolve the media URL.
        media_url: str
        resolved_start: str | None = inputs.start_time
        resolved_end: str | None = inputs.end_time
        _loopback_tmp: str | None = None  # temp file path when loopback fallback fires
        if inputs.sensor:
            # Path B: fetch a clip from VIOS. Let typed VIOS exceptions propagate so
            # guarded() maps them to the correct exit codes (3/5/7).
            if "vst" not in (deployment.services or {}):
                raise config_mod.ConfigError(
                    "--sensor requires the `vst` service in the deployment. Re-run `vss configure --base-url <URL>`."
                )
            media_url, resolved_start, resolved_end = _resolve_vios_clip(
                deployment, inputs.sensor, inputs.start_time, inputs.end_time
            )
            if _is_loopback_url(media_url):
                # The VIOS clip URL resolves to localhost — reachable from this CLI
                # host but blocked by rt_vlm's SSRF protection (or simply unreachable
                # from inside the VLM container in Docker deployments). Stream the
                # clip to a temp file and send it inline as base64.
                tmp_fd, _loopback_tmp = tempfile.mkstemp(suffix=".mp4")
                os.close(tmp_fd)
                _download_ok = False
                try:
                    with httpx.stream("GET", media_url, timeout=float(inputs.timeout)) as clip_resp:
                        clip_resp.raise_for_status()
                        with open(_loopback_tmp, "wb") as f:
                            for chunk in clip_resp.iter_bytes(chunk_size=65536):
                                f.write(chunk)
                    media_url = _loopback_tmp
                    _download_ok = True
                except httpx.TimeoutException:
                    detail = f"VIOS clip download timed out after {inputs.timeout}s"
                    # VIOS resolution succeeded; resolved_start/resolved_end are available.
                    clip_input: MemoryInput = adapter.build_input(
                        prompt=inputs.prompt,
                        sensor=inputs.sensor,
                        start_time=resolved_start,
                        end_time=resolved_end,
                        media_url=None,
                        intent=inputs.intent,
                        model_params=model_params,
                    )
                    _write_terminal(
                        memory, adapter, job_id=job_id, created_at=created_at,
                        input_data=clip_input, status="timeout", message=detail,
                    )
                    click.echo(f"vss: {detail} (job {job_id})", err=True)
                    return Result(
                        body={"job_id": job_id, "status": "timeout"}, exit=Exit.TIMEOUT, job_id=job_id
                    )
                except Exception as exc:
                    # httpx.HTTPError (network/protocol failure) or OSError
                    # during the write — both signal VIOS is unreachable, not a
                    # caller mistake. Write a terminal record and exit as
                    # BACKEND_UNREACHABLE (3) so callers/retries treat this
                    # correctly instead of seeing an invalid-input (2) exit.
                    detail = f"cannot fetch VIOS clip for loopback base64 fallback: {exc}"
                    clip_input = adapter.build_input(
                        prompt=inputs.prompt,
                        sensor=inputs.sensor,
                        start_time=resolved_start,
                        end_time=resolved_end,
                        media_url=None,
                        intent=inputs.intent,
                        model_params=model_params,
                    )
                    _write_terminal(
                        memory, adapter, job_id=job_id, created_at=created_at,
                        input_data=clip_input, status="failed", message=detail,
                    )
                    click.echo(f"vss: {detail}", err=True)
                    return Result(
                        body={"job_id": job_id, "status": "failed", "error": detail},
                        exit=Exit.BACKEND_UNREACHABLE,
                        job_id=job_id,
                    )
                finally:
                    # Clean up the temp file if the download failed.  On success
                    # (_download_ok=True) the file is kept for the VLM call below.
                    if not _download_ok:
                        with contextlib.suppress(OSError):
                            os.unlink(_loopback_tmp)
                        _loopback_tmp = None
        elif inputs.file:
            # Path A (file): local file path read and sent as base64-encoded bytes.
            media_url = inputs.file
        else:
            # Path A (url): pre-resolved HTTP/HTTPS handle passed directly to the VLM.
            media_url = inputs.media_url  # type: ignore[assignment]

        # True for --file, "--media-url <path> --use-base64", or the loopback
        # SSRF fallback where the clip was streamed to a temp file: the video
        # content is machine-specific bytes, not a retrievable URL handle.
        _use_base64_effective = options.use_base64 or bool(inputs.file) or _loopback_tmp is not None

        # Wrap everything that references _loopback_tmp in try/finally so the
        # temp file is deleted even if adapter.build_input or the VLM call raises.
        try:
            input_data: MemoryInput = adapter.build_input(
                prompt=inputs.prompt,
                sensor=inputs.sensor,
                start_time=resolved_start,
                end_time=resolved_end,
                media_url=media_url if (not inputs.sensor and not _use_base64_effective) else None,
                intent=inputs.intent,
                model_params=model_params,
            )

            vlm_url = deployment.endpoint("rt_vlm").rstrip("/") + _COMPLETIONS_PATH
            if _use_base64_effective:
                # Stream the JSON body chunk-by-chunk from the file.  This keeps only
                # one 192 KB raw chunk in memory at a time instead of the entire encoded
                # payload, the joined string, the data-URI f-string, and the json.dumps
                # output that the list-then-join approach created simultaneously.
                file_to_read = _loopback_tmp if _loopback_tmp is not None else media_url
                # Pre-validate readability before giving the file to httpx.  If
                # the open() fails inside the content generator, httpx wraps the
                # OSError as httpx.WriteError (an httpx.HTTPError subclass) and
                # the caller sees BACKEND_UNREACHABLE instead of INVALID_INPUT.
                try:
                    open(file_to_read, "rb").close()
                except OSError as exc:
                    raise InvalidInput(f"cannot read local file {file_to_read!r}: {exc}") from exc
                response = httpx.post(
                    vlm_url,
                    content=_iter_base64_json(
                        prompt=inputs.prompt,
                        file_path=file_to_read,
                        model=model,
                        max_tokens=inputs.max_tokens,
                        temperature=inputs.temperature,
                        num_frames=inputs.num_frames,
                    ),
                    headers={"Content-Type": "application/json"},
                    timeout=float(inputs.timeout),
                )
            else:
                response = httpx.post(
                    vlm_url,
                    json=_build_vlm_request(
                        prompt=inputs.prompt,
                        media_url=media_url,
                        model=model,
                        max_tokens=inputs.max_tokens,
                        temperature=inputs.temperature,
                        num_frames=inputs.num_frames,
                    ),
                    timeout=float(inputs.timeout),
                )
        except httpx.TimeoutException:
            detail = f"VLM call timed out after {inputs.timeout}s"
            _write_terminal(
                memory,
                adapter,
                job_id=job_id,
                created_at=created_at,
                input_data=input_data,
                status="timeout",
                message=detail,
            )
            click.echo(f"vss: {detail} (job {job_id})", err=True)
            return Result(body={"job_id": job_id, "status": "timeout"}, exit=Exit.TIMEOUT, job_id=job_id)
        except httpx.HTTPError as exc:
            detail = str(exc)
            _write_terminal(
                memory,
                adapter,
                job_id=job_id,
                created_at=created_at,
                input_data=input_data,
                status="failed",
                message=detail,
            )
            click.echo(f"vss: RT-VLM unreachable at {vlm_url}: {exc}", err=True)
            return Result(
                body={"job_id": job_id, "status": "failed", "error": detail},
                exit=Exit.BACKEND_UNREACHABLE,
                job_id=job_id,
            )
        finally:
            # Guarantee temp file deletion whether the VLM call succeeded, failed,
            # or raised — including if adapter.build_input raised before the call.
            if _loopback_tmp is not None:
                with contextlib.suppress(OSError):
                    os.unlink(_loopback_tmp)
                _loopback_tmp = None

        if response.status_code >= 400:
            detail = f"HTTP {response.status_code}"
            _write_terminal(
                memory,
                adapter,
                job_id=job_id,
                created_at=created_at,
                input_data=input_data,
                status="failed",
                message=detail,
            )
            code = Exit.BACKEND_UNREACHABLE if response.status_code >= 500 else Exit.INVALID_INPUT
            click.echo(f"vss: VLM backend error {detail}: {response.text[:500]}", err=True)
            return Result(body={"job_id": job_id, "status": "failed", "error": detail}, exit=code, job_id=job_id)

        try:
            completion = response.json()
        except ValueError:
            detail = "VLM response was not valid JSON"
            _write_terminal(
                memory,
                adapter,
                job_id=job_id,
                created_at=created_at,
                input_data=input_data,
                status="failed",
                message=detail,
            )
            click.echo(f"vss: {detail}", err=True)
            return Result(
                body={"job_id": job_id, "status": "failed", "error": detail},
                exit=Exit.BACKEND_UNREACHABLE,
                job_id=job_id,
            )

        try:
            answer = _extract_answer(completion)
        except ValueError as exc:
            detail = str(exc)
            _write_terminal(
                memory,
                adapter,
                job_id=job_id,
                created_at=created_at,
                input_data=input_data,
                status="failed",
                message=detail,
            )
            click.echo(f"vss: {detail}", err=True)
            return Result(
                body={"job_id": job_id, "status": "failed", "error": detail},
                exit=Exit.BACKEND_UNREACHABLE,
                job_id=job_id,
            )

        completion_id: str | None = completion.get("id")
        body: dict[str, Any] = {
            "job_id": job_id,
            "status": "completed",
            "answer": answer,
            "model": completion.get("model") or model,
        }
        body["intent"] = inputs.intent

        # Point call: write the terminal record once.
        if memory is None:
            body["persisted"] = False
            return Result(body=body, exit=Exit.SUCCESS, job_id=job_id)

        output = adapter.build_output(
            answer=answer,
            model=completion.get("model") or model,
            media_url=None if _use_base64_effective else media_url,
            intent=inputs.intent,
            completion_id=completion_id,
        )
        terminal = adapter.terminal_record(
            job_id=job_id,
            created_at=created_at,
            status="completed",
            input_data=input_data,
            output=output,
        )
        persist_error: str | None = None
        try:
            memory.service.upsert(terminal)
        except memory_mod.write_failures() as exc:
            persist_error = str(exc)
            click.echo(f"vss: unified memory write failed ({exc})", err=True)

        if persist_error:
            body["persisted"] = False
            body["persist_error"] = persist_error
            return Result(body=body, exit=Exit.PARTIAL, job_id=job_id)

        body["persisted"] = True
        body["memory_index"] = memory.index
        return Result(body=body, exit=Exit.SUCCESS, job_id=job_id)


def _write_terminal(
    memory: Any,
    adapter: Any,
    *,
    job_id: str,
    created_at: str,
    input_data: Any,
    status: str,
    message: str,
) -> None:
    """Best-effort terminal write on failure paths."""
    if memory is None:
        return
    from vss_core.memory.models import MemoryError

    record = adapter.terminal_record(
        job_id=job_id,
        created_at=created_at,
        status=status,
        input_data=input_data,
        error=MemoryError(code=status, message=message),
    )
    with contextlib.suppress(Exception):
        memory.service.upsert(record)


VLM = VlmGroup()

__all__ = ["VLM", "VlmGroup", "VlmInput", "VlmOptions"]
