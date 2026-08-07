# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""``vss summarize`` on the fixed verb grammar.

Summarization stays a thin client over the LVS REST API
(``POST /v1/summarize``); this group does not re-implement it. What the group
owns is the job shape around that call: mint a ``job_id``, persist the result
to unified memory, and report both through the framework's ``Result``.

The option surface splits the same three ways ``search`` did:

* **The request stays** as :class:`SummarizeInput` -- what the caller is
  asking the VLM for, named exactly as the REST API names it so the payload
  needs no translation table.
* **Endpoints leave entirely.** The LVS origin, Elasticsearch, RT-Embed and
  the embedding model are read from ``~/.vss/config.json``, which ``vss
  configure`` populated from what those backends reported about themselves.
  ``--backend-url``/``--es-endpoint``/``--embedding-endpoint`` described a
  *deployment*, not a request, and are gone (NFR-6).
* **Persistence identity and transport** are caller preferences rather than
  request fields, so they arrive as :class:`SummarizeOptions` through
  ``extra_params`` instead of being folded into the payload.

Only ``run`` is implemented here. ``status``/``get``/``list`` are pure reads
against the memory index (§6.2), so they are inherited from
:class:`~vss_cli.group.CommandGroup` and answer from ``ctx.memory``. There is
deliberately no ``recall``: fetching one record by id *is* ``get``, and
querying recent ones *is* ``list``. Persistence writes go through the in-process
``vss_core.memory`` service (write-ahead lifecycle + ``SummaryAdapter``).
"""

from __future__ import annotations

import contextlib
import json
import secrets
import time
from typing import TYPE_CHECKING
from typing import Any
from typing import ClassVar

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import model_validator

from . import config as config_mod
from . import params as params_mod
from ._jobs import MARKER_COMPLETED
from ._jobs import JobLifecycle
from ._jobs import completion_marker
from .exits import Exit
from .group import CommandGroup
from .group import Context
from .group import InvalidInput
from .group import Result
from .memory_access import require_memory_service
from .memory_notes import note_result_payload
from .memory_notes import preflight_memory_note
from .memory_notes import resolve_write_memory_note
from .memory_notes import write_memory_note

if TYPE_CHECKING:
    from collections.abc import Sequence

    import click

#: Route the LVS service exposes under its recorded mount. ``vss configure``
#: records the ``agent`` service at ``/api``, so the full path resolves to the
#: deployment's ``/api/v1/summarize``.
_SUMMARIZE_PATH = "/v1/summarize"

#: Job ids stay ``summarize-<ULID>`` (design §5.2/§7.2), while the record's
#: ``group`` token is the shorter unified-schema ``summary``.
_JOB_DOMAIN = "summarize"

#: Crockford base32, for ULID job ids.
_CROCKFORD32 = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


class SummarizeInput(BaseModel):
    """Summarize a video and persist the result to unified memory.

    Exactly one source is required: ``--id`` names media the deployment has
    already ingested, ``--url`` points at a video to fetch directly.

    Sampling and chunking fields are passed through to the VLM untouched and
    are named as the REST API names them. Omitted fields are absent from the
    request rather than sent as null, so the backend's own defaults apply.

    ``--enable-vlm-structured-output`` is worth setting whenever the result
    will be persisted: it asks the VLM for a JSON object with ``video_summary``
    and ``events``, which is the shape memory stores. Prose output is still
    persistable, but arrives as a summary with no events.
    """

    # Unknown keys are an error rather than something to drop. Click rejects
    # unknown flags itself, so this guards the programmatic callers where a
    # misspelled key would otherwise pass silently with the default.
    model_config = ConfigDict(extra="forbid")

    id: str | None = Field(None, description="ID of an already-added file or live stream.")
    url: str | None = Field(None, description="Direct URL to a video to summarize (HTTP/HTTPS/S3).")
    model: str | None = Field(
        None,
        description="VLM to summarize with. Defaults to the model the deployment's RT-VLM reports serving.",
    )
    prompt: str | None = Field(None, description="VLM prompt.")
    system_prompt: str | None = Field(None, description="VLM system prompt.")
    chunk_duration: int | None = Field(None, ge=1, description="Chunk duration in seconds.")
    chunk_overlap_duration: int | None = Field(None, ge=0, description="Chunk overlap duration in seconds.")
    temperature: float | None = Field(None, ge=0.0, le=2.0, description="Sampling temperature.")
    top_p: float | None = Field(None, ge=0.0, le=1.0, description="Nucleus sampling probability mass.")
    top_k: int | None = Field(None, ge=0, description="Top-k sampling cutoff.")
    max_tokens: int | None = Field(None, ge=1, description="Maximum tokens to generate.")
    seed: int | None = Field(None, description="Sampling seed, for reproducible generations.")
    num_frames_per_chunk: int | None = Field(None, ge=1, description="Frames sampled from each chunk.")
    enable_audio: bool = Field(False, description="Transcribe the audio stream alongside the video.")
    enable_vlm_structured_output: bool = Field(
        False,
        description="Request structured JSON (summary + events). Recommended when persisting.",
    )

    @model_validator(mode="after")
    def _exactly_one_source(self) -> SummarizeInput:
        # Click can express "mutually exclusive" only by hand-rolled callbacks;
        # stating it on the model keeps the rule with the fields and applies it
        # to programmatic callers too.
        if bool(self.id) == bool(self.url):
            raise ValueError("exactly one of id or url is required")
        return self


class SummarizeOptions(BaseModel):
    """Persistence identity and transport. Configures the *job*, not the request.

    :class:`SummarizeInput` is ``extra=forbid``, so none of these can be sent
    to the VLM by accident -- they are collected separately and routed to the
    memory write or the HTTP client.
    """

    model_config = ConfigDict(extra="forbid")

    persist: bool = Field(True, description="Persist the summary to unified memory.")
    video_id: str | None = Field(
        None,
        description="video_id recorded for the persisted record. Defaults to --id; required with --url.",
    )
    media_source: str = Field("vst", description="media_ref.source recorded for the persisted record.")
    media_name: str | None = Field(None, description="media_ref.name, e.g. the original filename.")
    request_timeout_seconds: int = Field(
        3600,
        ge=1,
        description="HTTP timeout for the (long-running) summarization request.",
    )
    memory_index: str | None = Field(None, description="Elasticsearch index for unified memory.")


def _ulid() -> str:
    """A lexicographically sortable 26-char ULID (48-bit time + 80-bit random).

    Stdlib-only so the group stays dependency-light; sortability keeps
    ``job_id`` ordering stable over time.
    """
    value = (int(time.time() * 1000) & ((1 << 48) - 1)) << 80 | secrets.randbits(80)
    return "".join(_CROCKFORD32[(value >> shift) & 0x1F] for shift in range(125, -1, -5))


def _mint_job_id() -> str:
    return f"{_JOB_DOMAIN}-{_ulid()}"


def _default_model(deployment: config_mod.Deployment) -> str:
    """The VLM the deployment reports serving, or a ConfigError naming the fix."""
    service = deployment.services.get("rt_vlm")
    if service and service.models:
        return service.models[0]
    raise config_mod.ConfigError(
        f"deployment at {deployment.base_url} reports no RT-VLM model, so --model cannot be defaulted. "
        f"Pass --model explicitly, or re-run `vss configure --base-url {deployment.base_url}`."
    )


def _summary_content(completion: dict[str, Any]) -> dict[str, Any]:
    """Map an LVS completion into summary text + events for the memory adapter.

    Structured output yields a JSON object with ``video_summary`` and
    ``events``; prose is wrapped as a summary with no events so it stays
    persistable either way.
    """
    try:
        text = completion["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as error:
        # A ValueError, not an InvalidInput: the caller's request was fine and
        # the summary may still be in hand, so this degrades the job to partial
        # rather than reporting it as bad input.
        raise ValueError("summarization response has no choices[0].message.content") from error

    parsed: Any = None
    if isinstance(text, str):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = None

    if isinstance(parsed, dict) and "video_summary" in parsed:
        return {"video_summary": parsed["video_summary"], "events": parsed.get("events") or []}
    return {"video_summary": text if isinstance(text, str) else json.dumps(text), "events": []}


def _memory_input(inputs: SummarizeInput, options: SummarizeOptions, model: str) -> Any:
    from vss_core.memory.adapters import SummaryAdapter

    media_ref: dict[str, Any] = {"source": options.media_source}
    if inputs.id:
        media_ref["stream_id"] = inputs.id
    if options.media_name:
        media_ref["name"] = options.media_name
    params = inputs.model_dump(exclude_none=True, exclude_defaults=True)
    params["model"] = model
    return SummaryAdapter.build_input(
        prompt=inputs.prompt,
        video_id=options.video_id or inputs.id,
        media_ref=media_ref,
        params=params,
    )


def _memory_output(completion: dict[str, Any], model: str) -> Any:
    from vss_core.memory.adapters import SummaryAdapter

    content = _summary_content(completion)
    answer = content.get("video_summary")
    events = content.get("events") if isinstance(content.get("events"), list) else []
    return SummaryAdapter.build_output(
        answer=str(answer) if answer is not None else None,
        events=list(events),
        ext={
            "completion_id": completion.get("id"),
            "created": completion.get("created"),
            "model": completion.get("model") or model,
            "video_summary": answer,
        },
    )


class SummarizeGroup(CommandGroup):
    """Summarize video and persist to memory."""

    name: ClassVar[str] = "summarize"
    summary: ClassVar[str] = "Summarize video and persist to memory"

    Input: ClassVar[type[BaseModel] | None] = SummarizeInput
    extra_params: ClassVar[Sequence[click.Parameter]] = tuple(params_mod.options_from_model(SummarizeOptions))

    def run(self, action: str, inputs: BaseModel, ctx: Context) -> Result:  # noqa: ARG002 - fixed verb signature
        import click
        import httpx

        # Named so the framework's exit-code table recognises them: it maps a
        # library failure to an exit code by class name, so a plain
        # ConnectionError would surface as exit 1 with a traceback.
        from vss_core._foundation.errors import BackendUnreachableError
        from vss_core.memory.adapters import SummaryAdapter
        from vss_core.memory.models import MemoryError

        if not isinstance(inputs, SummarizeInput):  # pragma: no cover - the framework builds this
            raise TypeError(f"expected SummarizeInput, got {type(inputs).__name__}")

        deployment = ctx.deployment or config_mod.load()
        options = SummarizeOptions(**{k: v for k, v in ctx.extra.items() if k in SummarizeOptions.model_fields})
        memory_config = config_mod.load_memory_config()
        note_decision = resolve_write_memory_note(ctx.extra, config=memory_config)
        preflight_memory_note(persist=options.persist, decision=note_decision, config=memory_config)

        # Fail before the expensive summarization: a persisted record needs a
        # video_id, which for a --url summary can only come from --video-id.
        asset_id = options.video_id or inputs.id
        if options.persist and not asset_id:
            raise InvalidInput("cannot persist a --url summary without --video-id (pass --video-id or --no-persist)")

        request = inputs.model_dump(exclude_none=True, exclude_defaults=True)
        model = inputs.model or _default_model(deployment)
        request["model"] = model

        job_id = _mint_job_id()
        service = None
        lifecycle: JobLifecycle | None = None
        if options.persist:
            if ctx.memory is not None and getattr(ctx.memory, "service", None) is not None:
                service = ctx.memory.service
            else:
                service = require_memory_service(deployment, memory_index=options.memory_index)
            lifecycle = JobLifecycle.start(
                group="summary",
                adapter=SummaryAdapter(),
                input_data=_memory_input(inputs, options, model),
                persist=True,
                service=service,
                job_id=job_id,
                write_submitted=True,
            )
            lifecycle.write_running()

        url = deployment.endpoint("agent").rstrip("/") + _SUMMARIZE_PATH
        try:
            response = httpx.post(url, json=request, timeout=float(options.request_timeout_seconds))
        except httpx.TimeoutException as error:
            if lifecycle is not None:
                with contextlib.suppress(Exception):
                    lifecycle.write_terminal(
                        status="timeout",
                        error=MemoryError(code="timeout", message=str(error)),
                    )
            # Exit 7 carries the job id as a correlation handle: reconcile with
            # `status` rather than re-running an hour of summarization.
            click.echo(
                f"vss: summarization timed out after {options.request_timeout_seconds}s (job {job_id})",
                err=True,
            )
            raise SystemExit(int(Exit.TIMEOUT)) from error
        except httpx.HTTPError as error:
            if lifecycle is not None:
                with contextlib.suppress(Exception):
                    lifecycle.write_terminal(
                        status="failed",
                        error=MemoryError(code="backend_unreachable", message=str(error)),
                    )
            raise BackendUnreachableError("lvs", f"unreachable at {url}: {error}") from error

        if response.status_code >= 500:
            raise BackendUnreachableError("lvs", f"backend error HTTP {response.status_code}")
        if response.status_code >= 400:
            raise InvalidInput(f"summarization rejected HTTP {response.status_code}: {response.text[:500]}")

        try:
            completion = response.json()
        except ValueError as error:
            raise BackendUnreachableError("lvs", "response was not valid JSON") from error
        if not isinstance(completion, dict):
            raise BackendUnreachableError("lvs", "response was not a JSON object")

        body: dict[str, Any] = {"job_id": job_id, "summary": completion}
        if not options.persist or lifecycle is None:
            return Result(body=body, exit=Exit.SUCCESS, job_id=job_id)

        try:
            output = _memory_output(completion, model)
            record = lifecycle.write_terminal(status="completed", output=output)
        except (config_mod.ConfigError, RuntimeError, ValueError) as error:
            # Never lose the summary the caller already paid for: degrade to
            # partial so only the write is retried, not the whole job.
            body["persist"] = {"status": "failed", "error": str(error), "persisted": False}
            return Result(body=body, exit=Exit.PARTIAL, job_id=job_id)

        body["persist"] = {
            "status": "complete",
            "persisted": lifecycle.persisted,
            "job_id": record.job.job_id,
            "group": record.job.group,
        }

        harness_written = False
        marker: str | None = None
        if note_decision.enabled and lifecycle.persisted:
            note_result = write_memory_note(record, persisted=True, config=memory_config)
            body["harness_memory"] = note_result_payload(note_result)
            harness_written = note_result.wrote
            if not note_result.ok:
                click.echo(f"vss: harness memory note failed: {note_result.detail}", err=True)
                marker = completion_marker(
                    MARKER_COMPLETED,
                    group="summary",
                    job_id=job_id,
                    status="completed",
                    persisted=True,
                    exit_hint=int(Exit.PARTIAL),
                    harness_memory_written=False,
                )
                # Keep completed summary + ES record; explicit note failure is exit 6.
                if note_decision.forced:
                    return Result(
                        body=body,
                        exit=Exit.PARTIAL,
                        job_id=job_id,
                        extra={"completion_marker": marker} if marker else {},
                    )
            else:
                marker = completion_marker(
                    MARKER_COMPLETED,
                    group="summary",
                    job_id=job_id,
                    status="completed",
                    persisted=True,
                    exit_hint=int(Exit.SUCCESS if lifecycle.persisted else Exit.PARTIAL),
                    harness_memory_written=harness_written,
                )
        elif note_decision.enabled and not lifecycle.persisted:
            body["harness_memory"] = {
                "status": "skipped",
                "written": False,
                "detail": "structured record was not persisted",
            }

        exit_code = Exit.SUCCESS if lifecycle.persisted else Exit.PARTIAL
        extra = {"completion_marker": marker} if marker else {}
        return Result(body=body, exit=exit_code, job_id=job_id, extra=extra)

SUMMARIZE = SummarizeGroup()

__all__ = ["SUMMARIZE", "SummarizeGroup", "SummarizeInput", "SummarizeOptions"]
