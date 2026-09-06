# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright contributors to the vLLM project
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.

# Adapted from
# https://github.com/lm-sys/FastChat/blob/168ccc29d3f7edc50823016105c024fe2282732a/fastchat/protocol/openai_api_protocol.py
import time
from typing import Any, ClassVar, Literal, TypeAlias

import regex as re
from pydantic import BaseModel, ConfigDict, Field, model_validator
from vllm.entrypoints.chat_utils import make_tool_call_id
from vllm.logger import init_logger
from vllm.sampling_params import SamplingParams
from vllm.utils import random_uuid
from vllm.utils.import_utils import resolve_obj_by_qualname

logger = init_logger(__name__)


class OpenAIBaseModel(BaseModel):
    # OpenAI API does allow extra fields
    model_config = ConfigDict(extra="allow")

    # Cache class field names
    field_names: ClassVar[set[str] | None] = None

    @model_validator(mode="wrap")
    @classmethod
    def __log_extra_fields__(cls, data, handler):
        result = handler(data)
        if not isinstance(data, dict):
            return result
        field_names = cls.field_names
        if field_names is None:
            # Get all class field names and their potential aliases
            field_names = set()
            for field_name, field in cls.model_fields.items():
                field_names.add(field_name)
                if alias := getattr(field, "alias", None):
                    field_names.add(alias)
            cls.field_names = field_names

        # Compare against both field names and aliases
        if any(k not in field_names for k in data):
            logger.warning(
                "The following fields were present in the request but ignored: %s",
                data.keys() - field_names,
            )
        return result


class ErrorInfo(OpenAIBaseModel):
    message: str
    type: str
    param: str | None = None
    code: int


class ErrorResponse(OpenAIBaseModel):
    error: ErrorInfo


class ModelPermission(OpenAIBaseModel):
    id: str = Field(default_factory=lambda: f"modelperm-{random_uuid()}")
    object: str = "model_permission"
    created: int = Field(default_factory=lambda: int(time.time()))
    allow_create_engine: bool = False
    allow_sampling: bool = True
    allow_logprobs: bool = True
    allow_search_indices: bool = False
    allow_view: bool = True
    allow_fine_tuning: bool = False
    organization: str = "*"
    group: str | None = None
    is_blocking: bool = False


class ModelCard(OpenAIBaseModel):
    id: str
    object: str = "model"
    created: int = Field(default_factory=lambda: int(time.time()))
    owned_by: str = "vllm"
    root: str | None = None
    parent: str | None = None
    max_model_len: int | None = None
    permission: list[ModelPermission] = Field(default_factory=list)


class ModelList(OpenAIBaseModel):
    object: str = "list"
    data: list[ModelCard] = Field(default_factory=list)


class PromptTokenUsageInfo(OpenAIBaseModel):
    cached_tokens: int | None = None


class UsageInfo(OpenAIBaseModel):
    prompt_tokens: int = 0
    total_tokens: int = 0
    completion_tokens: int | None = 0
    prompt_tokens_details: PromptTokenUsageInfo | None = None


class RequestResponseMetadata(BaseModel):
    request_id: str
    final_usage_info: UsageInfo | None = None


class JsonSchemaResponseFormat(OpenAIBaseModel):
    name: str
    description: str | None = None
    # schema is the field in openai but that causes conflicts with pydantic so
    # instead use json_schema with an alias
    json_schema: dict[str, Any] | None = Field(default=None, alias="schema")
    strict: bool | None = None


class LegacyStructuralTag(OpenAIBaseModel):
    begin: str
    # schema is the field, but that causes conflicts with pydantic so
    # instead use structural_tag_schema with an alias
    structural_tag_schema: dict[str, Any] | None = Field(default=None, alias="schema")
    end: str


class LegacyStructuralTagResponseFormat(OpenAIBaseModel):
    type: Literal["structural_tag"]
    structures: list[LegacyStructuralTag]
    triggers: list[str]


class StructuralTagResponseFormat(OpenAIBaseModel):
    type: Literal["structural_tag"]
    format: Any


AnyStructuralTagResponseFormat: TypeAlias = (
    LegacyStructuralTagResponseFormat | StructuralTagResponseFormat
)


