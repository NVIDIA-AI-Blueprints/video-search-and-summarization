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

"""Tests for ``core.cameras.calibration_fields``.

Pins two helpers:

* ``add_region_field`` — sensor-region computation from map size +
  per-sensor ``translationToGlobalCoordinates`` / ``scaleFactor``,
  including the two skip-and-warn branches (missing key / invalid
  numeric data).
* ``update_tripwire_roi_groups`` (+ private ``_fill_groups_for_items``)
  — populates ``groups`` from group-to-sensor membership intersections;
  exercises ``added`` / ``corrected`` / ``unchanged`` outcomes and
  the ``empty sensors -> ValueError`` guard.
"""

import logging

import pytest

from spatialai_data_utils.core.cameras.calibration_fields import (
    _fill_groups_for_items,
    add_region_field,
    update_tripwire_roi_groups,
)


# ---------------------------------------------------------------------------
# add_region_field
# ---------------------------------------------------------------------------


def _sensor(*, sensor_id="Camera_01", scale=10.0, tx=5.0, ty=7.0):
    return {
        "id": sensor_id,
        "scaleFactor": scale,
        "translationToGlobalCoordinates": {"x": tx, "y": ty},
    }


class TestAddRegionField:
    def test_region_origin_is_negated_translation(self):
        """``origin = [-tx, -ty]`` per the function docstring."""
        sensors = [_sensor(tx=5.0, ty=7.0, scale=10.0)]
        add_region_field(sensors, map_width=100, map_height=200)
        region = sensors[0]["region"]
        assert region["origin"] == [-5.0, -7.0]

    def test_region_dimensions_scale_pixels_to_world_meters(self):
        sensors = [_sensor(scale=10.0)]
        add_region_field(sensors, map_width=100, map_height=200)
        dims = sensors[0]["region"]["dimensions"]
        # width  = map_width  / scale = 100 / 10 = 10
        # length = map_height / scale = 200 / 10 = 20
        assert dims == {"length": 20.0, "width": 10.0}

    def test_region_place_level_is_constant_region(self):
        sensors = [_sensor()]
        add_region_field(sensors, map_width=10, map_height=10)
        assert sensors[0]["region"]["placeLevel"] == "region"

    def test_missing_required_calibration_key_warns_and_skips(self, caplog):
        """A sensor lacking ``scaleFactor`` should be skipped (no
        ``region`` field written) and a warning logged."""
        sensors = [
            {"id": "Camera_01", "translationToGlobalCoordinates": {"x": 0, "y": 0}},
            _sensor(sensor_id="Camera_02"),
        ]
        with caplog.at_level(logging.WARNING):
            add_region_field(sensors, map_width=10, map_height=10)
        assert "region" not in sensors[0]  # skipped
        assert "region" in sensors[1]  # second sensor still processed
        assert "Camera_01" in caplog.text

    def test_zero_scale_factor_warns_and_skips(self, caplog):
        sensors = [_sensor(scale=0.0)]
        with caplog.at_level(logging.WARNING):
            add_region_field(sensors, map_width=10, map_height=10)
        assert "region" not in sensors[0]
        assert "Camera_01" in caplog.text

    def test_non_numeric_translation_warns_and_skips(self, caplog):
        """``translation["x"] = None`` triggers a ``TypeError`` on the
        negation step, hitting the second except branch."""
        sensors = [{
            "id": "Camera_X",
            "scaleFactor": 1.0,
            "translationToGlobalCoordinates": {"x": None, "y": 0.0},
        }]
        with caplog.at_level(logging.WARNING):
            add_region_field(sensors, map_width=10, map_height=10)
        assert "region" not in sensors[0]
        assert "Camera_X" in caplog.text


# ---------------------------------------------------------------------------
# _fill_groups_for_items + update_tripwire_roi_groups
# ---------------------------------------------------------------------------


