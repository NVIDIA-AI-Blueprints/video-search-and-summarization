# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import io
from types import SimpleNamespace

import av
import numpy as np
import pytest
from PIL import Image

from vlm_pipeline.software_video_decoder import decode_file_jpegs


class Selector:
    selects_all_frames = False

    def set_chunk(self, chunk):
        self.targets = [chunk.start_pts - chunk.pts_offset_ns]

    def choose_frame(self, _buffer, pts):
        if self.targets and pts >= self.targets[0]:
            self.targets.pop(0)
            return True
        return False

    @property
    def selection_done(self):
        return not self.targets


@pytest.fixture
def video(tmp_path):
    path = tmp_path / "clip.mp4"
    with av.open(str(path), "w") as output:
        stream = output.add_stream("mpeg4", rate=10)
        stream.width = 16
        stream.height = 16
        stream.pix_fmt = "yuv420p"
        for i in range(20):
            rgb = np.zeros((16, 16, 3), dtype=np.uint8)
            rgb[:, :, 0 if i < 10 else 2] = 255
            for packet in stream.encode(av.VideoFrame.from_ndarray(rgb, format="rgb24")):
                output.mux(packet)
        for packet in stream.encode():
            output.mux(packet)
    return path


def test_software_decode_respects_chunk_window_and_timestamp_offset(video):
    chunk = SimpleNamespace(
        file=str(video),
        start_pts=6_000_000_000,
        end_pts=7_000_000_000,
        pts_offset_ns=5_000_000_000,
    )
    frames, times = decode_file_jpegs(chunk, Selector(), frame_width=8, frame_height=6)
    assert len(frames) == 1
    assert times == [6.0]
    image = Image.open(io.BytesIO(frames[0].tobytes()))
    assert image.size == (8, 6)
    assert np.asarray(image)[0, 0, 2] > 240  # second second's blue frames
    assert np.asarray(image)[0, 0, 0] < 10


@pytest.mark.parametrize("start,end", [(0, -1), (-1, 10), (10, 10)])
def test_software_decode_rejects_invalid_windows(video, start, end):
    chunk = SimpleNamespace(file=str(video), start_pts=start, end_pts=end, pts_offset_ns=0)
    with pytest.raises(ValueError, match="bounded positive"):
        decode_file_jpegs(chunk, Selector())


def test_software_decode_reports_corrupt_media(tmp_path):
    path = tmp_path / "invalid.mp4"
    path.write_bytes(b"invalid video")
    chunk = SimpleNamespace(file=str(path), start_pts=0, end_pts=1_000_000_000, pts_offset_ns=0)
    with pytest.raises(av.error.InvalidDataError):
        decode_file_jpegs(chunk, Selector())


def test_software_decode_does_not_invent_frames_beyond_recording(video):
    chunk = SimpleNamespace(
        file=str(video), start_pts=3_000_000_000, end_pts=4_000_000_000, pts_offset_ns=0
    )
    with pytest.raises(ValueError, match="no selected frames"):
        decode_file_jpegs(chunk, Selector())


def test_software_decode_uses_default_selector_for_multiple_frames(video):
    from common.chunk_info import ChunkInfo

    from vlm_pipeline.video_file_frame_getter import DefaultFrameSelector

    chunk = ChunkInfo(file=str(video), start_pts=0, end_pts=2_000_000_000)
    frames, times = decode_file_jpegs(chunk, DefaultFrameSelector(4))
    assert len(frames) == 4
    assert times == [0.0, 0.5, 1.0, 1.5]
    colors = [np.asarray(Image.open(io.BytesIO(frame.tobytes())))[0, 0] for frame in frames]
    assert all(color[0] > 240 for color in colors[:2])
    assert all(color[2] > 240 for color in colors[2:])


def test_software_decode_rejects_all_frames_selection(video):
    from common.chunk_info import ChunkInfo

    from vlm_pipeline.video_file_frame_getter import DefaultFrameSelector

    chunk = ChunkInfo(file=str(video), start_pts=0, end_pts=2_000_000_000)
    with pytest.raises(ValueError, match="unbounded frame selection"):
        decode_file_jpegs(chunk, DefaultFrameSelector(-1))
