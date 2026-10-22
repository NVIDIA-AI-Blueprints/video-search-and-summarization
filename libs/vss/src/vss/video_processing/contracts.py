# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""JSON-safe schemas and structural protocols for video processing."""

from __future__ import annotations

import math
from collections.abc import Sequence
from pathlib import Path
from typing import Annotated, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PositiveInt = Annotated[int, Field(gt=0)]
NonNegativeInt = Annotated[int, Field(ge=0)]


class FrozenModel(BaseModel):
    """Common strict immutable JSON model configuration."""

    model_config = ConfigDict(frozen=True, extra="forbid")


def _integer_ns(value: object) -> object:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("nanosecond values must be integers")
    return value


def _finite(value: object) -> object:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("value must be a finite number")
    if not math.isfinite(value):
        raise ValueError("value must be a finite number")
    return value


@runtime_checkable
class SourceRef(Protocol):
    """Structural source contract accepted without importing ingestion."""

    url: str | None
    source_id: str


SourceInput = str | Path | SourceRef


class MediaFacts(FrozenModel):
    """JSON-safe source facts; unknown metadata is represented by ``None``."""

    source_id: str = Field(description="Stable identity for this processing operation.")
    codec: str | None = Field(
        default=None,
        description="Video codec name or backend four-character tag, if known.",
    )
    duration_s: float | None = Field(
        default=None, ge=0, description="Video duration in seconds."
    )
    fps: float | None = Field(
        default=None, gt=0, description="Average frame rate in frames per second."
    )
    width: PositiveInt | None = Field(
        default=None, description="Coded frame width in pixels."
    )
    height: PositiveInt | None = Field(
        default=None, description="Coded frame height in pixels."
    )
    audio: bool | None = Field(
        default=None, description="Whether an audio stream is present, if known."
    )
    container: str | None = Field(
        default=None, description="Container format name, if known."
    )
    warnings: tuple[str, ...] = Field(
        default=(), description="Limitations in probed metadata."
    )
    frame_count: NonNegativeInt | None = Field(
        default=None, description="Frame count, if known."
    )
    duration_quality: Literal["video", "container", "unknown"] = Field(
        default="unknown", description="Provenance of duration_s."
    )
    frame_rate_mode: Literal["cfr", "vfr", "unknown"] = Field(
        default="unknown", description="Whether presentation timing is constant-rate."
    )
    timestamp_origin_ns: int | None = Field(
        default=None,
        description="First video presentation timestamp in raw source nanoseconds.",
    )
    color_matrix: Literal["bt601", "bt709"] | None = Field(
        default=None,
        description="Decoded YUV matrix coefficients, when reported by the source.",
    )
    color_range: Literal["limited", "full"] | None = Field(
        default=None,
        description="Decoded YUV sample range, when reported by the source.",
    )
    schema_version: Literal["1.0"] = Field(
        default="1.0", description="Schema contract version."
    )

    @field_validator("duration_s", "fps", mode="before")
    @classmethod
    def finite_optional_numbers(cls, value: object) -> object:
        return None if value is None else _finite(value)

    @field_validator("timestamp_origin_ns", mode="before")
    @classmethod
    def integer_origin(cls, value: object) -> object:
        return None if value is None else _integer_ns(value)

    @field_validator("source_id")
    @classmethod
    def nonempty_source_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("source_id must not be empty")
        return value


class TimeWindow(FrozenModel):
    """Half-open source-relative nanosecond interval ``[start_ns, end_ns)``."""

    start_ns: NonNegativeInt = Field(
        default=0, description="Inclusive start in source-relative nanoseconds."
    )
    end_ns: NonNegativeInt | None = Field(
        default=None,
        description="Exclusive end in source-relative nanoseconds; None means EOF.",
    )
    schema_version: Literal["1.0"] = Field(
        default="1.0", description="Schema contract version."
    )

    @field_validator("start_ns", "end_ns", mode="before")
    @classmethod
    def integral_bounds(cls, value: object) -> object:
        return None if value is None else _integer_ns(value)

    @model_validator(mode="after")
    def ordered(self) -> TimeWindow:
        if self.end_ns is not None and self.end_ns <= self.start_ns:
            raise ValueError("end_ns must be greater than start_ns")
        return self


