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
AICity'26 MTMC scene-id / scene-name lookup helpers.

Provides convenient access to the packaged
``scenes/scene_id_to_name.json`` asset that maps the integer scene IDs
appearing in column 0 of the official AICity'26 MTMC ground-truth and
submission text files (``23`` / ``24`` / ``25``) to their canonical
human-readable scene directory names (``Warehouse_023`` /
``Warehouse_024`` / ``Warehouse_025``).

The mapping is the de-facto convention used by the AICity'26
validation server; bundling it here avoids each consumer having to
hand-roll the same three-entry JSON object.  Sibling of
:mod:`spatialai_data_utils.datasets.aicity25.scene_utils`.

Public functions:

* :func:`get_default_scene_id_to_name_path` — return the on-disk path
  to the packaged JSON file.  Useful when a CLI wants to forward the
  path to a sub-tool that takes a file argument instead of a dict.
* :func:`load_default_scene_id_to_name` — return the packaged mapping
  as a fresh ``dict[str, str]``.
"""

import json
import os.path as osp
from importlib.resources import files
from typing import Dict


_PACKAGE_ROOT = "spatialai_data_utils.datasets.aicity26"
_SCENES_REL_PATH = "scenes/scene_id_to_name.json"


def get_default_scene_id_to_name_path() -> str:
    """Return the on-disk path to the packaged scene-id mapping JSON.

    Uses :func:`importlib.resources.files` so the lookup works equally
    well from a source checkout and from an installed wheel (the
    matching ``release/MANIFEST.in`` entry ensures the file is
    included in the distribution).

    :return: Absolute path to
        ``spatialai_data_utils/datasets/aicity26/scenes/scene_id_to_name.json``.
    """
    resource = files(_PACKAGE_ROOT).joinpath(*_SCENES_REL_PATH.split("/"))
    return str(resource)


def load_default_scene_id_to_name() -> Dict[str, str]:
    """Load the packaged ``{scene_id_str: scene_name}`` mapping.

    Each call returns a fresh dictionary, so callers are free to
    mutate the result without affecting subsequent calls or
    other tools that share the same import.

    :return: ``{"23": "Warehouse_023", "24": "Warehouse_024",
        "25": "Warehouse_025"}``.
    """
    path = get_default_scene_id_to_name_path()
    if not osp.exists(path):
        # When this fires, the wheel was built without the asset —
        # most likely because release/MANIFEST.in lost its
        # ``include .../scenes/*.json`` line.
        raise FileNotFoundError(
            f"Packaged scene-id mapping not found at {path!r}; check "
            f"release/MANIFEST.in for the matching include line."
        )
    with open(path, "r") as fp:
        mapping = json.load(fp)
    if not isinstance(mapping, dict):
        raise ValueError(
            f"Packaged scene-id mapping at {path!r} must be a JSON "
            f"object; got {type(mapping).__name__}."
        )
    return {str(k): str(v) for k, v in mapping.items()}