class ResponseFormat(OpenAIBaseModel):
    # type must be "json_schema", "json_object", or "text"
    type: Literal["text", "json_object", "json_schema"]
    json_schema: JsonSchemaResponseFormat | None = None


AnyResponseFormat: TypeAlias = (
    ResponseFormat | StructuralTagResponseFormat | LegacyStructuralTagResponseFormat
)


class StreamOptions(OpenAIBaseModel):
    include_usage: bool | None = True
    continuous_usage_stats: bool | None = False


class FunctionDefinition(OpenAIBaseModel):
    name: str
    description: str | None = None
    parameters: dict[str, Any] | None = None


# extra="forbid" is a workaround to have kwargs as a field,
# see https://github.com/pydantic/pydantic/issues/3125
class LogitsProcessorConstructor(BaseModel):
    qualname: str
    args: list[Any] | None = None
    kwargs: dict[str, Any] | None = None

    model_config = ConfigDict(extra="forbid")


LogitsProcessors = list[str | LogitsProcessorConstructor]


def get_logits_processors(
    processors: LogitsProcessors | None, pattern: str | None
) -> list[Any] | None:
    if processors and pattern:
        logits_processors = []
        for processor in processors:
            qualname = processor if isinstance(processor, str) else processor.qualname
            if not re.match(pattern, qualname):
                raise ValueError(
                    f"Logits processor '{qualname}' is not allowed by this "
                    "server. See --logits-processor-pattern engine argument "
                    "for more information."
                )
            try:
                logits_processor = resolve_obj_by_qualname(qualname)
            except Exception as e:
                raise ValueError(f"Logits processor '{qualname}' could not be resolved: {e}") from e
            if isinstance(processor, LogitsProcessorConstructor):
                logits_processor = logits_processor(*processor.args or [], **processor.kwargs or {})
            logits_processors.append(logits_processor)
        return logits_processors
    elif processors:
        raise ValueError(
            "The `logits_processors` argument is not supported by this "
            "server. See --logits-processor-pattern engine argument "
            "for more information."
        )
    return None


class FunctionCall(OpenAIBaseModel):
    # Internal field to preserve native tool call ID from tool parser.
    # Excluded from serialization to maintain OpenAI API compatibility
    # (function object should only contain 'name' and 'arguments').
    id: str | None = Field(default=None, exclude=True)
    name: str
    arguments: str


class ToolCall(OpenAIBaseModel):
    id: str = Field(default_factory=make_tool_call_id)
    type: Literal["function"] = "function"
    function: FunctionCall


class DeltaFunctionCall(BaseModel):
    name: str | None = None
    arguments: str | None = None


# a tool call delta where everything is optional
class DeltaToolCall(OpenAIBaseModel):
    id: str | None = None
    type: Literal["function"] | None = None
    index: int
    function: DeltaFunctionCall | None = None


class ExtractedToolCallInformation(BaseModel):
    # indicate if tools were called
    tools_called: bool

    # extracted tool calls
    tool_calls: list[ToolCall]

    # content - per OpenAI spec, content AND tool calls can be returned rarely
    # But some models will do this intentionally
    content: str | None = None


class DeltaMessage(OpenAIBaseModel):
    role: str | None = None
    content: str | None = None
    reasoning: str | None = None
    tool_calls: list[DeltaToolCall] = Field(default_factory=list)


####### Tokens IN <> Tokens OUT #######
class GenerateRequest(BaseModel):
    request_id: str = Field(
        default_factory=random_uuid,
        description=(
            "The request_id related to this request. If the caller does "
            "not set it, a random_uuid will be generated. This id is used "
            "through out the inference process and return in response."
        ),
    )
    token_ids: list[int]
    """The token ids to generate text from."""

    # features: MultiModalFeatureSpec
    # TODO (NickLucche): implement once Renderer work is completed
    features: str | None = None
    """The processed MM inputs for the model."""

    sampling_params: SamplingParams
    """The sampling parameters for the model."""

    model: str | None = None

    stream: bool | None = False
    stream_options: StreamOptions | None = None
    cache_salt: str | None = Field(
        default=None,
        description=(
            "If specified, the prefix cache will be salted with the provided "
            "string to prevent an attacker to guess prompts in multi-user "
            "environments. The salt should be random, protected from "
            "access by 3rd parties, and long enough to be "
            "unpredictable (e.g., 43 characters base64-encoded, corresponding "
            "to 256 bit)."
        ),
    )
    priority: int = Field(
        default=0,
        description=(
            "The priority of the request (lower means earlier handling; "
            "default: 0). Any priority other than 0 will raise an error "
            "if the served model does not use priority scheduling."
        ),
    )
    kv_transfer_params: dict[str, Any] | None = Field(
        default=None,
        description="KVTransfer parameters used for disaggregated serving.",
    )


