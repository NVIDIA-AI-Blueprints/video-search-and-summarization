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

"""Tests for ``spatialai_data_utils.visualization.video_utils``.

Builds tiny synthetic videos on the fly with ``cv2.VideoWriter`` so
the test suite stays self-contained — no fixture videos shipped.
Round-trip tests (``video → frames → video``) verify the two
helpers compose cleanly.
"""

import os

import cv2
import numpy as np
import pytest

from spatialai_data_utils.visualization.video_utils.frame2video import (
    DEFAULT_GLOB_PATTERNS,
    STATUS_NO_FRAMES_FOUND,
    frames_to_video,
)
from spatialai_data_utils.visualization.video_utils.video2frame import (
    DEFAULT_FRAME_PATTERN,
    STATUS_CANNOT_OPEN,
    STATUS_COMPLETED,
    STATUS_EMPTY_FILE,
    STATUS_FILE_NOT_FOUND,
    STATUS_SKIPPED,
    diagnose_video_file,
    video_to_frames,
)


def _make_synthetic_video(
    path: str, *, n_frames: int = 12, width: int = 64, height: int = 48,
    fps: float = 10.0,
) -> None:
    """Write an ``n_frames``-long synthetic video at *path*.

    Each frame is a unique solid colour (cycling through hue) so the
    test can assert frame-by-frame distinctness in the round-trip
    direction without needing a reference image set on disk.
    """
    writer = cv2.VideoWriter(
        path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height),
    )
    assert writer.isOpened(), f"Failed to open VideoWriter at {path}"
    for i in range(n_frames):
        # Distinct colour per frame: cycle through hue space at full
        # saturation/value, convert to BGR.
        hue = int(180 * i / max(1, n_frames))
        hsv = np.full((height, width, 3), (hue, 255, 255), dtype=np.uint8)
        bgr = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)
        writer.write(bgr)
    writer.release()


# =====================================================================
# Tests for video_to_frames (single-video extraction)
# =====================================================================

class TestVideoToFrames:
    """Cover the single-video → frames helper."""

    def test_extract_all_frames(self, tmp_path):
        """Default settings extract every frame at the configured pattern."""
        video_path = str(tmp_path / "in.mp4")
        out_dir = str(tmp_path / "frames")
        _make_synthetic_video(video_path, n_frames=10)

        status = video_to_frames(video_path, out_dir)

        assert status == STATUS_COMPLETED
        files = set(os.listdir(out_dir))
        # Default pattern is "{frame_id}.png" — no zero pad, PNG ext.
        # Don't sort lexicographically here ("10.png" would land
        # before "2.png"); use a set instead since we only care about
        # presence, not order (smart sort is verified separately).
        assert len(files) == 10
        assert files == {f"{i}.png" for i in range(10)}

    def test_frame_skip(self, tmp_path):
        """``frame_skip=N`` keeps every Nth source frame, numbered consecutively."""
        video_path = str(tmp_path / "in.mp4")
        out_dir = str(tmp_path / "frames_skip")
        _make_synthetic_video(video_path, n_frames=10)

        status = video_to_frames(video_path, out_dir, frame_skip=3)

        assert status == STATUS_COMPLETED
        files = set(os.listdir(out_dir))
        # 10 source frames at stride 3 starting at 0 → indices 0, 3, 6, 9
        # → 4 output frames numbered 0..3 (consecutive over kept frames).
        assert files == {"0.png", "1.png", "2.png", "3.png"}

    def test_start_and_end_frame(self, tmp_path):
        """``start_frame`` / ``end_frame`` trim to a sub-range of the source."""
        video_path = str(tmp_path / "in.mp4")
        out_dir = str(tmp_path / "frames_trim")
        _make_synthetic_video(video_path, n_frames=10)

        status = video_to_frames(
            video_path, out_dir, start_frame=2, end_frame=7,
        )

        assert status == STATUS_COMPLETED
        files = sorted(os.listdir(out_dir))
        # [2, 7) → 5 frames kept, output indices 0..4.
        assert len(files) == 5

    def test_custom_frame_pattern_png(self, tmp_path):
        """Pattern with PNG extension produces PNG outputs."""
        video_path = str(tmp_path / "in.mp4")
        out_dir = str(tmp_path / "frames_png")
        _make_synthetic_video(video_path, n_frames=4)

        status = video_to_frames(
            video_path, out_dir, frame_pattern="frame_{:04d}.png",
        )

        assert status == STATUS_COMPLETED
        files = sorted(os.listdir(out_dir))
        assert files == ["frame_0000.png", "frame_0001.png",
                         "frame_0002.png", "frame_0003.png"]

    def test_skipped_when_already_complete(self, tmp_path):
        """Re-running on a populated dir without ``overwrite`` returns 'skipped'."""
        video_path = str(tmp_path / "in.mp4")
        out_dir = str(tmp_path / "frames_skip")
        _make_synthetic_video(video_path, n_frames=8)
        first = video_to_frames(video_path, out_dir)
        assert first == STATUS_COMPLETED

        second = video_to_frames(video_path, out_dir)

        assert second == STATUS_SKIPPED

    def test_overwrite_re_runs_extraction(self, tmp_path):
        """``overwrite=True`` forces re-extraction even when output looks complete."""
        video_path = str(tmp_path / "in.mp4")
        out_dir = str(tmp_path / "frames_overwrite")
        _make_synthetic_video(video_path, n_frames=4)
        video_to_frames(video_path, out_dir)
        # Replace the contents with a sentinel so we can detect the re-extract.
        for f in os.listdir(out_dir):
            os.remove(os.path.join(out_dir, f))
        with open(os.path.join(out_dir, "sentinel"), "w") as fp:
            fp.write("touched")

        # Without overwrite, the sentinel-only directory has zero
        # matching .png files so the helper should NOT skip — but
        # this is implementation detail.  The point of this test is
        # the explicit ``overwrite=True`` path.
        status = video_to_frames(video_path, out_dir, overwrite=True)

        assert status == STATUS_COMPLETED
        assert "0.png" in os.listdir(out_dir)

    def test_missing_file_returns_status(self, tmp_path):
        assert video_to_frames(
            str(tmp_path / "nope.mp4"), str(tmp_path / "out"),
        ) == STATUS_FILE_NOT_FOUND

    def test_frame_skip_zero_does_not_crash(self, tmp_path):
        """``frame_skip=0`` is clamped to 1 — must not ZeroDivisionError.

        The arithmetic in both the expected-count formula and the
        per-frame stride check uses ``frame_skip`` as a divisor; a
        bare 0 would crash if not guarded.  Mirrors the same clamp
        already in :func:`expected_extraction_count`.
        """
        video_path = str(tmp_path / "in.mp4")
        out_dir = str(tmp_path / "frames_zero")
        _make_synthetic_video(video_path, n_frames=4)

        status = video_to_frames(video_path, out_dir, frame_skip=0)

        # Clamped to 1 → behaves like a no-op skip → all 4 frames written.
        assert status == STATUS_COMPLETED
        assert len(os.listdir(out_dir)) == 4

    def test_frame_skip_negative_does_not_crash(self, tmp_path):
        """Same clamp covers negative values."""
        video_path = str(tmp_path / "in.mp4")
        out_dir = str(tmp_path / "frames_neg")
        _make_synthetic_video(video_path, n_frames=4)

        status = video_to_frames(video_path, out_dir, frame_skip=-3)

        assert status == STATUS_COMPLETED
        assert len(os.listdir(out_dir)) == 4

    def test_empty_file_returns_status(self, tmp_path):
        empty = tmp_path / "empty.mp4"
        empty.touch()
        assert video_to_frames(str(empty), str(tmp_path / "out")) == STATUS_EMPTY_FILE

    def test_garbage_file_returns_cannot_open(self, tmp_path):
        """Random bytes that aren't a valid video are rejected by OpenCV."""
        garbage = tmp_path / "garbage.mp4"
        garbage.write_bytes(b"not a video at all" * 10)
        # Codecs may say "cannot_open" OR "invalid_properties" depending on the
        # OpenCV build — both are correct rejection paths for non-decodable
        # input.  Accept either.
        status = video_to_frames(str(garbage), str(tmp_path / "out"))
        assert status in (STATUS_CANNOT_OPEN, "invalid_properties")


