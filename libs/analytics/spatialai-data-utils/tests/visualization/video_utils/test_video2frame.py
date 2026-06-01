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

"""Coverage supplement for ``visualization.video_utils.video2frame``
— pins:

* ``video_to_frames`` STATUS_INVALID_PROPERTIES (bad metadata),
* ``video_to_frames`` STATUS_NO_FRAMES_EXTRACTED + STATUS_INCOMPLETE_EXTRACTION
  + STATUS_WRITE_ERROR (mid-extraction failure paths),
* ``video_to_frames`` empty-frame skip mid-stream,
* ``diagnose_video_file`` cannot-open + bad-property issue branches,
* ``expected_extraction_count`` zero-frame video early-return,
* ``_run_one_job`` happy path + exception wrap,
* ``video_to_frames_batch`` end-to-end with workers + summary log,
* ``_log_batch_summary`` per-status counts log line.
"""

import logging
import os

import cv2
import numpy as np
import pytest

from spatialai_data_utils.visualization.video_utils import video2frame
from spatialai_data_utils.visualization.video_utils.video2frame import (
    STATUS_CANNOT_OPEN,
    STATUS_COMPLETED,
    STATUS_INVALID_PROPERTIES,
    STATUS_NO_FRAMES_EXTRACTED,
    STATUS_WRITE_ERROR,
    _log_batch_summary,
    _run_one_job,
    diagnose_video_file,
    expected_extraction_count,
    video_to_frames,
    video_to_frames_batch,
)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _make_synthetic_video(path, *, n_frames=4, width=32, height=24, fps=10.0):
    writer = cv2.VideoWriter(
        path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height),
    )
    assert writer.isOpened(), f"Failed to open VideoWriter at {path}"
    for i in range(n_frames):
        frame = np.full((height, width, 3), (i * 30 % 256, 128, 200),
                          dtype=np.uint8)
        writer.write(frame)
    writer.release()


# ---------------------------------------------------------------------------
# video_to_frames — STATUS_INVALID_PROPERTIES + STATUS_WRITE_ERROR
# ---------------------------------------------------------------------------


def test_invalid_properties_when_video_metadata_is_garbage(tmp_path, monkeypatch):
    """A video that opens fine but reports nonsensical metadata
    (frame_count=0 or fps=0 etc.) returns STATUS_INVALID_PROPERTIES.

    Drive this by stubbing cv2.VideoCapture to report bad metadata."""
    class _BadCap:
        def __init__(self, *a, **kw):
            pass

        def isOpened(self):
            return True

        def get(self, prop):
            return 0  # all metadata zero -> invalid

        def release(self):
            pass

        def read(self):  # pragma: no cover - not reached
            return False, None

    # File must actually exist + be non-empty so the function reaches
    # cv2.VideoCapture; only metadata is bad.
    fake = tmp_path / "fake.mp4"
    fake.write_bytes(b"placeholder bytes")
    monkeypatch.setattr(cv2, "VideoCapture", _BadCap)
    out = video_to_frames(
        str(fake), str(tmp_path / "frames"),
    )
    assert out == STATUS_INVALID_PROPERTIES


def test_write_error_when_imwrite_returns_false(tmp_path, monkeypatch):
    """``cv2.imwrite`` returning False (e.g. disk full, bad path)
    triggers STATUS_WRITE_ERROR mid-extraction."""
    video_path = str(tmp_path / "in.mp4")
    _make_synthetic_video(video_path, n_frames=4)
    out_dir = tmp_path / "frames"
    out_dir.mkdir()

    monkeypatch.setattr(cv2, "imwrite", lambda path, frame: False)
    out = video_to_frames(video_path, str(out_dir))
    assert out == STATUS_WRITE_ERROR


# ---------------------------------------------------------------------------
# video_to_frames — empty-frame skip + NO_FRAMES_EXTRACTED
# ---------------------------------------------------------------------------


def test_no_frames_extracted_when_every_read_returns_empty_frame(
    tmp_path, monkeypatch,
):
    """If every ``cap.read()`` returns an empty frame, no file is
    written and the function returns STATUS_NO_FRAMES_EXTRACTED."""
    empty_frame = np.zeros((0, 0, 3), dtype=np.uint8)

    class _EmptyCap:
        def __init__(self, *a, **kw):
            self._calls = 0

        def isOpened(self):
            return True

        def get(self, prop):
            if prop == cv2.CAP_PROP_FRAME_COUNT:
                return 4
            if prop == cv2.CAP_PROP_FPS:
                return 10.0
            if prop in (cv2.CAP_PROP_FRAME_WIDTH, cv2.CAP_PROP_FRAME_HEIGHT):
                return 32
            return 0

        def read(self):
            self._calls += 1
            if self._calls > 4:
                return False, None
            return True, empty_frame  # frame.size == 0 -> skip branch

        def release(self):
            pass

    fake = tmp_path / "fake.mp4"
    fake.write_bytes(b"placeholder bytes")
    monkeypatch.setattr(cv2, "VideoCapture", _EmptyCap)
    out = video_to_frames(
        str(fake), str(tmp_path / "out"),
    )
    assert out == STATUS_NO_FRAMES_EXTRACTED


# ---------------------------------------------------------------------------
# diagnose_video_file — cannot-open + bad-property issue branches
# ---------------------------------------------------------------------------


def test_diagnose_reports_cannot_open_for_garbage_file(tmp_path):
    bad = tmp_path / "broken.mp4"
    bad.write_bytes(b"not a video header at all")
    diag = diagnose_video_file(str(bad))
    assert diag["can_open"] is False
    assert "Cannot open video file" in diag["issues"][0]


