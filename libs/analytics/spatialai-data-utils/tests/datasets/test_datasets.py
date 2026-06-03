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

"""Tests for :mod:`spatialai_data_utils.datasets`.

Covers three layers:

* :mod:`spatialai_data_utils.datasets.splits` — the dataset-agnostic
  split loaders (``load_split_from_yaml`` / ``_json`` / ``_py``) and
  the small ``no_common_elements`` predicate used by dataset-specific
  split builders.
* :mod:`spatialai_data_utils.datasets.aicity24.dataset_utils` — the
  AICity'24-specific split logic (``get_train_test_split``,
  ``get_available_scenes``, ``get_scene_names_splits``).
* :mod:`spatialai_data_utils.datasets.aicity25` — the
  ``load_class_config_from_file`` re-export, parsing tests against
  the shipped ``scenes/scene_id_to_name.json``, plus the AICity'25
  spec metadata (``CLASS_ID_TO_NAME`` and ``NUM_FIELDS``) in
  :mod:`spatialai_data_utils.datasets.aicity25.spec`.
"""

import json
import os
import os.path as osp

import pytest

from spatialai_data_utils.datasets.aicity24.dataset_utils import (
    get_available_scenes,
    get_scene_info_from_name,
    get_scene_names_splits,
    get_train_test_split,
)
from spatialai_data_utils.datasets.scenes import (
    get_cam_name_from_string,
    get_scene_info_from_token,
    get_scene_key,
    parse_scene_and_group,
    sort_cam_names_by_id,
)
from spatialai_data_utils.datasets.splits import (
    load_split_from_json,
    load_split_from_py,
    load_split_from_yaml,
    no_common_elements,
)
from spatialai_data_utils.utils.string_utils import extract_numbers


# ---------------------------------------------------------------------------
# Generic split loaders + helper (datasets.splits)
# ---------------------------------------------------------------------------


class TestNoCommonElements:
    """``no_common_elements`` is a small set-intersection helper."""

    def test_disjoint_lists_return_true(self):
        assert no_common_elements([1, 2, 3], [4, 5, 6]) is True

    def test_overlapping_lists_return_false(self):
        assert no_common_elements([1, 2, 3], [3, 4, 5]) is False

    def test_empty_lists_return_true(self):
        assert no_common_elements([], []) is True

    def test_one_empty_list_returns_true(self):
        assert no_common_elements([], [1, 2]) is True

    def test_string_elements(self):
        assert no_common_elements(["a", "b"], ["c", "d"]) is True
        assert no_common_elements(["a", "b"], ["b", "c"]) is False

    def test_duplicate_elements_within_one_list_do_not_change_result(self):
        """Inputs are converted to sets, so duplicates within a list don't matter."""
        assert no_common_elements([1, 1, 2], [3, 4]) is True


class TestLoadSplitFromYaml:
    """``load_split_from_yaml`` consumes a YAML at a given path."""

    def test_happy_path(self, tmp_path):
        path = tmp_path / "split.yaml"
        path.write_text(
            "train:\n  - s_a\n  - s_b\n"
            "val:\n  - s_c\n"
            "test:\n  - s_d\n  - s_e\n"
        )
        train, val, test = load_split_from_yaml(str(path))
        assert train == ["s_a", "s_b"]
        assert val == ["s_c"]
        assert test == ["s_d", "s_e"]

    def test_missing_keys_default_to_empty_lists(self, tmp_path):
        """Partial YAML (only ``train``) yields empty val/test lists."""
        path = tmp_path / "split.yaml"
        path.write_text("train:\n  - s_a\n")
        train, val, test = load_split_from_yaml(str(path))
        assert train == ["s_a"]
        assert val == []
        assert test == []

    def test_missing_file_raises_file_not_found(self, tmp_path):
        missing = str(tmp_path / "no_such_file.yaml")
        with pytest.raises(FileNotFoundError, match="not found"):
            load_split_from_yaml(missing)


class TestLoadSplitFromJson:
    """``load_split_from_json`` consumes a JSON at a given path."""

    def test_happy_path(self, tmp_path):
        path = tmp_path / "split.json"
        path.write_text(json.dumps({
            "train": ["s_a", "s_b"], "val": ["s_c"], "test": ["s_d"],
        }))
        train, val, test = load_split_from_json(str(path))
        assert train == ["s_a", "s_b"]
        assert val == ["s_c"]
        assert test == ["s_d"]

    def test_missing_file_raises_file_not_found(self, tmp_path):
        missing = str(tmp_path / "no_such_file.json")
        with pytest.raises(FileNotFoundError, match="not found"):
            load_split_from_json(missing)