# =====================================================================
# Tests for diagnose_video_file
# =====================================================================

class TestDiagnoseVideoFile:
    """Cover the standalone diagnostic helper."""

    def test_healthy_video(self, tmp_path):
        """A valid video reports no issues + populated properties dict.

        ``issues`` being an **empty list** is the canonical "ok"
        signal — there is no "no issues" sentinel string the caller
        has to scan for.
        """
        video_path = str(tmp_path / "ok.mp4")
        _make_synthetic_video(video_path, n_frames=6)

        result = diagnose_video_file(video_path)

        assert result["file_exists"] is True
        assert result["file_size"] > 0
        assert result["can_open"] is True
        assert result["properties"]["frame_count"] >= 1
        assert result["issues"] == []

    def test_missing_file(self, tmp_path):
        result = diagnose_video_file(str(tmp_path / "absent.mp4"))
        assert result["file_exists"] is False
        assert "File does not exist" in result["issues"]

    def test_empty_file(self, tmp_path):
        empty = tmp_path / "empty.mp4"
        empty.touch()
        result = diagnose_video_file(str(empty))
        assert result["file_exists"] is True
        assert result["file_size"] == 0
        assert any("empty" in s.lower() for s in result["issues"])


# =====================================================================
# Tests for frames_to_video (single-camera encoding)
# =====================================================================

