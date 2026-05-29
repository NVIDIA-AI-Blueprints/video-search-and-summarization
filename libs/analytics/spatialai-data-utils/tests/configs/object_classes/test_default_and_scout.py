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

"""Pin the schema of the per-dataset object-class config modules.

The ``default`` (single-class ``person``) and ``scout`` configs are
consumed by converters / loaders that iterate ``CLASS_LIST`` and look
up the same key in ``MAP_CLASS_NAMES`` / ``ATTRIBUTE_DICT`` /
``CLASS_RANGE_DICT``. These tests pin the cross-dict key consistency
so that adding a class to ``CLASS_LIST`` without populating the
companion dicts fails fast at test time.
"""

import pytest

from spatialai_data_utils.configs.object_classes import default, scout


@pytest.mark.parametrize("module", [default, scout], ids=["default", "scout"])
def test_class_list_is_non_empty_lowercase(module):
    assert isinstance(module.CLASS_LIST, list)
    assert len(module.CLASS_LIST) > 0
    assert all(isinstance(c, str) and c == c.lower() for c in module.CLASS_LIST)


@pytest.mark.parametrize("module", [default, scout], ids=["default", "scout"])
def test_companion_dicts_cover_every_class(module):
    """``MAP_CLASS_NAMES`` / ``ATTRIBUTE_DICT`` / ``CLASS_RANGE_DICT``
    must have one entry per ``CLASS_LIST`` member; converters look up
    the same key in all three."""
    expected = set(module.CLASS_LIST)
    assert set(module.MAP_CLASS_NAMES) == expected
    assert set(module.ATTRIBUTE_DICT) == expected
    assert set(module.CLASS_RANGE_DICT) == expected


@pytest.mark.parametrize("module", [default, scout], ids=["default", "scout"])
def test_map_class_names_capitalize_lowercase_keys(module):
    """The converters use ``MAP_CLASS_NAMES`` to canonicalize lowercase
    class strings (as they appear in raw data) into the capitalized
    NVSchema form. ``"person" -> "Person"`` is the canonical example."""
    for raw, canonical in module.MAP_CLASS_NAMES.items():
        assert raw == raw.lower()
        assert canonical[0].isupper(), f"{raw} -> {canonical!r} should start uppercase"


@pytest.mark.parametrize("module", [default, scout], ids=["default", "scout"])
def test_class_range_dict_values_are_positive_meters(module):
    for cls, rng in module.CLASS_RANGE_DICT.items():
        assert isinstance(rng, (int, float)) and rng > 0, (
            f"{module.__name__}.CLASS_RANGE_DICT[{cls!r}] must be a positive distance"
        )


def test_scout_metadata_matches_dataset_spec():
    """SCOUT dataset metadata is referenced by the loader / preprocessor
    via these constants — pin them to catch silent reconfiguration."""
    assert scout.DATASET_NAME == "SCOUT"
    assert scout.NUM_CAMERAS == 25
    assert scout.FPS == 10
    assert scout.RESOLUTION == [1920, 1080]
    assert scout.COORDINATE_SYSTEM == "world"


def test_default_sub_class_dict_is_empty():
    """The single-class ``default`` config has no sub-class hierarchy."""
    assert default.SUB_CLASS_DICT == {}
    assert scout.SUB_CLASS_DICT == {}
