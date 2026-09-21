# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""``vss summarize`` on the fixed verb grammar.

Summarization stays a thin client over the LVS REST API
(``POST /v1/summarize``); this group does not re-implement it. What the group
owns is the job shape around that call: mint a ``job_id``, persist the result
to unified memory when static memory policy enables it, and report both
through the framework's ``Result``.

The option surface splits the same three ways ``search`` did:

* **The request stays** as :class:`SummarizeInput` -- what the caller is
  asking the VLM for, named exactly as the REST API names it so the payload
  needs no translation table.
* **Endpoints leave entirely.** The LVS origin and the Elasticsearch holding
  memory are read from ``~/.vss/config.json``, which ``vss configure``
  populated from what those backends reported about themselves.
  ``--backend-url``/``--es-endpoint``/``--embedding-endpoint`` described a
  *deployment*, not a request, and are gone (NFR-6).
* **Persistence identity and transport** are caller preferences rather than
  request fields, so they arrive as :class:`SummarizeOptions` through
  ``extra_params`` instead of being folded into the payload.

Only ``run`` is implemented here. ``status``/``get``/``list`` are pure reads
against the memory index (§6.2), so they are inherited from
:class:`~vss_cli.group.CommandGroup` and answer from the same
``nv.vss.memory/1.0`` records this group writes. There is deliberately no
``recall``: fetching one record by id *is* ``get``, and querying recent ones
*is* ``list``.

Persistence is in-process through ``vss_core.memory``. The job is written
twice -- ``submitted`` before the VLM call, terminal after -- so a run that
times out or dies still leaves a record saying so, which ``status`` and ``get``
can answer from.

What exit 7 gives back is that handle, not the work: the record is written
``timeout``, a terminal status, and carries no ``backend_ref``, so nothing
revisits it if LVS finishes server-side after the client stopped waiting.
"Resume by job id" therefore means the caller can identify the job and decide
to run it again -- not that an in-flight VLM call can be rejoined.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from datetime import timedelta
import json
from typing import TYPE_CHECKING
from typing import Annotated
from typing import Any
from typing import ClassVar

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import field_validator
from pydantic import model_validator

from vss_cli import config as config_mod
from vss_cli import params as params_mod
from vss_cli.exits import Exit
from vss_cli.group import CommandGroup
from vss_cli.group import Context
from vss_cli.group import InvalidInput

if TYPE_CHECKING:
    from collections.abc import Sequence

    import click

    from vss_cli.lifecycle import Job
    from vss_core.memory import MemoryInput
    from vss_core.memory import RecordBundle

    from .memory_adapter import SummaryAdapter

#: Route the LVS service exposes under its recorded mount. ``vss configure``
#: records the ``lvs`` service, so the full path resolves to that service's own
#: ``/v1/summarize`` -- not the agent's, which has no such endpoint.
_SUMMARIZE_PATH = "/v1/summarize"

#: Job ids stay ``summarize-<ULID>`` (design §5.2/§7.2), while the record's
#: ``group`` token is the shorter unified-schema ``summary``.
_JOB_DOMAIN = "summarize"

#: Crockford base32, for ULID job ids.
_CROCKFORD32 = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

#: Kept here as group-level tuning so tests and deployments can bound retries.
_TERMINAL_WRITE_ATTEMPTS = 3
_TERMINAL_WRITE_BACKOFF_SECONDS = 0.5

#: Below this an event time is an offset into the clip rather than an epoch
#: instant (1e8 seconds is 1973), which is how LVS answers without a
#: ``creation_time`` to anchor against.
_EPOCH_FLOOR = 1e8