class DecodePlan(FrozenModel):
    """Sequential or explicit sparse decode plan."""

    window: TimeWindow = Field(
        default_factory=TimeWindow, description="Half-open source-time window."
    )
    sample_timestamps_ns: tuple[NonNegativeInt, ...] | None = Field(
        default=None,
        description=(
            "Ordered target times; None decodes sequentially and () decodes none."
        ),
    )
    schema_version: Literal["1.0"] = Field(
        default="1.0", description="Schema contract version."
    )

    @field_validator("sample_timestamps_ns", mode="before")
    @classmethod
    def integral_targets(cls, value: object) -> object:
        if value is None:
            return None
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
            raise ValueError("sample_timestamps_ns must be a sequence of integers")
        return tuple(_integer_ns(item) for item in value)

    @model_validator(mode="after")
    def ordered_targets(self) -> DecodePlan:
        targets = self.sample_timestamps_ns
        if targets is not None:
            if any(a > b for a, b in zip(targets, targets[1:], strict=False)):
                raise ValueError("sample timestamps must be nondecreasing")
            if any(target < self.window.start_ns for target in targets):
                raise ValueError("sample timestamps must be inside the window")
            if self.window.end_ns is not None and any(
                target >= self.window.end_ns for target in targets
            ):
                raise ValueError("sample timestamps must be inside the window")
        return self


