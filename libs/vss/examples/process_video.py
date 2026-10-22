# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Exercise probing, extraction, streaming, sampling, encoding, and clips."""

from __future__ import annotations

import argparse
import asyncio
from contextlib import aclosing
from pathlib import Path

from vss.video_processing import (
    DecodeOptions,
    DecodePlan,
    SamplingPolicy,
    decode_frames,
    extract_frames,
    materialize_clip,
    probe_video,
    subsample_frames,
)


async def main(
    video_path: Path,
    *,
    decoder: str,
    frame_count: int,
    test_clip: bool,
) -> None:
    options = DecodeOptions(decoder=decoder)
    facts = await probe_video(video_path, options=options)
    assert facts.width is not None and facts.width > 0
    assert facts.height is not None and facts.height > 0
    print(facts.model_dump_json(indent=2))

    timestamps_s = (
        [facts.duration_s * index / frame_count for index in range(frame_count)]
        if facts.duration_s is not None and facts.duration_s > 0
        else [0.0]
    )
    frames = await extract_frames(
        video_path,
        timestamps_s,
        source_id="example-video",
        options=options,
    )
    assert frames
    for frame in frames:
        assert frame.tensor.shape == (facts.height, facts.width, 3)
        assert not frame.tensor.flags.writeable
        print(frame.metadata.model_dump_json())
    jpeg = frames[0].to_encoded(format="jpeg")
    png = frames[0].to_encoded(format="png")
    assert jpeg.mime_type == "image/jpeg" and jpeg.data_base64
    assert png.mime_type == "image/png" and png.data_base64
    print(f"encoded jpeg={len(jpeg.data_base64)} png={len(png.data_base64)}")

    stream = decode_frames(
        video_path,
        DecodePlan(),
        source_id="example-video",
        options=options,
    )
    streamed = []
    async with aclosing(stream):
        async for frame in stream:
            streamed.append(frame)
            if len(streamed) == 10:
                break
    assert streamed
    assert all(
        earlier.timestamp_ns <= later.timestamp_ns
        for earlier, later in zip(streamed, streamed[1:], strict=False)
    )
    print(f"streamed {len(streamed)} ordered frames")

    if facts.duration_s is not None and facts.duration_s > 0:
        sampled = [
            frame
            async for frame in subsample_frames(
                video_path,
                SamplingPolicy(mode="count", count=4),
                source_id="example-video",
                options=options,
            )
        ]
        assert sampled
        print(f"sampled {len(sampled)} frames")

    if test_clip:
        if facts.duration_s is None:
            raise ValueError(
                "--test-clip requires video duration metadata, but none was available"
            )
        if facts.duration_s <= 0:
            raise ValueError("--test-clip requires a video with a positive duration")
        clip_end = min(facts.duration_s, 2.0)
        clip_end -= min(1e-6, clip_end / 2)
        artifact = await materialize_clip(
            video_path,
            0.0,
            clip_end,
            source_id="example-video",
        )
        try:
            assert artifact.path.is_file()
            assert artifact.duration_s > 0
            print(f"verified clip {artifact.path} ({artifact.duration_s:.3f}s)")
        finally:
            artifact.cleanup()
        assert not artifact.path.exists()
        print("clip cleanup verified")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", type=Path)
    parser.add_argument(
        "--decoder",
        choices=("auto", "opencv", "pynv"),
        default="auto",
        help="Decoder used for both probing and frame extraction.",
    )
    parser.add_argument(
        "--frames",
        type=int,
        default=3,
        help="Number of representative frames to extract.",
    )
    parser.add_argument(
        "--test-clip",
        action="store_true",
        help="Also materialize and clean up a clip; requires ffmpeg and ffprobe.",
    )
    args = parser.parse_args()
    if args.frames <= 0:
        parser.error("--frames must be greater than zero")
    asyncio.run(
        main(
            args.video,
            decoder=args.decoder,
            frame_count=args.frames,
            test_clip=args.test_clip,
        )
    )
