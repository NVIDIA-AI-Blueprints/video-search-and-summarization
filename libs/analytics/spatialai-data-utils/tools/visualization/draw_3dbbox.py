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

"""
NVSchema 3D Bounding Box Visualization Tool

Minimal CLI that drives the stage-1 (project) and stage-2 (draw)
helpers end-to-end on an NVSchema JSON-lines model-results file.  The
CLI processes **one input row at a time**: each line carries its own
``sensorId``, ``timestamp``, ``id`` (frame id) and object list, so
each row resolves independently to one or more target cameras and
gets projected / drawn only for that row's target(s).

Required paths:

* ``--input_data_path``  : JSON-lines NVSchema model-results file.
                           Each line is expected to carry at least
                           ``{"id", "sensorId", "objects"}`` and
                           optionally ``"timestamp"``.
* ``--output_data_path`` : directory where annotated images are
                           written (created if missing).
* ``--calib_path``       : scene calibration JSON.  May be grouped
                           (contains BEV sensor groups named
                           ``bev-sensor-*``) or flat.
* ``--image_dir``        : directory containing one sub-folder per
                           **concrete** camera (never per BEV group).
                           See below for per-row lookup rules.

``sensorId`` resolution
-----------------------

The CLI runs in one of two modes — picked by ``--ground_truth`` —
and each mode interprets the row's ``sensorId`` field one way only
(no auto-detection / fallback chain):

* **Default / model-output mode** (``--ground_truth`` *not* set):
  ``sensorId`` is expected to name a **BEV sensor group**
  (e.g. ``bev-sensor-1``, ``bev-sensor-2``, … — matches a group
  name in the calibration's ``sensors[*].group.name``).  The row's
  detections are projected onto **every** member camera of the
  group, each producing its own output image.  The group→member
  mapping is taken verbatim from the calibration JSON.
* **Ground-truth mode** (``--ground_truth``): ``sensorId`` is
  expected to name a **concrete camera** (matches a key in the
  flattened ``{cam: calib}`` dict).  The row is projected onto
  exactly that one camera.  Use this for ground-truth NVSchema
  exports where every annotation is already attributed to the
  observing camera that saw it.

In both modes, rows whose ``sensorId`` doesn't match the expected
kind (or whose calibration entry is missing) are skipped quietly
and counted in ``skipped_sensor_not_in_calib`` at the end of the
run.

Image path resolution per (row, target_camera)
-----------------------------------------------

For each resolved target camera ``cam_name`` the CLI looks under
``<image_dir>/<cam_name>/`` (concrete camera folder — never the BEV
group id).  The row's optional ``info`` map (``{cam_name:
ISO_timestamp}``) drives the per-camera lookup priority, with a
**±200 ms tolerance window** absorbing sub-millisecond acquisition
skew between the row's nominal timestamps and the actually-captured
frames.  Orchestration lives in :func:`_resolve_cam_frame_path`
(CLI-private; binds NVSchema ``info``-map semantics) and delegates
the per-target three-tier lookup to the library helper
:func:`spatialai_data_utils.datasets.frame_paths.resolve_frame_path_with_window`.

When ``info[cam_name]`` (or, in its absence, the row's outer
``timestamp``) is set, ``resolve_frame_path_with_window`` runs:

  T1. **Substring match** via
      :func:`spatialai_data_utils.datasets.frame_paths.resolve_frame_path`
      (``canonical_fallback=False``).  Returns the file whose
      basename contains the target timestamp (``:`` ↔ ``-`` and
      sub-second-width normalisation on both sides) — e.g.
      ``"2025-04-14T00:36:45.109Z"`` matches
      ``"42_2025-04-14T00-36-45.109Z.jpg"``.
  T2. **Nearest within ±200 ms** via
      :func:`spatialai_data_utils.datasets.frame_paths.find_nearest_frame_path`.
      Recovers from sub-millisecond skew: when the row publishes
      ``info[Camera_01] = "...45.109Z"`` but the on-disk file is
      ``"...45.110Z"`` (1 ms late), the substring tier misses but
      this tier picks ``45.110Z`` (``|delta| = 1 ms ≤ 200 ms``).
  T3. **Strict skip vs canonical fallback** —
      :func:`spatialai_data_utils.datasets.frame_paths.cam_dir_has_ts_encoded_frame`
      disambiguates: (a) the camera folder contains at least one
      timestamp-encoded image → return ``None`` (``skipped_image_
      not_found`` — the dataset is timestamp-driven and there's
      genuinely no in-window frame, so we'd rather skip than
      render onto a frame from a different instant);
      (b) the folder has no timestamp-encoded images (legacy
      frame-id-keyed dataset) → fall through to
      ``resolve_frame_path`` with no timestamp, which delegates to
      the canonical filename patterns (``rgb/``, ``images/``,
      scout, ``frames/``, bare ``<id>.<ext>`` — see
      ``utils/camera_name_utils.py:_build_non_h5_frame_patterns``).
      ``<id>`` is the integer row id used as-is (not zero-padded).

The three top-level branches in :func:`_resolve_cam_frame_path` map
to the three NVSchema ``info`` shapes:

1. **``info[cam_name]`` present** — call
   :func:`resolve_frame_path_with_window` anchored on
   ``info[cam_name]`` (per-camera nominal timestamp).
2. **``info`` non-empty but ``cam_name`` missing** — bracket scan via
   :func:`find_frame_path_in_ts_range` over
   ``[min(info.values()), max(info.values())]`` first; on miss,
   call :func:`resolve_frame_path_with_window` anchored on the row's
   outer ``timestamp`` (the same ±200 ms recovery applies); when
   ``timestamp`` is also absent, fall through to canonical patterns
   directly.  Used for member cameras that drop out of the row's
   ``info`` map.
3. **``info`` empty / missing** — call
   :func:`resolve_frame_path_with_window` anchored on the row's
   outer ``timestamp``; when ``timestamp`` is also missing, fall
   through to canonical patterns directly (legacy no-timestamp
   behaviour).

Net effect: when timestamps are available, the lookup is strictly
timestamp-driven (with the 200 ms recovery window); only legacy
canonical-pattern datasets ever take the canonical fallback when
timestamps are present.  When no timestamps are available the
canonical fallback applies straight away.

The camera-tag overlay drawn on the rendered image uses the same
timestamp source: ``info[cam_name]`` when present, else the row's
outer ``timestamp``.

Optional knobs:

* ``--recentering``       : apply group-origin recentering to the
                            calibration.  Use when the input row's
                            box coords come from a model trained
                            with recentered targets.  **Do NOT
                            combine with** ``--ground_truth`` — GT
                            NVSchema rows are exported in world
                            frame, so recentering the calibration
                            misaligns the projection.
* ``--line_thickness``    : wireframe line thickness in pixels
                            (default 2).
* ``--no_shade_heading``  : disable the semi-transparent heading-face
                            shading.
* ``--object_class_tag``    : object-class config name (e.g.
                            ``warehouse``) or path to a ``.py`` config
                            file.  Drives both (1) **class filtering** —
                            boxes whose ``type`` isn't recognised by the
                            config are dropped before drawing — and
                            (2) **display-name remap** for the kept
                            boxes' text labels.  Defaults to
                            ``warehouse``; pass ``none`` to disable
                            both (every box drawn with its raw ``type``).
* ``--color_by``          : auto-coloring mode — ``class``
                            (default for this CLI: one colour per
                            ``type``, so every Person/Transporter/…
                            renders in the same colour) or
                            ``track_id`` (one colour per NVSchema
                            ``id``).  Class-mode walks ``COLOR_MAP``
                            in FIFO order: the first class
                            encountered claims slot 0, the next
                            slot 1, and so on — so 2–5 classes get
                            maximally-separated colours instead of
                            risking CRC collisions mod 50.  Binding
                            is **per-call** (per-frame × per-camera
                            in this CLI), so the same class may
                            land on different slots across frames
                            if the data order differs.  The general
                            ``draw_3dbbox_batch.py`` CLI defaults to
                            ``track_id`` because its gt_json_aicity / pkl
                            modes typically show per-track
                            annotations.
* ``--ground_truth``      : interpret each row's ``sensorId`` as a
                            concrete camera name instead of a BEV
                            sensor-group name (default).  Required
                            when feeding ground-truth NVSchema
                            files where annotations are recorded
                            against single observing cameras.

Output layout: annotated images land directly under
``<output_data_path>/<cam_name>/<source_image_name>`` — always keyed
by the *concrete* camera, not the row's ``sensorId``.  So a row with
``sensorId: "bev-sensor-1"`` that resolves to four member cameras
produces four output images under four camera sub-folders; a row
with ``sensorId: "Camera_08"`` produces one output under
``Camera_08/``.  The NVSchema file's location / name is not used to
carve an extra per-scene subdirectory, and the output filename is
the source image filename unchanged.

Scope: one input row × one target camera → one rendered image.  For
batch jobs that need frame caps, confidence filtering, H5-backed
image sources, or the gt_json_aicity / sparse4d-pkl input modes, use
``tools/visualization/draw_3dbbox_batch.py`` instead (or call
:func:`spatialai_data_utils.visualization.visualize_nvschema`
directly).

Example usage::

  # Bare minimum
  python tools/visualization/draw_3dbbox.py \\
      --input_data_path  results/scene_001.jsonl \\
      --output_data_path output/viz \\
      --calib_path       data/mtmc/Scene/calibration.json \\
      --image_dir        data/mtmc/Scene/images

  # With styling + calibration recentering
  python tools/visualization/draw_3dbbox.py \\
      --input_data_path  results/scene_001.jsonl \\
      --output_data_path output/viz \\
      --calib_path       data/mtmc/Scene/calibration_clustered.json \\
      --image_dir        data/mtmc/Scene/images \\
      --recentering \\
      --line_thickness 3 \\
      --object_class_tag warehouse

  # Ground-truth NVSchema (sensorId is a concrete camera, not a BEV group)
  python tools/visualization/draw_3dbbox.py \\
      --input_data_path  data/mtmc/Scene/ground_truth.jsonl \\
      --output_data_path output/viz_gt \\
      --calib_path       data/mtmc/Scene/calibration.json \\
      --image_dir        data/mtmc/Scene/images \\
      --ground_truth
"""

