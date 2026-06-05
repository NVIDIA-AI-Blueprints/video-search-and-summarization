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
Camera-calibration loaders.

Reads multi-camera calibration data from disk in the formats this
toolkit supports — NVSchema JSON (the default), BEVFormer JSON
(legacy AICity'24 layout), and Sparse4D ``.pkl`` info files — and
turns it into the canonical in-memory shape (``{cam_name: calib_info}``
dicts, optionally with BEV-group memberships) that the rest of the
package consumes.

============================
Output formats
============================

Loaders fall into two families, distinguishable from the function
name alone:

* **Raw NVSchema** — sensor dicts straight off disk
  (``{intrinsicMatrix, extrinsicMatrix, group, ...}``).  Loaded by
  :func:`load_calib_json`.
* **Decoded** ``calib_info`` — parsed numpy-friendly entries
  (``{intrinsic_matrix, w2c_matrix, w2p_matrix, image_size}``).
  Loaded by every ``load_calib_into_dict[_*]`` function — the
  ``into_dict`` infix is the family marker. Within this family,
  the suffix tells you the output container shape:

  * bare or ``_with_<companion>`` → **flat** ``{cam: calib_info}``,
    optionally returned alongside companion data
    (e.g. ``_with_group_memberships`` adds ``{group: [cams]}``).
  * ``_grouped[_<variant>]`` → **nested** by group
    (``{group: {cam: calib_info}}``) plus per-group area metadata.
  * ``_from_pkl`` is the exception to the suffix rule — it returns
    ``(flat {cam: calib_info}, infos)``: the decoded flat dict plus
    the pkl's per-frame ``infos`` list.
  * ``_from_bevformer`` returns the raw legacy BEVFormer dict as-is,
    *not* the decoded ``calib_info`` shape.

============================
Which loader should I use?
============================

Public loaders, by use case:

+---------------------------------------------+--------------------------------------------------------+
| When you need to...                         | Use                                                    |
+=============================================+========================================================+
| **Raw** NVSchema sensor dicts (e.g. for     | :func:`load_calib_json`                                |
| camera-grouping pipelines that inspect      |                                                        |
| ``attributes`` / ``intrinsicMatrix``        |                                                        |
| directly).                                  |                                                        |
+---------------------------------------------+--------------------------------------------------------+
| **Decoded** flat ``{cam: calib_info}`` from | :func:`load_calib_into_dict`                          |
| one NVSchema JSON, with optional sensor-id  |                                                        |
| filtering.                                  |                                                        |
+---------------------------------------------+--------------------------------------------------------+
| Same as above **plus** the                  | :func:`load_calib_into_dict_with_group_memberships`   |
| ``{group_name: [cam_names]}`` BEV-group     |                                                        |
| membership map.                             |                                                        |
+---------------------------------------------+--------------------------------------------------------+
| Decoded calibration from a Sparse4D         | :func:`load_calib_into_dict_from_pkl`                 |
| ``.pkl`` info file produced during data     |                                                        |
| prep.                                       |                                                        |
+---------------------------------------------+--------------------------------------------------------+
| Format-agnostic "give me calibration for    | :func:`load_calib`                                     |
| this scene directory" — picks BEVFormer or  |                                                        |
| NVSchema based on ``calib_mode``, supports  |                                                        |
| BEV grouping.                               |                                                        |
+---------------------------------------------+--------------------------------------------------------+
| High-level scene calibration resolver with  | :func:`resolve_scene_calib`                            |
| extra path / group handling.                |                                                        |
+---------------------------------------------+--------------------------------------------------------+

Sensor-level helpers:

* :func:`get_calib_dict` — curate one raw NVSchema sensor entry into
  the canonical ``calib_info`` shape consumed downstream.
* :func:`get_calib_dict_from_cam_data` — same shape, but built from
  explicit intrinsic / extrinsic numpy arrays.

Validation / transformation:

* :func:`validate_calibration_data` — run an NVSchema dict against
  the bundled JSON schema (raises on failure).
* :func:`apply_recentering` — recenter group extrinsics around their
  group origin (used by the BEV-grouping pipeline).

Convenience lookups (used heavily by tools / converters):

* :func:`get_camera_name_to_bev_name_map` — physical-camera-name →
  BEV-sensor-name mapping read straight from a calibration file.
* :func:`fetch_fps_from_calibration` — pull the scene FPS attribute.

Note: the following are internal building blocks of the dispatchers
above and are not intended for direct external use:

* :func:`load_calib_into_dict_from_bevformer`
* :func:`load_calib_into_dict_grouped_buffer_zone`
* :func:`load_calib_into_dict_grouped`
* :func:`load_calib_into_dict_grouped_random`
* :func:`load_calib_into_dict_native`

============================
Supported input formats
============================

* **NVSchema** (the default, JSON) — schema bundled at
  ``spatialai_data_utils/schemas/calibration.json``; supports per-
  sensor metadata, BEV sensor groups (with origin / dimensions),
  buffer-zone calibrations, and coordinate-transformation params for
  world↔pixel mapping.
* **BEVFormer** (JSON) — legacy AICity'24 layout, loaded via
  :func:`load_calib_into_dict_from_bevformer` (called internally by
  :func:`load_calib` when ``calib_mode='aic24'``).
* **Sparse4D** (``.pkl``) — info-file format produced by data-prep
  scripts, loaded via :func:`load_calib_into_dict_from_pkl`.
"""

import json
import logging
import os
import pickle
import random
import warnings
from copy import deepcopy
from pathlib import Path

import numpy as np
from jsonschema import validate
from jsonschema.exceptions import ValidationError

from spatialai_data_utils.constants import (
    KEY_CAM_INTRINSIC,
    KEY_CAMS,
    KEY_IMAGE_SIZE,
    KEY_INTRINSIC_MATRIX,
    KEY_ORIGIN,
    KEY_SENSOR2WORLD,
    KEY_W2C_MATRIX,
    KEY_W2P_MATRIX,
)
from spatialai_data_utils.core.cameras.origin import calculate_group_origin
from spatialai_data_utils.core.cameras.utils import get_calib_field
from spatialai_data_utils.utils.filesystem_utils import (
    load_json_from_file,
    validate_file_path,
)


# ===========================================================================
# 1. Module-level constants
# ===========================================================================

logger = logging.getLogger(__name__)


BUFFER_ZONE_NAMES = {
    "bev-sensor-buffer-zone": "calibration_buffer_zone.json",
    "bev-sensor-buffer-zone-c3": "calibration_buffer_zone_c3.json",
    "bev-sensor-buffer-zone-c4": "calibration_buffer_zone_c4.json",
    "bev-sensor-buffer-zone-c6": "calibration_buffer_zone_c6.json",
}


# Path to the bundled calibration JSON schema. Resolved relative to the
# package root so editable installs and wheel installs both work.
_CALIBRATION_SCHEMA_PATH = (
    Path(__file__).parent.parent / "schemas" / "calibration.json"
)


# Cached schema dict to avoid re-reading the file on every validation.
_calibration_schema = None


# ===========================================================================
# 2. Attribute extraction helpers (sensor-level + file-level)
# ===========================================================================

def _sensor_attribute(sensor, name):
    """Return ``sensor["attributes"][i]["value"]`` for the entry named *name*.

    Both NVSchema calibration variants (synthetic + real-world / AIC)
    store metadata like ``frameWidth`` / ``frameHeight`` / ``fps`` under
    ``sensor["attributes"]`` as a list of ``{"name": ..., "value": ...}``
    dicts.  This helper centralises that lookup.

    :param sensor: Single-sensor dict from a calibration JSON.
    :param name: Attribute name to look up.
    :return: The attribute's ``"value"`` (as a string, per the on-disk
        schema) or ``None`` if the attribute is absent or has no value.
    """
    for attr in sensor.get("attributes", []) or []:
        if attr.get("name") == name:
            return attr.get("value")
    return None


def _sensor_image_size(sensor):
    """Extract per-sensor ``(width, height)`` from a calibration sensor dict.

    Reads ``frameWidth`` / ``frameHeight`` from the sensor's
    ``attributes`` list (string values in both NVSchema variants),
    coerces them to ``int``, and returns ``(width, height)``.  Returns
    ``None`` if either attribute is absent or not a non-empty integer
    string — callers should fall back to their own default.

    :param sensor: Single-sensor dict from a calibration JSON.
    :return: ``(width, height)`` tuple or ``None``.
    :rtype: tuple(int, int) or None
    """
    w_raw = _sensor_attribute(sensor, "frameWidth")
    h_raw = _sensor_attribute(sensor, "frameHeight")
    if not w_raw or not h_raw:
        return None
    try:
        return int(w_raw), int(h_raw)
    except (TypeError, ValueError):
        return None


def get_camera_name_to_bev_name_map(calibration_file: str) -> dict:
    """
    Create a mapping from camera names to their associated BEV group names.

    Reads the calibration file and builds a dictionary mapping each camera sensor ID
    to a list of BEV group names it belongs to. A camera can belong to multiple groups.

    :param calibration_file: Path to calibration JSON file with sensor group information.
    :type calibration_file: str
    :return: Dictionary mapping camera IDs to lists of BEV group names.
    :rtype: dict
    """
    valid_calibration_path = validate_file_path(calibration_file)
    if not os.path.exists(valid_calibration_path):
        raise FileNotFoundError(
            f"Calibration file `{valid_calibration_path}` does not exist."
        )

    try:
        data = load_json_from_file(valid_calibration_path)
    except ValueError as e:
        raise ValueError(
            f"Failed to load calibration JSON `{valid_calibration_path}`: {e}"
        ) from e

    sensors = data.get("sensors")
    if not isinstance(sensors, list):
        raise ValueError(
            f"Malformed calibration JSON `{valid_calibration_path}`: missing or non-list 'sensors'."
        )

    camera_to_group: dict = {}
    for sensor_data in sensors:
        if not isinstance(sensor_data, dict):
            raise ValueError(
                f"Malformed calibration JSON `{valid_calibration_path}`: sensor entry "
                f"is not a dict (got {sensor_data!r})."
            )
        sensor_id = sensor_data.get("id")
        group = sensor_data.get("group")
        if sensor_id is None or not isinstance(group, dict) or "name" not in group:
            raise ValueError(
                f"Malformed calibration JSON `{valid_calibration_path}`: sensor entry "
                f"missing 'id' or 'group.name' (got {sensor_data!r})."
            )
        camera_to_group.setdefault(sensor_id, []).append(group["name"])
    return camera_to_group


def fetch_fps_from_calibration(calibration_file: str) -> float:
    """
    Retrieve the FPS value from a calibration file.

    :param calibration_file: Path to the calibration file.
    :type calibration_file: str
    :return: The frames per second (FPS) value.
    :rtype: float
    """
    valid_calibration_path = validate_file_path(calibration_file)
    if not os.path.exists(valid_calibration_path):
        raise FileNotFoundError(
            f"Calibration file `{valid_calibration_path}` does not exist."
        )

    try:
        calibration_info = load_json_from_file(valid_calibration_path)
    except ValueError as e:
        raise ValueError(
            f"Failed to load calibration JSON `{valid_calibration_path}`: {e}"
        ) from e

    sensors = calibration_info.get("sensors")
    if not isinstance(sensors, list):
        raise ValueError(
            f"Malformed calibration JSON `{valid_calibration_path}`: missing or non-list 'sensors'."
        )

    fps = None
    for sensor in sensors:
        if not isinstance(sensor, dict):
            raise ValueError(
                f"Malformed calibration JSON `{valid_calibration_path}`: sensor entry "
                f"is not a dict (got {sensor!r})."
            )
        attributes = sensor.get("attributes")
        if not isinstance(attributes, list):
            raise ValueError(
                f"Malformed calibration JSON `{valid_calibration_path}`: "
                f"sensor missing or non-list 'attributes' (got {sensor!r})."
            )
        for attribute in attributes:
            if not isinstance(attribute, dict):
                raise ValueError(
                    f"Malformed calibration JSON `{valid_calibration_path}`: "
                    f"attribute entry is not a dict "
                    f"(got {attribute!r}, type={type(attribute).__name__})."
                )
            name = attribute.get("name")
            if name is None or "value" not in attribute:
                raise ValueError(
                    f"Malformed calibration JSON `{valid_calibration_path}`: "
                    f"attribute missing 'name' or 'value' (got {attribute!r})."
                )
            if name == "fps":
                value = float(attribute["value"])
                if fps is None:
                    fps = value
                elif fps != value:
                    raise ValueError(
                        f"Unmatched FPS for sensors: {fps} != {value}."
                    )
    if fps is None:
        raise ValueError(
            f"FPS not available in calibration `{valid_calibration_path}`."
        )
    return fps


# ===========================================================================
# 3. Sensor-level curators
# ===========================================================================

def get_calib_dict(sensor):
    """
    Extract and format calibration matrices from a sensor dictionary.

    Supports two on-disk schemas (auto-detected via which key is
    present) and produces the same unified output in both cases:

    * **Synthetic schema** — ``sensor["intrinsicMatrix"]`` (3x3) plus
      ``sensor["extrinsicMatrix"]`` (3x4, world-to-camera).  K and
      ``w2c`` come out exactly as written.

    * **Real-world / AIC schema** — ``sensor["cameraMatrix"]`` (3x4,
      already the full world-to-pixel ``K @ [R | t]``).  Rather than
      RQ-decomposing into K and ``[R | t]`` (which introduces sign
      ambiguity), we store the 3x4 as the 4x4 ``"w2c_matrix"``
      directly (padded with ``[0, 0, 0, 1]``) and set
      ``"intrinsic_matrix"`` to the identity.  The downstream
      projection pipeline — which composes the two as
      ``intrinsic @ w2c`` — then produces the correct pixel
      coordinates verbatim, and the depth check still works because
      the third row of a world-to-pixel matrix is exactly the
      extrinsic's ``[R_2 | t_z]`` row (i.e. depth in camera space).

    Downstream consumers that need the "true" intrinsic / extrinsic
    split (e.g. to undistort points or reason about focal length) can
    detect the real-world case by checking whether
    ``"intrinsic_matrix"`` is the identity.

    If the sensor records its frame dimensions (``frameWidth`` /
    ``frameHeight`` under ``attributes``), the resulting dict also
    includes an ``"image_size"`` entry as ``[width, height]`` so
    downstream tools can do in-image visibility checks without a
    separate source of truth.  When the attributes are absent or
    malformed, ``"image_size"`` is omitted and callers fall back to
    the package default (:data:`spatialai_data_utils.constants.IMAGE_SIZE`).

    :param sensor: Single-sensor dict from a calibration JSON.
    :type sensor: dict
    :return: A dictionary containing ``"intrinsic_matrix"`` (3x3 list),
             ``"w2c_matrix"`` (4x4 list - world-to-camera **extrinsic**),
             ``"w2p_matrix"`` (4x4 list - world-to-pixel **projection**
             = ``K @ W2C``), and optionally ``"image_size"``
             (``[width, height]``).
    :rtype: dict
    :raises KeyError: If neither schema's required fields are present.
    """
    if "intrinsicMatrix" in sensor and "extrinsicMatrix" in sensor:
        # Synthetic / OmniverseSIM schema: real K + real [R | t].
        intrin_ext = np.eye(4)
        intrin_ext[:3, :3] = np.array(sensor["intrinsicMatrix"])
        extrin = np.eye(4)
        extrin[:3] = np.array(sensor["extrinsicMatrix"])
        proj_w2c = extrin
        proj_w2p = intrin_ext @ proj_w2c
    elif "cameraMatrix" in sensor:
        # Real-world / AIC schema: single 3x4 world-to-pixel matrix.
        # Use it directly as "w2c" (padded) with identity K — the
        # downstream projection pipeline applies intrinsic @ w2c, which
        # collapses to the cameraMatrix itself and gives correct pixels.
        proj_w2c = np.eye(4)
        proj_w2c[:3] = np.asarray(sensor["cameraMatrix"]).reshape(3, 4)
        intrin_ext = np.eye(4)
        proj_w2p = intrin_ext @ proj_w2c
    else:
        raise KeyError(
            f"Sensor {sensor.get('id', '<unknown>')!r} is missing both "
            f"('intrinsicMatrix' + 'extrinsicMatrix') and 'cameraMatrix'. "
            f"Available keys: {sorted(sensor.keys())}"
        )

    calib_info = {
        KEY_INTRINSIC_MATRIX: intrin_ext[:3, :3].tolist(),
        KEY_W2C_MATRIX: proj_w2c.tolist(),
        KEY_W2P_MATRIX: proj_w2p.tolist(),
    }
    image_size = _sensor_image_size(sensor)
    if image_size is not None:
        calib_info[KEY_IMAGE_SIZE] = [image_size[0], image_size[1]]
    return calib_info


def get_calib_dict_from_cam_data(cam_intrinsic, w2c_matrix):
    """Build a standard calibration dict from intrinsic and w2c extrinsic matrices.

    This is the pkl-data counterpart of :func:`get_calib_dict` (which works
    with NVSchema JSON sensor dicts).

    .. note::
       The ``sensor2world_transform`` field stored in data pkl files is
       actually the world-to-camera extrinsic matrix (with group-origin
       recentering already applied), despite its name.  Pass it directly
       as *w2c_matrix* — do **not** invert it.

    :param cam_intrinsic: 3x3 camera intrinsic matrix.
    :type cam_intrinsic: numpy.ndarray
    :param w2c_matrix: 4x4 world-to-camera extrinsic matrix.
    :type w2c_matrix: numpy.ndarray
    :return: Dictionary with ``"intrinsic_matrix"`` (3x3 list),
        ``"w2c_matrix"`` (4x4 list - extrinsic), and
        ``"w2p_matrix"`` (4x4 list - projection = ``K @ W2C``).
    :rtype: dict
    """
    intrinsic = np.asarray(cam_intrinsic, dtype=np.float64)
    w2c = np.asarray(w2c_matrix, dtype=np.float64)
    intrin_4x4 = np.eye(4)
    intrin_4x4[:3, :3] = intrinsic
    return {
        KEY_INTRINSIC_MATRIX: intrinsic.tolist(),
        KEY_W2C_MATRIX: w2c.tolist(),
        KEY_W2P_MATRIX: (intrin_4x4 @ w2c).tolist(),
    }


# ===========================================================================
# 4. Schema validation
# ===========================================================================

def _load_calibration_schema() -> dict:
    """Load and cache the bundled calibration JSON schema.

    :return: The calibration schema as a dictionary.
    :rtype: dict
    """
    global _calibration_schema
    if _calibration_schema is None:
        with open(_CALIBRATION_SCHEMA_PATH, "r") as f:
            _calibration_schema = json.load(f)
    return _calibration_schema


def validate_calibration_data(calibration_data: dict) -> None:
    """Validate calibration data against the bundled calibration JSON schema.

    :param calibration_data: The calibration data to validate.
    :type calibration_data: dict
    :raises jsonschema.exceptions.ValidationError: If the calibration data
        does not conform to the schema.
    """
    schema = _load_calibration_schema()
    validate(instance=calibration_data, schema=schema)


# ===========================================================================
# 5. Public NVSchema loaders
# ===========================================================================

def load_calib_json(input_path, load_original=False, validate=False):
    """Load raw calibration data from an NVSchema ``calibration.json``.

    Three input shapes are accepted, all yielding the same parsed JSON:

    1. *Directory* — looks for ``calibration.json`` inside it.
    2. *File named ``calibration.json``* — uses the file directly (its
       parent is the scene directory).
    3. *Any other JSON file* — loaded directly. Only valid in
       conjunction with ``load_original=True`` because the
       ID-keyed return shape relies on the NVSchema ``"sensors"``
       array layout, which an arbitrary file may not have.

    :param input_path: Directory, ``calibration.json`` file, or
        arbitrary JSON file. Accepts ``str`` or :class:`pathlib.Path`.
    :type input_path: str | pathlib.Path
    :param load_original: When ``True`` return the raw JSON dict
        (with the ``"sensors"`` array intact). When ``False`` return a
        flat ``{cam_id: sensor}`` mapping keyed by sensor id (the
        NVSchema-internal shape used downstream by the camera-grouping
        pipeline). Defaults to ``False``.
    :type load_original: bool, optional
    :param validate: When ``True`` validate the loaded data against the
        bundled calibration JSON schema via
        :func:`validate_calibration_data`. Validation failures are
        logged at WARNING level (not raised) so partially-valid
        calibration files remain inspectable. Defaults to ``False``.
    :type validate: bool, optional
    :return: Raw JSON dict if ``load_original=True``; flat
        ``{cam_id: sensor}`` mapping otherwise.
    :rtype: dict
    :raises ValueError: If ``input_path`` is an arbitrary JSON file but
        ``load_original=False`` (the ID-keyed shape requires the
        NVSchema layout).
    """
    input_path = Path(input_path)
    if input_path.is_dir():
        calib_json_path = input_path / "calibration.json"
    elif input_path.name == "calibration.json":
        calib_json_path = input_path
    else:
        if not load_original:
            raise ValueError(
                "load_original=False requires a scene directory or a "
                f"'calibration.json' file; got {input_path!r}. "
                "Pass load_original=True to load arbitrary JSON files."
            )
        calib_json_path = input_path

    with open(calib_json_path) as f:
        calib_dict_raw = json.load(f)

    if validate:
        try:
            validate_calibration_data(calib_dict_raw)
        except ValidationError as e:
            logger.warning(f"Calibration data validation failed: {e.message}")

    if load_original:
        return calib_dict_raw

    calib_dict = {}
    for sensor in calib_dict_raw["sensors"]:
        cam_name = sensor["id"]
        calib_dict[cam_name] = sensor
    return calib_dict


def _load_calib_and_groups_impl(calib_path, sensor_ids, recentering):
    """Shared core of the ``load_calib_*_from_json_path`` family.

    Returns a ``(calib_dict, cams_by_group)`` tuple.  The flat
    ``calib_dict`` half mirrors what
    :func:`load_calib_into_dict` has always produced; the
    ``cams_by_group`` half is ``{group_name: [cam_names]}`` for
    grouped calibrations (``sensors[*].group.name`` present — e.g.
    ``bev-sensor-1``) and an empty dict otherwise.

    *sensor_ids* filtering is applied to *calib_dict* only — the
    group→member mapping is preserved verbatim so callers can still
    answer "what cameras does a BEV group name map to?" even when
    their downstream processing is scoped to a subset.
    """
    result = load_calib_into_dict_native(calib_path)
    if isinstance(result, tuple):
        calib_dict_by_group, group_area_dict = result
        if recentering and group_area_dict:
            apply_recentering(calib_dict_by_group, group_area_dict)
        calib_dict = {}
        cams_by_group = {}
        for group_name, cams in calib_dict_by_group.items():
            calib_dict.update(cams)
            cams_by_group[group_name] = list(cams.keys())
    else:
        calib_dict = result
        cams_by_group = {}

    if sensor_ids is not None:
        calib_dict = {k: v for k, v in calib_dict.items() if k in sensor_ids}
    return calib_dict, cams_by_group


def load_calib_into_dict(calib_path, sensor_ids=None, recentering=False):
    """Load calibration from a direct JSON file path, flattening any groups.

    Wraps :func:`load_calib_into_dict_native` and merges all camera groups
    into a single flat ``{cam_name: calib_info}`` dictionary.

    When *recentering* is True and the calibration file contains group
    information with an origin, :func:`apply_recentering` is called on the
    grouped calibration before flattening.

    Callers that need the group-to-member-cameras mapping alongside
    the flat dict (e.g. to expand a BEV-sensor-id row into its member
    cameras) should use
    :func:`load_calib_into_dict_with_group_memberships` instead.

    :param calib_path: Path to a calibration JSON file.
    :type calib_path: str
    :param sensor_ids: Optional subset of camera names to include.
        ``None`` means all cameras.
    :type sensor_ids: list[str] or None
    :param recentering: Apply group-origin recentering. Defaults to False.
    :type recentering: bool
    :return: Flat calibration dictionary ``{cam_name: calib_info}``.
    :rtype: dict
    """
    calib_dict, _ = _load_calib_and_groups_impl(
        calib_path, sensor_ids, recentering,
    )
    return calib_dict


def load_calib_into_dict_with_group_memberships(calib_path, recentering=False):
    """Load decoded calibration + BEV-group memberships from a JSON file path.

    Sibling of :func:`load_calib_into_dict` that *additionally*
    surfaces the BEV group → member-cameras mapping carried by
    grouped calibrations (each ``sensors[*]`` entry has a
    ``"group": {"name": ..., "type": "bev", ...}`` block).  Output is
    still a **flat** ``{cam: calib_info}`` dict — the second tuple
    element is just a companion ``{group_name: [cam_names]}`` lookup
    table.  Callers that want the calibration **nested** by group
    instead should use :func:`load_calib_into_dict_grouped`.

    The membership map exists to resolve ambiguously-shaped
    ``sensorId`` fields downstream:

    * ``sensorId`` matches a concrete camera → project onto that one.
    * ``sensorId`` matches a group (e.g. ``bev-sensor-1``) → fan out
      over the group's member cameras.

    For ungrouped calibrations (legacy flat JSON, no ``"group"``
    field on sensors) the second dict is empty.

    Uses :func:`apply_recentering` internally when *recentering* is
    True and the calibration carries group-origin metadata — applied
    to the grouped dict *before* flattening, identical to the
    :func:`load_calib_into_dict` behaviour.

    No ``sensor_ids`` filtering is exposed here because the BEV-group
    use-case needs the full camera inventory to honour every member
    of a group.  Callers that want a subset can filter the returned
    ``calib_dict`` themselves.

    :param calib_path: Path to a calibration JSON file.
    :type calib_path: str
    :param recentering: Apply group-origin recentering. Defaults to False.
    :type recentering: bool
    :return: ``(calib_dict, cams_by_group)`` — a flat
        ``{cam_name: calib_info}`` dict plus a
        ``{group_name: [cam_names]}`` membership map (empty for
        ungrouped calibrations).
    :rtype: tuple(dict, dict)
    """
    return _load_calib_and_groups_impl(
        calib_path, sensor_ids=None, recentering=recentering,
    )


# ===========================================================================
# 6. Public pkl loader
# ===========================================================================

def load_calib_into_dict_from_pkl(pkl_path, sensor_ids=None):
    """Load calibration dict and per-frame info from a data pkl file.

    The pkl stores per-frame ``infos`` where each frame carries camera
    intrinsics and ``sensor2world_transform`` matrices.  A standard
    ``calib_dict`` (keyed by camera name) is built from the first frame
    and the full ``infos`` list is returned for frame-path look-up.

    The ``sensor2world_transform`` field in the pkl is actually the
    world-to-camera extrinsic with group-origin recentering already
    baked in, so no additional recentering step is needed.

    Assumes **static cameras**: only the first frame's calibration is used
    as the reference. If any subsequent frame has a different intrinsic or
    extrinsic matrix (within a small numerical tolerance), a warning is
    emitted — the returned calibration will still use the first frame.

    .. warning::
       **Security**: this function calls :func:`pickle.load` on the
       user-supplied path.  Pickle deserialization can execute
       arbitrary code (`CWE-502
       <https://cwe.mitre.org/data/definitions/502.html>`_).
       **Only load ``.pkl`` files from trusted sources.**  If you're
       consuming pkl files produced by someone else's training
       pipeline, verify the file's SHA-256 / provenance before calling
       this function.  A future release will migrate this format to a
       safer on-disk container (JSON / HDF5 / protobuf) — see the
       ``TODO`` below the ``pickle.load`` call.

    :param pkl_path: Path to the ``.pkl`` file.
    :type pkl_path: str
    :param sensor_ids: Optional subset of camera names to include.
        ``None`` means all cameras.
    :type sensor_ids: list[str] or None
    :return: ``(calib_dict, infos)`` — calibration dict keyed by camera
        name and the raw per-frame info list from the pkl.
    :rtype: tuple(dict, list)
    """
    # TODO(security): migrate this format off ``pickle`` to a safe
    # container (JSON / HDF5 / protobuf).  pickle.load executes
    # arbitrary code on malicious input — see the docstring warning
    # and the :func:`spatialai_data_utils.loaders.ground_truth.load_gt_from_pkl`
    # counterpart (both share the same underlying file and need to
    # migrate together).
    with open(pkl_path, "rb") as fh:
        data = pickle.load(fh)

    infos = data["infos"]
    ref_cams = infos[0][KEY_CAMS]

    calib_dict = {}
    for cam_name, cam_data in ref_cams.items():
        if sensor_ids is not None and cam_name not in sensor_ids:
            continue
        calib_dict[cam_name] = get_calib_dict_from_cam_data(
            cam_data[KEY_CAM_INTRINSIC],
            cam_data[KEY_SENSOR2WORLD],
        )

    # Check that per-frame calibration matches the reference (assume static).
    _warn_if_calibration_varies(infos, calib_dict)

    return calib_dict, infos


def _warn_if_calibration_varies(infos, calib_dict, atol=1e-6):
    """Emit a warning if any frame's calibration differs from the reference.

    :param infos: Per-frame info list from a data pkl.
    :param calib_dict: Reference calibration built from ``infos[0]``.
    :param atol: Absolute tolerance for matrix comparison.
    """
    for i, info in enumerate(infos[1:], start=1):
        cams = info.get(KEY_CAMS, {})
        for cam_name, ref_calib in calib_dict.items():
            cam_data = cams.get(cam_name)
            if cam_data is None:
                continue
            ref_intrin = np.asarray(ref_calib[KEY_INTRINSIC_MATRIX])
            ref_w2c = np.asarray(ref_calib[KEY_W2C_MATRIX])
            cur_intrin = np.asarray(cam_data[KEY_CAM_INTRINSIC])
            cur_w2c = np.asarray(cam_data[KEY_SENSOR2WORLD])
            if (
                not np.allclose(ref_intrin, cur_intrin, atol=atol)
                or not np.allclose(ref_w2c, cur_w2c, atol=atol)
            ):
                warnings.warn(
                    f"Calibration for camera '{cam_name}' varies between "
                    f"frame 0 and frame {i}. load_calib_into_dict_from_pkl assumes "
                    f"static cameras and uses only the first frame.",
                    UserWarning,
                    stacklevel=3,
                )
                return


# ===========================================================================
# 7. Internal building blocks (called only by the dispatcher)
# ===========================================================================

def _load_group(group_dict, key):
    """
    Helper function to extract group name and info if the type matches the key.

    :param group_dict: Dictionary representing the group information for a sensor.
    :type group_dict: dict
    :param key: The group type to look for (e.g., 'bev').
    :type key: str
    :return: Tuple (group_name, group_info_dict) if type matches, else (None, None).
             `group_info_dict` contains 'origin' and 'dimensions'.
    :rtype: tuple(str or None, dict or None)
    """
    if group_dict["type"] == key:
        group_name = group_dict["name"]
        group_info_dict = {
            "origin": group_dict["origin"],
            "dimensions": group_dict["dimensions"],
        }
        return group_name, group_info_dict
    else:
        return None, None


def _load_calib_buffer_zone(scene_path, group_name, calib_file_name):
    """
    Helper function to load calibration and group info for a specific buffer zone file.

    Constructs the path to the buffer zone calibration file, checks existence,
    and calls `load_calib_into_dict_grouped_buffer_zone` if found.

    :param scene_path: Path to the main scene directory.
    :type scene_path: str
    :param group_name: The name to assign to this buffer zone group (e.g., "bev-sensor-buffer-zone").
    :type group_name: str
    :param calib_file_name: The filename of the buffer zone calibration JSON.
    :type calib_file_name: str
    :return: Tuple ``(calib_dict_by_group, group_area_dict)`` as returned
             by :func:`load_calib_into_dict_grouped_buffer_zone`, or ``({}, {})``
             if the file doesn't exist.
    :rtype: tuple(dict, dict)
    """
    calib_path = os.path.join(scene_path, calib_file_name)
    if not os.path.exists(calib_path):
        print(
            f"[info] no buffer zone calibration found for {scene_path}, {calib_file_name}"
        )
        return {}, {}
    calib_dict_by_group, group_area_dict = load_calib_into_dict_grouped_buffer_zone(
        calib_path, group_name,
    )
    return calib_dict_by_group, group_area_dict


def load_calib_into_dict_native(calib_json_path):
    """
    Load calibration data from a generic synthetic dataset calibration JSON file.

    Parses a JSON file containing a list of sensor dictionaries and extracts
    calibration info for each sensor using ``get_calib_dict``.  The return
    shape depends on whether the calibration JSON declares BEV groupings:

    * **Grouped** (each ``sensors[*]`` entry carries a ``"group"`` block) →
      returns ``(calib_dict_by_group, group_area_dict)``: a nested
      ``{group_name: {cam_name: calib_info}}`` plus the per-group
      origin/dimensions metadata.
    * **Ungrouped** (legacy flat JSON, no ``"group"`` field) → returns a
      flat ``calib_dict`` (``{cam_name: calib_info}``).

    Callers that always want a flat dict regardless of source layout
    should use :func:`load_calib_into_dict` instead, which
    handles the union internally.

    :param calib_json_path: Path to the calibration JSON file.
    :type calib_json_path: str
    :return: ``calib_dict`` (flat) for ungrouped files, or
        ``(calib_dict_by_group, group_area_dict)`` for grouped files.
    :rtype: dict or tuple(dict, dict)
    """
    with open(calib_json_path) as f:
        calib_json_dict = json.load(f)

    use_group = "group" in calib_json_dict["sensors"][0]

    if use_group:
        calib_dict_by_group = {}
        group_area_dict = {}
        for sensor in calib_json_dict["sensors"]:
            cam_name = sensor["id"]
            group_name = sensor["group"]["name"]
            if group_name not in calib_dict_by_group:
                calib_dict_by_group[group_name] = {}
            calib_dict_by_group[group_name][cam_name] = get_calib_dict(sensor)
            group_name, group_info_dict = _load_group(sensor["group"], key="bev")
            if group_name not in group_area_dict:
                group_area_dict[group_name] = group_info_dict
        return calib_dict_by_group, group_area_dict

    calib_dict = {}
    for sensor in calib_json_dict["sensors"]:
        cam_name = sensor["id"]
        calib_dict[cam_name] = get_calib_dict(sensor)
    return calib_dict


def load_calib_into_dict_grouped_buffer_zone(calib_json_path, group_name):
    """
    Load calibration data and group cameras based on 'bev' group info from a JSON file.

    Assumes the calibration file contains sensors belonging to a single BEV group
    (e.g., buffer zone files). Extracts calibration for each sensor and organizes
    it under the provided `group_name`. Also extracts the group's area info (origin, dimensions).

    :param calib_json_path: Path to the calibration JSON file (e.g., a buffer zone file).
    :type calib_json_path: str
    :param group_name: The name to assign to the loaded camera group.
    :type group_name: str
    :return: A tuple containing:
             - calib_dict_by_group (dict): Nested dict
               ``{group_name: {cam_name: calib_info}}``.
             - group_area_dict (dict): Dictionary {`group_name`: {'origin': ..., 'dimensions': ...}}.
    :rtype: tuple(dict, dict)
    :raises AssertionError: If the JSON file contains sensors belonging to more than one BEV group.
    """
    "assume there is only one group in the calibration json file (buffer zone calibration files)"
    with open(calib_json_path) as f:
        calib_json_dict = json.load(f)
    calib_dict_by_group = {}
    group_area_dict = {}
    group_names_recorded = []
    for sensor in calib_json_dict["sensors"]:
        cam_name = sensor["id"]
        bev_group_name, bev_group_info_dict = _load_group(sensor["group"], key="bev")
        if bev_group_name not in group_names_recorded:
            group_names_recorded.append(bev_group_name)
        bev_group_name = group_name
        if bev_group_name not in calib_dict_by_group:
            calib_dict_by_group[bev_group_name] = {}
            group_area_dict[bev_group_name] = bev_group_info_dict
        calib_dict_by_group[bev_group_name][cam_name] = get_calib_dict(sensor)
    assert len(group_names_recorded) == 1, (
        "there should be only one group in the calibration json file (buffer zone calibration files)"
    )
    return calib_dict_by_group, group_area_dict


def load_calib_into_dict_grouped(
    scene_dir,
    use_training_grouping=False,
    use_sparse_training_camera_groups=False,
):
    """
    Load calibration data with camera groupings (standard or training) for a synthetic scene.

    Loads calibration from either 'calibration_grouped.json' or 'calibration_training.json'.
    Parses sensors, extracts their BEV group information using `_load_group`, and organizes
    calibration data into a nested dictionary structure: {group_name: {cam_name: calib_info}}.
    Also loads and merges calibration data from any defined buffer zone files.

    :param scene_dir: Path to the scene directory.
    :type scene_dir: str
    :param use_training_grouping: If True, loads from 'calibration_training.json' and
                                  renames groups; otherwise loads from 'calibration_grouped.json'.
                                  Defaults to False.
    :type use_training_grouping: bool, optional
    :param use_sparse_training_camera_groups: If True, loads from 'calibration_grouped_sparser.json' or
                                              'calibration_sparser.json' and renames groups;
                                              otherwise loads from 'calibration_grouped.json'.
                                              Defaults to False.
    :type use_sparse_training_camera_groups: bool, optional
    :return: A tuple containing:
             - calib_dict_by_group (dict): Nested dict
               ``{group_name: {cam_name: calib_info}}`` including
               buffer zones.
             - group_area_dict (dict): Dictionary {group_name: {'origin': ..., 'dimensions': ...}} for all groups.
    :rtype: tuple(dict, dict)
    """

    if use_training_grouping:
        calib_json_path = os.path.join(scene_dir, "calibration_training.json")
    elif use_sparse_training_camera_groups:
        calib_json_path = os.path.join(scene_dir, "calibration_grouped_sparser.json")
        if not os.path.exists(calib_json_path):
            calib_json_path = os.path.join(scene_dir, "calibration_sparser.json")
    else:
        calib_json_path = os.path.join(scene_dir, "calibration_grouped.json")

    calib_dict_by_group = {}
    group_area_dict = {}

    if not os.path.exists(calib_json_path):
        print(f"[info] no calibration info found for {scene_dir}, {calib_json_path}")

    else:
        print(f"loading calibration info from {calib_json_path} ...")
        with open(calib_json_path) as f:
            calib_json_dict = json.load(f)

        for sensor in calib_json_dict["sensors"]:
            cam_name = sensor["id"]
            bev_group_name, bev_group_info_dict = _load_group(
                sensor["group"], key="bev"
            )
            if use_training_grouping:
                bev_group_name = bev_group_name.replace(
                    "bev-sensor", "bev-sensor-training"
                )
            if use_sparse_training_camera_groups:
                bev_group_name = bev_group_name.replace(
                    "bev-sensor", "bev-sensor-sparser"
                )
            if bev_group_name not in calib_dict_by_group:
                calib_dict_by_group[bev_group_name] = {}
                group_area_dict[bev_group_name] = bev_group_info_dict
            calib_dict_by_group[bev_group_name][cam_name] = get_calib_dict(sensor)

    for buffer_zone_name in BUFFER_ZONE_NAMES.keys():
        calib_dict_buffer_zone, group_area_dict_buffer_zone = _load_calib_buffer_zone(
            scene_dir, buffer_zone_name, BUFFER_ZONE_NAMES[buffer_zone_name]
        )
        calib_dict_by_group.update(calib_dict_buffer_zone)
        group_area_dict.update(group_area_dict_buffer_zone)

    return calib_dict_by_group, group_area_dict


def load_calib_into_dict_grouped_random(
    scene_dir,
    n_groups=10,
    n_cams_range_per_group=[4, 10],
):
    """
    Load calibration and create random camera groups for a synthetic scene.

    Loads the base 'calibration.json', then randomly samples cameras without replacement
    to create `n_groups`, each containing a random number of cameras within the specified range.
    Useful for creating diverse training/evaluation scenarios.

    :param scene_dir: Path to the scene directory containing 'calibration.json'.
    :type scene_dir: str
    :param n_groups: The number of random groups to create. Defaults to 10.
    :type n_groups: int, optional
    :param n_cams_range_per_group: List or tuple [min, max] specifying the range for the
                                   number of cameras per group. Defaults to [4, 10].
    :type n_cams_range_per_group: list[int] or tuple(int, int), optional
    :return: A tuple containing:
             - calib_dict_by_group (dict): Nested dictionary
               ``{random_group_name: {cam_name: calib_info}}``.
             - group_area_dict (dict): An empty dictionary (random groups don't have predefined areas).
    :rtype: tuple(dict, dict)
    """
    # load calibration info from the original json file.
    # ``load_calib_into_dict_native`` returns either a flat
    # ``{cam_name: calib_info}`` dict (ungrouped JSON) **or** a
    # ``(calib_dict_by_group, group_area_dict)`` tuple (grouped JSON).
    # The random loader only cares about the flat camera-name set, so
    # flatten the tuple variant up front — keeps this entry point
    # compatible with both on-disk shapes.
    calib_dict_native = load_calib_into_dict_native(
        os.path.join(scene_dir, "calibration.json")
    )
    if isinstance(calib_dict_native, tuple):
        calib_dict_by_group_native, _group_areas_native = calib_dict_native
        calib_dict = {}
        for group_cams in calib_dict_by_group_native.values():
            calib_dict.update(group_cams)
    else:
        calib_dict = calib_dict_native
    calib_dict_raw = load_calib_json(scene_dir)
    # clip the range of the number of cameras per group
    n_cams_all = len(calib_dict)
    n_cams_range_per_group[0] = min(n_cams_range_per_group[0], n_cams_all)
    n_cams_range_per_group[1] = min(n_cams_range_per_group[1], n_cams_all)

    calib_dict_by_group = {}
    group_area_dict = {}
    calib_dict_raw_grouped = {}

    # randomly select n_groups from the original calibration info
    for group_id in range(n_groups):
        group_name = f"bev-sensor-random-{group_id}"
        calib_dict_by_group[group_name] = {}
        calib_dict_raw_grouped[group_name] = {}
        n_cams_per_group = random.randint(
            n_cams_range_per_group[0], n_cams_range_per_group[1]
        )
        for cam_name in random.sample(list(calib_dict.keys()), n_cams_per_group):
            calib_dict_by_group[group_name][cam_name] = calib_dict[cam_name]
            calib_dict_raw_grouped[group_name][cam_name] = calib_dict_raw[cam_name]
        group_origin, group_dimensions = calculate_group_origin(
            calib_dict_raw_grouped[group_name],
            list(calib_dict_raw_grouped[group_name].keys()),
        )
        group_area_dict[group_name] = {
            "origin": group_origin,
            "dimensions": group_dimensions,
        }
    return calib_dict_by_group, group_area_dict


def load_calib_into_dict_from_bevformer(scene_dir):
    """
    Load calibration data from the 'calibration_bevformer.json' file for a scene.

    This format was likely used in AICity 2024 (or similar BEVFormer-based setups).

    :param scene_dir: Path to the scene directory containing 'calibration_bevformer.json'.
    :type scene_dir: str
    :return: The loaded calibration dictionary, typically mapping camera names to their
             calibration details (intrinsic, extrinsic, etc.).
    :rtype: dict
    """
    with open(os.path.join(scene_dir, "calibration_bevformer.json")) as f:
        calib_dict = json.load(f)
    return calib_dict


# ===========================================================================
# 8. Master dispatcher
# ===========================================================================

def load_calib(
    scene_dir,
    calib_mode="aic24",
    camera_group_config=None,
    use_customized_calib: str = None,
):
    """
    Master function to load calibration based on mode and grouping configuration.

    Selects the appropriate loading function based on `calib_mode` ('aic24' or 'aic25').
    For 'aic25', further uses `camera_group_config` dictionary to determine whether to load
    standard grouped calibration, training groups, random groups, or combinations thereof.

    :param scene_dir: Path to the scene directory.
    :type scene_dir: str
    :param calib_mode: Specifies the calibration format/version ('aic24' or 'aic25').
                       Defaults to "aic24".
    :type calib_mode: str, optional
    :param camera_group_config: Dictionary controlling camera grouping behavior for 'aic25' mode.
                                Keys can include 'use_camera_groups', 'use_training_camera_groups',
                                'use_random_camera_groups', 'n_groups', 'n_cams_range_per_group'.
                                If None for 'aic25', loads the ungrouped 'calibration.json'.
                                Defaults to None.
    :type camera_group_config: dict, optional
    :return: For ``"aic24"``: flat ``calib_dict`` (``{cam_name: calib_info}``).
             For ``"aic25"``: ``(calib_dict_or_by_group, group_area_dict)``
             where the first element is **nested**
             (``{group_name: {cam_name: calib_info}}``) when grouping is
             enabled (``use_customized_calib`` or
             ``camera_group_config`` is not None), and is **flat** when
             ``camera_group_config`` is ``None`` and the underlying
             ``calibration.json`` is ungrouped.  ``group_area_dict``
             carries per-group origin/dimensions metadata for non-random
             groups (``None`` for the ungrouped flat case).
    :rtype: dict or tuple(dict, dict)
    :raises NotImplementedError: If `calib_mode` is unrecognized.
    :raises AssertionError: If required files ('calibration*.json') are missing or if
                            random grouping is requested without necessary parameters.
    """
    if calib_mode == "aic24":
        assert os.path.exists(os.path.join(scene_dir, "calibration_bevformer.json")), (
            "calibration_bevformer.json is not found"
        )
        calib_dict = load_calib_into_dict_from_bevformer(scene_dir)
        return calib_dict

    elif calib_mode == "aic25":
        if use_customized_calib:
            customized_calib_file_names = []
            for file_name in os.listdir(scene_dir):
                if file_name.startswith(
                    f"calibration_{use_customized_calib}"
                ) and file_name.endswith(".json"):
                    customized_calib_file_names.append(file_name)
            assert len(customized_calib_file_names) > 0, (
                f"no customized calibration file found for {use_customized_calib}"
            )

            calib_dict_by_group = {}
            group_area_dict = {}
            for customized_calib_file_name in customized_calib_file_names:
                group_name = (
                    customized_calib_file_name.replace("calibration_", "bev-sensor-")
                    .replace("_", "-")
                    .replace(".json", "")
                )
                # ``load_calib_into_dict_native`` is union-shaped: a tuple
                # ``(calib_dict_by_group, group_area_dict)`` for grouped
                # files, otherwise a flat ``calib_dict``.
                curr_result = load_calib_into_dict_native(
                    os.path.join(scene_dir, customized_calib_file_name)
                )
                if isinstance(curr_result, tuple) and len(curr_result) == 2:
                    # multiple groups in the calibration file
                    curr_calib_dict_by_group, curr_group_area_dict = curr_result
                    calib_dict_by_group.update(curr_calib_dict_by_group)
                    group_area_dict.update(curr_group_area_dict)
                    # update group names in calib_dict_by_group and group_area_dict
                    group_names_to_remove = list(calib_dict_by_group.keys())
                    for group_name in list(calib_dict_by_group.keys()):
                        new_group_name = group_name.replace(
                            "bev-sensor-", f"{use_customized_calib}-bev-sensor-"
                        )
                        calib_dict_by_group[new_group_name] = deepcopy(
                            calib_dict_by_group[group_name]
                        )
                        group_area_dict[new_group_name] = deepcopy(
                            group_area_dict[group_name]
                        )
                    for group_name in group_names_to_remove:
                        del calib_dict_by_group[group_name]
                        del group_area_dict[group_name]
                    continue
                # flat curr_result (ungrouped file): wrap as a single group
                curr_calib_dict = curr_result
                calib_dict_by_group[group_name] = curr_calib_dict
                try:
                    group_origin, group_dimensions = calculate_group_origin(
                        curr_calib_dict, list(curr_calib_dict.keys())
                    )
                    group_area_dict[group_name] = {
                        "origin": group_origin,
                        "dimensions": group_dimensions,
                    }
                except Exception as e:  # noqa: BLE001
                    # Broad catch is intentional: ``calculate_group_origin``
                    # can raise a wide range of exception types (``KeyError`` /
                    # ``TypeError`` / ``AttributeError`` from sensor-dict shape
                    # mismatches, ``ValueError`` / shapely ``GEOSException``
                    # from degenerate polygons, etc.). One bad group's
                    # geometry must not break the whole calibration load —
                    # the explicit ``None`` below is the documented degraded
                    # state downstream consumers handle.
                    logger.warning(
                        f"group area info calculation failed for {group_name}: {e}"
                    )
                    group_area_dict[group_name] = None
            return calib_dict_by_group, group_area_dict

        assert os.path.exists(os.path.join(scene_dir, "calibration.json")), (
            "calibration.json is not found"
        )

        if camera_group_config is None:
            # ``load_calib_into_dict_native`` is union-shaped: a flat
            # ``calib_dict`` for ungrouped files, or a nested
            # ``(calib_dict_by_group, group_area_dict)`` tuple for grouped
            # files.  Both flow through the bottom return verbatim.
            calib_dict = load_calib_into_dict_native(
                os.path.join(scene_dir, "calibration.json"),
            )
            return calib_dict, None

        use_grouping = camera_group_config.get(
            "use_camera_groups", False
        )  # load calibration_grouped.json
        use_training_grouping = camera_group_config.get(
            "use_training_camera_groups", False
        )  # load calibration_training.json
        use_sparse_training_camera_groups = camera_group_config.get(
            "use_sparse_training_camera_groups", False
        )
        use_random_grouping = camera_group_config.get(
            "use_random_camera_groups", False
        )
        n_groups = camera_group_config.get("n_groups", None)
        n_cams_range_per_group = camera_group_config.get(
            "n_cams_range_per_group", None
        )

        assert (
            use_grouping
            or use_training_grouping
            or use_sparse_training_camera_groups
            or use_random_grouping
        ), (
            "use_grouping and use_training_grouping and use_sparse_training_camera_groups and use_random_grouping cannot be all False"
        )

        calib_dict_by_group = {}
        group_area_dict = {}

        if use_grouping:
            curr_calib_dict_by_group, curr_group_area_dict = (
                load_calib_into_dict_grouped(
                    scene_dir,
                    use_training_grouping=False,
                )
            )
            calib_dict_by_group.update(curr_calib_dict_by_group)
            group_area_dict.update(curr_group_area_dict)

        if use_training_grouping:
            curr_calib_dict_by_group, curr_group_area_dict = (
                load_calib_into_dict_grouped(
                    scene_dir,
                    use_training_grouping=True,
                )
            )
            calib_dict_by_group.update(curr_calib_dict_by_group)
            group_area_dict.update(curr_group_area_dict)

        if use_sparse_training_camera_groups:
            curr_calib_dict_by_group, curr_group_area_dict = (
                load_calib_into_dict_grouped(
                    scene_dir,
                    use_sparse_training_camera_groups=True,
                )
            )
            calib_dict_by_group.update(curr_calib_dict_by_group)
            group_area_dict.update(curr_group_area_dict)

        if use_random_grouping:
            assert n_groups is not None, (
                "n_groups must be specified if use_random_grouping is True"
            )
            assert n_cams_range_per_group is not None, (
                "n_cams_range_per_group must be specified if use_random_grouping is True"
            )

            curr_calib_dict_by_group, curr_group_area_dict = (
                load_calib_into_dict_grouped_random(
                    scene_dir,
                    n_groups=n_groups,
                    n_cams_range_per_group=n_cams_range_per_group,
                )
            )
            calib_dict_by_group.update(curr_calib_dict_by_group)
            group_area_dict.update(curr_group_area_dict)

        return calib_dict_by_group, group_area_dict

    else:
        raise NotImplementedError


# ===========================================================================
# 9. High-level resolvers
# ===========================================================================

def resolve_scene_calib(
    scene_root,
    group_name=None,
    calib_mode="aic25",
    recentering=False,
    prebuilt_calib=None,
):
    """Return the flat per-camera calibration dict for a scene.

    Resolves the calibration dict used by downstream scene-level drivers
    (e.g. :func:`spatialai_data_utils.visualization.render.process_scene`)
    from one of two sources:

    * *prebuilt_calib* supplied — validated as a flat ``{cam: calib}``
      dict and returned as-is.
    * otherwise — loads calibration from *scene_root* via
      :func:`load_calib`, optionally applies recentering, and unwraps
      the group layer when a *group_name* was parsed from the scene
      name (or its `+sparser` variant).

    :param scene_root: Directory containing the scene's calibration file.
    :type scene_root: str
    :param group_name: Camera-group suffix from the full scene name
        (``"<scene>+<group>"``).  ``None`` means no group unwrapping.
        Groups containing ``"sparser"`` activate the sparse-training
        camera-group config.
    :type group_name: str or None
    :param calib_mode: Calibration format (e.g. ``"aic24"`` or
        ``"aic25"``).  Forwarded to :func:`load_calib`.
    :type calib_mode: str
    :param recentering: If True and a group-area dict is returned by
        :func:`load_calib`, apply :func:`apply_recentering` before
        flattening.
    :type recentering: bool
    :param prebuilt_calib: Already-loaded flat calibration dict. When
        provided, the on-disk calibration is skipped entirely.
    :type prebuilt_calib: dict or None
    :returns: Flat ``{camera_name: calib_info}`` dict with
        ``"intrinsic_matrix"`` / ``"w2c_matrix"`` entries.
    :rtype: dict[str, dict]
    :raises ValueError: If *prebuilt_calib* has a nested
        (``{group: {cam: calib}}``) shape instead of the expected flat
        shape — callers are responsible for flattening before passing in.
    """
    if prebuilt_calib is not None:
        # Sanity check: prebuilt_calib must be flat ({cam_name: calib_info}),
        # not nested ({group_name: {cam_name: calib_info}}).  Accept both
        # the canonical KEY_INTRINSIC_MATRIX and the legacy
        # ``"intrinsic matrix"`` (with space) key for backward compat.
        sample_value = next(iter(prebuilt_calib.values()), None)
        if sample_value is not None and get_calib_field(
            sample_value, KEY_INTRINSIC_MATRIX, default=None,
        ) is None:
            raise ValueError(
                "prebuilt_calib must be a flat dict keyed by camera name with "
                "'intrinsic_matrix' / 'w2c_matrix' entries (or their legacy "
                "equivalents 'intrinsic matrix' / 'projection matrix w2c'). "
                "Got a nested structure — flatten groups before passing in."
            )
        return prebuilt_calib

    if group_name is not None:
        use_sparser = "sparser" in group_name
        camera_group_config = {
            "use_camera_groups": True,
            "use_training_camera_groups": not use_sparser,
            "use_sparse_training_camera_groups": use_sparser,
            "use_random_camera_groups": False,
            "n_groups": None,
            "n_cams_range_per_group": None,
        }
    else:
        camera_group_config = None

    calib_result = load_calib(
        scene_root, calib_mode=calib_mode,
        camera_group_config=camera_group_config,
    )
    if isinstance(calib_result, tuple):
        # Nested return from ``load_calib`` (aic25 with grouping):
        # ``calib_dict_by_group`` carries ``{group: {cam: calib_info}}``.
        calib_dict_by_group, group_area_dict = calib_result
        if recentering and group_area_dict is not None:
            apply_recentering(calib_dict_by_group, group_area_dict)
        if group_name is not None:
            if group_name not in calib_dict_by_group:
                raise KeyError(
                    f"Requested camera-group {group_name!r} not found in "
                    f"calibration loaded from {scene_root!r}. "
                    f"Available groups: {sorted(calib_dict_by_group.keys())}"
                )
            return calib_dict_by_group[group_name]
        return calib_dict_by_group

    # Flat return from ``load_calib`` (aic24, or aic25 with an ungrouped
    # ``calibration.json`` source).  No group layer to recentre.
    calib_dict = calib_result
    if group_name is not None:
        # Legacy callers occasionally passed a camera name in place of a
        # group name on flat sources.  Slice the camera entry to preserve
        # the historical return type (``calib_info`` for that one camera);
        # raise the same ``KeyError`` as the nested branch when the name
        # is unknown.
        if group_name not in calib_dict:
            raise KeyError(
                f"Requested name {group_name!r} not found in flat "
                f"calibration loaded from {scene_root!r}. "
                f"Available cameras: {sorted(calib_dict.keys())}"
            )
        return calib_dict[group_name]
    return calib_dict


# ===========================================================================
# 10. Transformations
# ===========================================================================

def apply_recentering(calib_dict_by_group, group_area_dict):
    """Shift world-to-camera extrinsics so each group's origin maps to (0, 0).

    Modifies *calib_dict_by_group* in-place and returns it.

    :param calib_dict_by_group: Nested calibration dict
        ``{group_name: {cam_name: calib_info}}``.
    :type calib_dict_by_group: dict
    :param group_area_dict: ``{group_name: {"origin": [x, y], ...}}``.
    :type group_area_dict: dict
    :return: The (modified) *calib_dict_by_group*.
    :rtype: dict
    """
    for group_name, group_info in group_area_dict.items():
        if group_info is None:
            continue
        if not isinstance(group_info, dict) or KEY_ORIGIN not in group_info:
            raise KeyError(
                f"group_area_dict['{group_name}'] is missing required "
                f"'{KEY_ORIGIN}' key. Got: {group_info}"
            )
        origin = group_info[KEY_ORIGIN]
        if group_name not in calib_dict_by_group:
            continue
        for cam_name in calib_dict_by_group[group_name]:
            cam_info = calib_dict_by_group[group_name][cam_name]
            # Read with legacy-key fallback; write with the canonical
            # new keys (the recenter step canonicalises whatever shape
            # the input arrived in).
            w2c = np.array(get_calib_field(cam_info, KEY_W2C_MATRIX))
            c2w = np.linalg.inv(w2c)
            c2w[0, -1] -= origin[0]
            c2w[1, -1] -= origin[1]
            new_w2c = np.linalg.inv(c2w)

            # Rebuild w2p so the shifted w2c and the cached w2p stay in
            # sync; otherwise downstream projection code that relies on
            # w2p (e.g. legacy callers of build_world2img_from_calib) would
            # keep using the pre-recentering world-to-pixel matrix.
            intrinsic_4x4 = np.eye(4)
            intrinsic_4x4[:3, :3] = np.array(
                get_calib_field(cam_info, KEY_INTRINSIC_MATRIX)
            )
            new_w2p = intrinsic_4x4 @ new_w2c

            cam_info[KEY_W2C_MATRIX] = new_w2c.tolist()
            cam_info[KEY_W2P_MATRIX] = new_w2p.tolist()
    return calib_dict_by_group