class TestLoadSplitFromPy:
    """``load_split_from_py`` accepts three Python config formats."""

    def test_format_splits_dict(self, tmp_path):
        """Format 1: a top-level ``splits = {...}`` dictionary."""
        path = tmp_path / "splits1.py"
        path.write_text(
            "splits = {\n"
            "    'train': ['s_a', 's_b'],\n"
            "    'val': ['s_c'],\n"
            "    'test': ['s_d'],\n"
            "}\n"
        )
        train, val, test = load_split_from_py(str(path))
        assert (train, val, test) == (["s_a", "s_b"], ["s_c"], ["s_d"])

    def test_format_individual_top_level_vars(self, tmp_path):
        """Format 2: separate ``train``/``val``/``test`` top-level variables."""
        path = tmp_path / "splits2.py"
        path.write_text(
            "train = ['s_a', 's_b']\n"
            "val = ['s_c']\n"
            "test = ['s_d']\n"
        )
        train, val, test = load_split_from_py(str(path))
        assert (train, val, test) == (["s_a", "s_b"], ["s_c"], ["s_d"])

    def test_format_arbitrary_dict_with_split_keys(self, tmp_path):
        """Format 3: any dict containing ``train``/``val``/``test`` keys is picked up."""
        path = tmp_path / "splits3.py"
        path.write_text(
            "MY_SPLITS = {\n"
            "    'train': ['s_a'],\n"
            "    'val': ['s_b'],\n"
            "    'test': ['s_c'],\n"
            "}\n"
        )
        train, val, test = load_split_from_py(str(path))
        assert (train, val, test) == (["s_a"], ["s_b"], ["s_c"])

    def test_no_recognisable_splits_raises_value_error(self, tmp_path):
        """A Python file without any recognisable split structure is rejected."""
        path = tmp_path / "garbage.py"
        path.write_text("UNRELATED = 42\n")
        with pytest.raises(ValueError, match="Error loading Python split configuration"):
            load_split_from_py(str(path))

    def test_missing_file_raises_file_not_found(self, tmp_path):
        missing = str(tmp_path / "no_such_file.py")
        with pytest.raises(FileNotFoundError, match="not found"):
            load_split_from_py(missing)


# ---------------------------------------------------------------------------
# AICity'24 helpers
# ---------------------------------------------------------------------------


def _full_aicity24_scene_names():
    """Build the full ``scene_<id>`` list AICity'24 splits expect (1..90)."""
    return [f"scene_{i}" for i in range(1, 91)]


def _full_aicity24_mapping():
    """Map scene IDs ``"1".."90"`` to their corresponding ``scene_<id>`` names."""
    return {str(i): f"scene_{i}" for i in range(1, 91)}


class TestGetTrainTestSplitDefault:
    """``get_train_test_split`` with ``split_type='default'``."""

    def test_split_counts(self):
        train, val, test = get_train_test_split(
            _full_aicity24_scene_names(), _full_aicity24_mapping(), split_type="default",
        )
        assert len(train) == 40
        assert len(val) == 20
        assert len(test) == 30

    def test_train_val_test_are_disjoint(self):
        train, val, test = get_train_test_split(
            _full_aicity24_scene_names(), _full_aicity24_mapping(), split_type="default",
        )
        assert no_common_elements(train, test)
        assert no_common_elements(train, val)
        assert no_common_elements(val, test)

    def test_split_membership_at_boundaries(self):
        """Anchor a few representative IDs in their expected splits."""
        train, val, test = get_train_test_split(
            _full_aicity24_scene_names(), _full_aicity24_mapping(), split_type="default",
        )
        assert "scene_1" in train and "scene_40" in train
        assert "scene_41" in val and "scene_60" in val
        assert "scene_61" in test and "scene_90" in test

    def test_unknown_scene_in_input_is_ignored(self):
        """Scenes not present in ``scene_names`` drop out silently."""
        train, val, test = get_train_test_split(
            _full_aicity24_scene_names()[:10],  # only scenes 1..10 available
            _full_aicity24_mapping(),
            split_type="default",
        )
        assert len(train) == 10  # all 10 land in train (IDs 1..10 ⊂ train range)
        assert val == [] and test == []


