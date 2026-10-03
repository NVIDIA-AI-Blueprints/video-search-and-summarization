# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright contributors to the vLLM project

import enum
import time
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import torch
from typing_extensions import deprecated

from vllm.multimodal.inputs import MultiModalFeatureSpec
from vllm.pooling_params import PoolingParams
from vllm.sampling_params import SamplingParams
from vllm.utils import length_from_prompt_token_ids_or_embeds
from vllm.v1.engine import (
    EngineCoreEvent,
    EngineCoreEventType,
    EngineCoreRequest,
    FinishReason,
)
from vllm.v1.streaming.retention import HistorySegment, StreamingRetentionParams
from vllm.v1.structured_output.request import StructuredOutputRequest
from vllm.v1.utils import ConstantList

if TYPE_CHECKING:
    from vllm.lora.request import LoRARequest
    from vllm.v1.core.kv_cache_utils import BlockHash


@dataclass
class StreamingUpdate:
    """Lightweight data for streaming session continuation.

    Contains only the fields needed to update an existing streaming session
    with new input data.
    """

    mm_features: list[MultiModalFeatureSpec] | None
    prompt_token_ids: list[int] | None
    arrival_time: float
    sampling_params: SamplingParams | None
    discard_prior_output: bool = False

    @classmethod
    def from_request(cls, request: "Request") -> "StreamingUpdate | None":
        if not request.resumable:
            return None
        return cls(
            mm_features=request.mm_features,
            prompt_token_ids=request.prompt_token_ids,
            arrival_time=request.arrival_time,
            sampling_params=request.sampling_params,
            discard_prior_output=request.discard_prior_output,
        )