class SummarizeInput(BaseModel):
    """Summarize a video and apply the configured memory policy.

    Exactly one source is required: ``--id`` names media the deployment has
    already ingested, ``--url`` points at a video to fetch directly.

    ``--scenario`` and at least one ``--event`` are required because LVS
    requires them: its schema marks ``model``, ``scenario`` and ``events``
    mandatory and answers 422 without them. They are what steers the
    summarization, so there is no sensible default to invent here -- the
    caller states the use case and what to look for.

    Sampling and chunking fields are passed through to the VLM untouched and
    are named as the REST API names them. Omitted fields are absent from the
    request rather than sent as null, so the backend's own defaults apply.

    Structured output is on by default, matching LVS: it asks the VLM for a
    JSON object with ``video_summary`` and ``events``, which is the shape
    memory stores. ``--no-enable-vlm-structured-output`` gives prose, still
    persistable but as a summary with no events.
    """

    # Unknown keys are an error rather than something to drop. Click rejects
    # unknown flags itself, so this guards the programmatic callers where a
    # misspelled key would otherwise pass silently with the default.
    model_config = ConfigDict(extra="forbid")

    id: str | None = Field(None, description="ID of an already-added file or live stream.")
    url: str | None = Field(None, description="Direct URL to a video to summarize (HTTP/HTTPS/S3).")
    # Every bound below is copied from LVS's own SummarizationQuery
    # (services/video-summarization/src/vss_api_models.py), lengths as well as
    # ranges, so a value this model accepts is one the backend accepts. A
    # second, looser set here would spend a round trip to be told 422; a
    # stricter one would refuse values LVS documents, as `--chunk-duration 0`
    # -- its "no chunking" -- once was.
    scenario: str = Field(
        max_length=1024,
        description="Use-case context for the summarization, e.g. 'warehouse monitoring'.",
    )
    # The list caps come in pairs: how many items LVS takes, and how long each
    # item may be. Only the first is a keyword on the list -- the second has to
    # be annotated onto the element, or a 300-character --object-of-interest
    # spends the round trip to come back 422.
    events: list[Annotated[str, Field(max_length=1024)]] = Field(
        max_length=1000,
        description="Event to detect or summarize. Repeat for several.",
        json_schema_extra={params_mod.FLAG_KEY: "--event"},
    )
    objects_of_interest: list[Annotated[str, Field(max_length=256)]] = Field(
        default=[],
        max_length=1000,
        description="Object to detect or extract. Repeat for several.",
        json_schema_extra={params_mod.FLAG_KEY: "--object-of-interest"},
    )
    # The one bound not declared: LVS pins this to exactly 24 characters, which
    # the validator below produces from any ISO-8601 instant. Declared as a
    # length it would run first and refuse the spellings that normalize.
    creation_time: str | None = Field(
        None,
        description=(
            "Absolute start time of the media, ISO-8601 UTC. Anchors the times LVS reports: "
            "without it they are offsets from the start of the clip, which unified memory "
            "cannot store as instants, so the record cannot be written."
        ),
    )
    model: str | None = Field(
        None,
        max_length=1024,
        description="VLM to summarize with. Defaults to the model the deployment's RT-VLM reports serving.",
    )
    prompt: str | None = Field(None, max_length=512_000, description="VLM prompt.")
    system_prompt: str | None = Field(None, max_length=5000, description="VLM system prompt.")
    chunk_duration: int | None = Field(
        None, ge=0, le=3600, description="Chunk duration in seconds. 0 disables chunking."
    )
    chunk_overlap_duration: int | None = Field(None, ge=0, le=3600, description="Chunk overlap duration in seconds.")
    temperature: float | None = Field(None, ge=0.0, le=1.0, description="Sampling temperature.")
    top_p: float | None = Field(None, ge=0.0, le=1.0, description="Nucleus sampling probability mass.")
    top_k: int | None = Field(None, ge=1, le=1000, description="Top-k sampling cutoff.")
    max_tokens: int | None = Field(None, ge=1, le=1_000_000, description="Maximum tokens to generate.")
    seed: int | None = Field(None, ge=1, le=2**32 - 1, description="Sampling seed, for reproducible generations.")
    # LVS marks this deprecated and forwards it to
    # num_frames_per_second_or_fixed_frames_chunk, which the CLI does not expose.
    num_frames_per_chunk: int | None = Field(None, ge=0, le=120, description="Frames sampled from each chunk.")
    enable_audio: bool = Field(False, description="Transcribe the audio stream alongside the video.")
    enable_vlm_structured_output: bool = Field(
        # LVS defaults this to true. Declaring false here would have made
        # --no-enable-vlm-structured-output a no-op: the value would equal the
        # field default, exclude_defaults would drop it, and LVS would apply
        # its own true. Matching LVS keeps the negative spelling meaningful.
        True,
        description="Request structured JSON (summary + events). Recommended when persisting.",
    )

    @field_validator("creation_time")
    @classmethod
    def _millisecond_utc(cls, value: str | None) -> str | None:
        """LVS accepts exactly ``YYYY-MM-DDTHH:MM:SS.sssZ`` -- 24 characters, no more.

        Normalized here rather than left to the caller to count digits: any
        ISO-8601 instant becomes the one spelling LVS accepts, instead of a 422
        for a timestamp that was already unambiguous.
        """
        if value is None:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError(f"creation_time must be an ISO-8601 instant, got {value!r}") from error
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        parsed = parsed.astimezone(UTC)
        return f"{parsed:%Y-%m-%dT%H:%M:%S}.{parsed.microsecond // 1000:03d}Z"

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

    no_persist: bool = Field(False, description="Skip persistence for this summary.")
    write_memory_note: bool | None = Field(
        None,
        description="Override whether this persisted result is written to the configured Markdown cache.",
    )
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