class TestGetTrainTestSplitDefault1:
    """``get_train_test_split`` with the ``default1`` interleaved split."""

    def test_split_disjointness(self):
        train, val, test = get_train_test_split(
            _full_aicity24_scene_names(), _full_aicity24_mapping(), split_type="default1",
        )
        assert no_common_elements(train, test)

    def test_split_counts_are_nonempty(self):
        """``default1`` populates all three splits.

        Note: under ``default1`` ``val`` overlaps ``test`` by design
        (val IDs {11, 31, 51, 61, 71, 81} are also in test ranges), so
        the only disjointness invariant is between train and test —
        which the source itself asserts.
        """
        train, val, test = get_train_test_split(
            _full_aicity24_scene_names(), _full_aicity24_mapping(), split_type="default1",
        )
        assert len(train) > 0 and len(val) > 0 and len(test) > 0


class TestGetTrainTestSplitDefault2:
    """``default2`` includes leftover scenes in train via the catch-all loop."""

    def test_unmapped_scene_lands_in_train(self):
        """A scene not present in ``mapping`` should be appended to train under default2."""
        scene_names = _full_aicity24_scene_names() + ["bonus_scene_unmapped"]
        train, _, test = get_train_test_split(
            scene_names, _full_aicity24_mapping(), split_type="default2",
        )
        assert "bonus_scene_unmapped" in train
        assert no_common_elements(train, test)


class TestGetTrainTestSplitErrors:
    """Misuse paths."""

    def test_unknown_split_type_raises(self):
        with pytest.raises(NotImplementedError, match="not implemented"):
            get_train_test_split(
                _full_aicity24_scene_names(),
                _full_aicity24_mapping(),
                split_type="does_not_exist",
            )


class TestGetTrainTestSplitIncludeValIntoTrain:
    """``include_val_into_train=True`` folds val scenes into train."""

    def test_val_scenes_appear_in_train(self):
        train_only, val, _ = get_train_test_split(
            _full_aicity24_scene_names(), _full_aicity24_mapping(), split_type="default",
        )
        train_with_val, _, _ = get_train_test_split(
            _full_aicity24_scene_names(),
            _full_aicity24_mapping(),
            split_type="default",
            include_val_into_train=True,
        )
        assert set(val).issubset(set(train_with_val))
        assert len(train_with_val) == len(train_only) + len(val)


class TestGetAvailableScenes:
    """``get_available_scenes`` scans a directory and an optional CSV mapping."""

    def _make_scenes(self, root_path, scene_names):
        for s in scene_names:
            os.makedirs(osp.join(root_path, s))

    def test_returns_sorted_subdirectory_names(self, tmp_path):
        full_data = tmp_path / "full_data"
        full_data.mkdir()
        self._make_scenes(str(full_data), ["scene_b", "scene_a", "scene_c"])
        names, mapping = get_available_scenes(str(full_data))
        assert names == ["scene_a", "scene_b", "scene_c"]
        # Mapping CSV is missing — function returns an empty dict (and warns).
        assert mapping == {}

    def test_ignores_files_at_root(self, tmp_path):
        """Non-directory entries at the root are skipped."""
        full_data = tmp_path / "full_data"
        full_data.mkdir()
        self._make_scenes(str(full_data), ["scene_a"])
        (full_data / "stray_file.txt").write_text("not a scene")
        names, _ = get_available_scenes(str(full_data))
        assert names == ["scene_a"]

    def test_loads_mapping_csv_when_present(self, tmp_path):
        """If ``map_scene_id_to_name.csv`` exists alongside ``full_data``, it's loaded."""
        full_data = tmp_path / "full_data"
        full_data.mkdir()
        self._make_scenes(str(full_data), ["scene_a", "scene_b"])
        # The function derives the mapping path by replacing 'full_data' in the
        # normalized root path; place the CSV at the resulting location.
        mapping_csv = tmp_path / "map_scene_id_to_name.csv"
        mapping_csv.write_text("1:scene_a\n2:scene_b\n")
        names, mapping = get_available_scenes(str(full_data))
        assert names == ["scene_a", "scene_b"]
        assert mapping == {"1": "scene_a", "2": "scene_b"}


