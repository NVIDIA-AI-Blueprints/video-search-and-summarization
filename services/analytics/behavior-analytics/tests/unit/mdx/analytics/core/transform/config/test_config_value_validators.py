# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""Unit tests for the per-key value validators."""

import json
import unittest

from mdx.analytics.core.schema.config import SpeedViolationConfig
from mdx.analytics.core.transform.config.config_validator import (
    ALLOWED_APP_KEYS,
    ALLOWED_SENSOR_KEYS,
)
from mdx.analytics.core.transform.config.config_value_validators import (
    APP_VALUE_VALIDATORS,
    SENSOR_VALUE_VALIDATORS,
    _bool,
    _datetime_iso8601_z,
    _enum,
    _float,
    _int,
    _json_dict,
    _json_list_of_str,
    _json_pydantic,
    _non_empty_str,
    validate_value,
)


class TestBoolValidator(unittest.TestCase):
    def test_accepts_lowercase_true_false(self) -> None:
        for v in ["true", "false"]:
            with self.subTest(v=v):
                ok, _ = _bool()(v)
                self.assertTrue(ok)

    def test_rejects_anything_else(self) -> None:
        for v in ["True", "FALSE", "yes", "no", "1", "0", "", " true"]:
            with self.subTest(v=v):
                ok, reason = _bool()(v)
                self.assertFalse(ok)
                self.assertIn("'true' or 'false'", reason)


class TestIntValidator(unittest.TestCase):
    def test_unbounded(self) -> None:
        for v in ["0", "100", "-5", "999999"]:
            with self.subTest(v=v):
                ok, _ = _int()(v)
                self.assertTrue(ok)

    def test_min_inclusive(self) -> None:
        rule = _int(min=1)
        self.assertTrue(rule("1")[0])
        self.assertTrue(rule("100")[0])
        self.assertFalse(rule("0")[0])
        self.assertFalse(rule("-5")[0])

    def test_max_inclusive(self) -> None:
        rule = _int(max=10)
        self.assertTrue(rule("10")[0])
        self.assertFalse(rule("11")[0])

    def test_min_and_max(self) -> None:
        rule = _int(min=1, max=5)
        for good in ["1", "3", "5"]:
            self.assertTrue(rule(good)[0], good)
        for bad in ["0", "6"]:
            self.assertFalse(rule(bad)[0], bad)

    def test_non_int_string_rejected(self) -> None:
        for v in ["abc", "1.5", "", "  "]:
            with self.subTest(v=v):
                ok, reason = _int()(v)
                self.assertFalse(ok)
                self.assertIn("integer", reason)


class TestFloatValidator(unittest.TestCase):
    def test_accepts_int_strings(self) -> None:
        # ``float("1")`` is fine -- int strings still satisfy a float rule.
        ok, _ = _float()("1")
        self.assertTrue(ok)

    def test_min_max(self) -> None:
        rule = _float(min=0.0, max=1.0)
        for good in ["0", "0.5", "1.0", "1"]:
            self.assertTrue(rule(good)[0], good)
        for bad in ["-0.1", "1.5", "abc"]:
            ok, _ = rule(bad)
            self.assertFalse(ok, bad)

    def test_non_numeric_rejected(self) -> None:
        ok, reason = _float()("nope")
        self.assertFalse(ok)
        self.assertIn("number", reason)


class TestEnumValidator(unittest.TestCase):
    def test_accepts_listed_values(self) -> None:
        rule = _enum("0", "1", "2")
        for v in ["0", "1", "2"]:
            self.assertTrue(rule(v)[0])

    def test_rejects_other_values(self) -> None:
        rule = _enum("0", "1", "2")
        ok, reason = rule("5")
        self.assertFalse(ok)
        self.assertIn("'0'", reason)
        self.assertIn("'1'", reason)
        self.assertIn("'2'", reason)


class TestJsonListOfStrValidator(unittest.TestCase):
    def test_accepts_list_of_strings(self) -> None:
        for v in ['[]', '["a"]', '["foo", "bar"]']:
            with self.subTest(v=v):
                self.assertTrue(_json_list_of_str()(v)[0])

    def test_rejects_non_json(self) -> None:
        ok, _ = _json_list_of_str()("not-json[")
        self.assertFalse(ok)

    def test_rejects_non_list(self) -> None:
        for v in ['{"x": 1}', '"foo"', '5', 'null']:
            with self.subTest(v=v):
                ok, reason = _json_list_of_str()(v)
                self.assertFalse(ok)
                self.assertIn("list of strings", reason)

    def test_rejects_list_of_non_strings(self) -> None:
        ok, _ = _json_list_of_str()('[1, 2, 3]')
        self.assertFalse(ok)


