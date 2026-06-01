# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""Coverage supplement for
``visualization.video_utils.frame2video_grid`` — pins:

* ``_prepare_tile`` missing-path + ``cv2.imread`` None fallback (black tile),
* ``frames_to_video_grid`` empty after start_frame slice,
* ``frames_to_video_grid`` first-frame ``cv2.imread`` None (STATUS_READ_ERROR),
* ``frames_to_video_grid`` ``cv2.VideoWriter`` not opened (STATUS_WRITE_ERROR),
* ``frames_to_video_grid`` per-camera naming-convention mismatch warning,
* ``frames_to_video_grid`` ``label`` overlay branch.
"""

import logging
import os

import cv2
import numpy as np

from spatialai_data_utils.visualization.video_utils.frame2video_grid import (
    STATUS_NO_FRAMES_FOUND,
    STATUS_READ_ERROR,
    STATUS_WRITE_ERROR,
    _prepare_tile,
    frames_to_video_grid,
)


def _write_frame(path, *, shape=(60, 80, 3), value=128):
    img = np.full(shape, value, dtype=np.uint8)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    cv2.imwrite(path, img)


# ---------------------------------------------------------------------------
# _prepare_tile — missing path + bad imread fall back to black
# ---------------------------------------------------------------------------


def test_prepare_tile_missing_path_returns_black_tile():
    out = _prepare_tile(
        "/no/such/file.png", label="cam-0",
        tile_h=40, tile_w=50, with_label=False,
    )
    assert out.shape == (40, 50, 3)
    assert out.dtype == np.uint8
    # All-zero (black) tile.
    assert (out == 0).all()


def test_prepare_tile_unreadable_file_returns_black_tile(tmp_path):
    """A file that exists but isn't a valid image -> ``cv2.imread``
    returns None -> fallback to black tile."""
    bad = tmp_path / "not_image.png"
    bad.write_text("definitely not a png")
    out = _prepare_tile(
        str(bad), label="cam-x",
        tile_h=30, tile_w=40, with_label=False,
    )
    assert out.shape == (30, 40, 3)
    assert (out == 0).all()


# ---------------------------------------------------------------------------
# frames_to_video_grid — error paths + warning + label overlay
# ---------------------------------------------------------------------------


def test_returns_no_frames_when_master_dir_is_empty(tmp_path):
    """Empty master frame dir -> STATUS_NO_FRAMES_FOUND."""
    master = tmp_path / "cam0"
    master.mkdir()
    out = frames_to_video_grid(
        frame_dirs=[str(master)],
        output_path=str(tmp_path / "out.mp4"),
        fps=30, progress=False,
    )
    assert out == STATUS_NO_FRAMES_FOUND


def test_returns_no_frames_when_start_frame_skips_everything(tmp_path):
    """If ``start_frame`` slices past the available frames the
    sliced master list is empty -> STATUS_NO_FRAMES_FOUND (the
    post-slice second guard)."""
    master = tmp_path / "cam0"
    _write_frame(str(master / "f0000.png"))
    _write_frame(str(master / "f0001.png"))
    out = frames_to_video_grid(
        frame_dirs=[str(master)],
        output_path=str(tmp_path / "out.mp4"),
        fps=30, progress=False, start_frame=100,
    )
    assert out == STATUS_NO_FRAMES_FOUND


def test_returns_read_error_when_first_frame_unreadable(tmp_path):
    """First master frame is an invalid image file -> STATUS_READ_ERROR
    (the ``if first_frame is None`` guard)."""
    master = tmp_path / "cam0"
    master.mkdir()
    (master / "bad.png").write_text("not a png")
    out = frames_to_video_grid(
        frame_dirs=[str(master)],
        output_path=str(tmp_path / "out.mp4"),
        fps=30, progress=False, glob_patterns=("*.png",),
    )
    assert out == STATUS_READ_ERROR


def test_returns_write_error_when_video_writer_fails(tmp_path, monkeypatch):
    """If ``cv2.VideoWriter.isOpened()`` returns False (unwritable
    path, unknown codec, etc.) the function returns STATUS_WRITE_ERROR."""
    master = tmp_path / "cam0"
    _write_frame(str(master / "f0000.png"))

    class _StubWriter:
        def __init__(self, *args, **kwargs):
            pass

        def isOpened(self):
            return False

        def write(self, frame):  # pragma: no cover - not reached
            pass

        def release(self):
            pass

    monkeypatch.setattr(cv2, "VideoWriter", _StubWriter)
    out = frames_to_video_grid(
        frame_dirs=[str(master)],
        output_path=str(tmp_path / "out.mp4"),
        fps=30, progress=False,
    )
    assert out == STATUS_WRITE_ERROR


def test_warns_when_non_master_camera_lacks_master_first_frame(
    tmp_path, caplog,
):
    """When a non-master cam dir doesn't contain the master's first
    frame by basename, the pre-flight warning fires (and that cam's
    tiles silently fall back to black during encoding)."""
    master = tmp_path / "cam0"
    other = tmp_path / "cam1"
    _write_frame(str(master / "f0000.png"))
    _write_frame(str(master / "f0001.png"))
    # cam1 uses a totally different naming convention.
    _write_frame(str(other / "image_00.png"))
    _write_frame(str(other / "image_01.png"))

    with caplog.at_level(logging.WARNING):
        frames_to_video_grid(
            frame_dirs=[str(master), str(other)],
            output_path=str(tmp_path / "out.mp4"),
            fps=30, progress=False, max_workers=1,
        )
    assert "no file named" in caplog.text


def test_label_overlay_branch_is_exercised(tmp_path):
    """Passing a non-None ``label`` triggers the per-frame
    ``plot_frame_label`` overlay branch."""
    master = tmp_path / "cam0"
    _write_frame(str(master / "f0000.png"))
    _write_frame(str(master / "f0001.png"))
    out_path = tmp_path / "out.mp4"
    # max_workers=1 forces the in-process sequential branch (the
    # ThreadPool path is also covered when omitted).
    rc = frames_to_video_grid(
        frame_dirs=[str(master)],
        output_path=str(out_path),
        fps=30, progress=False, max_workers=1, label="scene-X",
    )
    # On success the function returns the 'completed' sentinel and
    # the encoded video lands at out_path.
    assert rc == "completed"
    assert out_path.is_file()


def test_defensive_grid_resize_runs_when_compose_drifts(tmp_path, monkeypatch):
    """Line 393: a defensive ``cv2.resize`` runs if ``_compose_grid``
    ever produces a grid whose shape doesn't match ``(out_h, out_w)``.
    Force that drift by stubbing ``_compose_grid`` to return a
    differently-shaped array."""
    from spatialai_data_utils.visualization.video_utils import frame2video_grid as mod

    master = tmp_path / "cam0"
    _write_frame(str(master / "f0000.png"), shape=(60, 80, 3))
    _write_frame(str(master / "f0001.png"), shape=(60, 80, 3))

    def _wrong_shape_compose(tiles, n_cols, tile_h, tile_w):
        # Deliberately wrong shape — the defensive resize will fix it.
        return np.zeros((tile_h + 7, tile_w + 5, 3), dtype=np.uint8)

    monkeypatch.setattr(mod, "_compose_grid", _wrong_shape_compose)
    out_path = tmp_path / "out_resize.mp4"
    rc = frames_to_video_grid(
        frame_dirs=[str(master)],
        output_path=str(out_path),
        fps=30, progress=False, max_workers=1,
    )
    assert rc == "completed"
    assert out_path.is_file()