class TestGetSceneInfoFromName:
    """``get_scene_info_from_name`` parses AICity'24 scene-dir names.

    The convention is ``{scene_type}_{scene_id}_{n_cameras}_{n_tracks}``
    where ``scene_type`` may itself contain underscores. The boundary is
    found by walking back three underscores from the end.
    """

    def test_simple_three_part_type(self):
        """Multi-segment scene_type with embedded underscores survives intact."""
        scene_type, scene_id, n_cameras, n_tracks = get_scene_info_from_name(
            "warehouse_floor1_scene001_20_150"
        )
        assert scene_type == "warehouse_floor1"
        assert scene_id == "scene001"
        assert n_cameras == 20
        assert n_tracks == 150

    def test_internal_warehouse_layout(self):
        """Real-world example pulled from the internal MTMC validation config."""
        scene_type, scene_id, n_cameras, n_tracks = get_scene_info_from_name(
            "full_warehouse_100000_10_20"
        )
        assert scene_type == "full_warehouse"
        assert scene_id == "100000"
        assert n_cameras == 10
        assert n_tracks == 20

    def test_n_cameras_and_n_tracks_are_ints(self):
        """The trailing two segments are coerced to ``int``."""
        _, _, n_cameras, n_tracks = get_scene_info_from_name(
            "type_id_5_42"
        )
        assert isinstance(n_cameras, int) and n_cameras == 5
        assert isinstance(n_tracks, int) and n_tracks == 42

    def test_non_int_trailing_segments_raise(self):
        """A scene name whose trailing segments aren't ints fails loudly.

        Pins the documented limitation: a name like ``"scene_001_extra"``
        won't parse with this helper — the trailing ``extra`` can't be
        coerced to ``int``.
        """
        with pytest.raises(ValueError):
            get_scene_info_from_name("scene_001_extra")


class TestGetSceneNamesSplits:
    """End-to-end convenience wrapper: scan + split in one call."""

    def test_default_split_against_synthetic_dataset(self, tmp_path):
        """A 90-scene tmp dataset + mapping CSV gets split by ``default``."""
        full_data = tmp_path / "full_data"
        full_data.mkdir()
        for i in range(1, 91):
            (full_data / f"scene_{i}").mkdir()
        (tmp_path / "map_scene_id_to_name.csv").write_text(
            "\n".join(f"{i}:scene_{i}" for i in range(1, 91)) + "\n"
        )
        train, val, test = get_scene_names_splits(str(full_data), split_type="default")
        assert len(train) == 40
        assert len(val) == 20
        assert len(test) == 30


# ---------------------------------------------------------------------------
# AICity'25 — packaged data + object-class re-export
# ---------------------------------------------------------------------------


class TestPackagedSceneIdToNameJson:
    """The shipped ``scenes/scene_id_to_name.json`` ships intact and loads cleanly."""

    def test_packaged_file_exists(self):
        from spatialai_data_utils.datasets.aicity25 import (
            get_default_scene_id_to_name_path,
        )
        assert osp.exists(get_default_scene_id_to_name_path()), (
            "scenes/scene_id_to_name.json was not packaged with the "
            "install — check release/MANIFEST.in."
        )

    def test_loader_returns_str_keyed_dict(self):
        """Keys must be the string form of ints; values must be scene names."""
        from spatialai_data_utils.datasets.aicity25 import (
            load_default_scene_id_to_name,
        )
        mapping = load_default_scene_id_to_name()
        assert isinstance(mapping, dict) and mapping, (
            "Packaged scene-id mapping must be a non-empty dict."
        )
        for k, v in mapping.items():
            assert isinstance(k, str) and k.isdigit(), (
                f"Scene-id key {k!r} must be the string form of an int."
            )
            assert isinstance(v, str) and v, (
                f"Scene name for id {k!r} must be a non-empty string."
            )

    def test_loader_pins_aicity25_track1_scenes(self):
        """Anchor the AICity'25 Track 1 four-warehouse mapping."""
        from spatialai_data_utils.datasets.aicity25 import (
            load_default_scene_id_to_name,
        )
        mapping = load_default_scene_id_to_name()
        assert mapping == {
            "17": "Warehouse_017",
            "18": "Warehouse_018",
            "19": "Warehouse_019",
            "20": "Warehouse_020",
        }

    def test_loader_returns_fresh_dict(self):
        """Each call must return an independent dict so callers can mutate."""
        from spatialai_data_utils.datasets.aicity25 import (
            load_default_scene_id_to_name,
        )
        first = load_default_scene_id_to_name()
        first["999"] = "scratch"
        second = load_default_scene_id_to_name()
        assert "999" not in second


