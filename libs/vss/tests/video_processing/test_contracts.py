# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Public schema and in-memory/transfer representation guardrails."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from typing import Any

import numpy as np
import pytest
from pydantic import ValidationError

EXPORTS = {
    "probe_video",
    "probe_video_sync",
    "decode_frames",
    "extract_frames",
    "extract_frames_sync",
    "subsample_frames",
    "materialize_clip",
    "MediaFacts",
    "TimeWindow",
    "DecodePlan",
    "SamplingPolicy",
    "DecodeOptions",
    "Frame",
    "FrameMetadata",
    "EncodedFrame",
    "ClipArtifact",
    "SourceRef",
    "register_decoder",
    "ProcessingError",
    "UnsupportedSourceError",
    "DecoderUnavailableError",
    "DecodeError",
    "TimestampUnavailableError",
    "ClipError",
}


def test_public_processing_exports(processing: Any) -> None:
    missing = sorted(EXPORTS - set(dir(processing)))
    assert not missing, f"Processing API not implemented yet: {', '.join(missing)}"


def test_media_facts_has_documented_fields_and_round_trips(processing: Any) -> None:
    facts = processing.MediaFacts(
        source_id="video-1",
        codec="h264",
        duration_s=120.5,
        fps=29.97,
        width=1920,
        height=1080,
        audio=True,
        container="mp4",
    )
    payload = json.loads(facts.model_dump_json())
    assert {
        "codec",
        "duration_s",
        "fps",
        "width",
        "height",
        "audio",
        "container",
        "warnings",
        "schema_version",
    } <= payload.keys()
    assert payload["schema_version"] == "1.0"
    assert processing.MediaFacts.model_validate_json(facts.model_dump_json()) == facts


def test_unknown_metadata_stays_unknown(processing: Any) -> None:
    facts = processing.MediaFacts(source_id="video-1", width=3, height=2)
    assert facts.duration_s is None
    assert facts.fps is None
    assert facts.audio is None  # Unknown is different from "no audio".
    assert facts.codec is None
    assert facts.container is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("duration_s", -1),
        ("duration_s", float("nan")),
        ("duration_s", float("inf")),
        ("fps", 0),
        ("fps", float("nan")),
        ("width", 0),
        ("height", -1),
        ("frame_count", -1),
        ("schema_version", "2.0"),
        ("unexpected", True),
    ],
)
def test_media_facts_rejects_invalid_values(
    processing: Any,
    field: str,
    value: object,
) -> None:
    data = {"source_id": "video-1", "width": 3, "height": 2, field: value}
    with pytest.raises(ValidationError):
        processing.MediaFacts(**data)


def test_metadata_and_configuration_are_frozen(processing: Any) -> None:
    facts = processing.MediaFacts(source_id="video-1", width=3, height=2)
    with pytest.raises(ValidationError):
        facts.width = 99
    options = processing.DecodeOptions()
    assert options.decoder == "auto"
    assert options.device_id == 0
    assert options.timestamp_mode == "best_effort"
    assert options.batch_size == 8
    assert options.max_concurrency == 4
    with pytest.raises(ValidationError):
        options.batch_size = 1


@pytest.mark.parametrize(
    "data",
    [
        {"batch_size": 0},
        {"max_concurrency": 0},
        {"device_id": -1},
        {"timestamp_mode": "pretend_exact"},
        {"unexpected": True},
    ],
)
def test_decode_options_validate(processing: Any, data: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        processing.DecodeOptions(**data)


def make_frame(processing: Any, pixels: np.ndarray | None = None) -> Any:
    return processing.Frame(
        tensor=pixels if pixels is not None else np.zeros((2, 3, 3), dtype=np.uint8),
        source_id="video-1",
        timestamp_ns=150_000_000,
        index=3,
        timestamp_quality="pts",
        decoder="memory",
        device="cpu",
    )


def test_frame_exposes_documented_fields_and_json_safe_metadata(
    processing: Any,
) -> None:
    frame = make_frame(processing)
    assert frame.tensor.shape == (2, 3, 3)
    assert frame.tensor.dtype == np.uint8
    assert frame.tensor.flags.c_contiguous
    assert frame.timestamp_ns == 150_000_000
    assert frame.source_id == "video-1"
    assert frame.index == 3
    payload = json.loads(frame.metadata.model_dump_json())
    assert payload["timestamp_ns"] == 150_000_000
    assert payload["timestamp_quality"] == "pts"
    assert "tensor" not in payload
    assert "url" not in payload


@pytest.mark.parametrize(
    "shape,dtype",
    [
        ((2, 3), np.uint8),
        ((2, 3, 4), np.uint8),
        ((3, 2, 3), np.float32),
        ((0, 3, 3), np.uint8),
    ],
)
def test_frame_rejects_invalid_pixel_arrays(
    processing: Any,
    shape: tuple[int, ...],
    dtype: Any,
) -> None:
    with pytest.raises((ValueError, TypeError)):
        make_frame(processing, np.zeros(shape, dtype=dtype))


def test_frame_owns_its_pixels_and_metadata(processing: Any) -> None:
    pixels = np.full((2, 3, 3), 42, dtype=np.uint8)
    frame = make_frame(processing, pixels)
    pixels.fill(0)
    assert np.all(frame.tensor == 42)
    with pytest.raises((AttributeError, FrozenInstanceError, ValidationError)):
        frame.timestamp_ns = 0


def test_frame_normalizes_noncontiguous_storage(processing: Any) -> None:
    pixels = np.zeros((2, 6, 3), dtype=np.uint8)[:, ::2, :]
    assert not pixels.flags.c_contiguous
    assert make_frame(processing, pixels).tensor.flags.c_contiguous


@pytest.mark.parametrize(
    "field,value",
    [
        ("timestamp_ns", -1),
        ("requested_timestamp_ns", -1),
        ("index", -1),
        ("source_id", ""),
        ("timestamp_quality", "exact_because_requested"),
        ("device", "unknown_accelerator"),
    ],
)
def test_frame_metadata_rejects_invalid_lineage_and_timing(
    processing: Any,
    field: str,
    value: object,
) -> None:
    data = {
        "source_id": "video-1",
        "timestamp_ns": 0,
        "index": 0,
        "timestamp_quality": "pts",
        "decoder": "memory",
        "device": "cpu",
    }
    data[field] = value
    with pytest.raises(ValidationError):
        processing.FrameMetadata(**data)


def test_unknown_source_frame_index_remains_unknown(processing: Any) -> None:
    metadata = processing.FrameMetadata(
        source_id="video-1",
        timestamp_ns=0,
        index=None,
        timestamp_quality="backend",
        decoder="opencv",
        device="cpu",
    )
    assert metadata.index is None
    assert (
        processing.FrameMetadata.model_validate_json(metadata.model_dump_json())
        == metadata
    )


def test_encoded_frame_is_json_safe_without_a_native_encoder(processing: Any) -> None:
    frame = make_frame(processing)
    encoded = processing.EncodedFrame(
        metadata=frame.metadata,
        mime_type="image/png",
        data_base64="iVBORw0KGgo=",
    )
    assert encoded.mime_type == "image/png"
    assert encoded.metadata == frame.metadata
    assert (
        processing.EncodedFrame.model_validate_json(encoded.model_dump_json())
        == encoded
    )


def test_encoding_rejects_unsupported_format(processing: Any) -> None:
    with pytest.raises(ValueError):
        make_frame(processing).to_base64(format="gif")
