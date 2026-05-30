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
Dataset-agnostic train/val/test split helpers.

These loaders read a ``{train: [...], val: [...], test: [...]}`` structure
from a YAML, JSON, or Python configuration file and return the three lists.
They contain no dataset-specific schema or path conventions, so any dataset
package under :mod:`spatialai_data_utils.datasets` (AICity'24, AICity'25,
or future additions) can use them directly.

Public functions:

* :func:`load_split_from_yaml` — read a YAML file with top-level
  ``train`` / ``val`` / ``test`` keys.
* :func:`load_split_from_json` — same, for JSON.
* :func:`load_split_from_py` — read a Python file that defines splits
  in any of three supported shapes (a top-level ``splits`` dict, three
  top-level ``train`` / ``val`` / ``test`` variables, or any dict
  variable containing those keys).
* :func:`no_common_elements` — small set-difference predicate used by
  dataset-specific split builders to validate that train / test do not
  overlap.

Missing keys default to empty lists; missing files raise
:class:`FileNotFoundError`; malformed Python files raise
:class:`ValueError`.
"""

import json
import os.path as osp
from typing import List, Tuple

import yaml


def load_split_from_yaml(
    yaml_path: str,
) -> Tuple[List[str], List[str], List[str]]:
    """Load train/val/test scene splits from a YAML configuration file.

    The YAML file is expected to have top-level ``train``, ``val``, and
    ``test`` keys, each mapping to a list of scene names. Any key that
    is missing yields an empty list.

    :param yaml_path: Path to the YAML file.
    :type yaml_path: str
    :return: ``(train_scenes, val_scenes, test_scenes)``.
    :rtype: tuple[list[str], list[str], list[str]]
    :raises FileNotFoundError: If ``yaml_path`` does not exist.
    """
    if not osp.exists(yaml_path):
        raise FileNotFoundError(f"Scene splits YAML file not found: {yaml_path}")

    with open(yaml_path, "r") as f:
        splits = yaml.safe_load(f)

    train_scenes = splits.get("train", [])
    val_scenes = splits.get("val", [])
    test_scenes = splits.get("test", [])

    return train_scenes, val_scenes, test_scenes


def load_split_from_json(
    json_path: str,
) -> Tuple[List[str], List[str], List[str]]:
    """Load train/val/test scene splits from a JSON configuration file.

    The JSON file is expected to be an object with top-level ``train``,
    ``val``, and ``test`` keys, each an array of scene names. Any key
    that is missing yields an empty list.

    :param json_path: Path to the JSON file.
    :type json_path: str
    :return: ``(train_scenes, val_scenes, test_scenes)``.
    :rtype: tuple[list[str], list[str], list[str]]
    :raises FileNotFoundError: If ``json_path`` does not exist.
    """
    if not osp.exists(json_path):
        raise FileNotFoundError(f"Scene splits JSON file not found: {json_path}")

    with open(json_path, "r") as f:
        splits = json.load(f)

    train_scenes = splits.get("train", [])
    val_scenes = splits.get("val", [])
    test_scenes = splits.get("test", [])

    return train_scenes, val_scenes, test_scenes


def load_split_from_py(
    py_path: str,
) -> Tuple[List[str], List[str], List[str]]:
    """Load train/val/test scene splits from a Python configuration file.

    The Python file is executed in a fresh namespace; the splits are
    pulled from the resulting bindings using the first match of:

    1. A top-level dict named ``splits``.
    2. Top-level lists named ``train`` / ``val`` / ``test``.
    3. Any top-level dict variable that contains at least one of the
       keys ``train`` / ``val`` / ``test``.

    Missing keys yield empty lists.

    :param py_path: Path to the Python file.
    :type py_path: str
    :return: ``(train_scenes, val_scenes, test_scenes)``.
    :rtype: tuple[list[str], list[str], list[str]]
    :raises FileNotFoundError: If ``py_path`` does not exist.
    :raises ValueError: If the file cannot be executed or contains no
        recognisable split structure.
    """
    if not osp.exists(py_path):
        raise FileNotFoundError(f"Scene splits Python file not found: {py_path}")

    namespace: dict = {}

    try:
        with open(py_path, "r") as f:
            code = f.read()

        exec(code, namespace)

        splits = None

        if "splits" in namespace:
            splits = namespace["splits"]
        elif "train" in namespace or "val" in namespace or "test" in namespace:
            splits = {
                "train": namespace.get("train", []),
                "val": namespace.get("val", []),
                "test": namespace.get("test", []),
            }
        else:
            for _key, value in namespace.items():
                if isinstance(value, dict) and any(
                    k in value for k in ["train", "val", "test"]
                ):
                    splits = value
                    break

        if splits is None:
            raise ValueError(
                f"No valid split configuration found in {py_path}. "
                "Expected either a 'splits' dictionary, individual "
                "'train'/'val'/'test' variables, or a dictionary "
                "containing 'train'/'val'/'test' keys."
            )

        train_scenes = splits.get("train", [])
        val_scenes = splits.get("val", [])
        test_scenes = splits.get("test", [])

        return train_scenes, val_scenes, test_scenes

    except Exception as e:
        raise ValueError(
            f"Error loading Python split configuration from {py_path}: {str(e)}"
        )


def no_common_elements(list1, list2) -> bool:
    """Return ``True`` if ``list1`` and ``list2`` share no elements.

    Used by dataset-specific split builders to validate that
    train / val / test partitions do not overlap.

    :param list1: First list.
    :type list1: list
    :param list2: Second list.
    :type list2: list
    :return: ``True`` when the intersection is empty, ``False`` otherwise.
    :rtype: bool
    """
    return len(set(list1).intersection(set(list2))) == 0
