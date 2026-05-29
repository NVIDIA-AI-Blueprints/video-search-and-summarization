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

"""Tests for the error / branch paths in
``visualization.video_utils.frame2video`` that the existing
``test_video_utils.py`` doesn't exercise.

Covers:

* ``frames_to_video`` — start_frame past end → ``STATUS_NO_FRAMES_FOUND``;
  first frame unreadable → ``STATUS_READ_ERROR``; mid-loop unreadable
  frame → writer release + ``STATUS_READ_ERROR``; writer fails to open
  → ``STATUS_WRITE_ERROR``.
* ``_run_one_job`` — happy path and exception-caught path; ``progress``
  is force-set to ``False`` to avoid bar conflicts under multi-process.
* ``frames_to_video_batch`` — empty input, 2-tuple jobs, 3-tuple jobs
  with per-job overrides, invalid tuple-length raise, summary logging.
* ``_log_batch_summary`` — empty short-circuit; non-empty produces the
  per-status breakdown.
"""

import logging
import os
from unittest.mock import patch

import cv2
import numpy as np

import pytest

from spatialai_data_utils.visualization.video_utils.frame2video import (
    STATUS_NO_FRAMES_FOUND,
    STATUS_READ_ERROR,
    STATUS_WRITE_ERROR,
    _log_batch_summary,
    _run_one_job,
    frames_to_video,
    frames_to_video_batch,
)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _populate_frames(frame_dir, n_frames=4, h=48, w=64):
    os.makedirs(frame_dir, exist_ok=True)
    for i in range(n_frames):
        img = np.full((h, w, 3), int(255 * i / max(1, n_frames)), dtype=np.uint8)
        cv2.imwrite(os.path.join(frame_dir, f"{i:09d}.jpg"), img)


# ---------------------------------------------------------------------------
# frames_to_video — error/branch paths
# ---------------------------------------------------------------------------


class TestFramesToVideoBranches:
    def test_start_frame_past_end_returns_no_frames_found(self, tmp_path):
        frame_dir = str(tmp_path / "frames")
        _populate_frames(frame_dir, n_frames=4)
        out = str(tmp_path / "out.mp4")
        status = frames_to_video(
            frame_dir, out, fps=5.0, start_frame=10, progress=False,
        )
        assert status == STATUS_NO_FRAMES_FOUND

    def test_first_frame_unreadable_returns_read_error(self, tmp_path):
        """``cv2.imread`` returns ``None`` for unreadable files —
        e.g. a JPG file that contains non-image bytes."""
        frame_dir = str(tmp_path / "frames")
        os.makedirs(frame_dir, exist_ok=True)
        # Looks like a JPG by extension but is gibberish bytes.
        with open(os.path.join(frame_dir, "000000001.jpg"), "wb") as f:
            f.write(b"not an image")
        out = str(tmp_path / "out.mp4")
        status = frames_to_video(frame_dir, out, fps=5.0, progress=False)
        assert status == STATUS_READ_ERROR

    def test_midloop_unreadable_frame_returns_read_error(self, tmp_path):
        """When the FIRST frame is valid but a later frame is
        corrupt, ``frames_to_video`` must release the writer and
        return ``STATUS_READ_ERROR`` (not raise)."""
        frame_dir = str(tmp_path / "frames")
        _populate_frames(frame_dir, n_frames=2)  # two good frames
        # Append one corrupt frame whose name sorts last.
        with open(os.path.join(frame_dir, "999999999.jpg"), "wb") as f:
            f.write(b"not an image")
        out = str(tmp_path / "out.mp4")
        status = frames_to_video(frame_dir, out, fps=5.0, progress=False)
        assert status == STATUS_READ_ERROR

    def test_writer_fails_to_open_returns_write_error(self, tmp_path):
        """When ``VideoWriter`` reports ``isOpened() is False`` (e.g.
        codec rejected by the backend), the helper must return
        ``STATUS_WRITE_ERROR`` cleanly."""
        frame_dir = str(tmp_path / "frames")
        _populate_frames(frame_dir, n_frames=2)
        out = str(tmp_path / "out.mp4")

        # Patch VideoWriter to always report not-opened; preserves the
        # public surface for the rest of the function.
        class _ClosedWriter:
            def isOpened(self):
                return False

            def write(self, frame):  # pragma: no cover - never called
                pass

            def release(self):
                pass

        with patch.object(cv2, "VideoWriter", return_value=_ClosedWriter()):
            status = frames_to_video(frame_dir, out, fps=5.0, progress=False)
        assert status == STATUS_WRITE_ERROR


# ---------------------------------------------------------------------------
# _run_one_job
# ---------------------------------------------------------------------------


