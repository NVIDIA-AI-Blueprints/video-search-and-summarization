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
Camera-calibration data helpers used by the camera-grouping pipeline.

Public surface:

* :func:`get_calib_field` — read a single-camera ``calib_info`` field
  with backward-compat fallback for legacy key names.
* :func:`extract_camera_matrices` — pull intrinsic / extrinsic numpy
  matrices out of a sensor calibration dict (handles both processed
  and raw formats).
* :func:`save_calibration_data` — write a calibration dict to a JSON
  file (used by the camera-grouping pipeline to persist regenerated
  calibration files; symmetric of the loaders, which live in
  :mod:`spatialai_data_utils.loaders.calibration`).
* :func:`load_map_data` — load a 2D floor-plan map image used by the
  visualization stack.

Calibration-file *loading* and *schema validation* live in
:mod:`spatialai_data_utils.loaders.calibration`.
"""

import json
import logging
import os
from pathlib import Path
from typing import Optional, Tuple

import numpy as np
from PIL import Image

from spatialai_data_utils.constants import (
    KEY_IMAGE_SIZE,
    KEY_INTRINSIC_MATRIX,
    KEY_W2C_MATRIX,
    KEY_W2P_MATRIX,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Calibration-dict key access (with legacy-key backward-compat fallback)
# ---------------------------------------------------------------------------

# Legacy → new key mapping for backward-compat reads.  Used by
# :func:`get_calib_field` to transparently accept old-style dict keys
# ("intrinsic matrix" with a space, "projection matrix w2c" misnaming
# the extrinsic, etc.) without forcing a one-shot data migration of
# every saved calibration file / pickled dict.  The current canonical
# keys are owned by :mod:`spatialai_data_utils.constants` (this module
# only provides the read-side fallback behaviour).
_LEGACY_CALIB_KEYS_TO_NEW = {
    "intrinsic matrix": KEY_INTRINSIC_MATRIX,
    "projection matrix w2c": KEY_W2C_MATRIX,
    "projection matrix w2p": KEY_W2P_MATRIX,
    "image size": KEY_IMAGE_SIZE,
}

# Reverse lookup (new → legacy) so :func:`get_calib_field` can find
# the legacy string when only the canonical key is requested.
# Pre-built once because the map is tiny + immutable.
_NEW_CALIB_KEYS_TO_LEGACY = {
    new: legacy for legacy, new in _LEGACY_CALIB_KEYS_TO_NEW.items()
}


_CALIB_FIELD_MISSING = object()


def get_calib_field(calib_info, key, default=_CALIB_FIELD_MISSING):
    """Read a single-camera ``calib_info`` field, falling back to its legacy key.

    Tries the canonical *key* first (e.g. ``KEY_W2C_MATRIX ==
    "w2c_matrix"``); if not present, looks up the matching legacy
    string (``"projection matrix w2c"``) and returns that value
    instead.  This lets old calibration files / hand-built dicts using
    the pre-rename keys keep loading without a one-shot data
    migration.

    :param calib_info: Single-camera calibration dict.
    :type calib_info: dict
    :param key: Canonical (new) calibration field key — typically one
        of :data:`spatialai_data_utils.constants.KEY_INTRINSIC_MATRIX`,
        :data:`spatialai_data_utils.constants.KEY_W2C_MATRIX`,
        :data:`spatialai_data_utils.constants.KEY_W2P_MATRIX`,
        :data:`spatialai_data_utils.constants.KEY_IMAGE_SIZE`.
    :param default: Value to return when neither the canonical *key*
        nor its legacy alternative is present.  When omitted, a
        :class:`KeyError` is raised — same semantics as ``dict[key]``.
    :return: The field value (legacy or canonical), or *default*.
    :raises KeyError: When the field is missing and no *default* was
        supplied.
    """
    if key in calib_info:
        return calib_info[key]
    legacy = _NEW_CALIB_KEYS_TO_LEGACY.get(key)
    if legacy is not None and legacy in calib_info:
        return calib_info[legacy]
    if default is _CALIB_FIELD_MISSING:
        raise KeyError(
            f"Calibration field {key!r} not found "
            f"(also tried legacy {legacy!r}). "
            f"Available keys: {sorted(calib_info.keys())}"
        )
    return default


def load_map_data(map_file: Optional[Path] = None) -> Tuple[Optional[Path], Optional[Image.Image], Optional[int], Optional[int]]:
    """
    Normalize the map_file argument and load the map image dimensions once.
    Returns (map_path_or_none, map_width_or_none, map_height_or_none).
    """
    if map_file is None:
        return None, None, None, None
    # Accept Path-like inputs and normalize
    map_path_local = Path(map_file)
    if not map_path_local.exists():
        return None, None, None, None
    try:
        from PIL import Image

        # ``Image.open`` is lazy and keeps the file handle alive until
        # the returned Image is GC'd, which trips ResourceWarning under
        # strict pytest modes. Use the context manager + ``.copy()`` so
        # the underlying file is closed as soon as we've read pixels.
        with Image.open(map_path_local) as src_image:
            src_image.load()
            map_image_local = src_image.copy()
        width, height = map_image_local.size
        return map_path_local, map_image_local, width, height
    except Exception as exc:
        logger.warning(f"Could not load map image '{map_path_local}': {exc}")
        return map_path_local, None, None, None


def discover_map_file(
    input_path: Path, map_filename: str = "Top.png"
) -> Optional[Path]:
    """Locate the BEV / top-view map image for a scene.

    *input_path* is either the scene directory or the ``calibration.json``
    file inside it. The map image is searched for in the conventional
    locations, returning the first that exists:

    1. directly under the scene directory (``<scene>/Top.png``), and
    2. inside an ``images/`` subfolder (``<scene>/images/Top.png``) — the
       layout used by the warehouse sample-data calibration bundles.

    :param input_path: Scene directory or calibration-file path.
    :type input_path: Path
    :param map_filename: Map image filename to look for. Defaults to
        ``"Top.png"``.
    :type map_filename: str
    :return: Path to the first existing candidate, or ``None`` if neither
        location has the map image.
    :rtype: Path or None
    """
    base_dir = input_path if input_path.is_dir() else input_path.parent
    for candidate in (base_dir / map_filename, base_dir / "images" / map_filename):
        if candidate.is_file():
            return candidate
    return None


def extract_camera_matrices(sensor_data):
    """
    Extract intrinsic and extrinsic matrices from sensor calibration data.

    This function handles multiple calibration data formats and extracts the
    camera matrices needed for geometric calculations. Supports both processed
    and raw sensor data formats.

    :param sensor_data: Dictionary containing sensor calibration data.
    :type sensor_data: dict
    :return: Tuple of (intrinsic_matrix, extrinsic_matrix) or (None, None) if not found.
    :rtype: tuple(numpy.ndarray, numpy.ndarray) or tuple(None, None)
    """
    try:
        # Try to get from processed calibration format.  Accept both the
        # canonical new keys (``"intrinsic_matrix"`` / ``"w2c_matrix"``)
        # and the legacy ones (``"intrinsic matrix"`` / ``"projection
        # matrix w2c"``) via :func:`get_calib_field`'s fallback.
        intrinsic_val = get_calib_field(
            sensor_data, KEY_INTRINSIC_MATRIX, default=None,
        )
        w2c_val = get_calib_field(sensor_data, KEY_W2C_MATRIX, default=None)
        if intrinsic_val is not None and w2c_val is not None:
            intrinsic = np.array(intrinsic_val)
            extrinsic = np.array(w2c_val)

            # Validate matrix shapes
            if not _validate_camera_matrices(intrinsic, extrinsic):
                return None, None

            return intrinsic, extrinsic

        # Try to get from raw sensor format
        if "intrinsicMatrix" in sensor_data and "extrinsicMatrix" in sensor_data:
            intrinsic = np.array(sensor_data["intrinsicMatrix"])
            extrinsic = np.array(sensor_data["extrinsicMatrix"])

            # Validate matrix shapes
            if not _validate_camera_matrices(intrinsic, extrinsic):
                logger.warning(
                    f"Invalid camera matrices for sensor {sensor_data['id']}"
                )
                return None, None

            return intrinsic, extrinsic

    except Exception as e:
        logger.error(f"Error extracting camera matrices: {e}")

    return None, None


def _validate_camera_matrices(intrinsic, extrinsic):
    """
    Validate camera matrix shapes and data integrity.

    :param intrinsic: Intrinsic camera matrix.
    :type intrinsic: numpy.ndarray
    :param extrinsic: Extrinsic camera matrix.
    :type extrinsic: numpy.ndarray
    :return: True if matrices are valid, False otherwise.
    :rtype: bool
    """
    # Validate intrinsic matrix (should be 3x3 or 3x4)
    if intrinsic.shape not in [(3, 3), (3, 4)]:
        logger.warning(
            f"Invalid intrinsic matrix shape: {intrinsic.shape}. Expected (3, 3) or (3, 4)"
        )
        return False

    # Validate extrinsic matrix (should be 3x4 or 4x4)
    if extrinsic.shape not in [(3, 4), (4, 4)]:
        logger.warning(
            f"Invalid extrinsic matrix shape: {extrinsic.shape}. Expected (3, 4) or (4, 4)"
        )
        return False

    # Check for NaN or Inf values
    if np.any(np.isnan(intrinsic)) or np.any(np.isinf(intrinsic)):
        logger.warning("Intrinsic matrix contains NaN or Inf values")
        return False

    if np.any(np.isnan(extrinsic)) or np.any(np.isinf(extrinsic)):
        logger.warning("Extrinsic matrix contains NaN or Inf values")
        return False

    return True


def save_calibration_data(calibration_data, output_file):
    """Save calibration data to a JSON file (creating parent dirs as needed).

    Used by the camera-grouping pipeline (BEV / clustering / group-utils)
    to persist regenerated calibration files. The symmetric *load* side
    lives in :mod:`spatialai_data_utils.loaders.calibration`.

    :param calibration_data: Dictionary containing calibration data to save.
    :type calibration_data: dict
    :param output_file: Path to the output JSON file.
    :type output_file: str
    :raises OSError: If the file (or its parent directories) cannot be written.
    """
    try:
        output_dir = os.path.dirname(output_file)
        if output_dir and not os.path.exists(output_dir):
            os.makedirs(output_dir, exist_ok=True)

        with open(output_file, "w") as f:
            json.dump(calibration_data, f, indent=4)

    except (OSError, IOError) as e:
        logger.error(f"Failed to save calibration data to {output_file}: {e}")
        raise