def _default_model(deployment: config_mod.Deployment) -> str:
    """The VLM the deployment reports serving, or a ConfigError naming the fix.

    LVS first, because LVS is what serves this request: it reports the model
    it summarizes with, which is the one that has to appear in the payload.
    RT-VLM is the fallback for a deployment recorded before ``vss configure``
    probed ``lvs``, where the two are usually the same model anyway.
    """
    for name in ("lvs", "rt_vlm"):
        service = deployment.services.get(name)
        if service and service.models:
            return service.models[0]
    raise config_mod.ConfigError(
        f"deployment at {deployment.base_url} reports no LVS or RT-VLM model, so --model cannot be defaulted. "
        f"Pass --model explicitly, or re-run `vss configure --base-url {deployment.base_url}`."
    )


def _instant(value: Any, anchor: datetime | None) -> Any:
    """Spell one LVS event time the way unified memory stores instants.

    LVS answers in numbers -- epoch seconds once ``creation_time`` anchors the
    clip, plain offsets into it otherwise -- while memory keys recall off
    ISO-8601 instants. Non-numeric values pass through untouched: a backend
    that already returns a timestamp needs no help.
    """
    if isinstance(value, str):
        try:
            seconds = float(value)
        except ValueError:
            return value
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        seconds = float(value)
    else:
        return value

    if seconds >= _EPOCH_FLOOR:
        return datetime.fromtimestamp(seconds, UTC).isoformat().replace("+00:00", "Z")
    if anchor is None:
        raise ValueError(
            f"event time {value!r} is an offset into the clip, which unified memory cannot store as an "
            f"instant; pass --creation-time <media start, ISO-8601 UTC> so the times are absolute"
        )
    return (anchor + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")


def _summary_content(completion: dict[str, Any], anchor: datetime | None = None) -> dict[str, Any]:
    """Map an LVS completion into the memory write's ``content`` contract.

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
        events = []
        for row in parsed.get("events") or []:
            if not isinstance(row, dict):
                raise ValueError(f"summary event must be an object, got {type(row).__name__}")
            event = {
                key: _instant(value, anchor) if key in ("start_time", "end_time", "timestamp") else value
                for key, value in row.items()
            }
            # Each event becomes a child record keyed on an absolute instant.
            if "timestamp" not in event and event.get("start_time") is not None:
                event["timestamp"] = event["start_time"]
            events.append(event)
        return {"video_summary": parsed["video_summary"], "events": events}
    return {"video_summary": text if isinstance(text, str) else json.dumps(text), "events": []}


def _adapter() -> SummaryAdapter:
    """The adapter that maps this group's jobs onto memory records.

    One binding rather than an import at each use, and resolved on call rather
    than at module scope: every ``vss_core`` import in this file is deliberately
    lazy, so ``vss summarize --help`` does not pay for the memory package.

    The concrete class, not the registry's ``get_adapter``: ``build_input`` and
    ``build_output`` live on the subclass with per-group signatures, which the
    ``MemoryAdapter`` protocol cannot type. The adapter holds no state, so an
    instance serves the static builders and the record builders alike.
    """
    from .memory_adapter import SummaryAdapter

    return SummaryAdapter()


def _resolve_memory_sensor(
    deployment: config_mod.Deployment,
    inputs: SummarizeInput,
    options: SummarizeOptions,
) -> Any | None:
    """Resolve a raw ``--id`` to VIOS identity when memory needs a name."""
    if options.media_name or not inputs.id or not deployment.has("vst"):
        return None
    from vss_core import vios

    return asyncio.run(vios.resolve_sensor(str(deployment.base_url).rstrip("/"), inputs.id))


def _memory_input(
    inputs: SummarizeInput,
    options: SummarizeOptions,
    request: dict[str, Any],
    resolved_sensor: Any | None = None,
) -> MemoryInput:
    """The record's request side: what was asked, of which asset, with what.

    ``params`` carries the LVS request verbatim, so a persisted job describes
    the call that produced it without a second schema to keep in step.
    """
    media_ref: dict[str, Any] = {"source": options.media_source}
    if inputs.id:
        media_ref["stream_id"] = inputs.id
    if inputs.url:
        media_ref["url"] = inputs.url
    media_name = options.media_name or getattr(resolved_sensor, "name", None)
    if media_name:
        media_ref["name"] = media_name
    if resolved_sensor is not None:
        media_ref["sensor_id"] = resolved_sensor.sensor_id
        media_ref["stream_id"] = resolved_sensor.stream_id
    return _adapter().build_input(
        prompt=inputs.prompt,
        video_id=options.video_id or inputs.id,
        media_ref=media_ref,
        params=request,
    )


def _memory_provenance(completion: dict[str, Any], model: str) -> dict[str, Any]:
    """Small parent ``output.ext`` metadata — never nested event collections."""
    provenance = {
        "completion_id": completion.get("id"),
        "model": completion.get("model") or model,
        "created": completion.get("created"),
    }
    return {k: v for k, v in provenance.items() if v is not None}


class SummarizeGroup(CommandGroup):
    """Summarize video and persist to memory."""

    name: ClassVar[str] = "summarize"
    summary: ClassVar[str] = "Summarize video and persist to memory"

    Input: ClassVar[type[BaseModel] | None] = SummarizeInput
    #: Checked before dispatch, so a deployment without LVS says so instead of
    #: failing later and less clearly -- as `--model cannot be defaulted`, which
    #: names the wrong cause -- and before configured persistence opens
    #: Elasticsearch for a summarization that was never going to run.
    requires: ClassVar[frozenset[str]] = frozenset({"lvs"})
    extra_params: ClassVar[Sequence[click.Parameter]] = tuple(params_mod.options_from_model(SummarizeOptions))
    @classmethod
    def adapter(cls) -> type[SummaryAdapter]:
        from .memory_adapter import SummaryAdapter

        return SummaryAdapter

    def prepare(self, action: str, inputs: BaseModel, ctx: Context, job: Job, *, persist: bool) -> None:  # noqa: ARG002 - fixed hook signature
        if not isinstance(inputs, SummarizeInput):  # pragma: no cover - the framework builds this
            raise TypeError(f"expected SummarizeInput, got {type(inputs).__name__}")

        deployment = ctx.deployment or config_mod.load()
        options = SummarizeOptions(**{k: v for k, v in ctx.extra.items() if k in SummarizeOptions.model_fields})

        # Fail before the expensive summarization: a persisted record needs a
        # video_id, which for a --url summary can only come from --video-id.
        asset_id = options.video_id or inputs.id
        if persist and not asset_id:
            raise InvalidInput("cannot persist a --url summary without --video-id (pass --video-id or --no-persist)")

        request = inputs.model_dump(exclude_none=True, exclude_defaults=True)
        model = inputs.model or _default_model(deployment)
        request["model"] = model

        resolved_sensor = _resolve_memory_sensor(deployment, inputs, options) if persist else None
        job.input_data = _memory_input(inputs, options, request, resolved_sensor)
        job.asset_id = asset_id
        job.state = _SummarizeRun(
            deployment=deployment,
            options=options,
            request=request,
            model=model,
            anchor=datetime.fromisoformat(inputs.creation_time.replace("Z", "+00:00"))
            if inputs.creation_time
            else None,
        )

    def execute(self, action: str, inputs: BaseModel, ctx: Context, job: Job) -> Any:  # noqa: ARG002 - fixed hook signature
        import httpx

        from vss_cli.lifecycle import JobError

        state: _SummarizeRun = job.state
        url = state.deployment.endpoint("lvs").rstrip("/") + _SUMMARIZE_PATH
        try:
            response = httpx.post(url, json=state.request, timeout=float(state.options.request_timeout_seconds))
        except httpx.TimeoutException as error:
            # Exit 7 carries the job id as a correlation handle: reconcile with
            # `status` rather than re-running an hour of summarization.
            raise JobError(
                str(error),
                exit=Exit.TIMEOUT,
                status="timeout",
                diagnostic=(
                    f"vss: summarization timed out after {state.options.request_timeout_seconds}s (job {job.job_id})"
                ),
            ) from error
        except httpx.HTTPError as error:
            raise JobError(
                str(error), exit=Exit.BACKEND_UNREACHABLE, diagnostic=f"vss: lvs unreachable at {url}: {error}"
            ) from error

        if response.status_code >= 400:
            detail = f"HTTP {response.status_code}"
            if response.status_code >= 500:
                raise JobError(detail, exit=Exit.BACKEND_UNREACHABLE, diagnostic=f"vss: lvs backend error {detail}")
            # Built through InvalidInput rather than by hand: a second copy of
            # the prefix would drift the moment that one is reworded.
            raise JobError(
                detail,
                exit=Exit.INVALID_INPUT,
                diagnostic=InvalidInput(f"summarization rejected {detail}: {response.text[:500]}").format_message(),
            )

        try:
            completion = response.json()
        except ValueError as error:
            detail = "response was not valid JSON"
            raise JobError(detail, exit=Exit.BACKEND_UNREACHABLE, diagnostic=f"vss: lvs {detail}") from error
        if not isinstance(completion, dict):
            detail = "response was not a JSON object"
            raise JobError(detail, exit=Exit.BACKEND_UNREACHABLE, diagnostic=f"vss: lvs {detail}")
        return completion

    def build_bundle(self, job: Job, output: Any) -> RecordBundle:
        """The completed parent plus one ``event`` child per event."""
        state: _SummarizeRun = job.state
        content = _summary_content(output, state.anchor)
        state.content = content
        return self.adapter()().terminal_bundle(
            job_id=job.job_id,
            created_at=job.created_at,
            status="completed",
            input_data=job.input_data,
            answer=content["video_summary"],
            events=content["events"],
            ext=_memory_provenance(output, state.model),
            default_sensor_id=(job.input_data.sensors[0].id if job.input_data.sensors else None),
        )

    def render(self, job: Job, output: Any, persist: dict[str, Any] | None) -> dict[str, Any]:
        state: _SummarizeRun = job.state
        if persist is not None and persist.get("status") == "complete" and state.content is not None:
            persist["events"] = len(state.content["events"])
        return {"summary": output}


@dataclass
class _SummarizeRun:
    """What ``prepare`` resolved, for ``execute`` and ``build_bundle``."""

    deployment: config_mod.Deployment
    options: SummarizeOptions
    request: dict[str, Any]
    model: str
    anchor: datetime | None
    content: dict[str, Any] | None = None


SUMMARIZE = SummarizeGroup()

__all__ = ["SUMMARIZE", "SummarizeGroup", "SummarizeInput", "SummarizeOptions"]