class TestFramesToVideo:
    """Cover the single-camera frames → video helper."""

    def _populate_frames_dir(self, frame_dir, n_frames=8, ext=".jpg"):
        os.makedirs(frame_dir, exist_ok=True)
        for i in range(n_frames):
            img = np.full((48, 64, 3), int(255 * i / n_frames), dtype=np.uint8)
            cv2.imwrite(os.path.join(frame_dir, f"{i:09d}{ext}"), img)

    def test_round_trip_video_to_frames_to_video(self, tmp_path):
        """End-to-end: video → frames → video produces a playable output.

        We don't assert frame-by-frame equality (codec re-encoding
        introduces small numerical drift) but DO require the output
        video to be openable + report the correct frame count.
        """
        src = str(tmp_path / "src.mp4")
        frame_dir = str(tmp_path / "round_frames")
        out = str(tmp_path / "out.mp4")
        _make_synthetic_video(src, n_frames=12)

        assert video_to_frames(src, frame_dir) == STATUS_COMPLETED
        assert frames_to_video(frame_dir, out, fps=10.0, progress=False) == "completed"

        cap = cv2.VideoCapture(out)
        assert cap.isOpened()
        try:
            assert int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) == 12
            assert int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) == 64
            assert int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) == 48
        finally:
            cap.release()

    def test_no_frames_found(self, tmp_path):
        """Empty (or nonexistent) input directory returns ``no_frames_found``."""
        frame_dir = str(tmp_path / "empty_frames")
        os.makedirs(frame_dir, exist_ok=True)
        out = str(tmp_path / "noframes.mp4")
        assert frames_to_video(
            frame_dir, out, progress=False,
        ) == STATUS_NO_FRAMES_FOUND

    def test_glob_pattern_filter(self, tmp_path):
        """Custom glob pattern picks only the matching files."""
        frame_dir = str(tmp_path / "mixed")
        os.makedirs(frame_dir, exist_ok=True)
        # Mix .jpg and .png; only .png should be picked.
        for i in range(4):
            cv2.imwrite(
                os.path.join(frame_dir, f"frame_{i:04d}.png"),
                np.full((48, 64, 3), 100, dtype=np.uint8),
            )
        # Stray JPG that should be excluded.
        cv2.imwrite(
            os.path.join(frame_dir, "stray.jpg"),
            np.zeros((48, 64, 3), dtype=np.uint8),
        )
        out = str(tmp_path / "png_only.mp4")

        status = frames_to_video(
            frame_dir, out, fps=5.0, glob_patterns=("*.png",),
            progress=False,
        )

        assert status == "completed"
        cap = cv2.VideoCapture(out)
        try:
            assert int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) == 4
        finally:
            cap.release()

    def test_start_and_end_frame_trim(self, tmp_path):
        """``start_frame`` / ``end_frame`` trim the sorted-by-name list."""
        frame_dir = str(tmp_path / "trim_frames")
        self._populate_frames_dir(frame_dir, n_frames=10)
        out = str(tmp_path / "trim.mp4")

        status = frames_to_video(
            frame_dir, out, fps=5.0,
            start_frame=2, end_frame=7,
            progress=False,
        )

        assert status == "completed"
        cap = cv2.VideoCapture(out)
        try:
            assert int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) == 5
        finally:
            cap.release()

    def test_down_sample_halves_resolution(self, tmp_path):
        """``down_sample=2`` halves the output width and height."""
        frame_dir = str(tmp_path / "ds_frames")
        self._populate_frames_dir(frame_dir, n_frames=4)
        out = str(tmp_path / "ds.mp4")

        status = frames_to_video(
            frame_dir, out, fps=5.0, down_sample=2, progress=False,
        )

        assert status == "completed"
        cap = cv2.VideoCapture(out)
        try:
            assert int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) == 32
            assert int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) == 24
        finally:
            cap.release()

    def test_skipped_when_output_exists(self, tmp_path):
        """Existing output file is left untouched without ``overwrite=True``."""
        frame_dir = str(tmp_path / "exists_frames")
        self._populate_frames_dir(frame_dir, n_frames=4)
        out = str(tmp_path / "exists.mp4")
        # First run creates the file.
        first = frames_to_video(frame_dir, out, fps=5.0, progress=False)
        assert first == "completed"
        original_mtime = os.path.getmtime(out)

        # Second run with overwrite=False should short-circuit.
        second = frames_to_video(
            frame_dir, out, fps=5.0, overwrite=False, progress=False,
        )

        assert second == "skipped"
        assert os.path.getmtime(out) == original_mtime

    def test_label_overlay_renders(self, tmp_path):
        """A non-None ``label`` perturbs the output (overlay was drawn)."""
        frame_dir = str(tmp_path / "label_frames")
        self._populate_frames_dir(frame_dir, n_frames=4)
        out_plain = str(tmp_path / "plain.mp4")
        out_label = str(tmp_path / "label.mp4")

        frames_to_video(frame_dir, out_plain, fps=5.0, progress=False)
        frames_to_video(
            frame_dir, out_label, fps=5.0, label="HELLO", progress=False,
        )

        # Compare first decoded frame: with-label render must differ.
        cap_a = cv2.VideoCapture(out_plain)
        cap_b = cv2.VideoCapture(out_label)
        try:
            ok_a, frame_a = cap_a.read()
            ok_b, frame_b = cap_b.read()
            assert ok_a and ok_b
            assert not np.array_equal(frame_a, frame_b)
        finally:
            cap_a.release()
            cap_b.release()


# =====================================================================
# Default-constants sanity
# =====================================================================

def test_default_frame_pattern_is_consistent():
    """The library exposes the same default pattern the CLI prints."""
    assert DEFAULT_FRAME_PATTERN == "{frame_id}.png"


def test_default_glob_patterns_includes_jpg_and_png():
    """The default glob covers both common frame formats."""
    assert "*.jpg" in DEFAULT_GLOB_PATTERNS
    assert "*.png" in DEFAULT_GLOB_PATTERNS


# =====================================================================
# Tests for layout-preset / canonical-pattern alignment
# =====================================================================

class TestFrameNamePatternPresets:
    """Verify each named preset round-trips through ``video_to_frames``
    AND aligns with the corresponding entry in
    ``_build_non_h5_frame_patterns`` (which the visualization stack
    uses for image discovery).

    Catches regressions where a preset gets renamed in
    ``video2frame.py`` but the canonical-layout list drifts away,
    silently breaking auto-discovery for newly-extracted frames.
    """

    def test_aic_preset_matches_canonical_layout(self, tmp_path):
        """``aic`` preset writes ``<dir>/000000006.jpg`` matching pattern #1."""
        from spatialai_data_utils.datasets.frame_paths import (
            _build_non_h5_frame_patterns,
        )
        from spatialai_data_utils.visualization.video_utils.video2frame import (
            FRAME_NAME_PATTERN_PRESETS,
        )
        # Sanity: the preset's pattern is the same string the canonical
        # layout uses (just expressed as a str.format vs an f-string).
        # We probe equivalence on a single frame id.
        canonical = _build_non_h5_frame_patterns(
            scene_dir="/scene", cam_name="cam", frame_id=6,
        )
        # Canonical pattern #1 is "<scene>/<cam>/images/000000006.jpg".
        assert canonical[0].endswith("/images/000000006.jpg")
        # Preset 'aic' should generate the same basename.
        assert FRAME_NAME_PATTERN_PRESETS["aic"].format(6) == "000000006.jpg"

    def test_isaac_png_preset_matches_canonical(self):
        """``isaac_png`` preset matches the ``rgb/rgb_<05d>.png`` layout."""
        from spatialai_data_utils.datasets.frame_paths import (
            _build_non_h5_frame_patterns,
        )
        from spatialai_data_utils.visualization.video_utils.video2frame import (
            FRAME_NAME_PATTERN_PRESETS,
        )
        canonical = _build_non_h5_frame_patterns(
            scene_dir="/scene", cam_name="cam", frame_id=6,
        )
        assert canonical[1].endswith("/rgb/rgb_00006.png")
        assert FRAME_NAME_PATTERN_PRESETS["isaac_png"].format(6) == "rgb_00006.png"

    def test_isaac_jpg_preset_matches_canonical(self):
        """``isaac_jpg`` preset matches the ``rgb/rgb_<05d>.jpg`` layout."""
        from spatialai_data_utils.visualization.video_utils.video2frame import (
            FRAME_NAME_PATTERN_PRESETS,
        )
        assert FRAME_NAME_PATTERN_PRESETS["isaac_jpg"].format(6) == "rgb_00006.jpg"

    def test_scout_preset_no_zero_padding(self):
        """``scout`` preset uses raw integer formatting (no zero pad)."""
        from spatialai_data_utils.visualization.video_utils.video2frame import (
            FRAME_NAME_PATTERN_PRESETS,
        )
        assert FRAME_NAME_PATTERN_PRESETS["scout"].format(6) == "image_6.jpg"
        assert FRAME_NAME_PATTERN_PRESETS["scout"].format(123) == "image_123.jpg"

    def test_bare_presets_emit_just_the_extension(self):
        """``bare_jpg`` / ``bare_png`` produce ``<int>.<ext>``."""
        from spatialai_data_utils.visualization.video_utils.video2frame import (
            FRAME_NAME_PATTERN_PRESETS,
        )
        assert FRAME_NAME_PATTERN_PRESETS["bare_jpg"].format(42) == "42.jpg"
        assert FRAME_NAME_PATTERN_PRESETS["bare_png"].format(42) == "42.png"

    def test_default_pattern_is_no_ts_form(self):
        """``DEFAULT_FRAME_PATTERN`` is the no-timestamp short form.

        ``video_to_frames`` doesn't have a wall-clock source, so the
        package default is the fallback ``"{frame_id}.png"``.  The
        timestamp form (``"{frame_id}_{timestamp}.png"``) is a
        downstream-emitted convention that the smart sort in
        :func:`list_frame_paths` knows how to handle.
        """
        assert DEFAULT_FRAME_PATTERN == "{frame_id}.png"

    def test_extracting_with_isaac_jpg_preset_writes_isaac_layout(self, tmp_path):
        """End-to-end: extracting with the ``isaac_jpg`` preset produces
        files that match the canonical Isaac mirror layout, so the
        downstream image-discovery resolver finds them.
        """
        from spatialai_data_utils.visualization.video_utils.video2frame import (
            FRAME_NAME_PATTERN_PRESETS,
        )
        video_path = str(tmp_path / "in.mp4")
        out_dir = str(tmp_path / "rgb")  # parent picks the canonical 'rgb' subdir
        _make_synthetic_video(video_path, n_frames=3)

        status = video_to_frames(
            video_path, out_dir,
            frame_pattern=FRAME_NAME_PATTERN_PRESETS["isaac_jpg"],
        )

        assert status == STATUS_COMPLETED
        assert sorted(os.listdir(out_dir)) == [
            "rgb_00000.jpg", "rgb_00001.jpg", "rgb_00002.jpg",
        ]