class TestAICity25Spec:
    """Spec constants in ``datasets.aicity25.spec`` pin the official table.

    ``CLASS_ID_TO_NAME`` and ``NUM_FIELDS`` are shared between the
    evaluator, the submission converters under ``tools/aicity25/``,
    and any future AICity'25 consumers (visualizers, comparators,
    drift analysis, etc.). Changes to either constant are
    spec-breaking, so we pin them here.
    """

    def test_class_id_to_name_is_exactly_six_entries(self):
        """The AICity'25 spec defines exactly these six classes (0-5)."""
        from spatialai_data_utils.datasets.aicity25.spec import (
            CLASS_ID_TO_NAME,
        )
        assert CLASS_ID_TO_NAME == {
            0: "Person",
            1: "Forklift",
            2: "NovaCarter",
            3: "Transporter",
            4: "FourierGR1T2",
            5: "AgilityDigit",
        }

    def test_class_ids_are_contiguous_from_zero(self):
        """AICity'25 requires class IDs to start at 0 and be contiguous."""
        from spatialai_data_utils.datasets.aicity25.spec import (
            CLASS_ID_TO_NAME,
        )
        ids = sorted(CLASS_ID_TO_NAME.keys())
        assert ids == list(range(len(ids)))

    def test_num_fields_matches_format_string(self):
        """11 = scene_id, class_id, object_id, frame_id, x, y, z, w, l, h, yaw."""
        from spatialai_data_utils.datasets.aicity25.spec import (
            NUM_FIELDS,
        )
        assert NUM_FIELDS == 11


class TestPackagedAICity26SceneIdToNameJson:
    """The shipped ``datasets/aicity26/scenes/scene_id_to_name.json`` ships intact and loads cleanly."""

    def test_packaged_file_exists(self):
        from spatialai_data_utils.datasets.aicity26 import (
            get_default_scene_id_to_name_path,
        )
        assert osp.exists(get_default_scene_id_to_name_path()), (
            "datasets/aicity26/scenes/scene_id_to_name.json was not "
            "packaged with the install — check release/MANIFEST.in."
        )

    def test_loader_returns_str_keyed_dict(self):
        """Keys must be the string form of ints; values must be scene names."""
        from spatialai_data_utils.datasets.aicity26 import (
            load_default_scene_id_to_name,
        )
        mapping = load_default_scene_id_to_name()
        assert isinstance(mapping, dict) and mapping, (
            "Packaged scene-id mapping must be a non-empty dict."
        )
        for k, v in mapping.items():
            assert isinstance(k, str) and k.isdigit(), (
                f"Scene-id key {k!r} must be the string form of an int."
            )
            assert isinstance(v, str) and v, (
                f"Scene name for id {k!r} must be a non-empty string."
            )

    def test_loader_pins_aicity26_mtmc_scenes(self):
        """Anchor the AICity'26 MTMC three-warehouse mapping (23/24/25)."""
        from spatialai_data_utils.datasets.aicity26 import (
            load_default_scene_id_to_name,
        )
        mapping = load_default_scene_id_to_name()
        assert mapping == {
            "23": "Warehouse_023",
            "24": "Warehouse_024",
            "25": "Warehouse_025",
        }

    def test_loader_returns_fresh_dict(self):
        """Each call must return an independent dict so callers can mutate."""
        from spatialai_data_utils.datasets.aicity26 import (
            load_default_scene_id_to_name,
        )
        first = load_default_scene_id_to_name()
        first["999"] = "scratch"
        second = load_default_scene_id_to_name()
        assert "999" not in second

    def test_aicity26_scene_ids_dont_collide_with_aicity25(self):
        """The 2025 and 2026 scene-id sets must stay disjoint."""
        from spatialai_data_utils.datasets.aicity25 import (
            load_default_scene_id_to_name as load25,
        )
        from spatialai_data_utils.datasets.aicity26 import (
            load_default_scene_id_to_name as load26,
        )
        assert not (set(load25()) & set(load26())), (
            "AICity'25 and AICity'26 scene-id sets must not overlap."
        )