class TestJsonDictValidator(unittest.TestCase):
    def test_accepts_object(self) -> None:
        for v in ['{}', '{"k": "v"}', '{"a": 1, "b": [2,3]}']:
            with self.subTest(v=v):
                self.assertTrue(_json_dict()(v)[0])

    def test_rejects_non_object(self) -> None:
        for v in ['[1,2,3]', '"foo"', '5', 'null']:
            with self.subTest(v=v):
                ok, _ = _json_dict()(v)
                self.assertFalse(ok)

    def test_rejects_invalid_json(self) -> None:
        ok, reason = _json_dict()("not-json{")
        self.assertFalse(ok)
        self.assertIn("parse error", reason)


class TestJsonPydanticValidator(unittest.TestCase):
    def test_accepts_default_dump(self) -> None:
        """The default model_dump_json() output must round-trip cleanly."""
        rule = _json_pydantic(SpeedViolationConfig)
        value = SpeedViolationConfig().model_dump_json()
        ok, reason = rule(value)
        self.assertTrue(ok, reason)

    def test_rejects_wrong_shape(self) -> None:
        """Field of the wrong type produces a Pydantic ValidationError."""
        rule = _json_pydantic(SpeedViolationConfig)
        # mphThreshold is float in the model -- a string here violates the schema.
        bad = json.dumps({"mphThreshold": "not-a-number"})
        ok, reason = rule(bad)
        self.assertFalse(ok)
        self.assertIn("SpeedViolationConfig", reason)

    def test_rejects_non_json(self) -> None:
        rule = _json_pydantic(SpeedViolationConfig)
        ok, reason = rule("definitely{not}json")
        self.assertFalse(ok)
        self.assertIn("SpeedViolationConfig", reason)


class TestDatetimeValidator(unittest.TestCase):
    def test_accepts_iso8601_with_z(self) -> None:
        for v in [
            "1970-01-01T00:00:00.000Z",
            "2024-01-01T12:30:00Z",
            "2026-12-31T23:59:59.999Z",
        ]:
            with self.subTest(v=v):
                self.assertTrue(_datetime_iso8601_z()(v)[0])

    def test_rejects_missing_z_suffix(self) -> None:
        ok, reason = _datetime_iso8601_z()("2024-01-01T12:30:00+00:00")
        self.assertFalse(ok)
        self.assertIn("'Z' suffix", reason)

    def test_rejects_unparseable(self) -> None:
        ok, reason = _datetime_iso8601_z()("not-a-dateZ")
        self.assertFalse(ok)
        self.assertIn("ISO-8601", reason)


class TestNonEmptyStrValidator(unittest.TestCase):
    def test_accepts_text(self) -> None:
        self.assertTrue(_non_empty_str()("Person")[0])

    def test_rejects_whitespace_only(self) -> None:
        for v in ["", "   ", "\t\n"]:
            with self.subTest(v=v):
                ok, reason = _non_empty_str()(v)
                self.assertFalse(ok)
                self.assertIn("non-empty", reason)


class TestValidateValueDispatcher(unittest.TestCase):
    """``validate_value`` looks up the rule and runs it; missing names pass."""

    def test_missing_name_passes(self) -> None:
        ok, reason = validate_value("notInRegistry", "anything", APP_VALUE_VALIDATORS)
        self.assertTrue(ok)
        self.assertEqual(reason, "")

    def test_known_app_key_runs_rule(self) -> None:
        ok, _ = validate_value("behaviorMaxPoints", "500", APP_VALUE_VALIDATORS)
        self.assertTrue(ok)
        ok, reason = validate_value("behaviorMaxPoints", "0", APP_VALUE_VALIDATORS)
        self.assertFalse(ok)
        self.assertIn(">= 1", reason)

    def test_known_sensor_key_runs_rule(self) -> None:
        ok, _ = validate_value("tripwireMinPoints", "5", SENSOR_VALUE_VALIDATORS)
        self.assertTrue(ok)
        ok, reason = validate_value("tripwireMinPoints", "0", SENSOR_VALUE_VALIDATORS)
        self.assertFalse(ok)
        self.assertIn(">= 1", reason)


