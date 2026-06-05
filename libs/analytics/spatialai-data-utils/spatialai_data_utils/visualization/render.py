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
3D Bounding Box Rendering Pipeline

High-level functions for rendering 3D bounding boxes on multi-camera images.
Designed to be called programmatically (from notebooks, other scripts, or
downstream tools) without requiring CLI argument parsing.

Public entry points:

- :func:`visualize_nvschema` — **customer-facing one-shot**.  Takes a
  nvschema results file + calibration JSON + image directory, saves
  annotated images under an output directory.  No format dispatching;
  use this when you know your input is NVSchema.

- :func:`visualize_3dbbox` — **general dispatcher**.  Accepts any one
  of three result sources via keyword-only arguments:

  * ``nvschema_path`` — NVSchema JSON-lines model results.
  * ``gt_json_aicity_path``  — ground-truth JSON annotations.
  * ``data_pkl``       — sparse4d-style pkl (calib + image paths + GT
    all bundled).

- :func:`draw_bev_objects_bbox_in_image` — low-level stage-2 helper.  Takes
  the already-projected BEV objects (produced by
  :func:`spatialai_data_utils.core.geometry.projection.project_bev_objects_bbox_in_image`)
  and an image, returns the annotated image.

Typical programmatic usage::

    from spatialai_data_utils.visualization.render import visualize_nvschema

    # Single scene, selected sensors, save to disk.
    visualize_nvschema(
        nvschema_path="results/scene_001.json",
        calib_path="data/mtmc/scene_001/calibration.json",
        data_path="data/mtmc/scene_001",
        output_dir="output/visualization",
        sensor_ids=["Camera_01", "Camera_02"],
    )

    # General form with a bundled sparse4d pkl:
    from spatialai_data_utils.visualization.render import visualize_3dbbox
    visualize_3dbbox(
        output_dir="output/visualization",
        data_pkl="anno_pkls/scene_001_infos.pkl",
    )