class TestRunOneJob:
    def test_happy_path_returns_status_completed(self, tmp_path):
        frame_dir = str(tmp_path / "frames")
        _populate_frames(frame_dir, n_frames=3)
        out_path = str(tmp_path / "out.mp4")
        frame_dir_out, output_path_out, status = _run_one_job(
            (frame_dir, out_path, {"fps": 5.0}),
        )
        assert frame_dir_out == frame_dir
        assert output_path_out == out_path
        assert status == "completed"

    def test_exception_in_worker_is_captured_as_string(self, tmp_path):
        """If ``frames_to_video`` raises inside the worker, the
        wrapper must capture it as an ``exception: ...`` status rather
        than letting it propagate."""

        def _boom(*args, **kwargs):
            raise RuntimeError("worker exploded")

        with patch(
            "spatialai_data_utils.visualization.video_utils.frame2video.frames_to_video",
            side_effect=_boom,
        ):
            frame_dir, out_path, status = _run_one_job(
                ("/does/not/matter", "/out.mp4", {}),
            )
        assert status.startswith("exception:")
        assert "worker exploded" in status

    def test_progress_kwarg_is_forced_to_false_in_workers(self, tmp_path):
        """Worker wrappers override ``progress`` to ``False`` so multi-
        process tqdm bars don't clash on the same terminal."""
        captured_kwargs = {}

        def _capture(*args, **kwargs):
            captured_kwargs.update(kwargs)
            return "completed"

        with patch(
            "spatialai_data_utils.visualization.video_utils.frame2video.frames_to_video",
            side_effect=_capture,
        ):
            _run_one_job(("/dir", "/out.mp4", {"progress": True}))
        assert captured_kwargs.get("progress") is True  # per-job overrides survive

        captured_kwargs.clear()
        with patch(
            "spatialai_data_utils.visualization.video_utils.frame2video.frames_to_video",
            side_effect=_capture,
        ):
            _run_one_job(("/dir", "/out.mp4", {}))
        # No explicit progress in the per-job kwargs -> wrapper sets False.
        assert captured_kwargs.get("progress") is False


# ---------------------------------------------------------------------------
# frames_to_video_batch
# ---------------------------------------------------------------------------


@pytest.mark.filterwarnings(
    # ProcessPoolExecutor.fork() warns under Python 3.13 when the parent
    # process is multi-threaded (numpy / torch / pytorch3d / etc. have
    # all spawned worker threads by the time pytest gets here). The
    # warning isn't actionable from this test — it's a property of the
    # production code path — so filter just here to keep the suite
    # warning-free without globally muting DeprecationWarning.
    "ignore:This process .* is multi-threaded, use of fork.*:DeprecationWarning"
)
class TestFramesToVideoBatch:
    def test_empty_jobs_returns_empty_list_and_logs_no_jobs(self, caplog):
        with caplog.at_level(logging.INFO):
            out = frames_to_video_batch([])
        assert out == []
        assert "no jobs to run" in caplog.text

    def test_invalid_tuple_length_raises(self, tmp_path):
        with pytest.raises(ValueError, match="2- or 3-tuple"):
            frames_to_video_batch([
                ("frames", "out.mp4", {"fps": 5.0}, "extra"),  # 4-tuple
            ])

    def test_two_tuple_jobs_use_shared_kwargs(self, tmp_path):
        """``(frame_dir, output_path)`` jobs use the shared kwargs."""
        d1 = str(tmp_path / "f1")
        d2 = str(tmp_path / "f2")
        _populate_frames(d1, n_frames=2)
        _populate_frames(d2, n_frames=2)
        results = frames_to_video_batch(
            [(d1, str(tmp_path / "o1.mp4")),
             (d2, str(tmp_path / "o2.mp4"))],
            max_workers=1, fps=5.0,
        )
        statuses = [r[2] for r in results]
        assert sorted(statuses) == ["completed", "completed"]

    def test_three_tuple_jobs_overrides_shared_kwargs(self, tmp_path):
        """The 3-tuple form lets a job override the shared kwargs —
        e.g. inject a per-camera label."""
        d1 = str(tmp_path / "f1")
        _populate_frames(d1, n_frames=2)
        results = frames_to_video_batch(
            [(d1, str(tmp_path / "o1.mp4"), {"label": "cam-1"})],
            max_workers=1, fps=5.0,
        )
        assert results[0][2] == "completed"

    def test_summary_log_includes_per_status_counts(self, tmp_path, caplog):
        """End-of-batch summary lists per-status counts."""
        d1 = str(tmp_path / "f1")
        _populate_frames(d1, n_frames=2)
        # Second job points at an empty dir -> no_frames_found.
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        with caplog.at_level(logging.INFO):
            frames_to_video_batch(
                [(d1, str(tmp_path / "o1.mp4")),
                 (str(empty_dir), str(tmp_path / "o2.mp4"))],
                max_workers=1, fps=5.0,
            )
        # The summary line was emitted with both status counts.
        assert "summary" in caplog.text
        assert "1 completed" in caplog.text
        assert "1 no_frames_found" in caplog.text


# ---------------------------------------------------------------------------
# _log_batch_summary
# ---------------------------------------------------------------------------


def test_log_batch_summary_short_circuits_on_empty():
    """``_log_batch_summary`` should return without logging when the
    results list is empty."""
    log = logging.getLogger("test_batch_summary_empty")
    # Sanity: capture would-be log records via a handler.
    records = []

    class _Catcher(logging.Handler):
        def emit(self, record):
            records.append(record)

    log.addHandler(_Catcher())
    log.setLevel(logging.DEBUG)
    _log_batch_summary(log, [])
    assert records == []


def test_log_batch_summary_emits_count_breakdown_sorted_descending(caplog):
    """Statuses with higher counts come first in the log line."""
    log = logging.getLogger("test_batch_summary_breakdown")
    log.setLevel(logging.INFO)
    log.propagate = True
    with caplog.at_level(logging.INFO):
        _log_batch_summary(log, [
            ("d1", "o1", "completed"),
            ("d2", "o2", "completed"),
            ("d3", "o3", "completed"),
            ("d4", "o4", "skipped"),
        ])
    # "3 completed" comes before "1 skipped" (sort by -count).
    assert caplog.text.find("3 completed") < caplog.text.find("1 skipped")