# =====================================================================
# Tests for format helpers (spatialai_data_utils.visualization.video_utils.format)
# =====================================================================

from spatialai_data_utils.visualization.video_utils.format import (
    format_duration,
    format_size,
)


class TestFormatSize:
    """Cover the ``format_size`` byte-count formatter."""

    def test_zero_bytes(self):
        """``0 B`` is the floor case (must not divide-by-zero)."""
        assert format_size(0) == "0.0 B"

    def test_below_kb_uses_bytes(self):
        """Anything < 1024 stays in the ``B`` unit."""
        assert format_size(512) == "512.0 B"
        assert format_size(1023) == "1023.0 B"

    def test_kb_boundary(self):
        """Exactly 1024 promotes to ``KB``."""
        assert format_size(1024) == "1.0 KB"
        assert format_size(1024 * 5) == "5.0 KB"

    def test_mb_boundary(self):
        """Exactly 1024**2 promotes to ``MB``."""
        assert format_size(1024 ** 2) == "1.0 MB"

    def test_gb_boundary(self):
        """Exactly 1024**3 promotes to ``GB``."""
        assert format_size(1024 ** 3) == "1.0 GB"

    def test_tb_boundary(self):
        """Exactly 1024**4 promotes to ``TB``."""
        assert format_size(1024 ** 4) == "1.0 TB"

    def test_above_tb_stays_tb(self):
        """Exabyte-scale values keep the ``TB`` unit (no PB exposed)."""
        assert format_size(1024 ** 5).endswith(" TB")


class TestFormatDuration:
    """Cover the ``format_duration`` wall-time formatter."""

    def test_sub_second(self):
        """Sub-second values use ``ms`` with a rounded integer."""
        assert format_duration(0.5) == "500 ms"
        assert format_duration(0.001) == "1 ms"

    def test_sub_minute(self):
        """1s ≤ t < 60s uses ``S.Ss`` with one decimal."""
        assert format_duration(1.0) == "1.0s"
        assert format_duration(45.7) == "45.7s"
        assert format_duration(59.9) == "59.9s"

    def test_sub_hour(self):
        """60s ≤ t < 3600s uses ``MmSSs`` with zero-padded seconds."""
        assert format_duration(60) == "1m00s"
        assert format_duration(125) == "2m05s"
        assert format_duration(3599) == "59m59s"

    def test_hours(self):
        """t ≥ 3600s uses ``HhMMmSSs``."""
        assert format_duration(3600) == "1h00m00s"
        assert format_duration(3661) == "1h01m01s"
        assert format_duration(7325) == "2h02m05s"


# =====================================================================
# Tests for expected_extraction_count (skip-detection prediction)
# =====================================================================

from spatialai_data_utils.visualization.video_utils.video2frame import (
    expected_extraction_count,
)