# ---------------------------------------------------------------------------
# EVS Video Session protocol types
# ---------------------------------------------------------------------------


class EvsAdvancedConfig(OpenAIBaseModel):
    """Statistical tuning knobs for event detection.

    Sensible defaults cover the common case — only set these when
    profiling shows the default gates are mis-triggering.
    """

    min_clips: int | None = None
    """Minimum clips before spike detection activates (default: 3)."""

    max_recording_clips: int | None = None
    """Cap on clips in a single recorded event (default: 20)."""

    spike_std_k: float | None = None
    """Spike gate: score ≥ ema + k·max(σ, ratio·ema). Default: 3.0."""

    settling_std_k: float | None = None
    """Settle gate: score < pre_ema + k·max(σ, ratio·ema). Default: 2.0."""

    std_floor_ratio: float | None = None
    """Proportional σ floor as a fraction of EMA (default: 0.1).

    Effective floor = std_floor_ratio * ema.
    Self-calibrates across camera types: static cameras with low EMA
    get a narrower absolute floor, moving cameras with high EMA get a
    wider one."""

    downward_baseline_min: float | None = None
    """Minimum baseline EMA for downward-spike detection to arm (default: 0.10).

    Downward ("comes to rest") detection is only meaningful on
    moving-camera streams, which have an elevated baseline. Static
    cameras sit below this floor, so downward is auto-suppressed and
    quiet-level jitter no longer fires false events. Set to 0.0 to
    always allow downward (legacy symmetric behavior)."""

    decision_lag: int | None = None
    """Later chunks a present chunk waits for before its decision commits
    (default: 3).

    Buys slack for out-of-order arrivals: a chunk is only decided (and, when
    idle, discarded) once this many later chunks have landed. Lower values
    release idle clips sooner; 1 is appropriate for strictly in-order
    delivery, where the extra wait buys nothing."""


class VideoSessionSamplingParams(OpenAIBaseModel):
    """Decode parameters for the session's event-gated generations.

    Mirrors a subset of vLLM ``SamplingParams`` (where ``max_tokens`` is itself
    a sampling field). The event detector triggers generation autonomously
    (there is no per-call request body for it), so these set the sampling policy
    applied to every such generation in the session. This is the *only* place
    ``max_tokens`` is configured for the session (it defaults to 1024 when
    omitted); all other omitted fields fall back to vLLM ``SamplingParams``
    defaults. The explicit ``POST .../generate`` endpoint is unaffected — it
    takes sampling parameters from its own request body.
    """

    max_tokens: int | None = Field(default=None, ge=1)
    temperature: float | None = Field(default=None, ge=0.0)
    top_p: float | None = Field(default=None, gt=0.0, le=1.0)
    top_k: int | None = Field(default=None, ge=-1)
    min_p: float | None = Field(default=None, ge=0.0, le=1.0)
    presence_penalty: float | None = Field(default=None, ge=-2.0, le=2.0)
    frequency_penalty: float | None = Field(default=None, ge=-2.0, le=2.0)
    repetition_penalty: float | None = Field(default=None, gt=0.0)
    seed: int | None = None
    # ignore_eos lets event-gated generations run to max_tokens instead of
    # stopping at the natural EOS (used for OSL/perf runs via VLLM_IGNORE_EOS);
    # min_tokens sets a lower bound. Both are native vLLM SamplingParams fields
    # and flow through model_dump(exclude_none) -> SamplingParams(**cfg).
    ignore_eos: bool | None = None
    min_tokens: int | None = Field(default=None, ge=0)
    stop: list[str] | str | None = None
    stop_token_ids: list[int] | None = None

    def to_sampling_params_config(
        self, default_max_tokens: int = 1024
    ) -> dict[str, Any]:
        """Serializable kwargs for vLLM ``SamplingParams``.

        Mirrors the ``to_sampling_params`` idiom on the other OpenAI request
        models, but returns the dict form because the session persists this
        config and later expands it via ``SamplingParams(**cfg)`` (so the
        conversion has to survive serialization). Omitted fields are dropped
        (``exclude_none``) so they fall back to vLLM ``SamplingParams``
        defaults; ``max_tokens`` gets an explicit default so every event-gated
        generation is bounded. Constructing ``SamplingParams(**cfg)`` from the
        result is what validates ranges / cross-field constraints.
        """
        cfg = self.model_dump(exclude_none=True)
        cfg.setdefault("max_tokens", default_max_tokens)
        return cfg


