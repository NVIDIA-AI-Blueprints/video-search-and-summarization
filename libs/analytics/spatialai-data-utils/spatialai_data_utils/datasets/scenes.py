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
Scene-level helpers shared across MTMC datasets.

This module collects helpers that encode the toolkit's general
**scene-and-camera organisation** conventions. They are dataset-aware
(they know the scene-as-parent-of-cameras layout, the
``"<scene>+<group>"`` BEV-grouping convention, and the NuScenes-style
``"<scene>__<frame_id>"`` token format) but **not** specific to any
single dataset — those helpers live under
``spatialai_data_utils.datasets.<dataset_name>`` (e.g.
:func:`spatialai_data_utils.datasets.aicity24.dataset_utils.get_scene_info_from_name`
for the AICity'24 scene-dir naming convention).

Pure language-level / IO primitives (regex digit extraction, natural
sort key, optional-import shims, runtime data classes, etc.) live in
:mod:`spatialai_data_utils.utils` instead.

Public functions:

* :func:`sort_cam_names_by_id` — sort a list of camera names
  numerically with a natural-sort fallback for prefixed names like
  ``c01`` / ``c10``.
* :func:`get_cam_name_from_string` — pull a single camera id (the
  first or last digit run) out of an arbitrary string.
* :func:`get_cam_names_in_scene` — list per-camera dirs (or ``.h5``
  files) under a scene directory, with optional ``must_contain``
  filter.
* :func:`get_scene_key` — return the part of a scene name before the
  first ``_`` (toolkit-wide scene-prefix convention).
* :func:`parse_scene_and_group` — split a ``"<scene>+<group>"``
  string on its first ``+`` (toolkit-wide BEV-grouping convention).
* :func:`get_scene_info_from_token` — extract
  ``(scene_name, frame_id)`` from a NuScenes-style sample token.
"""

import os

from spatialai_data_utils.utils.string_utils import (
    extract_numbers,
    natural_sort_key,
)


def sort_cam_names_by_id(cam_names):
    """
    Sort a list of camera names numerically based on IDs extracted from the names.

    Attempts to convert names directly to integers first. If that fails
    (e.g., names like 'c01', 'c10'), uses :func:`natural_sort_key` for sorting.

    :param cam_names: A list of camera name strings.
    :type cam_names: list[str]
    :return: The sorted list of camera names.
    :rtype: list[str]
    """
    try:
        cam_names = sorted([int(n) for n in cam_names])
        cam_names = [str(n) for n in cam_names]
    except (TypeError, ValueError):
        cam_names = sorted(cam_names, key=natural_sort_key)
    return cam_names


def get_cam_name_from_string(string, num_index=-1):
    """
    Extract a camera name (assumed to be a number) from a string.

    Finds all numbers in the string using :func:`extract_numbers` and returns
    the number at the specified index (default is the last one).

    :param string: The input string potentially containing a camera ID number.
    :type string: str
    :param num_index: The index of the number to extract (negative indexing allowed).
                      Defaults to -1 (last number).
    :type num_index: int, optional
    :return: The extracted camera number as a string, or None if no numbers found.
    :rtype: str or None
    """
    numbers = extract_numbers(string)
    if len(numbers) > 0:
        return str(numbers[num_index])
    else:
        return None


def get_cam_names_in_scene(
    scene_dir, sort_by_id=True, h5_file=False, must_contain=None,
):
    """
    Get camera names within a scene directory.

    Lists subdirectories (or .h5 files if `h5_file` is True) in the given `scene_dir`.
    Optionally sorts the names numerically based on embedded IDs.

    :param scene_dir: Path to the scene directory.
    :type scene_dir: str
    :param sort_by_id: If True, attempts to sort camera names numerically based on IDs
                       extracted from the names. Falls back to natural sort if pure
                       numeric conversion fails. Defaults to True.
    :type sort_by_id: bool, optional
    :param h5_file: If True, lists files ending with '.h5' instead of directories.
                    Defaults to False.
    :type h5_file: bool, optional
    :param must_contain: Optional path component (file or directory)
        that each candidate camera dir must contain to be included.
        Use to filter out non-camera siblings inside a scene root —
        e.g. ``must_contain='rgb'`` keeps only dirs with an ``rgb``
        subdirectory, dropping ``videos/`` / ``resources/`` /
        ``calibration/`` etc. that ``os.scandir`` would otherwise
        return.  ``None`` (default) or empty string means no filter
        is applied.  Ignored when ``h5_file=True``.
    :type must_contain: str or None, optional
    :return: A list of camera names (directory or .h5 filenames).
    :rtype: list[str]
    """
    if h5_file:
        cam_names = [f.name for f in os.scandir(scene_dir) if f.name.endswith(".h5")]
    else:
        cam_names = [f.name for f in os.scandir(scene_dir) if f.is_dir()]
        if must_contain:
            cam_names = [
                n for n in cam_names
                if os.path.exists(os.path.join(scene_dir, n, must_contain))
            ]
    if sort_by_id:
        cam_names = sort_cam_names_by_id(cam_names)
    return cam_names


def get_scene_key(scene_name):
    """
    Extract the scene key (usually the first part) from a scene name.

    Assumes the scene key is the part before the first underscore.

    :param scene_name: The full scene name string.
    :type scene_name: str
    :return: The extracted scene key.
    :rtype: str
    """
    scene_key = scene_name.split("_")[0]
    return scene_key


def parse_scene_and_group(scene_name_full):
    """Split a scene name into the base scene name and an optional group name.

    Scene names may carry a camera-group suffix separated by ``+``, e.g.
    ``"scene_001+clustered-bev-sensor-1"``.  Only the first ``+``
    is used as the separator so that group names containing ``+`` are
    preserved.

    :param scene_name_full: Full scene name, optionally with ``+group`` suffix.
    :type scene_name_full: str
    :return: ``(scene_name, group_name)`` where *group_name* is ``None``
        when no ``+`` is present.
    :rtype: tuple[str, str | None]
    :raises TypeError: If *scene_name_full* is not a string.
    """
    if not isinstance(scene_name_full, str):
        raise TypeError(
            f"scene_name_full must be a str, got {type(scene_name_full).__name__}"
        )
    if "+" in scene_name_full:
        scene_name, group_name = scene_name_full.split("+", 1)
        return scene_name, group_name
    return scene_name_full, None


def get_scene_info_from_token(sample_token):
    """
    Extract scene name and frame ID from a sample token.

    Handles two common token formats:

    1. ``scene_name__frame_id``  (double underscore)
    2. ``scene_name_..._frame_id``  (single underscore, frame_id at end)

    :param sample_token: The sample token string.
    :type sample_token: str
    :return: A tuple containing (scene_name, frame_id). frame_id is an integer.
    :rtype: tuple(str, int)
    """
    if "__" in sample_token:
        scene_name, frame_id = sample_token.split("__")
        frame_id = int(frame_id)
    else:
        index = sample_token.rfind("_")
        scene_name = sample_token[:index]
        frame_id = int(sample_token[index + 1 :])
    return scene_name, frame_id