class TestExpectedExtractionCount:
    """Cover the pre-flight ``expected_extraction_count`` helper.

    Mirrors the skip-detection logic inside :func:`video_to_frames`,
    so each test exercises a path the actual extractor will take.
    """

    def test_full_extraction(self, tmp_path):
        """Fresh video + empty output dir → expected = source frame count."""
        video = str(tmp_path / "in.mp4")
        out_dir = str(tmp_path / "frames")
        _make_synthetic_video(video, n_frames=20)
        assert expected_extraction_count(video, out_dir) == 20

    def test_with_frame_skip(self, tmp_path):
        """``frame_skip=N`` divides the count (ceiling)."""
        video = str(tmp_path / "in.mp4")
        out_dir = str(tmp_path / "frames")
        _make_synthetic_video(video, n_frames=10)
        # 10 frames at stride 3 → indices 0,3,6,9 → 4 outputs.
        assert expected_extraction_count(
            video, out_dir, frame_skip=3,
        ) == 4

    def test_with_range(self, tmp_path):
        """``[start, end)`` window trims the count."""
        video = str(tmp_path / "in.mp4")
        out_dir = str(tmp_path / "frames")
        _make_synthetic_video(video, n_frames=20)
        assert expected_extraction_count(
            video, out_dir, start_frame=5, end_frame=15,
        ) == 10

    def test_returns_zero_when_already_extracted(self, tmp_path):
        """When output dir holds ≥ expected frames, predict skip → 0."""
        video = str(tmp_path / "in.mp4")
        out_dir = str(tmp_path / "frames")
        _make_synthetic_video(video, n_frames=8)
        # First run populates the dir.
        video_to_frames(video, out_dir)
        # Re-asking should return 0 (worker would skip).
        assert expected_extraction_count(video, out_dir) == 0

    def test_overwrite_bypasses_skip_detection(self, tmp_path):
        """``overwrite=True`` returns the full count even with populated dir."""
        video = str(tmp_path / "in.mp4")
        out_dir = str(tmp_path / "frames")
        _make_synthetic_video(video, n_frames=8)
        video_to_frames(video, out_dir)
        assert expected_extraction_count(
            video, out_dir, overwrite=True,
        ) == 8

    def test_missing_video_returns_zero(self, tmp_path):
        """Missing video → 0 (worker would return file_not_found)."""
        assert expected_extraction_count(
            str(tmp_path / "absent.mp4"), str(tmp_path / "out"),
        ) == 0

    def test_empty_window_returns_zero(self, tmp_path):
        """``end_frame <= start_frame`` → empty window → 0."""
        video = str(tmp_path / "in.mp4")
        out_dir = str(tmp_path / "frames")
        _make_synthetic_video(video, n_frames=20)
        assert expected_extraction_count(
            video, out_dir, start_frame=10, end_frame=10,
        ) == 0


# =====================================================================
# Tests for the multi-camera grid module
# (spatialai_data_utils.visualization.video_utils.frame2video_grid)
# =====================================================================

from spatialai_data_utils.visualization.video_utils.frame2video_grid import (
    _auto_cam_labels,
    auto_grid_cols,
    frames_to_video_grid,
)


class TestAutoGridCols:
    """Cover the grid-column heuristic ``ceil(sqrt(N))``."""

    @pytest.mark.parametrize(
        "n_items, expected_cols",
        [
            (1, 1),
            (2, 2),
            (3, 2),
            (4, 2),
            (5, 3),
            (6, 3),
            (8, 3),
            (9, 3),
            (10, 4),
            (12, 4),
            (15, 4),
            (16, 4),
            (17, 5),
            (25, 5),
        ],
    )
    def test_known_layouts(self, n_items, expected_cols):
        assert auto_grid_cols(n_items) == expected_cols

    def test_clamps_zero_or_negative_to_one(self):
        """Defensive: 0 / negative inputs don't return 0 (would div-by-0 callers)."""
        assert auto_grid_cols(0) == 1
        assert auto_grid_cols(-3) == 1


class TestAutoCamLabels:
    """Cover the ``_auto_cam_labels`` layout-detecting heuristic."""

    def test_shared_basename_uses_parent(self):
        """All dirs end in same name → frames-subdir layout; use parent."""
        dirs = [
            "/scene/Camera_01/rgb",
            "/scene/Camera_02/rgb",
            "/scene/Camera_03/rgb",
        ]
        assert _auto_cam_labels(dirs) == [
            "Camera_01", "Camera_02", "Camera_03",
        ]

    def test_unique_basenames_uses_basename(self):
        """Each dir has its own name → cam name IS the basename."""
        dirs = ["/scene/Camera_01", "/scene/Camera_02", "/scene/Camera_03"]
        assert _auto_cam_labels(dirs) == [
            "Camera_01", "Camera_02", "Camera_03",
        ]

    def test_single_dir_uses_basename(self):
        """A 1-element list isn't enough signal → fall back to basename."""
        assert _auto_cam_labels(["/scene/Camera_01/rgb"]) == ["rgb"]

    def test_handles_trailing_slash(self):
        """Trailing slashes are stripped before comparison."""
        dirs = [
            "/scene/Camera_01/rgb/",
            "/scene/Camera_02/rgb/",
        ]
        assert _auto_cam_labels(dirs) == ["Camera_01", "Camera_02"]

    def test_alternate_frames_subdir(self):
        """Heuristic generalises to any shared subdir name (e.g. 'images')."""
        dirs = [
            "/scene/cam_a/images",
            "/scene/cam_b/images",
        ]
        assert _auto_cam_labels(dirs) == ["cam_a", "cam_b"]


def _populate_cam_dirs(
    tmp_path, n_cams: int, n_frames: int, *,
    height: int = 48, width: int = 64,
) -> list:
    """Build ``<tmp>/Camera_NN/rgb/rgb_<NNNNN>.jpg`` per cam.

    Returns the list of frame_dirs the test can pass straight to
    :func:`frames_to_video_grid`.  Each cam writes a flat colour
    distinct from the others so a downstream test can verify
    cam ordering by sampling pixel colours.
    """
    frame_dirs = []
    for i in range(1, n_cams + 1):
        cam = f"Camera_{i:02d}"
        d = str(tmp_path / cam / "rgb")
        os.makedirs(d, exist_ok=True)
        # Distinct flat colour per cam — channel pattern (B, G, R).
        colour = ((i * 60) % 256, (i * 37) % 256, (i * 91) % 256)
        for f in range(n_frames):
            img = np.full((height, width, 3), colour, dtype=np.uint8)
            cv2.imwrite(os.path.join(d, f"rgb_{f:05d}.jpg"), img)
        frame_dirs.append(d)
    return frame_dirs


