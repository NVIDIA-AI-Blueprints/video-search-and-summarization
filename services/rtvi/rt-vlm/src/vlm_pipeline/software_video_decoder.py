# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""CPU decoding for file captions sent to a remote JPEG-input model."""

import io


def decode_file_jpegs(chunk, frame_selector, frame_width=0, frame_height=0):
    """Decode selected frames from one bounded file chunk without CUDA.

    Reuse the normal frame selector and return its JPEG-array/PTS contract.
    Only the selected frames are retained; each codec uses one CPU thread.
    Audio and local model preprocessing remain the GPU pipeline's responsibility.
    """
    import av
    import numpy as np

    start_ns = chunk.start_pts - chunk.pts_offset_ns
    end_ns = chunk.end_pts - chunk.pts_offset_ns
    if start_ns < 0 or end_ns <= start_ns:
        raise ValueError("Software decode requires a bounded positive chunk window")

    frame_selector.set_chunk(chunk)
    if frame_selector.selects_all_frames:
        raise ValueError("Software recovery does not support unbounded frame selection")

    frames = []
    frame_times = []
    with av.open(chunk.file) as container:
        if not container.streams.video:
            raise ValueError("File has no video stream")
        stream = container.streams.video[0]
        stream.codec_context.thread_count = 1
        container.seek(int(start_ns / 1e9 / stream.time_base), stream=stream, backward=True)
        for frame in container.decode(stream):
            if frame.pts is None:
                continue
            pts_ns = round(frame.pts * frame.time_base * 1_000_000_000)
            if pts_ns > end_ns:
                break
            if pts_ns < start_ns or not frame_selector.choose_frame(None, pts_ns):
                continue
            image = frame.to_image()
            if frame_width and frame_height:
                image = image.resize((frame_width, frame_height))
            encoded = io.BytesIO()
            image.save(encoded, format="JPEG", quality=95)
            frames.append(np.frombuffer(encoded.getvalue(), dtype=np.uint8))
            frame_times.append((pts_ns + chunk.pts_offset_ns) / 1e9)
            if frame_selector.selection_done:
                break
    if not frames:
        raise ValueError("Software decode produced no selected frames")
    return frames, frame_times