class Request:
    def __init__(
        self,
        request_id: str,
        prompt_token_ids: list[int] | None,
        sampling_params: SamplingParams | None,
        pooling_params: PoolingParams | None,
        client_index: int = 0,
        arrival_time: float | None = None,
        prompt_embeds: torch.Tensor | None = None,
        mm_features: list[MultiModalFeatureSpec] | None = None,
        lora_request: "LoRARequest | None" = None,
        cache_salt: str | None = None,
        priority: int = 0,
        trace_headers: Mapping[str, str] | None = None,
        block_hasher: Callable[["Request"], list["BlockHash"]] | None = None,
        resumable: bool = False,
        discard_prior_output: bool = False,
        reasoning_ended: bool | None = None,
    ) -> None:
        self.request_id = request_id
        self.client_index = client_index
        self.priority = priority
        self.sampling_params = sampling_params
        self.pooling_params = pooling_params
        self.lora_request = lora_request
        self.structured_output_request = StructuredOutputRequest.from_sampling_params(
            sampling_params
        )
        if self.structured_output_request is not None:
            self.structured_output_request.reasoning_ended = reasoning_ended
        self.arrival_time = arrival_time if arrival_time is not None else time.time()

        self.status = RequestStatus.WAITING
        self.events: list[EngineCoreEvent] = []
        self.stop_reason: int | str | None = None

        # P/D: Connector-specific KV transfer parameters.
        self.kv_transfer_params: dict[str, Any] | None = None
        # Per-session retention config (read from
        # `sampling_params.extra_args["streaming_retention"]` below);
        # preserved across chunks by `_update_request_as_session`.
        self.streaming_retention: StreamingRetentionParams | None = None
        self.streaming_previous_text_start_token: int | None = None
        self.streaming_previous_text_end_token: int | None = None
        self.streaming_eos_token_id: int | None = None
        self.streaming_assistant_continuation_token_ids: tuple[int, ...] = ()
        self.streaming_role_end_token_ids: tuple[int, ...] = ()
        self.streaming_user_role_start_token_ids: tuple[int, ...] = ()
        self.streaming_vision_start_token_id: int | None = None
        # Seeded V2 sampling normally derives its random draw from the token
        # position. Streaming compaction reuses dense token positions, so keep
        # a monotonic per-session draw cursor independent of the KV layout.
        self.streaming_sampling_cursor: int = 0

        if pooling_params is not None:
            # Pooling models.
            self.max_tokens = 1
        elif sampling_params is not None:
            # Generative models.
            assert sampling_params.max_tokens is not None
            self.max_tokens = sampling_params.max_tokens
            if self.structured_output_request is not None:
                self.status = RequestStatus.WAITING_FOR_FSM

            if sampling_params.extra_args is not None:
                self.kv_transfer_params = sampling_params.extra_args.get(
                    "kv_transfer_params"
                )
                _retention = sampling_params.extra_args.get("streaming_retention")
                if isinstance(_retention, StreamingRetentionParams):
                    self.streaming_retention = _retention  # in-process API
                elif isinstance(_retention, dict):
                    # Cross-process IPC: arrives as a plain dict.
                    self.streaming_retention = StreamingRetentionParams(**_retention)
                _previous_text_start = sampling_params.extra_args.get(
                    "streaming_previous_text_start_token"
                )
                if _previous_text_start is not None:
                    self.streaming_previous_text_start_token = int(
                        _previous_text_start
                    )
                _previous_text_end = sampling_params.extra_args.get(
                    "streaming_previous_text_end_token"
                )
                if _previous_text_end is not None:
                    self.streaming_previous_text_end_token = int(_previous_text_end)
                _streaming_eos = sampling_params.extra_args.get(
                    "streaming_eos_token_id"
                )
                if _streaming_eos is not None:
                    self.streaming_eos_token_id = int(_streaming_eos)
                _streaming_continuation = sampling_params.extra_args.get(
                    "streaming_assistant_continuation_token_ids"
                )
                if _streaming_continuation is not None:
                    self.streaming_assistant_continuation_token_ids = tuple(
                        int(token_id) for token_id in _streaming_continuation
                    )
                _streaming_role_end = sampling_params.extra_args.get(
                    "streaming_role_end_token_ids"
                )
                if _streaming_role_end is not None:
                    self.streaming_role_end_token_ids = tuple(
                        int(token_id) for token_id in _streaming_role_end
                    )
                _streaming_user_start = sampling_params.extra_args.get(
                    "streaming_user_role_start_token_ids"
                )
                if _streaming_user_start is not None:
                    self.streaming_user_role_start_token_ids = tuple(
                        int(token_id) for token_id in _streaming_user_start
                    )
                _streaming_vision_start = sampling_params.extra_args.get(
                    "streaming_vision_start_token_id"
                )
                if _streaming_vision_start is not None:
                    self.streaming_vision_start_token_id = int(
                        _streaming_vision_start
                    )
        else:
            raise ValueError("sampling_params and pooling_params can't both be unset")

        self.prompt_token_ids = prompt_token_ids
        self.prompt_embeds = prompt_embeds
        # Cache per-block prompt-embed hashes to avoid rehashing the same
        # tensor slices when generating extra keys.
        self._prompt_embeds_per_block_hashes: dict[tuple[int, int], bytes] = {}
        self.num_prompt_tokens = length_from_prompt_token_ids_or_embeds(
            prompt_token_ids, prompt_embeds
        )
        self._output_token_ids: list[int] = []
        self._all_token_ids: list[int] = (
            self.prompt_token_ids.copy()
            if self.prompt_token_ids is not None
            else [0] * self.num_prompt_tokens
        )

        # Per-token mRoPE positions, in lockstep with `_all_token_ids`.
        # Populated lazily by the model runner on first prefill; extended per
        # streaming chunk. `max_cached_position` is the highest position number
        # in the KV cache; eviction never decrements it, so positions may have
        # gaps after eviction.
        self._mrope_positions: list[tuple[int, int, int]] = []
        self.max_cached_position: int = -1

        # Used in async scheduling.
        self.num_output_placeholders = 0
        # Used in forced preemption (reset_prefix_cache) with async scheduling.
        self.discard_latest_async_tokens = False

        self.spec_token_ids: list[int] = []
        self.num_computed_tokens = 0
        self.cache_salt: str | None = cache_salt

        # Multi-modal related
        self.mm_features = mm_features or []

        # Read-only views
        # Prevent directly appending to these lists since
        # they should also be updated simultaneously.
        self.output_token_ids = ConstantList(self._output_token_ids)
        self.all_token_ids = ConstantList(self._all_token_ids)
        # trace_headers
        self.trace_headers = trace_headers
        # State
        # The number of tokens with prefix cache hits.
        self.num_cached_tokens = -1

        # True if this request is scheduled as a non-final prefill chunk.
        self.is_prefill_chunk = False

        # The number of NaNs in logits. A value greater than 0
        # indicates that the output is corrupted
        self.num_nans_in_logits = 0

        # The number of times this request has been preempted by the scheduler.
        self.num_preemptions = 0

        # The number of tokens that have been computed remotely.
        self.num_external_computed_tokens = 0

        self.block_hashes: list[BlockHash] = []
        # Store the block hasher without binding self to avoid creating a
        # reference cycle (Request -> partial -> Request) that prevents
        # immediate garbage collection via reference counting.
        self._block_hasher: Callable[[Request], list[BlockHash]] | None = block_hasher
        self.update_block_hashes()

        self.skip_reading_prefix_cache = self.get_skip_reading_prefix_cache()

        # Used for streaming
        self.resumable = resumable
        self.discard_prior_output = discard_prior_output
        # None entry in the queue means finished.
        self.streaming_queue: deque[StreamingUpdate | None] | None = None

        # Per-session segment bookkeeping: filled by the scheduler as chunks
        # arrive, consumed by `streaming.eviction` when over budget.
        self.session_history: list[HistorySegment] = []
        # Token ranges evicted scheduler-side since the last worker update,
        # each in the _all_token_ids index space at eviction time. Drained
        # into `NewRequestData.evicted_token_ranges` and replayed on the
        # worker to slice its mrope_positions in lockstep.
        self.pending_evicted_token_ranges: list[tuple[int, int]] = []

        # Re-prefill bookkeeping. `reprefill_count` is observability only.
        # `pending_reprefill` is set by `_reprefill_streaming_session` and
        # drained into `NewRequestData.is_reprefill`, telling the worker to
        # reset mRoPE to 0 (vs. eviction, which only slices it).
        self.reprefill_count: int = 0
        self.pending_reprefill: bool = False
        # A re-prefill must sample >=1 token to complete. When the trigger
        # found the session idle between frames, that token is a phantom
        # caption and this tells `update_from_output` to discard it. When a
        # queued chunk was folded first, the first sample is genuine output.
        self.reprefill_discard_next_sample: bool = False
        self.pending_kv_slot_relocations: list[
            list[tuple[int, int, int, int]]
        ] = []
        self.pending_mrope_relocation_order: list[int] = []
        self.text_relocation_count: int = 0
        self.last_reprefill_text_relocation_count: int = 0

    @property
    @deprecated(
        "Request.eos_token_id will be removed in v0.18. "
        "Please use Request.sampling_params.eos_token_id instead."
    )
    def eos_token_id(self) -> int | None:
        if self.sampling_params is None:
            return None

        return self.sampling_params.eos_token_id

    @classmethod
    def from_engine_core_request(
        cls,
        request: EngineCoreRequest,
        block_hasher: Callable[["Request"], list["BlockHash"]] | None,
    ) -> "Request":
        return cls(
            request_id=request.request_id,
            client_index=request.client_index,
            prompt_token_ids=request.prompt_token_ids,
            prompt_embeds=request.prompt_embeds,
            mm_features=request.mm_features,
            sampling_params=request.sampling_params,
            pooling_params=request.pooling_params,
            arrival_time=request.arrival_time,
            lora_request=request.lora_request,
            cache_salt=request.cache_salt,
            priority=request.priority,
            trace_headers=request.trace_headers,
            block_hasher=block_hasher,
            resumable=request.resumable,
            discard_prior_output=request.discard_prior_output,
            reasoning_ended=request.reasoning_ended,
        )

    def append_output_token_ids(
        self,
        token_ids: int | list[int],
    ) -> None:
        if isinstance(token_ids, int):
            self._output_token_ids.append(token_ids)
            self._all_token_ids.append(token_ids)
        else:
            self._output_token_ids.extend(token_ids)
            self._all_token_ids.extend(token_ids)

        self.update_block_hashes()

    def update_block_hashes(self) -> None:
        """Compute block hashes for any new full blocks and append them."""
        if self._block_hasher is not None:
            self.block_hashes.extend(self._block_hasher(self))

    @property
    def use_structured_output(self) -> bool:
        return self.structured_output_request is not None

    @property
    def num_tokens(self) -> int:
        return len(self._all_token_ids)

    @property
    def num_tokens_with_spec(self) -> int:
        return len(self._all_token_ids) + len(self.spec_token_ids)

    @property
    def num_output_tokens(self) -> int:
        return len(self._output_token_ids)

    @property
    def num_encoder_inputs(self) -> int:
        return len(self.mm_features)

    @property
    def has_encoder_inputs(self) -> bool:
        return self.num_encoder_inputs > 0

    def get_skip_reading_prefix_cache(self) -> bool:
        if (
            self.sampling_params is not None
            and self.sampling_params.skip_reading_prefix_cache is not None
        ):
            return self.sampling_params.skip_reading_prefix_cache
        elif (
            self.pooling_params is not None
            and self.pooling_params.skip_reading_prefix_cache is not None
        ):
            return self.pooling_params.skip_reading_prefix_cache
        return False

    def is_finished(self) -> bool:
        return RequestStatus.is_finished(self.status)

    def get_finished_reason(self) -> FinishReason | None:
        return RequestStatus.get_finished_reason(self.status)

    def get_num_encoder_embeds(self, input_id: int) -> int:
        assert input_id < len(self.mm_features)
        return self.mm_features[input_id].mm_position.get_num_embeds()

    def get_encoder_compute_size(self, input_id: int) -> int:
        assert input_id < len(self.mm_features)
        return self.mm_features[input_id].mm_position.get_encoder_compute_size()

    def uses_persistent_encoder_cache(self) -> bool:
        """True if encoder cache entries should live until their segment is
        evicted, rather than be freed after each chunk's forward pass.

        Re-prefill-enabled sessions need the surviving embeddings available at
        the next re-prefill so the encoder need not re-run on every retained
        frame. A `reprefill_threshold >= 1.0` (disabled) falls back to
        per-chunk freeing.
        """
        return (
            self.streaming_retention is not None
            and self.streaming_retention.reprefill_threshold < 1.0
        )

    def record_event(
        self,
        event_type: EngineCoreEventType,
        timestamp: float | None = None,
    ) -> None:
        self.events.append(EngineCoreEvent.new_event(event_type, timestamp))

    def take_events(self) -> list[EngineCoreEvent] | None:
        if not self.events:
            return None
        events, self.events = self.events, []
        return events

    def __lt__(self, other: "Request") -> bool:
        """
        Compare two requests based on priority, arrival time, and request ID.
        Used in priority scheduling.
        """
        if self.priority != other.priority:
            return self.priority < other.priority
        if self.arrival_time != other.arrival_time:
            return self.arrival_time < other.arrival_time
        if self.request_id != other.request_id:
            return self.request_id < other.request_id
        return id(self) < id(other)


