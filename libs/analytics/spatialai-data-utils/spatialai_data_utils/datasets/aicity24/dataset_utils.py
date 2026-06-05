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
AICity Challenge 2024 Dataset Utilities Module

This module provides utility functions for working with AICity Challenge 2024
datasets, including scene discovery, train/test splitting, and dataset organization.
It handles scene metadata, ID mappings, and split configuration for multi-camera
tracking and detection tasks.

Key Features:
- Discover available scenes in dataset directories
- Load scene ID to name mappings from CSV files
- Create train/test splits based on scene names
- Generate split configurations with metadata
- Validate split consistency (no overlap between train/test)
- Handle scene naming conventions for AICity'24

Main Functions:
- get_available_scenes: Scan directory for available scenes
- get_train_test_split: Create train/test split from scene list
- get_scene_names_splits: Generate split configuration with metadata
- no_common_elements: Validate split consistency

Scene Organization:
- Root directory contains scene subdirectories
- Each scene directory represents one location/scenario
- Scene ID to name mapping provided via CSV file
- Scenes can be filtered and split for training/testing

Split Configuration:
- Train and test scenes should have no overlap
- Split information includes scene names and counts
- Metadata includes camera counts and track information
- Supports custom split ratios and scene selection

CSV Mapping Format:
- File: map_scene_id_to_name.csv
- Format: {scene_id}:{scene_name}
- Example: "S01:warehouse_floor1_scene001"

Use Cases:
- Prepare datasets for training and evaluation
- Create reproducible train/test splits
- Generate dataset statistics and metadata
- Organize multi-scene datasets
- Validate dataset consistency

Typical Workflow:
1. Scan dataset directory to find all scenes
2. Load scene ID to name mapping
3. Select scenes for training and testing
4. Validate no overlap between splits
5. Generate split configuration with metadata
6. Use splits for data loading and evaluation