def build_session_sampling_params(
    config: dict[str, Any] | None, default_max_tokens: int = 1024
) -> SamplingParams:
    """Expand a persisted session sampling config into vLLM ``SamplingParams``.

    Inverse of ``VideoSessionSamplingParams.to_sampling_params_config``: the
    session stores the config dict and this rebuilds the ``SamplingParams`` at
    generate time. ``None``/empty (legacy sessions with no config) falls back to
    a bounded default so generation is always capped.
    """
    return SamplingParams(**(config or {"max_tokens": default_max_tokens}))


class VideoSessionCreateRequest(OpenAIBaseModel):
    """Request to create a new video analysis session.

    Spike detection is variance-aware:
    ``score ≥ old_ema + spike_std_k · max(std, ratio·ema)``.
    α is derived from ``event_chunk_duration_s / event_ema_memory_s``
    so baseline memory stays constant across different chunk sizes.
    """

    model: str
    token_budget: int = 8192
    prompt: str = ""
    # Optional system prompt for the session, emitted as a leading system turn
    # ahead of the user turn by every prompt builder. None/empty means no system
    # turn at all (the pre-existing behaviour).
    system_prompt: str | None = None
    # Optional timestamp-grounding instruction template applied to the prompt at
    # generate time. May contain ``{query}`` / ``{first_ts}`` / ``{last_ts}`` /
    # ``{timestamps}`` placeholders, which the session fills from the merged clip
    # range (the only point that knows the full event span). A template
    # containing ``{query}`` is treated as a full prompt template; one without it
    # is appended to the prompt as a suffix. None disables the instruction.
    timestamp_prompt_template: str | None = None
    # Sampling policy for this session's event-gated (auto) generations,
    # including max_tokens (itself a vLLM SamplingParams field). There is no
    # separate top-level max_tokens. None → vLLM SamplingParams defaults (the
    # server applies max_tokens=1024).
    sampling_params: VideoSessionSamplingParams | None = None
    event_only: bool = False
    event_chunk_duration_s: float | None = None
    event_ema_memory_s: float | None = None
    event_advanced: EvsAdvancedConfig | None = None


class VideoSessionCreateResponse(OpenAIBaseModel):
    """Response after creating a video session."""

    session_id: str


class VideoClipAddRequest(OpenAIBaseModel):
    """Request to add a video clip to a session.

    Follows the OpenAI content part convention. The video can be provided as:
    - A data URL: {"url": "data:video/mp4;base64,<base64_data>"}
    - An HTTP URL: {"url": "https://example.com/clip.mp4"}
    """

    video_url: dict[str, str]
    mm_processor_kwargs: dict[str, Any] | None = None
    timestamps: list[float] | None = None
    is_last: bool = False
    chunk_id: int | None = None


class VideoClipResponse(OpenAIBaseModel):
    """Response after adding a video clip to a session."""

    clip_id: str
    tokens_used: int
    tokens_remaining: int
    frames_kept: int
    frames_dropped: int
    kept_timestamps: list[float] = []
    stashed: bool = False
    generated: bool = False
    generate_trigger: str | None = None
    response_text: str | None = None
    response_usage: dict | None = None
    boundary_pending: bool = False
    """True when this clip arrived out-of-order and its boundary with the
    predecessor has not yet been resolved (first frame may be re-pruned
    once the predecessor arrives)."""
    event_started: bool = False
    """True when this clip triggered an EMA spike and event recording began."""
    idle_discarded: bool = False
    """True when event_only mode discarded this clip (no active event)."""
    round_timestamps: list[float] = []
    """Client timestamps of the clips that were analyzed in the generation
    round.  Empty when no generation occurred.  Via-engine should use
    these (not the current chunk's timestamps) to attribute the response."""