import argparse
import logging
import os
from typing import Any, Dict, List, Optional

import tqdm

from spatialai_data_utils.core.geometry.projection import (
    project_bev_objects_bbox_in_image,
)
from spatialai_data_utils.loaders.calibration import (
    load_calib_into_dict_with_group_memberships,
)
from spatialai_data_utils.loaders.nvschema import iter_frame_rows
from spatialai_data_utils.datasets.frame_paths import (
    _normalize_subsec_precision,
    _ts_to_fs_safe,
    find_frame_path_in_ts_range,
    resolve_frame_path,
    resolve_frame_path_with_window,
)
from spatialai_data_utils.visualization import (
    draw_bev_objects_bbox_in_image,
    draw_camera_tag,
    load_image,
    save_viz,
)

logger = logging.getLogger(__name__)

# Tolerance window applied to the per-camera nominal timestamp when
# the exact-match substring lookup misses.  Absorbs sub-millisecond
# acquisition skew between the row's nominal timestamp (from
# ``info[cam_name]`` or the row-level ``timestamp``) and the frame
# the camera actually captured.  When the nearest file's delta
# exceeds this window the camera is **skipped** (``return None``)
# rather than silently rendering on a frame from a different instant.
_NEAREST_FRAME_WINDOW_MS = 200


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments for the NVSchema visualization tool."""
    parser = argparse.ArgumentParser(
        description="Visualize NVSchema 3D bounding-box results on camera images",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--input_data_path", type=str, required=True,
        help="Path to the NVSchema JSON-lines model results file",
    )
    parser.add_argument(
        "--output_data_path", type=str, required=True,
        help="Directory where annotated images are written",
    )
    parser.add_argument(
        "--calib_path", type=str, required=True,
        help="Path to the scene's calibration JSON file",
    )
    parser.add_argument(
        "--image_dir", type=str, required=True,
        help="Directory with one sub-folder per **concrete** camera "
             "(BEV-sensor-group rows fan out to the group's member "
             "cameras).  Per-(row × cam) lookup honours the row's "
             "``info`` map first when present (per-camera nominal "
             "timestamps with bracket-scan fallback for cameras "
             "missing from ``info``), then the row's outer "
             "``timestamp`` for rows without an ``info`` map, then "
             "the canonical patterns (rgb/, images/, scout, bare "
             "<id>.<ext>).  ``:`` in input timestamps is normalised "
             "to ``-`` so JSON-form ISO strings match the dashed "
             "filesystem form filenames typically use.",
    )
    parser.add_argument(
        "--recentering", action="store_true",
        help="Apply group-origin recentering to the calibration. "
             "Pass this when the input row's bbox3d.coordinates were "
             "produced by a model trained with recentered targets.  "
             "DO NOT pass this with --ground_truth: GT NVSchema rows "
             "are exported in world frame, so recentering the "
             "calibration shifts the projection into a frame the "
             "boxes don't live in (the symptom is wireframes drifting "
             "off the visible scene objects).",
    )
    parser.add_argument(
        "--line_thickness", type=int, default=2,
        help="Wireframe line thickness in pixels (default: 2)",
    )
    parser.add_argument(
        "--no_shade_heading", action="store_true",
        help="Disable the semi-transparent heading-face shading",
    )
    parser.add_argument(
        "--object_class_tag", type=str, default="warehouse",
        help="Object-class config name (built-in, e.g. 'warehouse', "
             "'default', 'scout') or path to a .py config file.  "
             "Drives both (1) class filtering — boxes whose 'type' "
             "isn't recognised by the config are dropped — and "
             "(2) display-name remap for the kept boxes' labels.  "
             "Pass 'none' to disable both (every box drawn with its "
             "raw 'type' string).  Default: 'warehouse'.",
    )
    parser.add_argument(
        "--color_by", type=str, default="class",
        choices=("track_id", "class"),
        help="Auto-coloring mode for the wireframes: 'class' "
             "(default for this CLI) walks the palette in FIFO "
             "order — each new class seen in a frame claims the "
             "next COLOR_MAP slot, so every Person/Transporter/… "
             "renders in the same colour; 'track_id' cycles "
             "COLOR_MAP by NVSchema ``id`` (each track gets its "
             "own colour, the default for "
             "``draw_3dbbox_batch.py``).",
    )
    parser.add_argument(
        "--ground_truth", action="store_true",
        help="Treat each input row's ``sensorId`` as a CONCRETE "
             "camera name (default behaviour treats it as a BEV "
             "sensor-group name and fans out across the group's "
             "member cameras).  Use this for ground-truth NVSchema "
             "files where each annotation is recorded against a "
             "single observing camera.",
    )
    return parser.parse_args()


_CANONICAL_OUTPUT_EXT = ".png"


def _canonical_output_basename(
    frame_id: int, display_ts: Optional[str], source_path: str,
) -> str:
    """Build a consistent ``<frame_id>_<ts>.png`` output basename.

    Every rendered image is written under
    ``<output_data_path>/<cam_name>/`` with a basename that derives
    from the row's data — *not* the source filename — so the output
    layout is uniform regardless of which lookup tier
    (:func:`resolve_frame_path_with_window`'s T1 / T2, the bracket
    scan, or the canonical-pattern fallback) actually picked the
    source file.

    Naming rules:

    * ``frame_id`` is taken straight from the row (no zero-padding).
    * ``ts`` is the row's nominal timestamp for *this* camera —
      ``info[cam_name]`` when it exists, else the row-level
      ``timestamp``.  Both forms are run through
      :func:`_ts_to_fs_safe` so the resulting basename is filesystem-
      safe (``:`` is illegal on FAT / Windows / portable ext4).
    * Extension is **always** :data:`_CANONICAL_OUTPUT_EXT` (``.png``)
      regardless of the source file's extension.  PNG is lossless,
      so wireframe edges drawn by stage-2 don't accumulate JPEG
      compression artefacts on top of the source's existing
      compression — important for visual review of the rendered
      output.

    When neither ``info[cam_name]`` nor the row-level ``timestamp``
    is available (the legacy no-timestamp dataset case), the source
    basename is preserved verbatim — matches the pre-feature
    behaviour for purely frame-id-keyed datasets.

    :param frame_id: Integer row id.
    :type frame_id: int
    :param display_ts: ISO timestamp to embed in the output basename.
        Pass ``info[cam_name] or row_timestamp``; ``None`` falls back
        to the source basename.
    :type display_ts: str or None
    :param source_path: Filesystem path of the file the renderer
        loaded.  Used as the legacy fallback basename when
        *display_ts* is empty / ``None``.
    :type source_path: str
    :return: Output basename (no directory).
    :rtype: str
    """
    if not display_ts:
        return os.path.basename(source_path)
    return (
        f"{frame_id}_{_ts_to_fs_safe(display_ts)}{_CANONICAL_OUTPUT_EXT}"
    )


def _resolve_cam_frame_path(
    image_dir: str,
    cam_name: str,
    frame_id: int,
    info: Dict[str, str],
    row_timestamp: Optional[str],
) -> Optional[str]:
    """Resolve ``<image_dir>/<cam>/<frame>`` for one (row × camera) pair.

    Per-camera lookup priority — the row's ``info`` map (if present)
    takes precedence over the row's outer ``timestamp`` because it
    carries per-camera nominal timestamps that account for inter-
    camera acquisition skew within a synced BEV cluster:

    1. **``info[cam_name]`` present** — three-tier timestamp-driven
       lookup via :func:`resolve_frame_path_with_window` against
       *info[cam_name]*: substring → nearest-within-200ms →
       strict-skip-or-canonical.
    2. **``info`` bracket scan** — when *cam_name* is missing from
       ``info`` but the map carries at least one valid entry, use
       :func:`find_frame_path_in_ts_range` over
       ``[min(info.values()), max(info.values())]``.  Used for
       cameras that drop out of the row's per-camera timestamp map
       but whose physical acquisition is presumed to fall within the
       rest-of-cluster time window.  When the bracket scan misses,
       fall through to the per-target-ts three-tier lookup against
       *row_timestamp* (so the 200 ms window still applies); when
       ``row_timestamp`` is also missing, fall through to canonical
       filename patterns.
    3. **No ``info``** — three-tier timestamp-driven lookup via
       :func:`resolve_frame_path_with_window` against
       *row_timestamp*; when *row_timestamp* is also missing, fall
       through to canonical filename patterns directly.

    Returns ``None`` when (a) the camera folder is timestamp-encoded
    but no in-window frame matches the per-camera nominal (strict
    skip), or (b) the canonical-pattern fallback also misses.  The
    caller counts both as ``skipped_image_not_found``.

    :param image_dir: Directory holding one sub-folder per concrete
        camera.
    :type image_dir: str
    :param cam_name: Concrete camera name (sub-folder under
        *image_dir*).
    :type cam_name: str
    :param frame_id: Integer frame id used by the canonical-pattern
        fallback.
    :type frame_id: int
    :param info: ``{cam_name: ISO_timestamp}`` map taken straight from
        the NVSchema row's ``info`` field.  Empty dict is fine and
        forces branch 3.
    :type info: dict[str, str]
    :param row_timestamp: Row-level ``timestamp`` field (the fallback
        anchor when ``info`` is empty or missing this camera).
        ``None`` disables the timestamp-driven branches entirely.
    :type row_timestamp: str or None
    :return: Absolute filesystem path of the resolved image, or
        ``None`` when neither timestamp lookup nor the canonical
        patterns find a file.
    :rtype: str or None
    """
    cam_ts = info.get(cam_name) if info else None
    if cam_ts:
        return resolve_frame_path_with_window(
            image_dir, cam_name, frame_id, cam_ts,
            window_ms=_NEAREST_FRAME_WINDOW_MS,
        )
    if info:
        # Sub-second pad both transforms here is load-bearing — without
        # it ``sorted([".1Z", ".150Z"])`` returns ``[".150Z", ".1Z"]``
        # (lex; ``Z`` > ``0``-``9``), inverting ts_min/ts_max and
        # silently disabling the bracket scan for any cluster whose
        # ``info`` map carries mixed sub-second precision.
        ts_values = sorted(
            _normalize_subsec_precision(_ts_to_fs_safe(v))
            for v in info.values() if v
        )
        if ts_values:
            match = find_frame_path_in_ts_range(
                image_dir, cam_name, ts_values[0], ts_values[-1],
            )
            if match is not None:
                return match
        if row_timestamp:
            return resolve_frame_path_with_window(
                image_dir, cam_name, frame_id, row_timestamp,
                window_ms=_NEAREST_FRAME_WINDOW_MS,
            )
        return resolve_frame_path(image_dir, cam_name, frame_id)
    if row_timestamp:
        return resolve_frame_path_with_window(
            image_dir, cam_name, frame_id, row_timestamp,
            window_ms=_NEAREST_FRAME_WINDOW_MS,
        )
    return resolve_frame_path(image_dir, cam_name, frame_id)


def _resolve_targets(
    sensor_id: str,
    calib_dict: Dict[str, Dict[str, Any]],
    cams_by_group: Dict[str, List[str]],
    ground_truth: bool = False,
) -> List[str]:
    """Map a row's ``sensorId`` to one or more concrete target cameras.

    Behaviour is mode-driven (kept simple — one lookup per call):

    * **Ground-truth mode** (``ground_truth=True``): *sensor_id* is
      expected to name a concrete camera.  Returns ``[sensor_id]``
      when it's a key of *calib_dict*, else ``[]`` (no fallback to
      group resolution).
    * **Default / model-output mode** (``ground_truth=False``):
      *sensor_id* is expected to name a BEV sensor group.  Returns
      the group's member cameras (in calibration-declared order)
      when it's a key of *cams_by_group*, else ``[]``.

    Empty list → caller treats the row as
    ``skipped_sensor_not_in_calib``.

    :param sensor_id: Raw ``sensorId`` field from the NVSchema row.
    :type sensor_id: str
    :param calib_dict: ``{cam_name: calib_info}`` flat calibration dict.
    :type calib_dict: dict[str, dict]
    :param cams_by_group: ``{bev_sensor_id: [cam_names]}`` BEV-group
        membership map (empty for ungrouped calibrations).
    :type cams_by_group: dict[str, list[str]]
    :param ground_truth: Select GT-mode (concrete camera) vs default
        BEV-group mode.
    :type ground_truth: bool
    :return: One or more target camera names (BEV fan-out yields
        multiple), or ``[]`` if the row's sensor isn't recognised in
        the chosen mode.
    :rtype: list[str]
    """
    if ground_truth:
        return [sensor_id] if sensor_id in calib_dict else []
    return list(cams_by_group.get(sensor_id, []))


def main() -> None:
    """Parse CLI arguments and run the NVSchema visualization pipeline.

    Streams the NVSchema JSON-lines file one row at a time.  Each row
    contributes its own ``sensorId`` as the projection target (looked
    up in the calibration) and its own ``timestamp``/``id`` for
    image-path resolution; rows whose sensor is missing from the
    calibration or whose image cannot be resolved are skipped
    quietly.
    """
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    args = parse_args()

    object_class_tag = (
        args.object_class_tag
        if args.object_class_tag and args.object_class_tag.lower() != "none"
        else None
    )
    shade_heading = not args.no_shade_heading

    mode_label = "ground-truth (concrete-camera sensorId)" if args.ground_truth \
        else "model output (BEV-group sensorId)"
    logger.info("=== 3D BBox Visualization (NVSchema) ===")
    logger.info(f"  Input     : {args.input_data_path}")
    logger.info(f"  Calib     : {args.calib_path}")
    logger.info(f"  Images    : {args.image_dir}")
    logger.info(f"  Output    : {args.output_data_path}")
    logger.info(f"  Mode      : {mode_label}")
    logger.info("=========================================\n")

    calib_dict, cams_by_group = load_calib_into_dict_with_group_memberships(
        args.calib_path, recentering=args.recentering,
    )

    logger.info(f"Visualizing {args.input_data_path} ...")
    logger.info(f"  Calib sensors ({len(calib_dict)}): {sorted(calib_dict)}")
    # BEV groups only matter in non-GT mode (where sensorId names a group);
    # GT mode resolves directly against calib_dict so the group map is unused.
    if cams_by_group and not args.ground_truth:
        logger.info(
            f"  BEV groups ({len(cams_by_group)}): "
            f"{ {g: len(cs) for g, cs in cams_by_group.items()} }"
        )

    rendered = 0
    skipped_sensor = 0
    skipped_image = 0

    for row in tqdm.tqdm(
        iter_frame_rows(args.input_data_path),
        desc=f"Rows ({args.input_data_path})",
    ):
        sensor_id = row["sensor_id"]
        frame_id = row["frame_id"]
        timestamp = row["timestamp"]
        objects = row["objects"]
        info = row["info"]

        # Resolve the row's sensorId to one or more concrete target cameras
        # using the mode picked by ``--ground_truth``:
        # - GT mode: sensorId must be a concrete camera → single-target.
        # - Default mode: sensorId must be a BEV group → fan out over members.
        # Wrong-kind / unknown sensorIds yield an empty list and the row
        # is skipped (counted once, independent of group cardinality).
        target_cams = _resolve_targets(
            sensor_id, calib_dict, cams_by_group,
            ground_truth=args.ground_truth,
        )
        if not target_cams:
            skipped_sensor += 1
            continue

        for cam_name in target_cams:
            # Per-camera image lookup honours the row's ``info`` map
            # first (per-camera nominal timestamp) with a same-row
            # bracket-scan fallback for cameras missing from ``info``;
            # rows without ``info`` fall back to the row-level
            # ``timestamp`` (legacy single-timestamp behaviour).
            frame_path = _resolve_cam_frame_path(
                args.image_dir, cam_name, frame_id,
                info=info, row_timestamp=timestamp,
            )
            if frame_path is None:
                skipped_image += 1
                continue

            image = load_image(frame_path)
            if image is None:
                skipped_image += 1
                continue

            # Stage 1: project 3D boxes into image space; populates
            # ``bbox3d.info`` (sensorId + vertices) on each visible det.
            enriched_objects = project_bev_objects_bbox_in_image(
                sensor_id=cam_name,
                calib_dict=calib_dict,
                bev_objects=objects,
            )

            # Stage 2: draw the already-projected BEV objects onto the image.
            image = draw_bev_objects_bbox_in_image(
                enriched_objects,
                image,
                sensor_id=cam_name,
                thickness=args.line_thickness,
                shade_heading=shade_heading,
                object_class_tag=object_class_tag,
                color_by=args.color_by,
            )

            # Camera-tag timestamp + canonical output basename: prefer
            # the per-camera nominal timestamp from ``info`` (more
            # accurate when cameras are not perfectly synced); fall
            # back to the row-level timestamp for cameras missing from
            # ``info`` or for rows that don't carry an ``info`` map.
            # The same value drives both the in-image tag overlay and
            # the output filename so the output layout is uniform
            # regardless of which lookup tier picked the source file.
            display_ts = info.get(cam_name) or timestamp
            draw_camera_tag(image, cam_name, timestamp=display_ts)
            out_basename = _canonical_output_basename(
                frame_id, display_ts, frame_path,
            )
            # save_viz derives its output basename from
            # ``os.path.basename(frame_path)``, so passing the bare
            # canonical name is enough — no synthetic dirname needed.
            save_viz(image, args.output_data_path, cam_name, out_basename)
            rendered += 1

    logger.info(
        f"  Done: rendered={rendered}, "
        f"skipped_sensor_not_in_calib={skipped_sensor}, "
        f"skipped_image_not_found={skipped_image}"
    )
    logger.info(f"All visualizations saved to: {args.output_data_path}\n")


if __name__ == "__main__":
    main()