class RequestStatus(enum.IntEnum):
    """Status of a request."""

    WAITING = enum.auto()
    WAITING_FOR_FSM = enum.auto()
    WAITING_FOR_REMOTE_KVS = enum.auto()
    WAITING_FOR_STREAMING_REQ = enum.auto()
    RUNNING = enum.auto()
    PREEMPTED = enum.auto()
    # Note: anything after PREEMPTED will be considered
    # as a finished status.
    FINISHED_STOPPED = enum.auto()
    FINISHED_LENGTH_CAPPED = enum.auto()
    FINISHED_ABORTED = enum.auto()
    FINISHED_IGNORED = enum.auto()
    FINISHED_ERROR = enum.auto()
    FINISHED_REPETITION = enum.auto()

    def __str__(self) -> str:
        return self.name

    @staticmethod
    def is_finished(status: "RequestStatus") -> bool:
        return status > RequestStatus.PREEMPTED

    @staticmethod
    def get_finished_reason(status: "RequestStatus") -> FinishReason | None:
        return _FINISHED_REASON_MAP.get(status)


# Mapping of finished statuses to their finish reasons.
# NOTE: The ignored requests are the requests whose prompt lengths
# are longer than the model's length cap. Therefore, the stop
# reason should also be "length" as in OpenAI API.
_FINISHED_REASON_MAP = {
    RequestStatus.FINISHED_STOPPED: FinishReason.STOP,
    RequestStatus.FINISHED_LENGTH_CAPPED: FinishReason.LENGTH,
    RequestStatus.FINISHED_ABORTED: FinishReason.ABORT,
    RequestStatus.FINISHED_IGNORED: FinishReason.LENGTH,
    RequestStatus.FINISHED_ERROR: FinishReason.ERROR,
    RequestStatus.WAITING_FOR_STREAMING_REQ: FinishReason.STOP,
    RequestStatus.FINISHED_REPETITION: FinishReason.REPETITION,
}