class TestFillGroupsForItems:
    def test_added_when_groups_field_missing(self):
        items = [{"id": "tw-1", "sensors": ["Camera_01"]}]
        group_to_sensors = {"g1": {"Camera_01"}, "g2": {"Camera_02"}}
        counts = _fill_groups_for_items(items, group_to_sensors)
        assert counts == {"added": 1, "corrected": 0, "unchanged": 0}
        assert items[0]["groups"] == ["g1"]

    def test_added_when_groups_field_empty(self):
        items = [{"id": "tw-1", "sensors": ["Camera_01"], "groups": []}]
        counts = _fill_groups_for_items(items, {"g1": {"Camera_01"}})
        assert counts["added"] == 1

    def test_corrected_when_existing_groups_differ(self, caplog):
        items = [{"id": "tw-1", "sensors": ["Camera_01"], "groups": ["g2"]}]
        with caplog.at_level(logging.WARNING):
            counts = _fill_groups_for_items(items, {"g1": {"Camera_01"}})
        assert counts == {"added": 0, "corrected": 1, "unchanged": 0}
        assert items[0]["groups"] == ["g1"]
        assert "corrected" in caplog.text

    def test_unchanged_when_existing_groups_match(self):
        items = [{"id": "tw-1", "sensors": ["Camera_01"], "groups": ["g1"]}]
        counts = _fill_groups_for_items(items, {"g1": {"Camera_01"}})
        assert counts == {"added": 0, "corrected": 0, "unchanged": 1}

    def test_groups_are_natural_sorted(self):
        """Group names like ``bev-sensor-10`` should sort after
        ``bev-sensor-2`` (natural sort, not lexicographic)."""
        items = [{"id": "roi-1", "sensors": ["Camera_01"]}]
        group_to_sensors = {
            "bev-sensor-2": {"Camera_01"},
            "bev-sensor-10": {"Camera_01"},
        }
        _fill_groups_for_items(items, group_to_sensors)
        assert items[0]["groups"] == ["bev-sensor-2", "bev-sensor-10"]

    def test_intersection_picks_only_groups_sharing_a_sensor(self):
        items = [{"id": "roi-1", "sensors": ["Camera_01", "Camera_02"]}]
        group_to_sensors = {
            "g_with_01": {"Camera_01", "Camera_99"},
            "g_with_02": {"Camera_02"},
            "g_with_neither": {"Camera_88", "Camera_77"},
        }
        _fill_groups_for_items(items, group_to_sensors)
        assert items[0]["groups"] == ["g_with_01", "g_with_02"]

    def test_empty_sensors_list_raises(self):
        items = [{"id": "tw-broken", "sensors": []}]
        with pytest.raises(ValueError, match="non-empty 'sensors' list"):
            _fill_groups_for_items(items, {"g1": {"Camera_01"}})

    def test_missing_sensors_field_raises(self):
        items = [{"id": "tw-broken"}]  # no sensors key at all
        with pytest.raises(ValueError, match="non-empty 'sensors' list"):
            _fill_groups_for_items(items, {"g1": {"Camera_01"}})


class TestUpdateTripwireRoiGroups:
    def test_returns_silently_when_neither_tripwires_nor_rois(self):
        calib = {"sensors": []}  # no tripwires, no rois
        # No raise, no mutation of unrelated keys.
        update_tripwire_roi_groups(calib, {})
        assert calib == {"sensors": []}

    def test_fills_both_tripwires_and_rois(self):
        calib = {
            "tripwires": [{"id": "tw-1", "sensors": ["Camera_01"]}],
            "rois": [{"id": "roi-1", "sensors": ["Camera_02"]}],
        }
        group_to_sensors = {"g1": {"Camera_01"}, "g2": {"Camera_02"}}
        update_tripwire_roi_groups(calib, group_to_sensors)
        assert calib["tripwires"][0]["groups"] == ["g1"]
        assert calib["rois"][0]["groups"] == ["g2"]

    def test_handles_only_tripwires_present(self):
        calib = {"tripwires": [{"id": "tw-1", "sensors": ["Camera_01"]}]}
        update_tripwire_roi_groups(calib, {"g1": {"Camera_01"}})
        assert calib["tripwires"][0]["groups"] == ["g1"]

    def test_handles_only_rois_present(self):
        calib = {"rois": [{"id": "roi-1", "sensors": ["Camera_01"]}]}
        update_tripwire_roi_groups(calib, {"g1": {"Camera_01"}})
        assert calib["rois"][0]["groups"] == ["g1"]

    def test_logs_summary_counts_when_items_processed(self, caplog):
        calib = {
            "tripwires": [{"id": "tw-1", "sensors": ["Camera_01"]}],
            "rois": [],
        }
        with caplog.at_level(logging.INFO):
            update_tripwire_roi_groups(calib, {"g1": {"Camera_01"}})
        # Header + per-item info line + summary line.
        assert "1 tripwire(s)" in caplog.text
        assert "Tripwires:" in caplog.text