class TestAICity26Spec:
    """Spec constants in ``datasets.aicity26.spec`` pin the 2026 edition's table.

    The 2026 table is the 2025 set extended with ``PalletTruck`` at ID
    6.  Changes to either constant are spec-breaking, so we pin them
    here.
    """

    def test_class_id_to_name_extends_aicity25_with_pallet_truck(self):
        """AICity'26 = 2025's six classes + PalletTruck at ID 6."""
        from spatialai_data_utils.datasets.aicity26.spec import (
            CLASS_ID_TO_NAME,
        )
        assert CLASS_ID_TO_NAME == {
            0: "Person",
            1: "Forklift",
            2: "NovaCarter",
            3: "Transporter",
            4: "FourierGR1T2",
            5: "AgilityDigit",
            6: "PalletTruck",
        }

    def test_class_ids_are_contiguous_from_zero(self):
        """AICity'26 requires class IDs to start at 0 and be contiguous."""
        from spatialai_data_utils.datasets.aicity26.spec import (
            CLASS_ID_TO_NAME,
        )
        ids = sorted(CLASS_ID_TO_NAME.keys())
        assert ids == list(range(len(ids)))

    def test_num_fields_matches_aicity25(self):
        """AICity'26 keeps the 2025 11-column text format unchanged."""
        from spatialai_data_utils.datasets.aicity25.spec import (
            NUM_FIELDS as NUM_FIELDS_25,
        )
        from spatialai_data_utils.datasets.aicity26.spec import (
            NUM_FIELDS as NUM_FIELDS_26,
        )
        assert NUM_FIELDS_26 == NUM_FIELDS_25 == 11

    def test_2025_subset_of_2026_class_table(self):
        """Every 2025 (id, name) pair must survive verbatim into 2026."""
        from spatialai_data_utils.datasets.aicity25.spec import (
            CLASS_ID_TO_NAME as CLASS_25,
        )
        from spatialai_data_utils.datasets.aicity26.spec import (
            CLASS_ID_TO_NAME as CLASS_26,
        )
        for k, v in CLASS_25.items():
            assert k in CLASS_26 and CLASS_26[k] == v, (
                f"AICity'26 must inherit class id {k} ({v!r}) verbatim "
                f"from 2025; got {CLASS_26.get(k)!r}."
            )


class TestLoadClassConfigFromFileReExport:
    """``load_class_config_from_file`` is re-exported from the AICity'25 package."""

    def test_reexport_is_the_same_callable(self):
        from spatialai_data_utils.datasets.aicity25 import (
            load_class_config_from_file as reexport,
        )
        from spatialai_data_utils.loaders.object_classes import (
            load_class_config_from_file as canonical,
        )
        assert reexport is canonical

    def test_reexport_loads_a_minimal_config(self, tmp_path):
        """Sanity check that the re-export produces a usable config dict."""
        path = tmp_path / "classes.py"
        path.write_text(
            "CLASS_LIST = ['person', 'vehicle']\n"
            "SUB_CLASS_DICT = {'person': ['worker'], 'vehicle': ['truck']}\n"
        )
        from spatialai_data_utils.datasets.aicity25 import load_class_config_from_file
        cfg = load_class_config_from_file(str(path))
        assert cfg["CLASS_LIST"] == ["person", "vehicle"]
        assert cfg["CLASS_MAPPING_DICT"] == {"person": 0, "vehicle": 1}
        assert cfg["MAP_SUB_CLASS_TO_CLASS_DICT"] == {
            "worker": "person", "truck": "vehicle",
        }


# ---------------------------------------------------------------------------
# Scene + camera-name helpers (datasets.scenes)
# ---------------------------------------------------------------------------


class TestGetSceneKey:
    """``get_scene_key`` returns the part of a scene name before the first ``_``."""

    def test_extracts_first_underscore_segment(self):
        assert get_scene_key("warehouse_floor1_scene001") == "warehouse"

    def test_no_underscore_returns_whole_string(self):
        """Missing separator → ``split("_")[0]`` returns the whole string."""
        assert get_scene_key("warehouse") == "warehouse"

    def test_leading_underscore_returns_empty(self):
        """A scene name starting with ``_`` yields the empty prefix."""
        assert get_scene_key("_leading_underscore") == ""