"""

import json
import logging
import os
from typing import Any, Dict, List, Optional, Tuple, Union

import cv2
import numpy as np
import tqdm

from spatialai_data_utils.visualization.box_3d import (
    draw_bbox3d_on_img,
    draw_box3d_corners_on_img,
)
from spatialai_data_utils.visualization.coloring import (
    _assign_colors,
    _track_id_to_color_key,
    _validate_color_by,
)
from spatialai_data_utils.visualization.draw_utils import (
    build_world2img_from_calib,
    draw_camera_tag,
    generate_bbox_text,
    load_image,
    save_viz,
)
from spatialai_data_utils.loaders.nvschema import load_nvschema
from spatialai_data_utils.loaders.ground_truth import (
    load_gt_from_pkl,
    process_bbox3d_gt,
)
from spatialai_data_utils.loaders.calibration import (
    load_calib_into_dict,
    load_calib_into_dict_from_pkl,
    resolve_scene_calib,
)
from spatialai_data_utils.core.geometry.projection import (
    _select_bbox3d_projection,
    project_bev_objects_bbox_in_image,
)
from spatialai_data_utils.core.post_processing.filters import filter_dets_by_conf
from spatialai_data_utils.loaders.object_classes import load_object_class_config
from spatialai_data_utils.datasets.frame_paths import (
    frame_paths_from_pkl_info,
    get_frame_paths_of_multi_cameras,
    index_pkl_by_frame,
    resolve_frame_root,
)
from spatialai_data_utils.datasets.scenes import parse_scene_and_group
from spatialai_data_utils.constants import (
    KEY_CONFIDENCE,
    KEY_NVSCHEMA_ID,
    KEY_OBJECT_ID,
    KEY_PERSON_ID,
    KEY_TYPE,
    KEY_VERTICES,
)

logger = logging.getLogger(__name__)

__all__ = [
    # Top-level public API
    "visualize_nvschema",
    "visualize_3dbbox",
    # Stage-2 (draw already-projected BEV objects on image)
    "draw_bev_objects_bbox_in_image",
    # Mid-level building blocks (used by the two public entry points)
    "process_scene",
    "process_frame_nvschema",
    "process_frame_gt_json_aicity",
]

DEFAULT_LINE_THICKNESS = 2


# ---------------------------------------------------------------------------
# Display-name helper (color-related helpers live in coloring.py)
# ---------------------------------------------------------------------------

def _resolve_display_name(type_name: str, cfg) -> str:
    """Thin wrapper around :meth:`ObjectClassConfig.display_name` tolerating *cfg*=None."""
    if cfg is None:
        return type_name
    return cfg.display_name(type_name)


# ---------------------------------------------------------------------------
# Stage 2 — draw projected BEV objects on image
# ---------------------------------------------------------------------------

def draw_bev_objects_bbox_in_image(
    bev_objects: List[Dict],
    image: Union[str, np.ndarray],
    color: Optional[
        Union[Tuple[int, int, int], List[Tuple[int, int, int]]]
    ] = None,
    thickness: int = DEFAULT_LINE_THICKNESS,
    shade_heading: bool = True,
    draw_text_labels: bool = True,
    object_class_tag: Optional[str] = None,
    sensor_id: Optional[str] = None,
    color_by: str = "track_id",
) -> np.ndarray:
    """Render already-projected BEV objects onto a camera image.

    Stage 2 of the visualization pipeline.  Consumes the output of
    :func:`project_bev_objects_bbox_in_image` — a list of raw NVSchema object
    dicts whose existing ``"bbox3d"`` block has been enriched with an
    ``info`` ``map<string, string>`` holding ``{sensorId, vertices}``
    — and draws wireframe cuboids on the supplied image.

    No projection or calibration work is done here — the projected
    corners are parsed from ``det["bbox3d"]["info"]["vertices"]`` (a
    ``json.dumps``-encoded list per the ``Bbox3d.info``
    ``map<string, string>`` convention).

    Sensor selection:

    * ``sensor_id=None`` (default): draw every detection's projection.
      This is the common flow where the caller already knows the
      projection target.
    * ``sensor_id="<cam>"``: only draw detections whose
      ``bbox3d.info.sensorId`` matches; others are silently skipped.
      Use this as a safety filter when mixing dicts from different
      projection runs.

    :param bev_objects: List of raw NVSchema object dicts carrying
        projection metadata in ``bbox3d.info`` (output of
        :func:`project_bev_objects_bbox_in_image`).  Empty list is a
        no-op — returns the image unchanged.
    :type bev_objects: list[dict]
    :param image: Camera image to draw on.  Either a path (str) or a
        pre-loaded BGR numpy array ``(H, W, 3)``.
    :type image: str or numpy.ndarray
    :param color: Single BGR colour or a per-box list of BGR colours.
        When ``None``, colours are auto-assigned from ``COLOR_MAP``
        using *color_by* as the key (track id by default).  An
        explicit *color* takes full precedence and disables the
        *color_by* branch.
    :type color: tuple or list[tuple] or None
    :param thickness: Line thickness in pixels.
    :type thickness: int
    :param shade_heading: If True, shade the heading face
        semi-transparently.
    :type shade_heading: bool
    :param draw_text_labels: If True, stamp a ``"Class(id) score"``
        label on each box.
    :type draw_text_labels: bool
    :param object_class_tag: Optional object-class config name (e.g.
        ``"warehouse"``) or path to a Python config file.  When set,
        it drives **two** things:

        * **Class filtering** — only detections whose ``"type"`` is
          recognised by the config (a primary class, sub-class, or
          NVSchema display name — see
          :meth:`ObjectClassConfig.is_known_type`) are kept.
          Unrecognised types are dropped silently before colouring
          / text-label assembly so reviewers see only the classes
          the active taxonomy cares about.
        * **Display-name remap** — kept detections' ``"type"``
          strings are translated to human-readable display names
          via :meth:`ObjectClassConfig.display_name` for the text
          label.

        ``None`` (or ``"none"`` from the CLI flag) disables both —
        every detection is kept and the raw ``"type"`` value is used
        verbatim in the label.
    :type object_class_tag: str or None
    :param sensor_id: Optional sensor filter — if provided, only
        detections whose ``bbox3d.info.sensorId`` matches are drawn.
    :type sensor_id: str or None
    :param color_by: Auto-coloring mode used when *color* is ``None``:

        * ``"track_id"`` (default): per-object colour keyed on the
          NVSchema ``"id"`` field (ints wrap into ``COLOR_MAP``;
          non-numeric ids fall back to a Python-hash of the string,
          stable within one interpreter run).
        * ``"class"``: **FIFO palette walk** over the raw ``"type"``
          field (see :func:`_fifo_palette_slots`).  The first
          distinct class encountered in *bev_objects* claims
          ``COLOR_MAP[0]``, the next ``COLOR_MAP[1]``, and so on —
          maximum visual separation for small class counts, since
          the palette was curated for that.  Trade-off: the
          colour-to-class binding is **per-call**; if the first
          detection of Frame B is a different class than Frame A,
          their palette slots swap.  Callers that need cross-frame
          stable colours should inject an explicit per-box *color*
          list.

        Ignored when *color* is an explicit tuple or per-box list.
    :type color_by: str
    :return: Annotated image ``(H, W, 3)`` in BGR.
    :rtype: numpy.ndarray
    :raises FileNotFoundError: If *image* is a string path that fails
        to load.
    :raises KeyError: If any dict is missing the ``"bbox3d"`` block,
        its ``"info"`` map, or ``"info.vertices"``.
    :raises ValueError: If ``"info.vertices"`` isn't valid JSON, or
        if *color_by* is not one of
        ``{"track_id", "class"}``.
    """
    if color is None:
        _validate_color_by(color_by)
    if isinstance(image, str):
        img = cv2.imread(image)
        if img is None:
            raise FileNotFoundError(f"Failed to load image: {image}")
    else:
        img = image.copy()

    if not bev_objects:
        return img

    # Load the object-class config once up-front so it can drive BOTH
    # class-based filtering (drop boxes whose ``type`` isn't in the
    # config) and display-name remap below.  ``None`` skips both —
    # restoring the legacy "show every box, raw type strings" path.
    cfg = (
        load_object_class_config(object_class_tag) if object_class_tag else None
    )

    vertices_list: List = []
    kept_dets: List[Dict] = []
    for idx, det in enumerate(bev_objects):
        entry = _select_bbox3d_projection(det, idx, sensor_id)
        if entry is None:
            continue  # sensor filter excluded this detection
        if cfg is not None:
            type_name = str(det.get(KEY_TYPE, ""))
            if not cfg.is_known_type(type_name):
                continue  # class filter excluded this detection
        vertices_list.append(entry[KEY_VERTICES])
        kept_dets.append(det)

    if not kept_dets:
        return img

    vertices = np.asarray(vertices_list, dtype=np.float64)  # (N, 8, 2)

    if color is None:
        # Auto-coloring path: delegate the class-vs-track-id branch
        # to the shared helper so this function and
        # ``process_frame_gt_json_aicity`` stay in lockstep.
        color = _assign_colors(
            color_by=color_by,
            type_names=[str(det.get(KEY_TYPE, "")) for det in kept_dets],
            track_ids=[_track_id_to_color_key(det) for det in kept_dets],
        )

    texts: Optional[List[str]] = None
    if draw_text_labels:
        texts = []
        for det in kept_dets:
            raw_id = det.get(KEY_NVSCHEMA_ID, "?")
            score = float(det.get(KEY_CONFIDENCE, 1.0))
            type_name = det.get(KEY_TYPE, "unknown")
            display_name = _resolve_display_name(type_name, cfg)
            texts.append(f"{display_name}({raw_id}) {score:.2f}")

    return draw_box3d_corners_on_img(
        img,
        len(vertices),
        vertices,
        box_texts=texts,
        color=color,
        thickness=thickness,
        shade_heading=shade_heading,
    )


# ---------------------------------------------------------------------------
# Per-frame rendering
# ---------------------------------------------------------------------------

def process_frame_nvschema(
    det_dicts_sensors: Dict[str, List[Dict]],
    calib_dict: Dict[str, Dict],
    frame_paths: Dict[str, Any],
    vis_dir: str,
    conf_thresh: float = 0.1,
    h5_file: bool = False,
    line_thickness: int = DEFAULT_LINE_THICKNESS,
    shade_heading: bool = True,
    draw_camera_label: bool = True,
    object_class_tag: Optional[str] = None,
    color_by: str = "track_id",
) -> None:
    """Render one frame across all cameras for NVSchema results.

    :param det_dicts_sensors: Detections keyed by sensor name.
    :type det_dicts_sensors: dict[str, list[dict]]
    :param calib_dict: Calibration dict keyed by camera name.
    :type calib_dict: dict[str, dict]
    :param frame_paths: Camera name -> image path mapping.
    :type frame_paths: dict[str, Any]
    :param vis_dir: Output directory.
    :type vis_dir: str
    :param conf_thresh: Confidence threshold.
    :type conf_thresh: float
    :param h5_file: Whether images are stored in H5 format.
    :type h5_file: bool
    :param line_thickness: Line thickness for drawing boxes.
    :type line_thickness: int
    :param shade_heading: If True, shade the heading face semi-transparently.
    :type shade_heading: bool
    :param draw_camera_label: If True, stamp camera name on the image.
    :type draw_camera_label: bool
    :param color_by: Auto-coloring mode forwarded to
        :func:`draw_bev_objects_bbox_in_image`: ``"track_id"`` (default, per-``id``)
        or ``"class"`` (per-``type``).
    :type color_by: str
    """
    for cam_name, frame_path in frame_paths.items():
        if len(det_dicts_sensors) == 1:
            sensor_key = next(iter(det_dicts_sensors))
            det_dicts = det_dicts_sensors[sensor_key]
        else:
            det_dicts = det_dicts_sensors.get(cam_name, [])

        # Confidence filter first so stage 1 only sees boxes we care about.
        keep = filter_dets_by_conf(det_dicts, conf_thresh)
        bev_objects = [det_dicts[k] for k in keep]

        # Stage 1: project 3D -> 2D for this sensor.
        enriched_objects = project_bev_objects_bbox_in_image(
            sensor_id=cam_name,
            calib_dict=calib_dict,
            bev_objects=bev_objects,
        )

        # load_image / save_viz auto-detect H5 tuples vs string paths, so we
        # do not forward the top-level h5_file flag (it is only meaningful
        # for filesystem path *construction* upstream, not image loading).
        image = load_image(frame_path)
        if image is None:
            continue

        # Stage 2: draw the already-projected BEV objects onto the image.
        image = draw_bev_objects_bbox_in_image(
            enriched_objects,
            image,
            thickness=line_thickness,
            shade_heading=shade_heading,
            object_class_tag=object_class_tag,
            sensor_id=cam_name,
            color_by=color_by,
        )

        if draw_camera_label:
            draw_camera_tag(image, cam_name)
        save_viz(image, vis_dir, cam_name, frame_path)


def process_frame_gt_json_aicity(
    gt_frame: Any,
    calib_dict: Dict[str, Dict],
    frame_paths: Dict[str, Any],
    vis_dir: str,
    h5_file: bool = False,
    line_thickness: int = DEFAULT_LINE_THICKNESS,
    shade_heading: bool = True,
    draw_camera_label: bool = True,
    object_class_tag: Optional[str] = None,
    color_by: str = "track_id",
) -> None:
    """Render one frame across all cameras for ground truth JSON data.

    :param gt_frame: Ground-truth data for a single frame — either a list of
        detection dicts or a nested structure.
    :type gt_frame: list or dict
    :param calib_dict: Calibration dict keyed by camera name.
    :type calib_dict: dict[str, dict]
    :param frame_paths: Camera name -> image path mapping.
    :type frame_paths: dict[str, Any]
    :param vis_dir: Output directory.
    :type vis_dir: str
    :param h5_file: Whether images are stored in H5 format.
    :type h5_file: bool
    :param line_thickness: Line thickness for drawing boxes.
    :type line_thickness: int
    :param shade_heading: If True, shade the heading face semi-transparently.
    :type shade_heading: bool
    :param draw_camera_label: If True, stamp camera name on the image.
    :type draw_camera_label: bool
    :param object_class_tag: Optional object-class config name (e.g.
        ``"warehouse"``) or path to a Python config file.  When set,
        annotations whose ``"object type"`` is **not** recognised by
        the config (per :meth:`ObjectClassConfig.is_known_type`) are
        dropped before drawing — so reviewers see only the classes
        the active taxonomy cares about.  ``None`` keeps every
        annotation, mirroring the legacy gt_json_aicity behaviour.
    :type object_class_tag: str or None
    :param color_by: Auto-coloring mode (see
        :func:`draw_bev_objects_bbox_in_image`).
    :type color_by: str
    """
    _validate_color_by(color_by)

    cfg = (
        load_object_class_config(object_class_tag) if object_class_tag else None
    )

    bboxes_3d: List[list] = []
    labels: List[int] = []
    track_ids: List[int] = []
    # Collected alongside the other parallel lists so class-mode can
    # run a single FIFO palette walk over the full type sequence in
    # a post-pass, instead of tracking the assignment per-det inline.
    # gt_json_aicity uses ``"object type"`` (two words) for the class name —
    # distinct from NVSchema's single-word ``"type"``.
    type_names: List[str] = []

    items = gt_frame if isinstance(gt_frame, list) else [gt_frame]
    for det in items:
        det_list = det if isinstance(det, list) else [det]
        for d in det_list:
            if isinstance(d, dict):
                type_name = str(d.get("object type", ""))
                if cfg is not None and not cfg.is_known_type(type_name):
                    continue  # class-tag filter excluded this annotation
                obj_id = d.get(KEY_OBJECT_ID, d.get(KEY_PERSON_ID, 0))
                track_ids.append(obj_id)
                labels.append(0)
                bboxes_3d.append(process_bbox3d_gt(d))
                type_names.append(type_name)

    colors: List[tuple] = _assign_colors(
        color_by=color_by, type_names=type_names, track_ids=track_ids,
    )

    bboxes_3d_arr = np.array(bboxes_3d) if bboxes_3d else np.empty((0, 9))
    scores = np.ones(len(bboxes_3d_arr))
    labels_arr = np.array(labels)
    track_ids_arr = np.array(track_ids)

    # Per-frame (camera-invariant) text labels: use the actual GT class
    # strings (optionally remapped through the object-class config)
    # instead of a placeholder "gt" label.  GT has no meaningful
    # confidence (synthetic 1.0 above), so omit the score — class +
    # track id only.  Computed once per frame, reused across cameras.
    display_names = [_resolve_display_name(t, cfg) for t in type_names]
    texts = [
        f"{display_names[i]}({track_ids_arr[i]})"
        for i in range(len(bboxes_3d_arr))
    ]

    for cam_name, frame_path in frame_paths.items():
        # load_image / save_viz auto-detect H5 tuples vs string paths.
        image = load_image(frame_path)
        if image is None:
            continue

        if len(bboxes_3d_arr) > 0:
            world2img = build_world2img_from_calib(calib_dict, cam_name)
            image = draw_bbox3d_on_img(
                bboxes_3d_arr, image, world2img=world2img,
                bboxes3d_text=texts, color=colors, thickness=line_thickness,
                shade_heading=shade_heading,
            )

        if draw_camera_label:
            draw_camera_tag(image, cam_name)
        save_viz(image, vis_dir, cam_name, frame_path)


# ---------------------------------------------------------------------------
# Scene-level driver
# ---------------------------------------------------------------------------

# Per-viz-mode dispatch table — maps the string identifier used by
# ``process_scene`` / the ``visualize_3dbbox`` dispatcher to the
# concrete per-frame driver.  Adding a new input format means adding
# a ``process_frame_<format>`` function and a single entry below; the
# scene driver no longer needs an explicit ``if/elif`` chain.
_VIZ_MODE_DRIVERS = {
    "nvschema": process_frame_nvschema,
    "gt_json_aicity": process_frame_gt_json_aicity,
}


def process_scene(
    scene_name_full: str,
    scene_root: str,
    scene_results: Dict,
    viz_root: str,
    viz_mode: str,
    sensor_ids: Optional[List[str]] = None,
    conf_thresh: float = 0.1,
    n_frames: int = -1,
    h5_file: bool = False,
    calib_mode: str = "aic25",
    recentering: bool = False,
    line_thickness: int = DEFAULT_LINE_THICKNESS,
    shade_heading: bool = True,
    draw_camera_label: bool = True,
    prebuilt_calib: Optional[Dict[str, Dict]] = None,
    pkl_infos: Optional[List[Dict]] = None,
    object_class_tag: Optional[str] = None,
    color_by: str = "track_id",
) -> None:
    """Render all frames of a single scene.

    Iterates over ``scene_results`` in sorted frame-id order, builds the
    per-camera image paths, and dispatches to the per-frame renderer for
    each supported ``viz_mode``.  Writes annotated images under
    ``viz_root/scene_name_full/``.

    :param scene_name_full: Scene label used for the output subdirectory;
        optionally carries a ``+group_name`` suffix that's parsed and
        forwarded to :func:`resolve_scene_calib` when no *prebuilt_calib*
        is provided.
    :type scene_name_full: str
    :param scene_root: Directory containing this scene's per-camera image
        folders (or a ``frames/`` subdirectory).
    :type scene_root: str
    :param scene_results: Per-frame detection / GT data keyed by frame id.
        Each entry must match the shape expected by the per-frame driver
        selected via *viz_mode*.
    :type scene_results: dict
    :param viz_root: Root output directory.  Images are written under
        ``viz_root/scene_name_full/``.
    :type viz_root: str
    :param viz_mode: ``"nvschema"`` or ``"gt_json_aicity"``.
    :type viz_mode: str
    :param sensor_ids: Optional subset of camera names to render.
    :type sensor_ids: list[str] or None
    :param prebuilt_calib: Pre-built flat calibration dict.  When
        provided, disk-based calibration loading is skipped.
    :type prebuilt_calib: dict or None
    :param pkl_infos: Per-frame info list from a data pkl file.  When
        provided, image paths come from the pkl entries and the
        filesystem is not probed; ``h5_file`` is ignored (format is
        auto-detected per frame from the pkl).
    :type pkl_infos: list[dict] or None

    Other keyword arguments forward to :func:`resolve_scene_calib` and
    the per-frame drivers; see their docstrings for details.
    """
    logger.info(f"Visualizing {scene_name_full} ...")

    _, group_name = parse_scene_and_group(scene_name_full)

    calib_dict = resolve_scene_calib(
        scene_root=scene_root,
        group_name=group_name,
        calib_mode=calib_mode,
        recentering=recentering,
        prebuilt_calib=prebuilt_calib,
    )

    camera_names = list(calib_dict.keys())
    if sensor_ids is not None:
        camera_names = [c for c in camera_names if c in sensor_ids]

    # When pkl_infos is provided, paths come from the pkl (auto-detecting
    # JPEG/PNG vs H5 tuple per frame) rather than the filesystem.
    pkl_by_frame = index_pkl_by_frame(pkl_infos) if pkl_infos is not None else None
    frame_root = resolve_frame_root(scene_root)

    vis_dir = os.path.join(viz_root, scene_name_full)
    logger.info(f"  Cameras ({len(camera_names)}): {camera_names}")
    logger.info(f"  Output : {vis_dir}")

    # Resolve the per-frame driver and its kwargs ONCE per scene
    # rather than on every loop iteration.  Cosmetic kwargs are
    # identical between the two per-frame drivers; ``conf_thresh`` is
    # NVSchema-only (no confidence to filter on in gt_json_aicity).
    driver = _VIZ_MODE_DRIVERS.get(viz_mode)
    if driver is None:
        raise ValueError(
            f"Unknown viz_mode {viz_mode!r}; expected one of "
            f"{sorted(_VIZ_MODE_DRIVERS)!r}."
        )
    driver_kwargs = dict(
        h5_file=h5_file,
        line_thickness=line_thickness,
        shade_heading=shade_heading,
        draw_camera_label=draw_camera_label,
        object_class_tag=object_class_tag,
        color_by=color_by,
    )
    if viz_mode == "nvschema":
        driver_kwargs["conf_thresh"] = conf_thresh

    sorted_frame_ids = sorted(scene_results.keys())
    if n_frames > 0:
        sorted_frame_ids = sorted_frame_ids[:n_frames]

    for frame_id in tqdm.tqdm(sorted_frame_ids, desc=f"Frames ({scene_name_full})"):
        if pkl_by_frame is not None:
            pkl_info = pkl_by_frame.get(frame_id)
            if pkl_info is None:
                continue
            frame_paths = frame_paths_from_pkl_info(pkl_info, camera_names)
        else:
            frame_paths = get_frame_paths_of_multi_cameras(
                frame_root, frame_id, camera_names, h5_file=h5_file,
            )
        driver(
            scene_results[frame_id], calib_dict, frame_paths, vis_dir,
            **driver_kwargs,
        )

    logger.info(f"  Done: {vis_dir}\n")


# ---------------------------------------------------------------------------
# Top-level public API
# ---------------------------------------------------------------------------

def visualize_nvschema(
    nvschema_path: str,
    calib_path: str,
    data_path: str,
    output_dir: str,
    sensor_ids: Optional[List[str]] = None,
    *,
    conf_thresh: float = 0.1,
    n_frames: int = -1,
    h5_file: bool = False,
    recentering: bool = False,
    line_thickness: int = DEFAULT_LINE_THICKNESS,
    shade_heading: bool = True,
    draw_camera_label: bool = True,
    object_class_tag: Optional[str] = None,
    color_by: str = "track_id",
) -> None:
    """Visualize NVSchema detection / tracking results on camera images.

    Customer-facing one-shot wrapper that composes the stage-1 (project)
    and stage-2 (draw) helpers into a single call.  Loads NVSchema
    results + calibration from disk, iterates frames and sensors, and
    writes annotated images under ``output_dir/<scene>/``.

    :param nvschema_path: Path to an NVSchema JSON-lines file produced
        by the model (see :func:`load_nvschema`).  The file stem
        becomes the output sub-folder name.
    :type nvschema_path: str
    :param calib_path: Path to the scene's calibration JSON file.
    :type calib_path: str
    :param data_path: Path to the scene root (the directory containing
        per-camera image folders, or a ``frames/`` subdirectory).
    :type data_path: str
    :param output_dir: Directory under which annotated images are
        saved.  Created if it does not exist.
    :type output_dir: str
    :param sensor_ids: Optional list of camera names to render.
        ``None`` renders every camera present in the calibration.
    :type sensor_ids: list[str] or None
    :param conf_thresh: Detections with ``confidence`` below this are
        dropped before drawing.
    :type conf_thresh: float
    :param n_frames: Maximum number of frames to render (``-1`` = all).
    :type n_frames: int
    :param h5_file: Treat per-camera image directories as ``.h5`` files.
    :type h5_file: bool
    :param recentering: Apply group-origin recentering to the loaded
        calibration.
    :type recentering: bool
    :param line_thickness: Wireframe line thickness in pixels.
    :type line_thickness: int
    :param shade_heading: Semi-transparently shade the heading face.
    :type shade_heading: bool
    :param draw_camera_label: Stamp the camera name on each image.
    :type draw_camera_label: bool
    :param object_class_tag: Optional object-class config name (e.g.
        ``"warehouse"``) or path to a Python config file, used to
        resolve NVSchema ``type`` strings to display names.
    :type object_class_tag: str or None
    """
    logger.info("=== 3D BBox Visualization (NVSchema) ===")
    logger.info(f"  NVSchema  : {nvschema_path}")
    logger.info(f"  Calib     : {calib_path}")
    logger.info(f"  Data      : {data_path}")
    logger.info(f"  Output    : {output_dir}")
    logger.info(f"  Sensors   : {sensor_ids if sensor_ids else 'all'}")
    logger.info("=========================================\n")

    scene_results = load_nvschema(nvschema_path)
    prebuilt_calib = load_calib_into_dict(
        calib_path, sensor_ids, recentering=recentering,
    )
    scene_name = os.path.splitext(os.path.basename(nvschema_path))[0]

    process_scene(
        scene_name_full=scene_name,
        scene_root=data_path,
        scene_results=scene_results,
        viz_root=output_dir,
        viz_mode="nvschema",
        sensor_ids=sensor_ids,
        conf_thresh=conf_thresh,
        n_frames=n_frames,
        h5_file=h5_file,
        recentering=recentering,
        line_thickness=line_thickness,
        shade_heading=shade_heading,
        draw_camera_label=draw_camera_label,
        prebuilt_calib=prebuilt_calib,
        object_class_tag=object_class_tag,
        color_by=color_by,
    )

    logger.info(f"All visualizations saved to: {output_dir}\n")


def visualize_3dbbox(
    output_dir: str,
    *,
    nvschema_path: Optional[str] = None,
    gt_json_aicity_path: Optional[str] = None,
    data_pkl: Optional[str] = None,
    data_path: Optional[str] = None,
    calib_path: Optional[str] = None,
    sensor_ids: Optional[List[str]] = None,
    conf_thresh: float = 0.1,
    n_frames: int = -1,
    h5_file: bool = False,
    recentering: bool = False,
    line_thickness: int = DEFAULT_LINE_THICKNESS,
    shade_heading: bool = True,
    draw_camera_label: bool = True,
    object_class_tag: Optional[str] = None,
    calib_mode: str = "aic25",
    color_by: str = "track_id",
) -> None:
    """General 3D bbox visualizer supporting three input formats.

    Dispatches based on which source-of-truth argument is provided:

    * ``nvschema_path`` — NVSchema JSON-lines results from a model.
      Requires ``calib_path`` and ``data_path``.
    * ``gt_json_aicity_path`` — scene's ``ground_truth.json`` file.  Requires
      ``data_path``; ``calib_path`` is optional (auto-detected from the
      scene directory when omitted).
    * ``data_pkl`` — sparse4d-style pkl bundling calibration, per-frame
      image paths, and GT annotations in a single file.  ``calib_path``
      and ``data_path`` must be ``None``.

    Exactly one of the three source arguments must be supplied.

    :param output_dir: Root directory for annotated images.  Created if
        missing.  Each scene's images go under
        ``output_dir/<scene_name>/``.
    :type output_dir: str
    :param nvschema_path: NVSchema JSON-lines file.
    :type nvschema_path: str or None
    :param gt_json_aicity_path: Ground-truth JSON file (``ground_truth.json``).
    :type gt_json_aicity_path: str or None
    :param data_pkl: sparse4d-style pkl file (calib + image paths + GT).
    :type data_pkl: str or None
    :param data_path: Scene root directory (required for nvschema and
        gt_json_aicity modes; ignored for pkl mode).
    :type data_path: str or None
    :param calib_path: Calibration JSON file (used by nvschema and
        gt_json_aicity modes; must be ``None`` in pkl mode).
    :type calib_path: str or None
    :param sensor_ids: Optional subset of camera names to render.
    :type sensor_ids: list[str] or None

    All other keyword arguments forward to the underlying per-frame
    drivers; see :func:`process_scene`.

    :raises ValueError: If argument combinations are invalid (not
        exactly one source, missing ``data_path``, or ``calib_path``
        combined with ``data_pkl``).
    """
    sources = [
        ("nvschema_path", nvschema_path),
        ("gt_json_aicity_path", gt_json_aicity_path),
        ("data_pkl", data_pkl),
    ]
    given = [name for name, value in sources if value]
    if len(given) != 1:
        raise ValueError(
            "Exactly one of {nvschema_path, gt_json_aicity_path, data_pkl} must be "
            f"provided; got {given or 'none'}."
        )

    if data_pkl is not None and calib_path is not None:
        raise ValueError(
            "'calib_path' cannot be combined with 'data_pkl' — the pkl file "
            "already contains calibration data."
        )

    if data_pkl is None and data_path is None:
        raise ValueError(
            "'data_path' is required when using 'nvschema_path' or "
            "'gt_json_aicity_path' (points at the scene root containing images)."
        )

    # Build the per-frame-driver kwargs once; every dispatch branch
    # below shares the same cosmetic / filtering knobs.
    cosmetic = dict(
        line_thickness=line_thickness,
        shade_heading=shade_heading,
        draw_camera_label=draw_camera_label,
        object_class_tag=object_class_tag,
        color_by=color_by,
    )

    logger.info("=== 3D BBox Visualization ===")
    logger.info(f"  Source    : {given[0]}")
    logger.info(f"  Output    : {output_dir}")
    logger.info(f"  Sensors   : {sensor_ids if sensor_ids else 'all'}")

    if nvschema_path is not None:
        # NVSchema mode: delegate to ``visualize_nvschema`` which knows
        # how to load the JSON-lines results and the calibration.  The
        # banner re-emit by that function is intentional — it doubles
        # as a "this is the NVSchema dispatch branch" trace marker.
        if calib_path is None:
            raise ValueError("'calib_path' is required for nvschema mode.")
        logger.info(f"  Results   : {nvschema_path}")
        logger.info(f"  Calib     : {calib_path}")
        logger.info(f"  Data      : {data_path}")
        logger.info("==============================\n")
        visualize_nvschema(
            nvschema_path=nvschema_path, calib_path=calib_path,
            data_path=data_path, output_dir=output_dir, sensor_ids=sensor_ids,
            conf_thresh=conf_thresh, n_frames=n_frames, h5_file=h5_file,
            recentering=recentering, **cosmetic,
        )

    elif gt_json_aicity_path is not None:
        # GT-JSON-AICity mode: load the raw scene GT JSON as
        # ``{frame_id (int): [annotation_dict, ...]}`` and forward
        # to ``process_scene`` with viz_mode='gt_json_aicity'.  The
        # per-annotation dicts (with ``"3d location"``, ``"object
        # id"``, etc.) are what ``process_frame_gt_json_aicity``
        # expects — NOT the flattened 4-tuples that
        # ``loaders.ground_truth.load_det_3d_from_gt_scene`` produces
        # for evaluation.
        logger.info(f"  GT JSON   : {gt_json_aicity_path}")
        logger.info(f"  Data      : {data_path}")
        logger.info(
            f"  Calib     : {calib_path or 'auto-detect from data_path'}"
        )
        logger.info("==============================\n")

        with open(gt_json_aicity_path) as f:
            raw_gt = json.load(f)
        scene_results = {int(fid): annos for fid, annos in raw_gt.items()}
        scene_name = os.path.basename(os.path.normpath(data_path))

        prebuilt_calib = None
        if calib_path is not None:
            prebuilt_calib = load_calib_into_dict(
                calib_path, sensor_ids, recentering=recentering,
            )

        process_scene(
            scene_name_full=scene_name,
            scene_root=data_path,
            scene_results=scene_results,
            viz_root=output_dir,
            viz_mode="gt_json_aicity",
            sensor_ids=sensor_ids,
            n_frames=n_frames,
            h5_file=h5_file,
            calib_mode=calib_mode,
            recentering=recentering,
            prebuilt_calib=prebuilt_calib,
            **cosmetic,
        )

    else:  # data_pkl
        # PKL mode: the pkl bundles calibration, per-frame image
        # paths, and GT annotations, so no separate ``calib_path`` /
        # ``data_path`` is consumed.  GT is loaded as raw NVSchema
        # objects and routed through ``viz_mode="nvschema"`` so we
        # reuse the stage-1/stage-2 drawing path.
        logger.info(f"  Data pkl  : {data_pkl}")
        logger.info("==============================\n")

        prebuilt_calib, pkl_infos = load_calib_into_dict_from_pkl(data_pkl, sensor_ids)
        scene_results = load_gt_from_pkl(data_pkl)
        scene_name = os.path.splitext(os.path.basename(data_pkl))[0]

        process_scene(
            scene_name_full=scene_name,
            scene_root="",  # paths come from pkl_infos, not the FS
            scene_results=scene_results,
            viz_root=output_dir,
            viz_mode="nvschema",
            sensor_ids=sensor_ids,
            conf_thresh=conf_thresh,
            n_frames=n_frames,
            prebuilt_calib=prebuilt_calib,
            pkl_infos=pkl_infos,
            **cosmetic,
        )

    logger.info(f"All visualizations saved to: {output_dir}\n")