Dataset Structure:
{root_path}/
├── scene1/
│   ├── camera1/
│   ├── camera2/
│   └── ...
├── scene2/
└── map_scene_id_to_name.csv
"""

import os
import csv

from spatialai_data_utils.datasets.splits import no_common_elements


def get_scene_info_from_name(scene_name):
    """Parse an AICity'24 scene name into ``(scene_type, scene_id, n_cameras, n_tracks)``.

    AICity'24 scene directories were named with the trailing-suffix
    convention ``{scene_type}_{scene_id}_{n_cameras}_{n_tracks}`` —
    for example, ``"warehouse_floor1_scene001_20_150"`` parses to
    ``("warehouse_floor1", "scene001", 20, 150)``. The convention is
    AICity'24-specific; later challenges (AICity'25) use a different
    ``{type}_{date}`` scheme that does **not** parse with this helper.

    The boundary between ``scene_type`` and ``scene_id`` is found by
    walking back three underscores from the end (one each for
    ``n_tracks``, ``n_cameras``, and ``scene_id``); ``scene_type`` is
    therefore everything to the left of the third-to-last underscore
    and may itself contain underscores.

    :param scene_name: The full scene name string following the AICity'24
        convention.
    :type scene_name: str
    :return: ``(scene_type, scene_id, n_cameras, n_tracks)``. ``scene_id``
        is returned as a string; ``n_cameras`` and ``n_tracks`` are ints.
    :rtype: tuple[str, str, int, int]
    """

    def find_third_last_underscore(s):
        last = s.rfind("_")
        if last == -1:
            return -1
        second_last = s.rfind("_", 0, last)
        if second_last == -1:
            return -1
        third_last = s.rfind("_", 0, second_last)
        return third_last

    scene_name_split = scene_name.split("_")
    scene_id = scene_name_split[-3]
    n_cameras = int(scene_name_split[-2])
    n_tracks = int(scene_name_split[-1])
    index = find_third_last_underscore(scene_name)
    scene_type = scene_name[:index]

    return scene_type, scene_id, n_cameras, n_tracks


def get_available_scenes(root_path):
    """
    Scan a root directory to find available scene subdirectories.

    Also attempts to load a scene ID to scene name mapping from a CSV file
    located relative to the `root_path` (assumes a parallel structure like
    replacing 'full_data' with 'map_scene_id_to_name.csv').

    :param root_path: Path to the root directory containing scene subdirectories.
    :type root_path: str
    :return: A tuple containing:
             - scene_names (list[str]): Sorted list of subdirectory names found in `root_path`.
             - mapping_scene_id_to_name (dict): Dictionary mapping scene IDs (str) to scene names (str),
                                                loaded from the mapping CSV file if found, otherwise empty.
    :rtype: tuple(list[str], dict)
    """
    root_path = os.path.normpath(root_path)
    scene_names = sorted([f.name for f in os.scandir(root_path) if f.is_dir()])

    # load scene id to scene name mapping
    mapping_filename = root_path.replace("full_data", "map_scene_id_to_name.csv")
    mapping_scene_id_to_name = {}
    if os.path.exists(mapping_filename) and mapping_filename.endswith(".csv"):
        with open(mapping_filename, mode="r", encoding="utf-8-sig") as file:
            csvFile = csv.reader(file)
            for line in csvFile:
                scene_id, scene_name = line[0].split(":")
                mapping_scene_id_to_name[scene_id] = scene_name
    else:
        print(f"warning: mapping scene_id_to_name {mapping_filename} is not found!")

    return scene_names, mapping_scene_id_to_name


def get_train_test_split(
    scene_names: list,
    mapping_scene_id_to_name: dict,
    split_type: str = "default",
    include_val_into_train: bool = False,
):
    """
    Split a list of scene names into train, validation, and test sets based on predefined splits.

    :param scene_names: List of all available scene names to be split.
    :type scene_names: list
    :param mapping_scene_id_to_name: Dictionary mapping scene IDs (str) to scene names (str).
    :type mapping_scene_id_to_name: dict
    :param split_type: The type of predefined split to use ('default').
                       Defaults to "default".
    :type split_type: str, optional
    :param include_val_into_train: If True, scenes assigned to the validation split are
                                   also added to the training split. Defaults to False.
    :type include_val_into_train: bool, optional
    :return: A tuple containing three lists: (train_scene_names, val_scene_names, test_scene_names).
    :rtype: tuple(list[str], list[str], list[str])
    :raises NotImplementedError: If `split_type` is not recognized.
    :raises AssertionError: If the resulting train and test sets have overlapping scenes.
    """
    if split_type == "default":
        train_scene_ids = [str(i) for i in range(1, 41)]
        val_scene_ids = [str(i) for i in range(41, 61)]
        test_scene_ids = [str(i) for i in range(61, 91)]
    elif split_type == "default1":
        train_scene_ids = [str(i) for i in range(1, 11)]
        train_scene_ids.extend([str(i) for i in range(21, 31)])
        train_scene_ids.extend([str(i) for i in range(41, 51)])
        train_scene_ids.extend([str(i) for i in range(66, 71)])
        train_scene_ids.extend([str(i) for i in range(76, 81)])
        train_scene_ids.extend([str(i) for i in range(86, 91)])
        val_scene_ids = [str(i) for i in [11, 31, 51, 61, 71, 81]]
        test_scene_ids = [str(i) for i in range(11, 21)]
        test_scene_ids.extend([str(i) for i in range(31, 41)])
        test_scene_ids.extend([str(i) for i in range(51, 66)])
        test_scene_ids.extend([str(i) for i in range(71, 76)])
        test_scene_ids.extend([str(i) for i in range(81, 86)])
    elif split_type == "default2":
        train_scene_ids = [str(i) for i in range(1, 41)]
        train_scene_ids += [str(i) for i in range(41, 61)]
        val_scene_ids = [str(i) for i in [61, 71, 81]]
        test_scene_ids = [str(i) for i in range(61, 91)]
    else:
        raise NotImplementedError(f"split_type {split_type} is not implemented!")

    train_scene_names = []
    val_scene_names = []
    test_scene_names = []

    for scene_id in mapping_scene_id_to_name.keys():
        if mapping_scene_id_to_name[scene_id] in scene_names:
            if scene_id in train_scene_ids:
                train_scene_names.append(mapping_scene_id_to_name[scene_id])
            if scene_id in val_scene_ids:
                val_scene_names.append(mapping_scene_id_to_name[scene_id])
            if scene_id in test_scene_ids:
                test_scene_names.append(mapping_scene_id_to_name[scene_id])

    if split_type == "default2":
        curr_scene_names = train_scene_names + val_scene_names + test_scene_names
        for scene_name in scene_names:
            if scene_name not in curr_scene_names:
                train_scene_names.append(scene_name)

    if include_val_into_train:
        train_scene_names += val_scene_names

    assert no_common_elements(train_scene_names, test_scene_names), (
        "train and test sets have overlaps!"
    )

    return train_scene_names, val_scene_names, test_scene_names


def get_scene_names_splits(root_path, split_type, include_val_into_train=False):
    """
    Convenience function to get scene names and split them.

    Calls `get_available_scenes` and `get_train_test_split` sequentially.

    :param root_path: Path to the root directory containing scene subdirectories.
    :type root_path: str
    :param split_type: The type of predefined split to use (passed to `get_train_test_split`).
    :type split_type: str
    :param include_val_into_train: If True, validation scenes are included in the training set.
                                   Defaults to False.
    :type include_val_into_train: bool, optional
    :return: A tuple containing three lists: (train_scene_names, val_scene_names, test_scene_names).
    :rtype: tuple(list[str], list[str], list[str])
    """
    scene_names, mapping_scene_id_to_name = get_available_scenes(root_path)
    return get_train_test_split(
        scene_names, mapping_scene_id_to_name, split_type, include_val_into_train
    )