def test_diagnose_reports_invalid_metadata_issues(tmp_path, monkeypatch):
    """A successfully-opened cap that reports bad properties + bad
    first frame populates the ``issues`` list with multiple entries."""
    path = tmp_path / "v.mp4"
    path.write_bytes(b"placeholder")  # so file_exists + file_size > 0

    class _BadCap:
        def __init__(self, *a, **kw):
            pass

        def isOpened(self):
            return True

        def get(self, prop):
            return 0  # invalid frame count, fps, dimensions

        def read(self):
            return False, None

        def release(self):
            pass

    monkeypatch.setattr(cv2, "VideoCapture", _BadCap)
    diag = diagnose_video_file(str(path))
    assert diag["can_open"] is True
    issues = " | ".join(diag["issues"])
    assert "Invalid frame count" in issues
    assert "Invalid FPS" in issues
    assert "Invalid dimensions" in issues
    assert "Cannot read first frame" in issues


# ---------------------------------------------------------------------------
# expected_extraction_count — zero-frame video
# ---------------------------------------------------------------------------


def test_expected_extraction_count_returns_zero_for_zero_frames(
    tmp_path, monkeypatch,
):
    """A diagnosis with ``frame_count <= 0`` makes
    ``expected_extraction_count`` return 0 (no skip-detection)."""
    monkeypatch.setattr(video2frame, "diagnose_video_file",
                         lambda p: {
                             "can_open": True,
                             "properties": {"frame_count": 0},
                         })
    assert expected_extraction_count("any.mp4", str(tmp_path / "x")) == 0


def test_expected_extraction_count_handles_oserror_listing(tmp_path, monkeypatch):
    """If ``os.listdir`` raises OSError (e.g. permission denied),
    the helper coerces ``existing`` to 0 instead of propagating."""
    # Pretend a 10-frame video.
    monkeypatch.setattr(video2frame, "diagnose_video_file",
                         lambda p: {
                             "can_open": True,
                             "properties": {"frame_count": 10},
                         })
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    def _boom(_):
        raise OSError("synthetic permission denied")

    monkeypatch.setattr(os, "listdir", _boom)
    # Default pattern ".png" + non-empty (post-mock) listdir raises ->
    # branch sets existing=0 -> expected_count is returned as-is (10).
    out = expected_extraction_count(
        "any.mp4", str(out_dir), frame_pattern="{frame_id:09d}.png",
    )
    assert out == 10


# ---------------------------------------------------------------------------
# _run_one_job — happy + exception wrap
# ---------------------------------------------------------------------------


def test_run_one_job_happy_path(tmp_path):
    video = tmp_path / "in.mp4"
    _make_synthetic_video(str(video), n_frames=2)
    out_dir = tmp_path / "frames"
    v, o, status = _run_one_job((str(video), str(out_dir), {}))
    assert v == str(video)
    assert o == str(out_dir)
    assert status == STATUS_COMPLETED


def test_run_one_job_returns_exception_string_on_error(tmp_path, monkeypatch):
    """Any exception inside ``video_to_frames`` is caught + returned
    as a ``"exception: ..."`` status string."""
    def _boom(*a, **kw):
        raise RuntimeError("synthetic")

    monkeypatch.setattr(video2frame, "video_to_frames", _boom)
    _, _, status = _run_one_job(("v", "o", {}))
    assert status.startswith("exception:")
    assert "synthetic" in status


# ---------------------------------------------------------------------------
# video_to_frames_batch — end-to-end with workers + summary log
# ---------------------------------------------------------------------------


@pytest.mark.filterwarnings(
    "ignore:This process .* is multi-threaded:DeprecationWarning",
)
def test_video_to_frames_batch_runs_jobs_and_logs_summary(
    tmp_path, caplog,
):
    """End-to-end batch with two tiny videos exercises the
    ProcessPoolExecutor branch, per-job log line, and
    ``_log_batch_summary``."""
    job_specs = []
    for i in range(2):
        video = tmp_path / f"video_{i}.mp4"
        _make_synthetic_video(str(video), n_frames=2)
        out_dir = tmp_path / f"frames_{i}"
        job_specs.append((str(video), str(out_dir)))

    with caplog.at_level(logging.INFO):
        results = video_to_frames_batch(
            job_specs, max_workers=1,  # single worker -> deterministic ordering
        )
    assert len(results) == 2
    assert all(s == STATUS_COMPLETED for _, _, s in results)
    assert "video_to_frames_batch summary" in caplog.text


def test_video_to_frames_batch_no_jobs_logs_and_returns_empty(caplog):
    with caplog.at_level(logging.INFO):
        out = video_to_frames_batch([])
    assert out == []
    assert "no jobs to run" in caplog.text


# ---------------------------------------------------------------------------
# _log_batch_summary — empty list early-return + per-status counts
# ---------------------------------------------------------------------------


def test_log_batch_summary_empty_results_returns_early(caplog):
    log = logging.getLogger("test_log_batch_summary_empty")
    with caplog.at_level(logging.INFO):
        _log_batch_summary(log, [])
    # No "summary" line emitted.
    assert "video_to_frames_batch summary" not in caplog.text


def test_log_batch_summary_counts_per_status(caplog):
    log = logging.getLogger("test_log_batch_summary_counts")
    results = [
        ("v0", "o0", STATUS_COMPLETED),
        ("v1", "o1", STATUS_COMPLETED),
        ("v2", "o2", STATUS_CANNOT_OPEN),
    ]
    with caplog.at_level(logging.INFO):
        _log_batch_summary(log, results)
    assert "video_to_frames_batch summary (3 jobs)" in caplog.text
    # COMPLETED appears with count 2; CANNOT_OPEN with count 1.
    assert "2 " + STATUS_COMPLETED in caplog.text
    assert "1 " + STATUS_CANNOT_OPEN in caplog.text
