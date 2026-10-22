# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Local video facts, owned RGB frames, sampling, and verified clip copies.

The package imports NumPy, Pydantic, and the standard library only. Install
OpenCV 4.13+ for CPU decoding and image encoding, PyNvVideoCodec 2.2+ for NVIDIA
GPU decoding, and external FFmpeg/ffprobe for compressed clip materialization.
It supports local paths, local file URIs, and structural ``url``/``source_id``
objects; it does not download remote resources.

Example::

    import asyncio
    from contextlib import aclosing
    from pathlib import Path
    from vss.video_processing import DecodePlan, decode_frames

    async def first_frame(path: Path):
        stream = decode_frames(path, DecodePlan())
        async with aclosing(stream):
            return await anext(stream)

    frame = asyncio.run(first_frame(Path("video.mp4")))
    print(frame.timestamp_ns, frame.tensor.shape)  # source ns, (H, W, 3)

All plan/window/frame times are integer nanoseconds on a source-relative video
clock. ``extract_frames`` and ``materialize_clip`` accept seconds. Windows are
half-open. Returned frames own read-only contiguous uint8 RGB arrays; transfer
encoding is explicit and uses caller-installed OpenCV. Decoder sessions close
after iteration or cancellation, so use ``contextlib.aclosing`` when stopping
an async stream early. Sparse extraction returns a list proportional to the
number of requested timestamps.
"""

from .clips import ClipArtifact, materialize_clip
from .contracts import (
    DecodeOptions,
    DecodePlan,
    EncodedFrame,
    FrameMetadata,
    MediaFacts,
    SamplingPolicy,
    SourceInput,
    SourceRef,
    TimeWindow,
)
from .decoders.registry import register_decoder
from .exceptions import (
    ClipError,
    DecodeError,
    DecoderUnavailableError,
    ProcessingError,
    TimestampUnavailableError,
    UnsupportedSourceError,
)
from .execution import (
    decode_frames,
    extract_frames,
    extract_frames_sync,
    probe_video,
    probe_video_sync,
    subsample_frames,
)
from .frames import Frame

__all__ = [
    "ClipArtifact",
    "ClipError",
    "DecodeError",
    "DecodeOptions",
    "DecodePlan",
    "DecoderUnavailableError",
    "EncodedFrame",
    "Frame",
    "FrameMetadata",
    "MediaFacts",
    "ProcessingError",
    "SamplingPolicy",
    "SourceInput",
    "SourceRef",
    "TimeWindow",
    "TimestampUnavailableError",
    "UnsupportedSourceError",
    "decode_frames",
    "extract_frames",
    "extract_frames_sync",
    "materialize_clip",
    "probe_video",
    "probe_video_sync",
    "register_decoder",
    "subsample_frames",
]