class TestParseSceneAndGroup:
    """``parse_scene_and_group`` splits ``"<scene>+<group>"`` on its first ``+``."""

    def test_no_plus_returns_none_group(self):
        scene, group = parse_scene_and_group("scene_001")
        assert scene == "scene_001"
        assert group is None

    def test_simple_plus_separator(self):
        scene, group = parse_scene_and_group("scene_001+clustered-bev-sensor-1")
        assert scene == "scene_001"
        assert group == "clustered-bev-sensor-1"

    def test_only_first_plus_is_used(self):
        """Group names with embedded ``+`` are preserved (only the first ``+`` splits)."""
        scene, group = parse_scene_and_group("Scene+grp+with+plus")
        assert scene == "Scene"
        assert group == "grp+with+plus"

    def test_non_string_input_raises_type_error(self):
        with pytest.raises(TypeError, match="must be a str"):
            parse_scene_and_group(123)


class TestGetSceneInfoFromToken:
    """``get_scene_info_from_token`` extracts ``(scene, frame_id)`` from sample tokens."""

    def test_double_underscore_format(self):
        """Format 1: ``<scene>__<frame_id>`` (double underscore)."""
        scene, frame_id = get_scene_info_from_token("warehouse_scene_001__00042")
        assert scene == "warehouse_scene_001"
        assert frame_id == 42

    def test_trailing_underscore_int_format(self):
        """Format 2: ``<scene>_<frame_id>`` — fall back to last underscore."""
        scene, frame_id = get_scene_info_from_token("warehouse_scene_001_42")
        assert scene == "warehouse_scene_001"
        assert frame_id == 42

    def test_frame_id_is_int(self):
        _, frame_id = get_scene_info_from_token("scene__7")
        assert isinstance(frame_id, int)


class TestSortCamNamesById:
    """Numeric-then-natural-sort for camera names."""

    def test_pure_int_strings_sort_numerically(self):
        """``["1", "10", "2", "20"]`` should sort to ``[1, 2, 10, 20]``-style."""
        assert sort_cam_names_by_id(["10", "2", "1", "20"]) == ["1", "2", "10", "20"]

    def test_prefixed_names_use_natural_sort_fallback(self):
        """Names like ``c01`` / ``c10`` can't ``int()``-cast → natural sort kicks in."""
        names = ["c10", "c2", "c1", "c20"]
        assert sort_cam_names_by_id(names) == ["c1", "c2", "c10", "c20"]

    def test_camera_underscore_prefix(self):
        names = ["Camera_01", "Camera_10", "Camera_02", "Camera_100"]
        assert sort_cam_names_by_id(names) == [
            "Camera_01", "Camera_02", "Camera_10", "Camera_100",
        ]

    def test_empty_list_returns_empty_list(self):
        assert sort_cam_names_by_id([]) == []


class TestGetCamNameFromString:
    """Pull the Nth digit-run from an arbitrary string."""

    def test_default_returns_last_number(self):
        assert get_cam_name_from_string("Camera_01_image_42.jpg") == "42"

    def test_custom_index(self):
        """``num_index=0`` returns the first digit-run.

        Note: leading zeros are stripped because the underlying
        :func:`extract_numbers` ``int``-casts each run, so ``"01"``
        round-trips to ``"1"``.
        """
        assert get_cam_name_from_string("Camera_01_image_42.jpg", num_index=0) == "1"

    def test_no_numbers_returns_none(self):
        assert get_cam_name_from_string("no_digits_here") is None

    def test_negative_index_navigates_from_end(self):
        """``num_index=-2`` returns the second-from-last digit-run."""
        assert get_cam_name_from_string("a1b2c3", num_index=-2) == "2"


# ---------------------------------------------------------------------------
# Generic numeric-string helpers (utils.string_utils)
# ---------------------------------------------------------------------------


class TestExtractNumbers:
    """``extract_numbers`` returns every digit-run as an ``int``."""

    def test_multiple_runs(self):
        assert extract_numbers("a1b2c3") == [1, 2, 3]

    def test_no_digits_returns_empty_list(self):
        assert extract_numbers("no_digits_here") == []

    def test_multi_digit_runs_stay_grouped(self):
        """``"abc100def200"`` → ``[100, 200]`` (each run is one int)."""
        assert extract_numbers("abc100def200") == [100, 200]

    def test_handles_leading_zeros(self):
        """``int("01")`` is ``1`` — leading zeros collapse."""
        assert extract_numbers("Camera_01_007") == [1, 7]
