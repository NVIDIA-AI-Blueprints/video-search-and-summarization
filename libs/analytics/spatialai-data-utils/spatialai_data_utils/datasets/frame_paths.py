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
Frame-Path Resolution and PKL-Info Utilities Module.

Helpers that turn a ``(scene_dir, cam_name, frame_id)`` triple — or a
pkl-info entry — into an actual filesystem path to an image frame.
Split out of ``camera_name_utils`` so that module can stay focused on
just camera-name discovery / sorting / extraction.

Public functions:

* :func:`resolve_frame_path` — best-effort single-camera resolver
  (non-raising); honours an optional ``timestamp`` substring match.
* :func:`get_frame_path_of_single_camera` — strict single-camera
  resolver (raises if nothing matches the canonical layouts).
* :func:`get_frame_paths_of_multi_cameras` — multi-camera variant
  that returns ``{cam_name: path}``; supports both loose-image and
  HDF5 layouts.
* :func:`frame_paths_from_pkl_info` — extract per-camera frame paths
  from a single pkl-info entry's ``"cams"`` map.
* :func:`index_pkl_by_frame` — index a pkl ``infos`` list by
  ``frame_idx`` for fast lookup.
* :func:`resolve_frame_root` — pick the directory under which
  per-camera image folders live (handles the ``frames/`` layout).

Supported on-disk layouts:

1. Standard:    ``{scene_dir}/{cam_name}/images/{frame_id}.jpg``
2. Alternative: ``{scene_dir}/frames/{cam_name}/images/{frame_id}.jpg``
3. RGB:         ``{scene_dir}/{cam_name}/rgb/rgb_{frame_id}.png``
4. HDF5:        ``{scene_dir}/{cam_name}.h5/rgb/rgb_{frame_id}.jpg``
5. Bare:        ``{scene_dir}/{cam_name}/{frame_id}.{jpg,png,jpeg}``
"""

import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from spatialai_data_utils.constants import KEY_CAMS, KEY_DATA_PATH, KEY_FRAME_IDX
from spatialai_data_utils.datasets.scenes import get_cam_names_in_scene


_IMAGE_EXTS = (".png", ".jpg", ".jpeg")

# Embedded ISO substring used in image filenames, e.g.
# ``2025-04-14T00-36-45.109Z`` — drives :func:`find_frame_path_in_ts_range`.
# Time separator is permissive (``[-:]``) so the regex matches both
# the dashed filesystem-safe form (typical because most filesystems
# disallow ``:``) and the JSON ISO form (legal on Linux); the
# extracted substring is canonicalised via :func:`_ts_to_fs_safe` +
# :func:`_normalize_subsec_precision` before substring / range
# comparison.  Trailing ``Z`` is optional for tolerance.
_FILENAME_TIMESTAMP_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}[-:]\d{2}[-:]\d{2}\.\d+Z?"
)

# Pattern used by :func:`_normalize_subsec_precision` to find the
# fractional-seconds segment of an ISO-shaped timestamp.  Anchored on
# ``\.`` so it only fires inside an actual ``.\d+`` group; non-
# fractional dots (none in current callers, but defensive) are
# ignored.  Applied with ``count=1`` so timestamps with multiple
# numeric segments (rare) only get their first sub-second segment
# normalised.
_SUBSEC_RE = re.compile(r"\.(\d+)")

# Width that :func:`_normalize_subsec_precision` pads / truncates the
# sub-second fractional digits to.  9 = nanosecond precision — handles
# any ISO 8601 sub-second precision the toolkit is likely to encounter
# without truncation.
_CANONICAL_SUBSEC_WIDTH = 9


def get_frame_path_of_single_camera(scene_dir, cam_name, frame_id):
    """
    Construct the standard path to an image frame for a specific camera and frame ID.

    Checks for two common directory structures:

    1. ``scene_dir/cam_name/images/{frame_id:09}.{ext}``
    2. ``scene_dir/frames/cam_name/images/{frame_id:09}.{ext}`` (less common)

    For each candidate base directory the function tries every
    extension in :data:`_IMAGE_EXTS` (currently ``.png``, ``.jpg``,
    ``.jpeg``) and returns the first existing path.  Raises
    :class:`FileNotFoundError` (NOT ``AssertionError``) if no
    candidate exists, so the check still fires when Python is run
    with ``-O``.

    :param scene_dir: Path to the scene directory.
    :type scene_dir: str
    :param cam_name: The name/ID of the camera.
    :type cam_name: str
    :param frame_id: The frame ID (integer).
    :type frame_id: int
    :return: The constructed path to the image file.
    :rtype: str
    :raises FileNotFoundError: If no candidate path exists on disk.
    """
    if os.path.isdir(os.path.join(scene_dir, cam_name)):
        base_dir = os.path.join(scene_dir, cam_name)
    else:
        base_dir = os.path.join(scene_dir, "frames", cam_name)

    attempted = []
    for ext in _IMAGE_EXTS:
        candidate = os.path.join(base_dir, "images", f"{frame_id:09}{ext}")
        if os.path.exists(candidate):
            return candidate
        attempted.append(candidate)

    raise FileNotFoundError(
        f"No frame found for scene={scene_dir!r}, camera={cam_name!r}, "
        f"frame_id={frame_id}. Tried: {attempted}"
    )


def _build_non_h5_frame_patterns(scene_dir, cam_name, frame_id):
    """List of candidate filesystem paths a non-H5 image can live at.

    Order matters: the first existing match wins.  The list is the
    single source of truth for loose-JPG/PNG image layouts across the
    toolkit.  If you add a pattern here both
    :func:`resolve_frame_path` and
    :func:`get_frame_paths_of_multi_cameras` pick it up automatically.
    """
    return [
        # --- Historical layouts (AIC / Isaac / scout) -------------------
        os.path.join(scene_dir, cam_name, "images", f"{frame_id:09}.jpg"),
        os.path.join(scene_dir, cam_name, "rgb", f"rgb_{frame_id:05}.png"),
        os.path.join(scene_dir, cam_name, "rgb", f"rgb_{frame_id:05}.jpg"),
        os.path.join(scene_dir, cam_name, "rgb", f"{frame_id:09}.jpg"),
        os.path.join(scene_dir, cam_name, f"image_{frame_id}.jpg"),  # scout
        os.path.join(
            scene_dir, "frames", cam_name, "images", f"{frame_id:09}.jpg"
        ),
        # --- Bare <frame_id>.<ext> directly under the camera folder -----
        os.path.join(scene_dir, cam_name, f"{frame_id}.jpg"),
        os.path.join(scene_dir, cam_name, f"{frame_id}.png"),
        os.path.join(scene_dir, cam_name, f"{frame_id}.jpeg"),
    ]


def _ts_to_fs_safe(ts: str) -> str:
    """Replace ``:`` with ``-`` in an ISO timestamp.

    Bridges the JSON-style ISO format (``HH:MM:SS``) used in NVSchema
    rows / their per-camera ``info`` map to the filesystem-safe form
    (``HH-MM-SS``) typical of image filenames on filesystems which
    disallow ``:`` (FAT, Windows, ext4 with portability constraints).
    Idempotent — already-dashed inputs pass through unchanged.
    """
    return ts.replace(":", "-")


def _parse_iso_ts(ts: str) -> Optional[datetime]:
    """Best-effort parse of an ISO timestamp string to an aware
    :class:`datetime`.
    """
    # Convert dashed time portion (``T HH-MM-SS``) back to colon form
    # so :func:`datetime.fromisoformat` accepts it.  Preserves the
    # date's ``-`` separators verbatim.
    s = re.sub(r"T(\d{2})-(\d{2})-(\d{2})", r"T\1:\2:\3", ts)
    # Truncate sub-seconds to 6 digits (microsecond precision is the
    # limit of :class:`datetime`).
    s = re.sub(
        r"\.(\d{1,6})\d*",
        lambda m: "." + m.group(1).ljust(6, "0"),
        s,
        count=1,
    )
    # ``Z`` -> ``+00:00`` for compatibility with Python <= 3.10's
    # :func:`datetime.fromisoformat`.  Python 3.11+ accepts ``Z``
    # directly but normalising here keeps the helper version-agnostic.
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    # Naive datetimes (no offset in the input) are interpreted as UTC
    # so delta arithmetic is well-defined regardless of input convention.
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _normalize_subsec_precision(ts: str) -> str:
    """Right-pad / truncate first ``.\\d+`` segment to fixed width.

    This keeps lexicographic comparisons chronologically correct for
    timestamps with mixed fractional-second widths.
    """
    def _pad(m: "re.Match") -> str:
        digits = m.group(1)
        if len(digits) < _CANONICAL_SUBSEC_WIDTH:
            return "." + digits.ljust(_CANONICAL_SUBSEC_WIDTH, "0")
        return "." + digits[:_CANONICAL_SUBSEC_WIDTH]

    return _SUBSEC_RE.sub(_pad, ts, count=1)


def resolve_frame_path(
    scene_dir, cam_name, frame_id, timestamp=None, *,
    canonical_fallback: bool = True,
):
    """Best-effort single-camera image-path resolver (non-raising).

    Lookup order:

    1. **Timestamp-substring match** (only when *timestamp* is a
       non-empty string) — scan ``<scene_dir>/<cam_name>/`` (one
       level, no sub-folder recursion) in sorted order and return
       the first image file whose basename contains *timestamp* as a
       substring.  Both sides of the comparison are normalised via
       :func:`_ts_to_fs_safe` (``:`` <-> ``-``) **and**
       :func:`_normalize_subsec_precision` (right-pad sub-second
       digits to a fixed width) so JSON-form ISO timestamps match
       the dashed filesystem form, and timestamps with different
       sub-second widths (e.g. ``".1Z"`` vs ``".100Z"``) match.
       Filename extensions checked: ``.png`` / ``.jpg`` / ``.jpeg``
       (case-insensitive).
    2. **Canonical filesystem layouts** (skipped when
       ``canonical_fallback=False``) — delegates to
       :func:`_build_non_h5_frame_patterns` and returns the first
       existing match.  Covers the historical AIC / Isaac / scout /
       ``frames/`` variants plus bare ``<cam>/<frame_id>.<ext>``.
    3. If neither rule matches, returns ``None``.  Callers that need
       a hard guarantee should check the return value and raise
       their own error.

    :param scene_dir: Scene (or image-root) directory that contains
        per-camera sub-folders.
    :type scene_dir: str
    :param cam_name: Sensor / camera name (a sub-folder under
        *scene_dir* for the direct-timestamp / bare / rgb / images
        patterns; under ``scene_dir/frames/`` for the ``frames/``
        pattern).
    :type cam_name: str
    :param frame_id: Integer frame id as used by the NVSchema /
        gt_json_aicity contract (not zero-padded).
    :type frame_id: int
    :param timestamp: Optional timestamp string (e.g. the NVSchema
        row's ``"timestamp"`` field).  When present it is matched as
        a substring against filenames directly under the camera folder
        with ``:`` <-> ``-`` and sub-second-width normalisation on
        both sides.  ``None`` or empty string skips this branch
        entirely.
    :type timestamp: str or None
    :param canonical_fallback: When ``False``, the canonical-pattern
        branch is suppressed — the resolver returns ``None`` if the
        substring branch misses.  Defaults to ``True``.
    :type canonical_fallback: bool
    :return: Absolute filesystem path to the matching image, or
        ``None`` if no rule matches.
    :rtype: str or None
    """
    if timestamp:
        cam_dir = os.path.join(scene_dir, cam_name)
        if os.path.isdir(cam_dir):
            target = _normalize_subsec_precision(_ts_to_fs_safe(timestamp))
            for name in sorted(os.listdir(cam_dir)):
                if not name.lower().endswith(_IMAGE_EXTS):
                    continue
                canon_name = _normalize_subsec_precision(_ts_to_fs_safe(name))
                if target in canon_name:
                    return os.path.join(cam_dir, name)

    if not canonical_fallback:
        return None

    for path in _build_non_h5_frame_patterns(scene_dir, cam_name, frame_id):
        if os.path.exists(path):
            return path
    return None


def find_frame_path_in_ts_range(
    scene_dir: str, cam_name: str, ts_min: str, ts_max: str,
) -> Optional[str]:
    """Return first image whose embedded timestamp is in ``[ts_min, ts_max]``."""
    cam_dir = os.path.join(scene_dir, cam_name)
    if not os.path.isdir(cam_dir):
        return None
    canon_min = _normalize_subsec_precision(_ts_to_fs_safe(ts_min))
    canon_max = _normalize_subsec_precision(_ts_to_fs_safe(ts_max))
    for name in sorted(os.listdir(cam_dir)):
        if not name.lower().endswith(_IMAGE_EXTS):
            continue
        m = _FILENAME_TIMESTAMP_RE.search(name)
        if m is None:
            continue
        canon_file = _normalize_subsec_precision(_ts_to_fs_safe(m.group(0)))
        if canon_min <= canon_file <= canon_max:
            return os.path.join(cam_dir, name)
    return None


def find_nearest_frame_path(
    scene_dir: str, cam_name: str, target_ts: str, *,
    window_ms: int = 500,
) -> Optional[str]:
    """Return nearest timestamped image within ``±window_ms``."""
    cam_dir = os.path.join(scene_dir, cam_name)
    if not os.path.isdir(cam_dir):
        return None
    target_dt = _parse_iso_ts(target_ts)
    if target_dt is None:
        return None
    best_path: Optional[str] = None
    best_delta_ms: Optional[float] = None
    for name in sorted(os.listdir(cam_dir)):
        if not name.lower().endswith(_IMAGE_EXTS):
            continue
        m = _FILENAME_TIMESTAMP_RE.search(name)
        if m is None:
            continue
        file_dt = _parse_iso_ts(m.group(0))
        if file_dt is None:
            continue
        delta_ms = abs((file_dt - target_dt).total_seconds()) * 1000.0
        if delta_ms > window_ms:
            continue
        if best_delta_ms is None or delta_ms < best_delta_ms:
            best_path = os.path.join(cam_dir, name)
            best_delta_ms = delta_ms
    return best_path


def cam_dir_has_ts_encoded_frame(scene_dir: str, cam_name: str) -> bool:
    """Whether camera dir contains at least one timestamped image filename."""
    cam_dir = os.path.join(scene_dir, cam_name)
    if not os.path.isdir(cam_dir):
        return False
    for name in os.listdir(cam_dir):
        if not name.lower().endswith(_IMAGE_EXTS):
            continue
        if _FILENAME_TIMESTAMP_RE.search(name) is not None:
            return True
    return False


def resolve_frame_path_with_window(
    scene_dir: str, cam_name: str, frame_id: int, target_ts: str, *,
    window_ms: int = 500,
) -> Optional[str]:
    """Resolve with exact-substring -> nearest-window -> fallback flow."""
    match = resolve_frame_path(
        scene_dir, cam_name, frame_id,
        timestamp=target_ts, canonical_fallback=False,
    )
    if match is not None:
        return match

    match = find_nearest_frame_path(
        scene_dir, cam_name, target_ts, window_ms=window_ms,
    )
    if match is not None:
        return match

    if cam_dir_has_ts_encoded_frame(scene_dir, cam_name):
        return None
    return resolve_frame_path(scene_dir, cam_name, frame_id)


def get_frame_paths_of_multi_cameras(
    scene_dir, frame_id, cam_names=None, h5_file=False
):
    """
    Construct image frame paths for multiple cameras for a given frame ID.

    Iterates through `cam_names` (or all cameras found in `scene_dir` if None)
    and constructs the path for the specified `frame_id` using logic similar to
    `get_frame_path_of_single_camera`, including fallbacks for different potential
    image filename formats (e.g., .png, different frame padding).

    :param scene_dir: Path to the scene directory.
    :type scene_dir: str
    :param frame_id: The frame ID (integer).
    :type frame_id: int
    :param cam_names: Optional list of camera names to get paths for. If None,
                      uses `get_cam_names_in_scene` to find all cameras.
                      Defaults to None.
    :type cam_names: list[str], optional
    :param h5_file: If True, lists files ending with '.h5' instead of directories.
                    Defaults to False.
    :type h5_file: bool, optional
    :return: A dictionary mapping camera names to their corresponding image frame paths.
    :rtype: dict[str, str]
    :raises AssertionError: If a constructed path for a camera does not exist after checking fallbacks.
    """
    frame_paths = {}
    if cam_names is None:
        cam_names = get_cam_names_in_scene(scene_dir, h5_file=h5_file)
    for cam_name in cam_names:
        if h5_file:
            if cam_name.endswith(".h5"):
                # ``os.path.splitext`` strips only the FINAL extension,
                # so ``cam.01.h5`` → ``cam.01`` (vs the old
                # ``split(".")[0]`` which would have truncated to
                # ``cam`` for any cam name containing a dot).
                cam_key = os.path.splitext(cam_name)[0]
                frame_path = (
                    os.path.join(scene_dir, cam_name),
                    os.path.join("rgb", f"rgb_{frame_id:05}.jpg"),
                )
            else:
                cam_key = cam_name
                frame_path = (
                    os.path.join(scene_dir, f"{cam_name}.h5"),
                    os.path.join("rgb", f"rgb_{frame_id:05}.jpg"),
                )
            if not os.path.exists(frame_path[0]):
                # Explicit raise (vs ``assert``) so this still catches
                # missing H5 files when Python is run with ``-O``.
                raise FileNotFoundError(
                    f"H5 backing file does not exist: {frame_path[0]!r} "
                    f"(camera {cam_name!r}, frame {frame_id})"
                )
            frame_paths[cam_key] = frame_path
        else:
            frame_path = resolve_frame_path(scene_dir, cam_name, frame_id)
            if frame_path is None:
                candidates = _build_non_h5_frame_patterns(
                    scene_dir, cam_name, frame_id,
                )
                error_msg = (
                    f"Image not found for {cam_name}, frame {frame_id}. "
                    f"Checked paths:\n"
                )
                for p in candidates:
                    error_msg += f"  - {p}\n"
                # Mirror the H5 branch above: raise an explicit
                # ``FileNotFoundError`` (vs ``AssertionError``) so the
                # check still fires under ``python -O`` and so callers
                # can catch a single, semantically correct exception
                # type for the whole function.
                raise FileNotFoundError(error_msg)

            frame_paths[cam_name] = frame_path

    return frame_paths


def frame_paths_from_pkl_info(
    info: Dict,
    camera_names: List[str],
) -> Dict[str, Any]:
    """Extract per-camera frame paths from a single pkl info entry.

    A pkl info entry is one element of the ``infos`` list produced by the
    data-pkl pipeline.  Each entry's ``"cams"`` sub-dict is expected to
    contain, for every requested camera name, a ``"data_path"`` that is
    either a plain string (JPEG/PNG) or a ``(h5_path, key)`` list/tuple
    (HDF5).  This helper normalises both shapes into a flat
    ``{cam_name: frame_path}`` mapping suitable for downstream image
    loaders.

    pkl files are treated as the source of truth — if any requested
    camera is missing or lacks a ``"data_path"``, a :class:`KeyError`
    is raised rather than silently dropping the camera.

    :param info: One element of the ``infos`` list from the pkl file.
    :type info: dict
    :param camera_names: Camera names to include in the output.
    :type camera_names: list[str]
    :return: ``{cam_name: frame_path}`` where *frame_path* is either a
        string or an ``(h5_path, key)`` tuple depending on the pkl data.
    :rtype: dict[str, str or tuple]
    :raises KeyError: If ``info`` is missing ``"cams"``, a requested
        camera is missing from ``info["cams"]``, or a camera entry
        lacks ``"data_path"``.
    """
    if KEY_CAMS not in info:
        raise KeyError(
            f"pkl info entry is missing required '{KEY_CAMS}' field."
        )
    cams = info[KEY_CAMS]

    frame_paths: Dict[str, Any] = {}
    for cam_name in camera_names:
        if cam_name not in cams:
            raise KeyError(
                f"pkl info is missing camera '{cam_name}'. "
                f"Available: {sorted(cams.keys())}"
            )
        cam_data = cams[cam_name]
        if KEY_DATA_PATH not in cam_data:
            raise KeyError(
                f"pkl info for camera '{cam_name}' is missing required "
                f"'{KEY_DATA_PATH}' field."
            )
        dp = cam_data[KEY_DATA_PATH]
        frame_paths[cam_name] = tuple(dp) if isinstance(dp, (list, tuple)) else dp
    return frame_paths


def index_pkl_by_frame(pkl_infos: List[Dict]) -> Dict[int, Dict]:
    """Index a list of pkl info entries by their ``frame_idx``.

    Builds an ``{frame_idx: info}`` lookup for downstream scene-level
    drivers that need to fetch a specific frame's pkl info by its
    numeric frame id (e.g. when iterating scene results in sorted
    frame-id order).

    :param pkl_infos: List of per-frame info dicts from a data pkl file
        (the second return value of
        :func:`spatialai_data_utils.loaders.calibration.load_calib_into_dict_from_pkl`).
    :type pkl_infos: list[dict]
    :returns: ``{frame_idx (int): info_dict}`` mapping.
    :rtype: dict[int, dict]
    :raises KeyError: If any entry is missing ``frame_idx`` — pkl files
        are expected to contain complete per-frame information.
    """
    pkl_by_frame: Dict[int, Dict] = {}
    for idx, info in enumerate(pkl_infos):
        if KEY_FRAME_IDX not in info:
            raise KeyError(
                f"pkl info entry at index {idx} is missing required "
                f"'{KEY_FRAME_IDX}' field. The pkl file must include complete "
                "per-frame information."
            )
        pkl_by_frame[int(info[KEY_FRAME_IDX])] = info
    return pkl_by_frame


def resolve_frame_root(scene_root: str) -> str:
    """Return the directory under which per-camera image folders live.

    Some scene layouts keep per-camera image folders under a ``frames/``
    subdirectory; others place them directly under the scene root.
    This helper returns ``scene_root/frames`` if it exists, else
    *scene_root* itself — matching the fallback logic used by
    :func:`get_frame_paths_of_multi_cameras`.

    :param scene_root: Scene root directory.
    :type scene_root: str
    :returns: Path to the directory containing per-camera image folders.
    :rtype: str
    """
    frames_dir = os.path.join(scene_root, "frames")
    return frames_dir if os.path.isdir(frames_dir) else scene_root