class SamplingPolicy(FrozenModel):
    """Unambiguous target-generation policy for frame subsampling."""

    mode: Literal["count", "fps", "timestamps", "all"] = Field(
        description="Target selection mode."
    )
    count: PositiveInt | None = Field(
        default=None, description="Strict positive requested frame count."
    )
    fps: float | None = Field(
        default=None, gt=0, description="Sampling rate in frames per second."
    )
    timestamps_ns: tuple[NonNegativeInt, ...] | None = Field(
        default=None, description="Explicit ordered source-relative target times."
    )
    allow_missing_timestamps: bool = Field(
        default=False,
        description=(
            "Allow generated timestamp grids to end without satisfying every target."
        ),
    )
    schema_version: Literal["1.0"] = Field(
        default="1.0", description="Schema contract version."
    )

    @field_validator("count", mode="before")
    @classmethod
    def strict_count(cls, value: object) -> object:
        if value is None:
            return None
        return _integer_ns(value)

    @field_validator("timestamps_ns", mode="before")
    @classmethod
    def integral_timestamps(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            return tuple(_integer_ns(item) for item in value)
        raise ValueError("timestamps_ns must be a sequence of integer nanoseconds")

    @field_validator("fps", mode="before")
    @classmethod
    def finite_fps(cls, value: object) -> object:
        return None if value is None else _finite(value)

    @model_validator(mode="after")
    def matching_field_only(self) -> SamplingPolicy:
        expected = {
            "count": "count",
            "fps": "fps",
            "timestamps": "timestamps_ns",
            "all": None,
        }[self.mode]
        supplied = {
            "count": self.count is not None,
            "fps": self.fps is not None,
            "timestamps_ns": self.timestamps_ns is not None,
        }
        if any(value and key != expected for key, value in supplied.items()):
            raise ValueError("only the field matching mode may be set")
        if self.mode in {"count", "fps", "timestamps"} and not supplied[expected]:
            raise ValueError(f"mode={self.mode!r} requires {expected}")
        if self.allow_missing_timestamps and self.mode != "timestamps":
            raise ValueError(
                "allow_missing_timestamps is only valid for mode='timestamps'"
            )
        if self.timestamps_ns is not None and any(
            a > b
            for a, b in zip(self.timestamps_ns, self.timestamps_ns[1:], strict=False)
        ):
            raise ValueError("timestamps_ns must be nondecreasing")
        return self


class DecodeOptions(FrozenModel):
    """Native decoder selection and per-loop session limits."""

    decoder: str = Field(
        default="auto", description="auto, pynv, opencv, or registered plugin name."
    )
    device_id: NonNegativeInt = Field(
        default=0, description="GPU device ordinal for PyNvVideoCodec."
    )
    frame_transport: Literal["owned_cpu", "cuda_ipc"] = Field(
        default="owned_cpu",
        description=(
            "Decoded-frame transport. cuda_ipc keeps PyNv output as device NV12 "
            "for the shared-frame broker."
        ),
    )
    timestamp_mode: Literal["best_effort", "strict"] = Field(
        default="best_effort", description="Whether estimated timestamps are permitted."
    )
    batch_size: PositiveInt = Field(
        default=8, description="Maximum frames requested from each native read."
    )
    max_concurrency: PositiveInt = Field(
        default=4,
        description="Live sessions allowed per event loop and matching limit.",
    )
    schema_version: Literal["1.0"] = Field(
        default="1.0", description="Schema contract version."
    )

    @field_validator("device_id", "batch_size", "max_concurrency", mode="before")
    @classmethod
    def strict_ints(cls, value: object) -> object:
        return _integer_ns(value)

    @field_validator("decoder")
    @classmethod
    def decoder_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("decoder name must not be empty")
        return value


class FrameMetadata(FrozenModel):
    """JSON-safe frame provenance without pixel data."""

    source_id: str = Field(description="Original operation identity.")
    timestamp_ns: NonNegativeInt = Field(
        description="Observed source-relative presentation time."
    )
    requested_timestamp_ns: NonNegativeInt | None = Field(
        default=None, description="Requested target time, when selected sparsely."
    )
    index: NonNegativeInt | None = Field(
        default=None, description="Source frame index when known."
    )
    timestamp_quality: Literal["pts", "backend", "estimated"] = Field(
        default="pts", description="Observed timestamp provenance."
    )
    decoder: str = Field(description="Decoder backend name.")
    device: Literal["cpu", "gpu"] = Field(description="Where native decode occurred.")
    fallback_reason: str | None = Field(
        default=None,
        description="GPU rejection reason when automatic CPU fallback was used.",
    )
    schema_version: Literal["1.0"] = Field(
        default="1.0", description="Schema contract version."
    )

    @field_validator("timestamp_ns", "requested_timestamp_ns", "index", mode="before")
    @classmethod
    def integral_frame_times(cls, value: object) -> object:
        return None if value is None else _integer_ns(value)

    @field_validator("source_id", "decoder")
    @classmethod
    def nonempty_labels(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must not be empty")
        return value


class EncodedFrame(FrozenModel):
    """Base64 image transfer envelope with JSON-safe metadata."""

    metadata: FrameMetadata = Field(
        description="JSON-safe source and frame timing metadata."
    )
    mime_type: Literal["image/jpeg", "image/png"] = Field(
        description="Encoded image MIME type."
    )
    data_base64: str = Field(
        description="Plain base64 encoded image bytes, without a data URI prefix."
    )
    schema_version: Literal["1.0"] = Field(
        default="1.0", description="Schema contract version."
    )


class RawFrame(Protocol):
    """Structural native frame returned by registered decoder sessions."""

    tensor: object
    index: int | None
    timestamp_ns: int | None
    timestamp_quality: str


class DecoderSession(Protocol):
    """Blocking serialized decoder session protocol."""

    def probe(self) -> MediaFacts: ...
    def seek(self, timestamp_ns: int) -> None: ...
    def read_frames(self, batch_size: int) -> list[RawFrame]: ...
    def close(self) -> None: ...


class DecoderBackend(Protocol):
    """Factory-produced backend for native session creation."""

    def capability_map(self) -> dict[str, object]: ...
    def open(self, path: Path, options: DecodeOptions) -> DecoderSession: ...
