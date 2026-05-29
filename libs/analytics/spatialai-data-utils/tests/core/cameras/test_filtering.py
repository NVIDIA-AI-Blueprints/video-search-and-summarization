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

"""Tests for ``core.cameras.filtering``.

Covers the sensor-existence checks (dict + file variants),
``filter_sensors_in_objects`` (ROI / tripwire pruning, including the
"clear groups when sensors empty" rule), and
``filter_sensors_by_names`` end-to-end (validation pass, validation
fail, and ROI/tripwire propagation).
"""

import json

from spatialai_data_utils.core.cameras.filtering import (
    check_if_sensor_in_sensor_set,
    check_sensor_in_calibration_dict,
    check_sensor_in_calibration_file,
    check_sensors_in_calibration_dict,
    check_sensors_in_calibration_file,
    filter_sensors_by_names,
    filter_sensors_in_objects,
)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _calib_dict(sensor_ids=("Camera_01", "Camera_02"), rois=None, tripwires=None):
    out = {"sensors": [{"id": sid} for sid in sensor_ids]}
    if rois is not None:
        out["rois"] = rois
    if tripwires is not None:
        out["tripwires"] = tripwires
    return out


# ---------------------------------------------------------------------------
# check_if_sensor_in_sensor_set
# ---------------------------------------------------------------------------


def test_check_if_sensor_in_sensor_set_hit_and_miss():
    s = {"Camera_01", "Camera_02"}
    assert check_if_sensor_in_sensor_set("Camera_01", s) is True
    assert check_if_sensor_in_sensor_set("Camera_99", s) is False


# ---------------------------------------------------------------------------
# check_sensors_in_calibration_dict
# ---------------------------------------------------------------------------


def test_check_sensors_in_calibration_dict_all_present():
    assert check_sensors_in_calibration_dict(
        ["Camera_01"], _calib_dict()
    ) is True
    assert check_sensors_in_calibration_dict(
        ["Camera_01", "Camera_02"], _calib_dict()
    ) is True


def test_check_sensors_in_calibration_dict_missing_returns_false():
    assert check_sensors_in_calibration_dict(
        ["Camera_03"], _calib_dict()
    ) is False


def test_check_sensors_in_calibration_dict_rejects_non_dict_input():
    assert check_sensors_in_calibration_dict(["x"], "not a dict") is False


def test_check_sensors_in_calibration_dict_rejects_missing_sensors_key():
    assert check_sensors_in_calibration_dict(["x"], {"foo": "bar"}) is False


def test_check_sensors_in_calibration_dict_swallows_unexpected_exception(caplog):
    """Pass a malformed ``sensors`` entry (not a dict) that triggers
    ``sensor.get("id")`` to raise ``AttributeError`` inside the
    comprehension. The function should log + return False rather than
    propagate."""
    bad = {"sensors": [None]}  # None.get → AttributeError
    assert check_sensors_in_calibration_dict(["x"], bad) is False


# ---------------------------------------------------------------------------
# check_sensor_in_calibration_dict (single-sensor variant)
# ---------------------------------------------------------------------------


def test_check_sensor_in_calibration_dict_hit_and_miss():
    cd = _calib_dict()
    assert check_sensor_in_calibration_dict("Camera_01", cd) is True
    assert check_sensor_in_calibration_dict("Camera_99", cd) is False


def test_check_sensor_in_calibration_dict_rejects_non_dict():
    assert check_sensor_in_calibration_dict("x", "not a dict") is False


def test_check_sensor_in_calibration_dict_rejects_missing_sensors_key():
    assert check_sensor_in_calibration_dict("x", {"foo": "bar"}) is False


def test_check_sensor_in_calibration_dict_swallows_unexpected_exception():
    assert check_sensor_in_calibration_dict("x", {"sensors": [None]}) is False


# ---------------------------------------------------------------------------
# check_sensors_in_calibration_file + check_sensor_in_calibration_file
# ---------------------------------------------------------------------------


def test_check_sensors_in_calibration_file_loads_and_validates(tmp_path):
    path = tmp_path / "calib.json"
    path.write_text(json.dumps(_calib_dict()))
    assert check_sensors_in_calibration_file(["Camera_01"], str(path)) is True
    assert check_sensors_in_calibration_file(["Camera_99"], str(path)) is False


def test_check_sensors_in_calibration_file_missing_path_returns_false(tmp_path):
    assert check_sensors_in_calibration_file(
        ["Camera_01"], str(tmp_path / "no-such.json"),
    ) is False


def test_check_sensors_in_calibration_file_invalid_json_returns_false(tmp_path):
    path = tmp_path / "calib.json"
    path.write_text("{ not valid json")
    assert check_sensors_in_calibration_file(["Camera_01"], str(path)) is False