class TestFramesToVideoGrid:
    """Cover the multi-camera grid-video assembly helper."""

    def test_basic_4_cam_grid(self, tmp_path):
        """4 cams → auto 2x2 grid, output dimensions match target_height."""
        frame_dirs = _populate_cam_dirs(tmp_path, n_cams=4, n_frames=5)
        out = str(tmp_path / "grid.mp4")

        status = frames_to_video_grid(
            frame_dirs, out, fps=10.0, target_height=720, progress=False,
        )

        assert status == "completed"
        cap = cv2.VideoCapture(out)
        try:
            assert int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) == 5
            # 2 rows: tile_h = 720/2 = 360.  Source 64x48 → aspect 4:3
            # → tile_w = round(64 * 360/48) = 480.  2 cols → out_w = 960.
            assert int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) == 720
            assert int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) == 960
        finally:
            cap.release()

    def test_explicit_n_cols_overrides_auto(self, tmp_path):
        """4 cams + ``n_cols=4`` → 4x1 layout (overrides sqrt heuristic)."""
        frame_dirs = _populate_cam_dirs(tmp_path, n_cams=4, n_frames=3)
        out = str(tmp_path / "grid_4col.mp4")

        status = frames_to_video_grid(
            frame_dirs, out, fps=5.0, target_height=240,
            n_cols=4, progress=False,
        )

        assert status == "completed"
        cap = cv2.VideoCapture(out)
        try:
            # 1 row: tile_h = 240, aspect 4:3 → tile_w = 320, out_w = 1280.
            assert int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) == 240
            assert int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) == 1280
        finally:
            cap.release()

    def test_target_height_none_keeps_source_resolution(self, tmp_path):
        """``target_height=None`` → tiles at full source resolution."""
        frame_dirs = _populate_cam_dirs(tmp_path, n_cams=4, n_frames=3)
        out = str(tmp_path / "fullres.mp4")

        status = frames_to_video_grid(
            frame_dirs, out, fps=5.0, target_height=None, progress=False,
        )

        assert status == "completed"
        cap = cv2.VideoCapture(out)
        try:
            # Source 64x48 → 2x2 grid → 128x96.
            assert int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) == 96
            assert int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) == 128
        finally:
            cap.release()

    def test_no_frames_found_when_master_dir_empty(self, tmp_path):
        """First cam having no frames returns ``no_frames_found``."""
        empty_dir = str(tmp_path / "Camera_01" / "rgb")
        os.makedirs(empty_dir)
        # Subsequent cams DO have frames — first-cam-empty still trips.
        other_dirs = _populate_cam_dirs(tmp_path / "other", n_cams=2, n_frames=3)
        out = str(tmp_path / "out.mp4")

        status = frames_to_video_grid(
            [empty_dir, *other_dirs], out, fps=5.0, progress=False,
        )

        assert status == "no_frames_found"

    def test_no_frame_dirs_returns_no_frames_found(self, tmp_path):
        """Empty ``frame_dirs`` list short-circuits."""
        assert frames_to_video_grid(
            [], str(tmp_path / "out.mp4"), progress=False,
        ) == "no_frames_found"

    def test_skipped_when_output_exists(self, tmp_path):
        """Existing output + ``overwrite=False`` → ``skipped``."""
        frame_dirs = _populate_cam_dirs(tmp_path, n_cams=4, n_frames=3)
        out = str(tmp_path / "exists.mp4")
        # First run creates the file.
        first = frames_to_video_grid(
            frame_dirs, out, fps=5.0, progress=False,
        )
        assert first == "completed"
        original_mtime = os.path.getmtime(out)

        second = frames_to_video_grid(
            frame_dirs, out, fps=5.0, overwrite=False, progress=False,
        )

        assert second == "skipped"
        assert os.path.getmtime(out) == original_mtime

    def test_max_workers_correctness(self, tmp_path):
        """Sequential and threaded paths produce byte-identical output."""
        frame_dirs = _populate_cam_dirs(tmp_path, n_cams=4, n_frames=4)
        out_seq = str(tmp_path / "seq.mp4")
        out_par = str(tmp_path / "par.mp4")

        s1 = frames_to_video_grid(
            frame_dirs, out_seq, fps=5.0, target_height=240,
            max_workers=1, progress=False,
        )
        s2 = frames_to_video_grid(
            frame_dirs, out_par, fps=5.0, target_height=240,
            max_workers=4, progress=False,
        )
        assert s1 == s2 == "completed"
        # Decoded frame at index 0 should match across runs (codec is
        # deterministic for these inputs).
        cap_a = cv2.VideoCapture(out_seq)
        cap_b = cv2.VideoCapture(out_par)
        try:
            ok_a, frame_a = cap_a.read()
            ok_b, frame_b = cap_b.read()
            assert ok_a and ok_b
            assert frame_a.shape == frame_b.shape
            # Same pixel content → encoder produced the same first frame.
            assert np.array_equal(frame_a, frame_b)
        finally:
            cap_a.release()
            cap_b.release()

    def test_per_cam_label_perturbs_output(self, tmp_path):
        """``per_cam_label=True`` draws labels (output differs from suppressed)."""
        frame_dirs = _populate_cam_dirs(tmp_path, n_cams=4, n_frames=3)
        out_lbl = str(tmp_path / "labelled.mp4")
        out_no = str(tmp_path / "unlabelled.mp4")

        frames_to_video_grid(
            frame_dirs, out_lbl, fps=5.0, target_height=240,
            per_cam_label=True, progress=False,
        )
        frames_to_video_grid(
            frame_dirs, out_no, fps=5.0, target_height=240,
            per_cam_label=False, progress=False,
        )
        cap_a = cv2.VideoCapture(out_lbl)
        cap_b = cv2.VideoCapture(out_no)
        try:
            ok_a, frame_a = cap_a.read()
            ok_b, frame_b = cap_b.read()
            assert ok_a and ok_b
            # The label rectangles + text mean the two outputs differ.
            assert not np.array_equal(frame_a, frame_b)
        finally:
            cap_a.release()
            cap_b.release()

    def test_pads_non_rectangular_grid(self, tmp_path):
        """3 cams in a 2x2 grid → 1 black tile padded so dims stay consistent."""
        frame_dirs = _populate_cam_dirs(tmp_path, n_cams=3, n_frames=3)
        out = str(tmp_path / "padded.mp4")

        status = frames_to_video_grid(
            frame_dirs, out, fps=5.0, target_height=240,
            n_cols=2, per_cam_label=False, progress=False,
        )

        assert status == "completed"
        cap = cv2.VideoCapture(out)
        try:
            # 2x2 grid → out is 2*tile_w x 2*tile_h.  The bottom-right
            # quadrant is the padded black tile → BGR (0,0,0).
            ok, frame = cap.read()
            assert ok
            h, w = frame.shape[:2]
            br_quadrant = frame[h // 2 :, w // 2 :, :]
            # Bottom-right average should be very close to black (allow
            # for minor codec re-encoding drift).
            assert br_quadrant.mean() < 5.0
        finally:
            cap.release()

    def test_explicit_cam_labels_passed_through(self, tmp_path):
        """Caller-supplied ``cam_labels`` wins over auto-derive."""
        frame_dirs = _populate_cam_dirs(tmp_path, n_cams=2, n_frames=2)
        out = str(tmp_path / "labels.mp4")
        # Just exercise the path — content assertion is covered by
        # test_per_cam_label_perturbs_output.  Here we just check it
        # doesn't raise even with arbitrary labels.
        status = frames_to_video_grid(
            frame_dirs, out, fps=5.0, target_height=240,
            cam_labels=["Alpha", "Bravo"], per_cam_label=True,
            progress=False,
        )
        assert status == "completed"


# =====================================================================
# Tests for get_cam_names_in_scene's must_contain filter
# =====================================================================

from spatialai_data_utils.datasets.scenes import get_cam_names_in_scene


class TestGetCamNamesInSceneFilter:
    """Cover the ``must_contain`` filter — drops non-camera siblings
    inside scene roots that mix cam dirs with utility dirs (e.g. real
    MTMC scenes that have ``videos/``, ``resources/`` next to their
    ``Camera_*/`` dirs).
    """

    def _make_scene(self, root, layout):
        """Materialise ``layout`` (dict mapping ``cam_name -> contents``)
        under ``root``.  ``contents`` is a list of relative paths to
        create inside the camera dir; trailing-slash items become
        directories, others become empty files.
        """
        for cam, contents in layout.items():
            os.makedirs(os.path.join(root, cam), exist_ok=True)
            for item in contents:
                p = os.path.join(root, cam, item.rstrip("/"))
                if item.endswith("/"):
                    os.makedirs(p, exist_ok=True)
                else:
                    os.makedirs(os.path.dirname(p) or root, exist_ok=True)
                    open(p, "w").close()

    def test_no_filter_returns_all_subdirs(self, tmp_path):
        """Default ``must_contain=None`` keeps all subdirs (legacy
        behaviour — preserves backward compat with existing callers).
        """
        scene = str(tmp_path)
        self._make_scene(scene, {
            "Camera_01": ["rgb/"],
            "Camera_02": ["rgb/"],
            "videos": ["Camera_01.mp4"],   # non-cam sibling
            "resources": [],               # non-cam sibling
        })
        names = get_cam_names_in_scene(scene)
        assert sorted(names) == sorted(
            ["Camera_01", "Camera_02", "videos", "resources"],
        )

    def test_must_contain_subdir_filters_non_cams(self, tmp_path):
        """``must_contain='rgb'`` drops dirs without an ``rgb`` subdir."""
        scene = str(tmp_path)
        self._make_scene(scene, {
            "Camera_01": ["rgb/"],
            "Camera_02": ["rgb/"],
            "videos": ["Camera_01.mp4"],
            "resources": [],
        })
        names = get_cam_names_in_scene(scene, must_contain="rgb")
        assert names == ["Camera_01", "Camera_02"]

    def test_must_contain_file_works_for_layout_a(self, tmp_path):
        """``must_contain='video.mp4'`` filters to dirs with that file —
        the canonical Layout A check used by ``video2frame_scene.py``.
        """
        scene = str(tmp_path)
        self._make_scene(scene, {
            "Camera_01": ["video.mp4"],
            "Camera_02": ["video.mp4"],
            "Camera_03": ["other.txt"],   # exists as a dir but no video
            "videos": [],
        })
        names = get_cam_names_in_scene(scene, must_contain="video.mp4")
        assert names == ["Camera_01", "Camera_02"]

    def test_empty_must_contain_is_no_op(self, tmp_path):
        """Empty string is treated as "no filter" — same as default."""
        scene = str(tmp_path)
        self._make_scene(scene, {
            "Camera_01": ["rgb/"],
            "videos": ["Camera_01.mp4"],
        })
        names = get_cam_names_in_scene(scene, must_contain="")
        assert sorted(names) == sorted(["Camera_01", "videos"])

    def test_must_contain_ignored_when_h5_file(self, tmp_path):
        """``h5_file=True`` lists files; ``must_contain`` doesn't apply."""
        scene = str(tmp_path)
        for f in ["Camera_01.h5", "Camera_02.h5", "metadata.txt"]:
            open(os.path.join(scene, f), "w").close()
        names = get_cam_names_in_scene(
            scene, h5_file=True, must_contain="rgb",
        )
        assert sorted(names) == ["Camera_01.h5", "Camera_02.h5"]

    def test_natural_sort_after_filter(self, tmp_path):
        """Sort applies post-filter — same Camera_2 < Camera_10 ordering."""
        scene = str(tmp_path)
        self._make_scene(scene, {
            "Camera_2": ["rgb/"],
            "Camera_10": ["rgb/"],
            "Camera_5": ["rgb/"],
            "videos": [],
        })
        names = get_cam_names_in_scene(scene, must_contain="rgb")
        assert names == ["Camera_2", "Camera_5", "Camera_10"]


# =====================================================================
# Tests for the smart frame-list sort (timestamp / frame_id / lex)
# =====================================================================

from spatialai_data_utils.visualization.video_utils.frame2video import (
    _filename_ts_to_int,
    list_frame_paths,
    parse_frame_filename,
)


class TestFilenameTsToInt:
    """Cover ``_filename_ts_to_int`` — the int conversion helper that
    backs the smart-sort comparator.

    Both filesystem-safe (``T10-00-03.500Z``) and standard NVSchema
    (``T10:00:03.500Z``) shapes parse to the same int because the
    helper just concatenates the timestamp's digit runs.
    """

    def test_filesystem_safe_form(self):
        assert _filename_ts_to_int("2026-04-24T10-00-03.500Z") == 20260424100003500

    def test_standard_iso_form(self):
        # NVSchema uses ':' for time separators — same int result.
        assert _filename_ts_to_int("2026-04-24T10:00:03.500Z") == 20260424100003500

    def test_no_subsecond(self):
        # Missing milliseconds → padded to 0.
        assert _filename_ts_to_int("2026-04-24T10-00-03Z") == 20260424100003000

    def test_garbage_returns_none(self):
        assert _filename_ts_to_int("not-a-timestamp") is None

    def test_too_few_digits_returns_none(self):
        # 7 digits — no plausible YYYYMMDDHHMMSS field.
        assert _filename_ts_to_int("2026-04") is None

    def test_chronological_order_is_preserved(self):
        """Sorting the ints sorts the timestamps chronologically."""
        ts_strs = [
            "2026-04-24T10-00-05.000Z",
            "2026-04-24T10-00-03.300Z",
            "2026-04-24T10-00-04.000Z",
            "2026-04-24T10-00-03.500Z",
        ]
        ints = [_filename_ts_to_int(s) for s in ts_strs]
        # Sorted ints land in the same order as sorted timestamps.
        assert sorted(ints) == [
            _filename_ts_to_int("2026-04-24T10-00-03.300Z"),
            _filename_ts_to_int("2026-04-24T10-00-03.500Z"),
            _filename_ts_to_int("2026-04-24T10-00-04.000Z"),
            _filename_ts_to_int("2026-04-24T10-00-05.000Z"),
        ]


class TestParseFrameFilename:
    """Cover the ``<frame_id>(_<timestamp>)?.<ext>`` filename parser."""

    def test_no_timestamp(self):
        assert parse_frame_filename("/dir/5.png") == (5, None)

    def test_with_timestamp(self):
        fid, ts = parse_frame_filename(
            "/dir/100_2026-04-24T10-00-03.500Z.png",
        )
        assert fid == 100
        assert ts == 20260424100003500

    def test_zero_padded_legacy(self):
        # Legacy "{:09d}.jpg" still parses — 9 zeros, no underscore.
        assert parse_frame_filename("/dir/000000005.jpg") == (5, None)

    def test_non_digit_prefix_yields_no_match(self):
        # Isaac-mirror ``rgb_00005.jpg`` doesn't start with digits, so
        # the parser returns (None, None) and the smart sort falls back
        # to lex (which sorts these correctly anyway because the suffix
        # is zero-padded).
        assert parse_frame_filename("/dir/rgb_00005.jpg") == (None, None)


class TestListFramePathsSmartSort:
    """Cover the three-tier sort precedence: timestamp → frame_id → lex."""

    def _touch(self, root, names):
        for n in names:
            open(os.path.join(root, n), "w").close()

    def test_timestamp_sort_when_all_have_timestamps(self, tmp_path):
        """Out-of-order writes; timestamp sort yields chronological order."""
        # frame_id and timestamp deliberately disagree on rank — this
        # asserts the sort prefers timestamp.
        self._touch(str(tmp_path), [
            "200_2026-04-24T10-00-05.000Z.png",  # latest ts, biggest fid
            "100_2026-04-24T10-00-03.500Z.png",
            "5_2026-04-24T10-00-03.300Z.png",
            "10_2026-04-24T10-00-04.000Z.png",   # fid in between, ts later
            "1_2026-04-24T10-00-03.000Z.png",    # earliest ts, smallest fid
        ])

        paths = list_frame_paths(str(tmp_path), ["*.png"])
        names = [os.path.basename(p) for p in paths]

        assert names == [
            "1_2026-04-24T10-00-03.000Z.png",
            "5_2026-04-24T10-00-03.300Z.png",
            "100_2026-04-24T10-00-03.500Z.png",
            "10_2026-04-24T10-00-04.000Z.png",
            "200_2026-04-24T10-00-05.000Z.png",
        ]

    def test_frame_id_sort_when_no_timestamps(self, tmp_path):
        """No-pad frame_ids — lex sort would put '10' before '2'; the
        smart sort uses parsed integers."""
        self._touch(str(tmp_path), [
            "2.png", "10.png", "0.png", "11.png", "1.png",
        ])

        paths = list_frame_paths(str(tmp_path), ["*.png"])
        names = [os.path.basename(p) for p in paths]

        assert names == ["0.png", "1.png", "2.png", "10.png", "11.png"]

    def test_lex_fallback_for_legacy_isaac_names(self, tmp_path):
        """Names that don't start with digits → fall back to lex sort.

        Isaac-mirror's ``rgb_00005.jpg`` style sorts correctly under
        lex because the integer suffix is zero-padded.
        """
        self._touch(str(tmp_path), [
            "rgb_00010.jpg", "rgb_00002.jpg", "rgb_00005.jpg",
        ])

        paths = list_frame_paths(str(tmp_path), ["*.jpg"])
        names = [os.path.basename(p) for p in paths]

        assert names == ["rgb_00002.jpg", "rgb_00005.jpg", "rgb_00010.jpg"]

    def test_timestamp_ties_break_on_frame_id(self, tmp_path):
        """Two frames sharing a timestamp use frame_id as the tiebreaker."""
        self._touch(str(tmp_path), [
            "10_2026-04-24T10-00-03.000Z.png",
            "5_2026-04-24T10-00-03.000Z.png",
            "1_2026-04-24T10-00-03.000Z.png",
        ])

        paths = list_frame_paths(str(tmp_path), ["*.png"])
        names = [os.path.basename(p) for p in paths]

        # All ts equal → tiebreak by parsed int frame_id.
        assert names == [
            "1_2026-04-24T10-00-03.000Z.png",
            "5_2026-04-24T10-00-03.000Z.png",
            "10_2026-04-24T10-00-03.000Z.png",
        ]

    def test_empty_dir_returns_empty(self, tmp_path):
        assert list_frame_paths(str(tmp_path), ["*.png"]) == []
