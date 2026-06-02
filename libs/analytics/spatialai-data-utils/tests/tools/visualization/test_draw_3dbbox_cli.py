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

"""Unit tests for the CLI-private helpers in ``tools/visualization/draw_3dbbox.py``.

The CLI lives at ``tools/visualization/draw_3dbbox.py`` — a script
path, not a Python package — so it is loaded here via
:mod:`importlib` to keep ``sys.path`` untouched.  Only the underscore-
prefixed helpers documented below are exercised; end-to-end CLI
behaviour is covered separately through library-level tests of
``visualize_nvschema`` / ``draw_bev_objects_bbox_in_image`` and real-data
smoke tests.
"""
import importlib.util
import sys
from pathlib import Path


_CLI_PATH = (
    Path(__file__).resolve().parents[3]
    / "tools" / "visualization" / "draw_3dbbox.py"
)


def _load_cli_module():
    """Load ``draw_3dbbox.py`` as a throwaway module.

    Stashed under a leading-underscore name in ``sys.modules`` so it
    never collides with anything the test session imports later.
    """
    spec = importlib.util.spec_from_file_location(
        "_draw_3dbbox_cli_under_test", _CLI_PATH,
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


_CLI = _load_cli_module()


# =====================================================================
# Tests for _resolve_targets
# =====================================================================

class TestResolveTargets:
    """Tests for the CLI's sensorId → [concrete target cameras] resolver.

    The resolver runs in one of two mutually-exclusive modes (selected
    by the ``ground_truth`` kwarg, surfaced through the
    ``--ground_truth`` CLI flag):

    * **Default / model-output mode** (``ground_truth=False``):
      ``sensor_id`` is treated as a BEV-sensor-group name and fanned
      out via the group map; concrete-camera names are *not* a
      fallback (they return ``[]``).
    * **Ground-truth mode** (``ground_truth=True``): ``sensor_id`` is
      treated as a concrete camera name and looked up directly in
      the flat calib dict; BEV-group names are *not* a fallback (they
      return ``[]``).

    Each test exercises one mode in isolation so the absence of a
    fallback chain is enforced.
    """

    # ---- Default / model-output mode (ground_truth=False) ----------

    def test_default_mode_bev_group_fans_out_in_order(self):
        """Default mode: BEV group → its members in declared order."""
        flat = {c: {} for c in ("Camera", "Camera_01", "Camera_02", "Camera_03")}
        groups = {"bev-sensor-1": ["Camera", "Camera_01", "Camera_02", "Camera_03"]}
        targets = _CLI._resolve_targets("bev-sensor-1", flat, groups)
        # Member order must match the calibration-declared order so that
        # fan-out output is deterministic (makes md5-style smoke diffs
        # comparable run-to-run).
        assert targets == ["Camera", "Camera_01", "Camera_02", "Camera_03"]

    def test_default_mode_concrete_camera_returns_empty(self):
        """Default mode: a concrete-camera sensorId is *not* honoured.

        Without ``--ground_truth`` the CLI expects sensorId to name a
        BEV group; a concrete camera is therefore "wrong kind" and
        must be skipped — no silent fall-through to single-target
        projection.  This is the central behavioural change the
        ``--ground_truth`` flag introduces.
        """
        flat = {"Camera_01": {}, "Camera_02": {}}
        groups = {"bev-sensor-1": ["Camera_01", "Camera_02"]}
        assert _CLI._resolve_targets("Camera_01", flat, groups) == []

    def test_default_mode_unknown_sensor_returns_empty(self):
        flat = {"Camera_01": {}}
        groups = {"bev-sensor-1": ["Camera_01"]}
        assert _CLI._resolve_targets("Camera_XX", flat, groups) == []

    def test_default_mode_returns_a_copy_of_group_members(self):
        """Mutating the returned list must NOT mutate the group map in place."""
        flat = {"Camera_01": {}, "Camera_02": {}}
        groups = {"bev-sensor-1": ["Camera_01", "Camera_02"]}
        targets = _CLI._resolve_targets("bev-sensor-1", flat, groups)
        targets.append("mutation")
        # The original group map must still contain the canonical members.
        assert groups["bev-sensor-1"] == ["Camera_01", "Camera_02"]

    # ---- Ground-truth mode (ground_truth=True) ---------------------

    def test_gt_mode_concrete_camera_single_target(self):
        """GT mode: concrete camera → single-element list."""
        flat = {"Camera_01": {}, "Camera_02": {}}
        groups = {"bev-sensor-1": ["Camera_01", "Camera_02"]}
        assert _CLI._resolve_targets(
            "Camera_01", flat, groups, ground_truth=True,
        ) == ["Camera_01"]

    def test_gt_mode_bev_group_returns_empty(self):
        """GT mode: BEV-group sensorId is *not* honoured.

        Mirror of ``test_default_mode_concrete_camera_returns_empty`` —
        with ``--ground_truth`` the CLI expects sensorId to be a
        concrete camera; a BEV group is "wrong kind" and must be
        skipped (no silent fall-through to fan-out).
        """
        flat = {"Camera_01": {}, "Camera_02": {}}
        groups = {"bev-sensor-1": ["Camera_01", "Camera_02"]}
        assert _CLI._resolve_targets(
            "bev-sensor-1", flat, groups, ground_truth=True,
        ) == []

    def test_gt_mode_unknown_sensor_returns_empty(self):
        flat = {"Camera_01": {}}
        groups = {"bev-sensor-1": ["Camera_01"]}
        assert _CLI._resolve_targets(
            "Camera_XX", flat, groups, ground_truth=True,
        ) == []

    def test_gt_mode_ignores_group_map(self):
        """GT mode should not even need the group map.

        Passing an empty groups dict alongside a populated flat calib
        must still resolve concrete cameras — proving the resolver
        consults *only* ``calib_dict`` in GT mode (matches the CLI's
        decision to suppress the BEV-groups log line in that mode).
        """
        flat = {"Camera_05": {}}
        assert _CLI._resolve_targets(
            "Camera_05", flat, {}, ground_truth=True,
        ) == ["Camera_05"]

    # ---- Common across both modes ----------------------------------

    def test_empty_inputs_return_empty_list_in_both_modes(self):
        assert _CLI._resolve_targets("anything", {}, {}) == []
        assert _CLI._resolve_targets(
            "anything", {}, {}, ground_truth=True,
        ) == []


# =====================================================================
# Tests for _resolve_cam_frame_path
# =====================================================================

class TestResolveCamFramePath:
    """End-to-end tests for the (row × cam) image-path orchestrator.

    Three timestamp branches dispatch in priority order:

    * **info[cam] exact** — per-camera nominal timestamp wins when the
      row's ``info`` map lists the target camera.
    * **info bracket scan** — when the camera is missing from
      ``info``, scan the cam dir for an embedded timestamp in
      ``[min, max]`` of the rest of the cluster.
    * **row timestamp** — when ``info`` is empty / missing, fall back
      to the row's outer ``timestamp`` substring match.

    Each branch falls through to the canonical patterns inside
    :func:`spatialai_data_utils.datasets.frame_paths.resolve_frame_path`
    when its primary lookup yields no hit (so unit tests must touch
    fixture files in flat ``<image_dir>/<cam>/<basename>`` layouts so
    the canonical-pattern branch finds them when needed).
    """

    def _touch(self, *parts):
        path = Path(*parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        return str(path)

    # ---- info[cam] branch -----------------------------------------

    def test_info_cam_exact_match(self, tmp_path):
        """``info[cam]`` exact hit short-circuits before the range scan."""
        match = self._touch(
            str(tmp_path), "Camera_03",
            "006_2025-04-14T00-36-45.129Z.jpg",
        )
        info = {
            "Camera_01": "2025-04-14T00:36:45.109Z",
            "Camera_03": "2025-04-14T00:36:45.129Z",
        }
        assert _CLI._resolve_cam_frame_path(
            str(tmp_path), "Camera_03", 6,
            info=info, row_timestamp="2025-04-14T00:36:45.109Z",
        ) == match

    def test_info_cam_no_dir_falls_to_canonical(self, tmp_path):
        """When info[cam] doesn't match anything, canonical patterns kick in."""
        canonical = self._touch(
            str(tmp_path), "Camera_03", "rgb", "rgb_00006.jpg",
        )
        info = {"Camera_03": "1999-99-99T99-99-99.999Z"}
        assert _CLI._resolve_cam_frame_path(
            str(tmp_path), "Camera_03", 6,
            info=info, row_timestamp="anything",
        ) == canonical

    # ---- info bracket-scan branch ---------------------------------

    def test_info_missing_cam_bracket_scan_picks_in_range(self, tmp_path):
        """Missing-from-info cam → bracket scan over rest-of-cluster."""
        match = self._touch(
            str(tmp_path), "Camera_05",
            "006_2025-04-14T00-36-45.180Z.jpg",
        )
        info = {
            "Camera_01": "2025-04-14T00:36:45.109Z",
            "Camera_02": "2025-04-14T00:36:45.209Z",
        }
        # Camera_05 isn't in info; its file's embedded timestamp falls
        # within [Camera_01, Camera_02], so the bracket scan should
        # find it.
        assert _CLI._resolve_cam_frame_path(
            str(tmp_path), "Camera_05", 6,
            info=info, row_timestamp=None,
        ) == match

    def test_info_missing_cam_out_of_range_falls_to_canonical(self, tmp_path):
        """Bracket scan miss → canonical pattern fallback."""
        canonical = self._touch(
            str(tmp_path), "Camera_05", "rgb", "rgb_00006.jpg",
        )
        # Decoy: file whose timestamp is OUTSIDE the cluster window.
        self._touch(
            str(tmp_path), "Camera_05",
            "006_2025-04-14T00-36-46.500Z.jpg",
        )
        info = {
            "Camera_01": "2025-04-14T00:36:45.109Z",
            "Camera_02": "2025-04-14T00:36:45.209Z",
        }
        assert _CLI._resolve_cam_frame_path(
            str(tmp_path), "Camera_05", 6,
            info=info, row_timestamp=None,
        ) == canonical

    def test_info_with_only_falsy_values_falls_to_canonical(self, tmp_path):
        """``info = {cam: None}`` carries no usable timestamps → canonical."""
        canonical = self._touch(
            str(tmp_path), "Camera_05", "rgb", "rgb_00006.jpg",
        )
        info = {"Camera_01": None, "Camera_02": ""}
        assert _CLI._resolve_cam_frame_path(
            str(tmp_path), "Camera_05", 6,
            info=info, row_timestamp=None,
        ) == canonical

    # ---- row-timestamp branch -------------------------------------

    def test_no_info_uses_row_timestamp(self, tmp_path):
        """``info={}``: row timestamp drives the substring match."""
        match = self._touch(
            str(tmp_path), "Camera_08",
            "006_2025-04-14T00-36-45.009Z.jpg",
        )
        assert _CLI._resolve_cam_frame_path(
            str(tmp_path), "Camera_08", 6,
            info={}, row_timestamp="2025-04-14T00:36:45.009Z",
        ) == match

    def test_no_info_no_row_ts_falls_to_canonical(self, tmp_path):
        """Both info and row_timestamp absent → canonical pattern only."""
        canonical = self._touch(
            str(tmp_path), "Camera_08", "rgb", "rgb_00006.jpg",
        )
        assert _CLI._resolve_cam_frame_path(
            str(tmp_path), "Camera_08", 6,
            info={}, row_timestamp=None,
        ) == canonical

    def test_no_info_no_row_ts_no_canonical_returns_none(self, tmp_path):
        """No timestamp lookup AND no canonical match → None."""
        # No fixtures touched on disk.
        assert _CLI._resolve_cam_frame_path(
            str(tmp_path), "Camera_08", 6,
            info={}, row_timestamp=None,
        ) is None

    # ---- Branch precedence ----------------------------------------

    def test_info_cam_exact_beats_canonical(self, tmp_path):
        """``info[cam]`` substring hit wins over a canonical pattern file."""
        # Canonical layout (would normally win without timestamp dispatch)
        self._touch(
            str(tmp_path), "Camera_03", "rgb", "rgb_00006.jpg",
        )
        # Per-cam info match — under the cam folder, dashed.
        ts_match = self._touch(
            str(tmp_path), "Camera_03",
            "006_2025-04-14T00-36-45.129Z.jpg",
        )
        info = {"Camera_03": "2025-04-14T00:36:45.129Z"}
        assert _CLI._resolve_cam_frame_path(
            str(tmp_path), "Camera_03", 6,
            info=info, row_timestamp=None,
        ) == ts_match

    def test_info_present_cam_missing_falls_to_row_ts_window(self, tmp_path):
        """``info`` present but cam missing AND bracket scan misses:
        row_timestamp drives the per-target three-tier lookup.

        Replaces the legacy "skip row_timestamp branch when info
        present" rule — the new feature explicitly uses row_timestamp
        as the per-camera anchor for the nearest-within-500 ms window
        recovery when the info-derived bracket scan didn't match.

        Setup mirrors the legacy test but the assertion captures the
        new behaviour: the row-timestamp-anchored substring match
        wins because it's tier 1 of the per-target flow.
        """
        # File that the row-timestamp branch's substring tier should find.
        match = self._touch(
            str(tmp_path), "Camera_05",
            "006_2025-04-14T00-36-45.999Z.jpg",  # outside the info bracket range
        )
        # Decoy canonical file — should NOT be returned because the
        # per-target flow's tier 1 substring match hits first.
        self._touch(str(tmp_path), "Camera_05", "rgb", "rgb_00006.jpg")
        info = {
            "Camera_01": "2025-04-14T00:36:45.109Z",
            "Camera_02": "2025-04-14T00:36:45.209Z",
        }
        assert _CLI._resolve_cam_frame_path(
            str(tmp_path), "Camera_05", 6,
            info=info, row_timestamp="2025-04-14T00:36:45.999Z",
        ) == match

    # ---- Nearest-within-window (500 ms) feature ----

    def test_info_cam_close_miss_within_window_recovers(self, tmp_path):
        """``info[cam]`` exact substring miss BUT file is 1 ms away:
        nearest-within-500ms tier returns it.

        Common case: row publishes ``info[Camera_03] = "...45.109Z"``
        but the actual capture is ``"...45.110Z"`` (1 ms late).
        Tier 1 substring fails (different sub-second), tier 2 nearest
        catches it — no skip.
        """
        match = self._touch(
            str(tmp_path), "Camera_03",
            "006_2025-04-14T00-36-45.110Z.jpg",  # 1 ms after info[Camera_03]
        )
        info = {"Camera_03": "2025-04-14T00:36:45.109Z"}
        assert _CLI._resolve_cam_frame_path(
            str(tmp_path), "Camera_03", 6,
            info=info, row_timestamp=None,
        ) == match

    def test_info_cam_far_miss_outside_window_strict_skips(self, tmp_path):
        """``info[cam]`` miss AND file is 600 ms away (> 500 ms window):
        strict skip (return None) because the camera folder DOES have
        a timestamp-encoded file (just not within the window).

        This is the exact "skip this camera" rule the user requested:
        no canonical-pattern guess when timestamps are available.
        """
        # 600 ms after info[cam] — outside the 500 ms window.
        self._touch(
            str(tmp_path), "Camera_03",
            "006_2025-04-14T00-36-45.709Z.jpg",
        )
        # Canonical file present but should NOT be returned (strict skip).
        self._touch(str(tmp_path), "Camera_03", "rgb", "rgb_00006.jpg")
        info = {"Camera_03": "2025-04-14T00:36:45.109Z"}
        assert _CLI._resolve_cam_frame_path(
            str(tmp_path), "Camera_03", 6,
            info=info, row_timestamp=None,
        ) is None

    def test_info_cam_no_ts_files_falls_through_to_canonical(self, tmp_path):
        """Legacy datasets without embedded timestamps still fall back
        to canonical patterns even when ``info[cam]`` is published.

        Critical for backward compatibility: pre-existing scenes like
        scene_001 uses ``rgb/rgb_NNNNN.jpg`` filenames that carry no
        embedded ISO timestamps.  An ``info`` row published for such
        a scene must not strict-skip every camera — the camera
        folder's emptiness of timestamp-encoded files signals
        ``cam_dir_has_ts_encoded_frame=False`` and the canonical
        fallback kicks in.
        """
        canonical = self._touch(
            str(tmp_path), "Camera_03", "rgb", "rgb_00006.jpg",
        )
        info = {"Camera_03": "2025-04-14T00:36:45.109Z"}
        assert _CLI._resolve_cam_frame_path(
            str(tmp_path), "Camera_03", 6,
            info=info, row_timestamp=None,
        ) == canonical

    def test_no_info_close_miss_within_window_recovers(self, tmp_path):
        """No-info branch: row_timestamp's substring miss recovered by
        nearest-within-500ms (file is 1 ms late)."""
        match = self._touch(
            str(tmp_path), "Camera_03",
            "006_2025-04-14T00-36-45.110Z.jpg",
        )
        assert _CLI._resolve_cam_frame_path(
            str(tmp_path), "Camera_03", 6,
            info={}, row_timestamp="2025-04-14T00:36:45.109Z",
        ) == match

    def test_no_info_far_miss_outside_window_strict_skips(self, tmp_path):
        """No-info branch: row_timestamp 600 ms away → strict skip."""
        self._touch(
            str(tmp_path), "Camera_03",
            "006_2025-04-14T00-36-45.709Z.jpg",
        )
        # Canonical file present but skipped (timestamp-encoded dataset).
        self._touch(str(tmp_path), "Camera_03", "rgb", "rgb_00006.jpg")
        assert _CLI._resolve_cam_frame_path(
            str(tmp_path), "Camera_03", 6,
            info={}, row_timestamp="2025-04-14T00:36:45.109Z",
        ) is None

    def test_info_cam_missing_bracket_miss_within_row_window(self, tmp_path):
        """``info`` present but cam missing AND bracket scan misses
        (file outside cluster window): row_timestamp's nearest-within-
        500ms still recovers.

        Files:
          * info cluster window:  [.109Z, .209Z] (Camera_01, Camera_02)
          * row_timestamp:        .500Z
          * Camera_05's only file: .501Z (1 ms past row ts; outside
                                          cluster bracket)
        Branch 2 bracket scan misses (file is past .209Z); the new
        per-target row-ts flow catches it via tier 2 (1 ms ≤ 500 ms).
        """
        match = self._touch(
            str(tmp_path), "Camera_05",
            "006_2025-04-14T00-36-45.501Z.jpg",
        )
        info = {
            "Camera_01": "2025-04-14T00:36:45.109Z",
            "Camera_02": "2025-04-14T00:36:45.209Z",
        }
        assert _CLI._resolve_cam_frame_path(
            str(tmp_path), "Camera_05", 6,
            info=info, row_timestamp="2025-04-14T00:36:45.500Z",
        ) == match

    # ---- Mixed sub-second precision (info-values sort regression) ----

    def test_canonical_output_basename_uses_info_cam_with_colons_normalised(self):
        """``info[cam]`` drives the output basename; ``:`` → ``-`` so
        the filename is filesystem-safe; extension is always ``.png``."""
        out = _CLI._canonical_output_basename(
            42, "2025-04-14T00:36:45.109Z", "/whatever/path/source.jpg",
        )
        assert out == "42_2025-04-14T00-36-45.109Z.png"

    def test_canonical_output_basename_uses_png_for_png_source_too(self):
        """``.png`` source → ``.png`` output (no surprise — same ext)."""
        out = _CLI._canonical_output_basename(
            7, "2025-04-14T00:36:45.109Z", "/foo/img.png",
        )
        assert out == "7_2025-04-14T00-36-45.109Z.png"

    def test_canonical_output_basename_uses_png_for_jpg_source(self):
        """``.jpg`` source → ``.png`` output (PNG is the canonical
        default — keeps wireframes lossless on top of an already-
        compressed JPEG source)."""
        out = _CLI._canonical_output_basename(
            7, "2025-04-14T00:36:45.109Z", "/foo/img.jpg",
        )
        assert out == "7_2025-04-14T00-36-45.109Z.png"

    def test_canonical_output_basename_uses_png_when_source_has_no_extension(self):
        """A source path with no extension still produces a ``.png`` output."""
        out = _CLI._canonical_output_basename(
            7, "2025-04-14T00:36:45.109Z", "/foo/img_no_ext",
        )
        assert out == "7_2025-04-14T00-36-45.109Z.png"

    def test_canonical_output_basename_falls_back_to_source_name_when_no_ts(self):
        """No display_ts → source basename verbatim, including its
        original extension (legacy behaviour for frame-id-only datasets)."""
        out = _CLI._canonical_output_basename(
            7, None, "/foo/rgb_00007.jpg",
        )
        assert out == "rgb_00007.jpg"

    def test_canonical_output_basename_empty_ts_treated_as_none(self):
        """Empty-string display_ts → source basename (same as None)."""
        out = _CLI._canonical_output_basename(
            7, "", "/foo/rgb_00007.jpg",
        )
        assert out == "rgb_00007.jpg"

    def test_mixed_subsec_info_sorts_chronologically(self, tmp_path):
        """``info`` with mixed sub-second precision sorts in chronological
        order so the bracket scan's ``[ts_min, ts_max]`` is non-empty.

        Pre-fix, ``sorted([".1Z", ".150Z"])`` returned
        ``[".150Z", ".1Z"]`` (lex; ``Z`` > ``0``-``9``), inverting the
        bounds and silently returning ``None`` for every member camera
        not in ``info``.  After the
        :func:`~spatialai_data_utils.datasets.frame_paths._normalize_subsec_precision`
        wrap, both timestamps pad to ``".100000000Z"`` /
        ``".150000000Z"`` and sort chronologically.

        File ``Camera_05/...110Z.jpg`` (110 ms) sits inside the
        chrono-correct range ``[100 ms, 150 ms]`` but **outside** the
        bug's inverted ``[150 ms, 100 ms]`` range — so a passing
        result here proves the sort fix lands.
        """
        match = self._touch(
            str(tmp_path), "Camera_05",
            "006_2025-04-14T00-36-45.110Z.jpg",
        )
        info = {
            # Mixed precision: short form (100 ms) and long form (150 ms).
            "Camera_01": "2025-04-14T00:36:45.1Z",
            "Camera_02": "2025-04-14T00:36:45.150Z",
        }
        assert _CLI._resolve_cam_frame_path(
            str(tmp_path), "Camera_05", 6,
            info=info, row_timestamp=None,
        ) == match