def test_check_sensor_in_calibration_file_loads_and_validates(tmp_path):
    path = tmp_path / "calib.json"
    path.write_text(json.dumps(_calib_dict()))
    assert check_sensor_in_calibration_file("Camera_01", str(path)) is True
    assert check_sensor_in_calibration_file("Camera_99", str(path)) is False


def test_check_sensor_in_calibration_file_missing_path_returns_false(tmp_path):
    assert check_sensor_in_calibration_file(
        "Camera_01", str(tmp_path / "no-such.json"),
    ) is False


def test_check_sensor_in_calibration_file_invalid_json_returns_false(tmp_path):
    path = tmp_path / "calib.json"
    path.write_text("{ not valid json")
    assert check_sensor_in_calibration_file("Camera_01", str(path)) is False


# ---------------------------------------------------------------------------
# filter_sensors_in_objects
# ---------------------------------------------------------------------------


def test_filter_sensors_in_objects_drops_unrequested_sensors():
    objs = [
        {"id": "roi-1", "sensors": ["Camera_01", "Camera_03"], "groups": ["g1"]},
    ]
    out = filter_sensors_in_objects(objs, ["Camera_01"], object_type="rois")
    assert out[0]["sensors"] == ["Camera_01"]
    # Non-empty after filtering -> groups untouched.
    assert out[0]["groups"] == ["g1"]


def test_filter_sensors_in_objects_clears_groups_when_no_sensors_remain():
    """When every sensor in an object is removed and the object had a
    ``groups`` field, that field is cleared (post-filter clean-up)."""
    objs = [
        {"id": "roi-1", "sensors": ["Camera_03"], "groups": ["g1"]},
    ]
    out = filter_sensors_in_objects(objs, ["Camera_01"], object_type="rois")
    assert out[0]["sensors"] == []
    assert out[0]["groups"] == []


def test_filter_sensors_in_objects_preserves_objects_without_sensors_field():
    objs = [{"id": "roi-1", "metadata": "abc"}]  # no sensors key
    out = filter_sensors_in_objects(objs, ["Camera_01"])
    assert out == objs


def test_filter_sensors_in_objects_non_list_input_returned_unchanged():
    not_a_list = {"id": "roi-1"}
    out = filter_sensors_in_objects(not_a_list, ["Camera_01"])
    assert out is not_a_list


def test_filter_sensors_in_objects_does_not_mutate_input():
    original = [{"id": "roi-1", "sensors": ["A", "B"], "groups": ["g"]}]
    snapshot = json.dumps(original)
    filter_sensors_in_objects(original, ["A"])
    # Source is unchanged (filter takes a shallow copy per object).
    assert json.dumps(original) == snapshot


# ---------------------------------------------------------------------------
# filter_sensors_by_names
# ---------------------------------------------------------------------------


def test_filter_sensors_by_names_happy_path():
    calib = _calib_dict(("Camera_01", "Camera_02", "Camera_03"))
    out = filter_sensors_by_names(calib, ["Camera_01", "Camera_03"])
    kept_ids = [s["id"] for s in out["sensors"]]
    assert kept_ids == ["Camera_01", "Camera_03"]


def test_filter_sensors_by_names_returns_none_on_missing_sensor():
    """If any requested name is absent, the function returns ``None``
    so callers can fail fast rather than silently producing partial
    output."""
    calib = _calib_dict(("Camera_01",))
    assert filter_sensors_by_names(calib, ["Camera_01", "Camera_99"]) is None


def test_filter_sensors_by_names_returns_none_on_non_dict_input():
    assert filter_sensors_by_names("not a dict", ["x"]) is None


def test_filter_sensors_by_names_returns_none_on_missing_sensors_key():
    assert filter_sensors_by_names({"foo": "bar"}, ["x"]) is None


def test_filter_sensors_by_names_propagates_to_rois_and_tripwires():
    """The ROI/tripwire side-tables get their sensor lists filtered to
    the same selection."""
    calib = _calib_dict(
        ("Camera_01", "Camera_02"),
        rois=[{"id": "roi-1", "sensors": ["Camera_01", "Camera_02"], "groups": ["g"]}],
        tripwires=[{"id": "tw-1", "sensors": ["Camera_02"], "groups": ["g"]}],
    )
    out = filter_sensors_by_names(calib, ["Camera_01"])
    assert [s["id"] for s in out["sensors"]] == ["Camera_01"]
    assert out["rois"][0]["sensors"] == ["Camera_01"]
    # Tripwire's only sensor was dropped → groups cleared.
    assert out["tripwires"][0]["sensors"] == []
    assert out["tripwires"][0]["groups"] == []


def test_filter_sensors_by_names_swallows_unexpected_exception():
    """A malformed sensors entry triggers ``sensor.copy()`` to raise.
    The function logs + returns None instead of propagating."""
    bad = {"sensors": [None]}  # None.copy -> AttributeError
    assert filter_sensors_by_names(bad, []) is None