class TestRegistryCoversAllowlist(unittest.TestCase):
    """Every allowlisted key has a registered rule -- catch silent gaps."""

    def test_every_allowlisted_app_key_has_a_rule(self) -> None:
        missing = ALLOWED_APP_KEYS - APP_VALUE_VALIDATORS.keys()
        self.assertEqual(missing, set(), f"app keys missing value rules: {sorted(missing)}")

    def test_every_allowlisted_sensor_key_has_a_rule(self) -> None:
        missing = ALLOWED_SENSOR_KEYS - SENSOR_VALUE_VALIDATORS.keys()
        self.assertEqual(missing, set(), f"sensor keys missing value rules: {sorted(missing)}")

    def test_no_orphan_app_rules(self) -> None:
        """A rule registered for a non-allowlisted name is dead code."""
        orphans = APP_VALUE_VALIDATORS.keys() - ALLOWED_APP_KEYS
        self.assertEqual(orphans, set(), f"orphan app rules: {sorted(orphans)}")

    def test_no_orphan_sensor_rules(self) -> None:
        orphans = SENSOR_VALUE_VALIDATORS.keys() - ALLOWED_SENSOR_KEYS
        self.assertEqual(orphans, set(), f"orphan sensor rules: {sorted(orphans)}")


class TestSampleAppRules(unittest.TestCase):
    """A few end-to-end smoke checks of the registered app rules."""

    def test_traj_geo_coord_enable_is_bool(self) -> None:
        rule = APP_VALUE_VALIDATORS["trajGeoCoordEnable"]
        self.assertTrue(rule("true")[0])
        self.assertTrue(rule("false")[0])
        self.assertFalse(rule("1")[0])

    def test_behavior_max_points_int_min_1(self) -> None:
        rule = APP_VALUE_VALIDATORS["behaviorMaxPoints"]
        self.assertTrue(rule("1")[0])
        self.assertTrue(rule("999")[0])
        self.assertFalse(rule("0")[0])
        self.assertFalse(rule("abc")[0])

    def test_traj_direction_mode_enum_012(self) -> None:
        rule = APP_VALUE_VALIDATORS["trajDirectionMode"]
        for v in ["0", "1", "2"]:
            self.assertTrue(rule(v)[0])
        for v in ["3", "-1", "abc"]:
            self.assertFalse(rule(v)[0])

    def test_cluster_threshold_float_0_1(self) -> None:
        rule = APP_VALUE_VALIDATORS["clusterThreshold"]
        self.assertTrue(rule("0")[0])
        self.assertTrue(rule("0.5")[0])
        self.assertTrue(rule("1")[0])
        self.assertFalse(rule("1.5")[0])
        self.assertFalse(rule("-0.1")[0])

    def test_state_management_filter_json_list_of_str(self) -> None:
        rule = APP_VALUE_VALIDATORS["stateManagementFilter"]
        self.assertTrue(rule('["Person", "Forklift"]')[0])
        self.assertFalse(rule('not-json')[0])
        self.assertFalse(rule('{"a": 1}')[0])

    def test_image_location_mode_enum(self) -> None:
        rule = APP_VALUE_VALIDATORS["imageLocationMode"]
        self.assertTrue(rule("center")[0])
        self.assertTrue(rule("bottom_center")[0])
        self.assertFalse(rule("top_center")[0])

    def test_behavior_time_threshold_iso_z(self) -> None:
        rule = APP_VALUE_VALIDATORS["behaviorTimeThreshold"]
        self.assertTrue(rule("1970-01-01T00:00:00.000Z")[0])
        self.assertFalse(rule("1970-01-01T00:00:00+00:00")[0])

    def test_fov_count_violation_object_type_non_empty(self) -> None:
        rule = APP_VALUE_VALIDATORS["fovCountViolationIncidentObjectType"]
        self.assertTrue(rule("Person")[0])
        self.assertFalse(rule("")[0])
        self.assertFalse(rule("   ")[0])


class TestSampleSensorRules(unittest.TestCase):
    """A few end-to-end smoke checks of the registered sensor rules."""

    def test_anomaly_speed_violation_pydantic(self) -> None:
        rule = SENSOR_VALUE_VALIDATORS["anomalySpeedViolation"]
        # Default dump always validates.
        self.assertTrue(rule(SpeedViolationConfig().model_dump_json())[0])
        # Wrong type for a field fails.
        ok, reason = rule(json.dumps({"mphThreshold": "abc"}))
        self.assertFalse(ok)
        self.assertIn("SpeedViolationConfig", reason)

    def test_proximity_detection_threshold_float_min_0(self) -> None:
        rule = SENSOR_VALUE_VALIDATORS["proximityDetectionThreshold"]
        self.assertTrue(rule("0")[0])
        self.assertTrue(rule("1.8")[0])
        self.assertFalse(rule("-0.1")[0])

    def test_anomaly_classes_json_list_of_str(self) -> None:
        rule = SENSOR_VALUE_VALIDATORS["anomalyClasses"]
        self.assertTrue(rule('[]')[0])
        self.assertTrue(rule('["car", "truck"]')[0])
        self.assertFalse(rule('[1, 2]')[0])


if __name__ == "__main__":
    unittest.main()
