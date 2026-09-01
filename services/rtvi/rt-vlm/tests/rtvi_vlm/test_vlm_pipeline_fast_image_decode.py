# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import torch
from PIL import Image

from common.chunk_info import ChunkInfo
from vlm_pipeline.vlm_pipeline import (
    FAST_IMAGE_ASSET_CHUNK_DECODE_ENV,
    _split_local_image_asset_paths,
    _try_decode_image_asset_chunk,
)


def _write_image(path, color):
    Image.new("RGB", (8, 6), color=color).save(path)


def test_split_local_image_asset_paths_accepts_semicolon_image_list(tmp_path):
    first = tmp_path / "first.jpg"
    second = tmp_path / "second.png"
    _write_image(first, (255, 0, 0))
    _write_image(second, (0, 255, 0))

    assert _split_local_image_asset_paths(f"{first};{second}") == [
        str(first),
        str(second),
    ]


def test_split_local_image_asset_paths_rejects_non_local_or_non_image(tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"not a real video")

    assert _split_local_image_asset_paths("rtsp://example.com/live") is None
    assert _split_local_image_asset_paths(str(video)) is None
    assert _split_local_image_asset_paths(str(tmp_path / "missing.jpg")) is None


def test_try_decode_image_asset_chunk_is_opt_in(tmp_path, monkeypatch):
    image_path = tmp_path / "frame.jpg"
    _write_image(image_path, (255, 0, 0))
    monkeypatch.delenv(FAST_IMAGE_ASSET_CHUNK_DECODE_ENV, raising=False)

    chunk = ChunkInfo(file=str(image_path))

    assert _try_decode_image_asset_chunk(chunk, frame_width=4, frame_height=2) is None


def test_try_decode_image_asset_chunk_loads_hwc_uint8_tensor(tmp_path, monkeypatch):
    first = tmp_path / "first.jpg"
    second = tmp_path / "second.jpg"
    _write_image(first, (255, 0, 0))
    _write_image(second, (0, 255, 0))
    monkeypatch.setenv(FAST_IMAGE_ASSET_CHUNK_DECODE_ENV, "true")

    chunk = ChunkInfo(file=f"{first};{second}")

    result = _try_decode_image_asset_chunk(chunk, frame_width=4, frame_height=2)

    assert result is not None
    frames, frame_times, audio_frames, error = result
    assert isinstance(frames, torch.Tensor)
    assert frames.shape == (2, 2, 4, 3)
    assert frames.dtype == torch.uint8
    assert frame_times == [0.0, 0.0]
    assert audio_frames == []
    assert error is None


def test_try_decode_image_asset_chunk_skips_audio_and_jpeg_tensor_backends(tmp_path, monkeypatch):
    image_path = tmp_path / "frame.jpg"
    _write_image(image_path, (255, 0, 0))
    monkeypatch.setenv(FAST_IMAGE_ASSET_CHUNK_DECODE_ENV, "true")

    chunk = ChunkInfo(file=str(image_path))

    assert _try_decode_image_asset_chunk(chunk, enable_audio=True) is None
    assert _try_decode_image_asset_chunk(chunk, enable_jpeg_tensors=True) is None
