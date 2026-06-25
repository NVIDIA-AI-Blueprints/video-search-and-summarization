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
Test cases for filling "groups" field on tripwires and ROIs.

This module tests the helpers that populate the "groups" field on tripwires
and ROIs based on BEV sensor group membership:

1. ``_fill_groups_for_items`` – per-item group resolution logic

Uses synthetic calibration data for all tests – no external data files required.
"""

import pytest

from spatialai_data_utils.core.cameras.calibration_fields import (
    _fill_groups_for_items,
)


# ==============================================================================
# Test _fill_groups_for_items
# ==============================================================================


class TestFillGroupsForItems:
    """Test suite for _fill_groups_for_items helper."""

    def _group_map(self):
        return {
            "bev-sensor-1": {"Cam1", "Cam2"},
            "bev-sensor-2": {"Cam2", "Cam3"},
            "bev-sensor-3": {"Cam4"},
        }

    def test_single_group_match(self):
        items = [{"id": "tw1", "sensors": ["Cam1"]}]
        counts = _fill_groups_for_items(items, self._group_map())

        assert counts["added"] == 1
        assert items[0]["groups"] == ["bev-sensor-1"]

    def test_multiple_group_match(self):
        items = [{"id": "tw1", "sensors": ["Cam2"]}]
        _fill_groups_for_items(items, self._group_map())

        assert items[0]["groups"] == ["bev-sensor-1", "bev-sensor-2"]

    def test_sensor_in_disjoint_groups(self):
        items = [{"id": "tw1", "sensors": ["Cam1", "Cam4"]}]
        _fill_groups_for_items(items, self._group_map())

        assert items[0]["groups"] == ["bev-sensor-1", "bev-sensor-3"]

    def test_all_groups_match(self):
        items = [{"id": "tw1", "sensors": ["Cam1", "Cam3", "Cam4"]}]
        _fill_groups_for_items(items, self._group_map())

        assert items[0]["groups"] == ["bev-sensor-1", "bev-sensor-2", "bev-sensor-3"]

    def test_no_match(self):
        items = [{"id": "tw1", "sensors": ["UnknownCam"]}]
        _fill_groups_for_items(items, self._group_map())

        assert items[0]["groups"] == []

    def test_empty_sensors_list_raises(self):
        """Items with an empty sensors list should raise ValueError."""
        items = [{"id": "tw1", "sensors": []}]
        with pytest.raises(ValueError, match="tw1"):
            _fill_groups_for_items(items, self._group_map())

    def test_missing_sensors_field_raises(self):
        """Items without a sensors field should raise ValueError."""
        items = [{"id": "tw1"}]
        with pytest.raises(ValueError, match="tw1"):
            _fill_groups_for_items(items, self._group_map())

    def test_empty_items_list(self):
        counts = _fill_groups_for_items([], self._group_map())
        assert counts == {"added": 0, "corrected": 0, "unchanged": 0}

    def test_corrects_wrong_groups(self):
        """Wrong 'groups' field should be corrected."""
        items = [{"id": "tw1", "sensors": ["Cam4"], "groups": ["stale-group"]}]
        counts = _fill_groups_for_items(items, self._group_map())

        assert items[0]["groups"] == ["bev-sensor-3"]
        assert counts["corrected"] == 1

    def test_natural_sort_order(self):
        """Groups should be naturally sorted (bev-sensor-2 before bev-sensor-10)."""
        group_map = {
            "bev-sensor-10": {"CamA"},
            "bev-sensor-2": {"CamA"},
            "bev-sensor-1": {"CamA"},
        }
        items = [{"id": "tw1", "sensors": ["CamA"]}]
        _fill_groups_for_items(items, group_map)

        assert items[0]["groups"] == ["bev-sensor-1", "bev-sensor-2", "bev-sensor-10"]

    def test_multiple_items(self):
        items = [
            {"id": "tw1", "sensors": ["Cam1"]},
            {"id": "tw2", "sensors": ["Cam4"]},
            {"id": "tw3", "sensors": ["Cam2", "Cam3"]},
        ]
        counts = _fill_groups_for_items(items, self._group_map())

        assert counts["added"] == 3
        assert items[0]["groups"] == ["bev-sensor-1"]
        assert items[1]["groups"] == ["bev-sensor-3"]
        assert items[2]["groups"] == ["bev-sensor-1", "bev-sensor-2"]

    def test_empty_sensors_in_batch_raises(self):
        """An empty sensors list in a batch should raise before processing further."""
        items = [
            {"id": "tw1", "sensors": ["Cam1"]},
            {"id": "tw2", "sensors": []},
        ]
        with pytest.raises(ValueError, match="tw2"):
            _fill_groups_for_items(items, self._group_map())

    def test_fills_empty_groups(self):
        """Items with groups=[] should be counted as 'added'."""
        items = [{"id": "tw1", "sensors": ["Cam1"], "groups": []}]
        counts = _fill_groups_for_items(items, self._group_map())

        assert items[0]["groups"] == ["bev-sensor-1"]
        assert counts["added"] == 1

    def test_unchanged_correct_groups(self):
        """Items with already-correct groups should be counted as 'unchanged'."""
        items = [{"id": "tw1", "sensors": ["Cam1"], "groups": ["bev-sensor-1"]}]
        counts = _fill_groups_for_items(items, self._group_map())

        assert items[0]["groups"] == ["bev-sensor-1"]
        assert counts["unchanged"] == 1

    def test_mixed_states(self):
        """Test a mix of missing, empty, wrong, and correct items."""
        items = [
            {"id": "tw-missing", "sensors": ["Cam1"]},
            {"id": "tw-empty", "sensors": ["Cam4"], "groups": []},
            {"id": "tw-wrong", "sensors": ["Cam4"], "groups": ["bev-sensor-1"]},
            {"id": "tw-correct", "sensors": ["Cam2"], "groups": ["bev-sensor-1", "bev-sensor-2"]},
        ]
        counts = _fill_groups_for_items(items, self._group_map())

        assert counts == {"added": 2, "corrected": 1, "unchanged": 1}
        assert items[0]["groups"] == ["bev-sensor-1"]
        assert items[1]["groups"] == ["bev-sensor-3"]
        assert items[2]["groups"] == ["bev-sensor-3"]
        assert items[3]["groups"] == ["bev-sensor-1", "bev-sensor-2"]


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
