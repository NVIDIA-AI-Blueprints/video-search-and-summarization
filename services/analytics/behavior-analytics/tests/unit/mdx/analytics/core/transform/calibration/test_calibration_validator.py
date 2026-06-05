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

import copy
import unittest

from mdx.analytics.core.constants import (
    CALIBRATION_ACTION_DELETE,
    CALIBRATION_ACTION_UPSERT,
    CALIBRATION_ACTION_UPSERT_ALL,
)
from mdx.analytics.core.transform.calibration.calibration_validator import (
    CalibrationValidationError,
    validate,
)


def _good_full_calibration() -> dict:
    """A minimal payload that satisfies the vendored AJV schema.

    The schema's sensors[*].required is large; we fill exactly that set with
    plausible values so a single missing/wrong field in a given test can be
    isolated as the cause of any failure.
    """
    return {
        "version": "1.0",
        "osmURL": "",
        "calibrationType": "image",
        "sensors": [
            {
                "type": "camera",
                "id": "sensor-1",
                "origin": {"lat": 0.0, "lng": 0.0},
                "geoLocation": {"lat": 0.0, "lng": 0.0},
                "coordinates": {"x": 0.0, "y": 0.0},
                "scaleFactor": 1.0,
                "attributes": [],
                "place": [],
                "imageCoordinates": [],
                "globalCoordinates": [],
            }
        ],
    }


def _good_delete_payload() -> dict:
    """Minimal payload accepted by the delete-only schema."""
    return {
        "version": "1.0",
        "osmURL": "",
        "calibrationType": "image",
        "sensors": [{"id": "sensor-1"}],
    }


class TestValidateUpsertAll(unittest.TestCase):
    """upsert-all uses the full AJV schema."""

    def test_well_formed_payload_passes(self) -> None:
        validate(_good_full_calibration(), CALIBRATION_ACTION_UPSERT_ALL)  # no raise

    def test_missing_top_level_required_field_raises(self) -> None:
        bad = _good_full_calibration()
        del bad["calibrationType"]
        with self.assertRaises(CalibrationValidationError) as ctx:
            validate(bad, CALIBRATION_ACTION_UPSERT_ALL)
        self.assertIn("calibrationType", str(ctx.exception))

    def test_wrong_calibration_type_enum_raises(self) -> None:
        bad = _good_full_calibration()
        bad["calibrationType"] = "lidar"  # not in {geo, cartesian, image}
        with self.assertRaises(CalibrationValidationError):
            validate(bad, CALIBRATION_ACTION_UPSERT_ALL)

    def test_sensor_missing_required_field_raises(self) -> None:
        bad = _good_full_calibration()
        del bad["sensors"][0]["id"]
        with self.assertRaises(CalibrationValidationError) as ctx:
            validate(bad, CALIBRATION_ACTION_UPSERT_ALL)
        self.assertIn("id", str(ctx.exception))


class TestValidateUpsert(unittest.TestCase):
    """upsert uses the same full schema as upsert-all (web-api parity)."""

    def test_passes_when_full_schema_satisfied(self) -> None:
        validate(_good_full_calibration(), CALIBRATION_ACTION_UPSERT)  # no raise

    def test_fails_on_partial_sensor(self) -> None:
        bad = _good_full_calibration()
        del bad["sensors"][0]["origin"]
        with self.assertRaises(CalibrationValidationError):
            validate(bad, CALIBRATION_ACTION_UPSERT)


class TestValidateDelete(unittest.TestCase):
    """delete uses the minimal schema; only sensors[*].id is required."""

    def test_minimal_delete_payload_passes(self) -> None:
        validate(_good_delete_payload(), CALIBRATION_ACTION_DELETE)  # no raise

    def test_partial_sensor_without_legacy_fields_still_passes(self) -> None:
        """Critical case: deleted sensors emitted from legacy data may
        legitimately omit fields the full schema requires (no imageCoordinates,
        no origin). The delete validator must accept them.
        """
        payload = {
            "version": "1.0",
            "osmURL": "",
            "calibrationType": "image",
            "sensors": [
                # Each sensor only carries the id; no other required fields.
                {"id": "sensor-legacy-A"},
                {"id": "sensor-legacy-B"},
            ],
        }
        validate(payload, CALIBRATION_ACTION_DELETE)  # no raise

    def test_empty_sensors_array_raises(self) -> None:
        with self.assertRaises(CalibrationValidationError):
            validate({"sensors": []}, CALIBRATION_ACTION_DELETE)

    def test_sensor_missing_id_raises(self) -> None:
        with self.assertRaises(CalibrationValidationError):
            validate({"sensors": [{"type": "camera"}]}, CALIBRATION_ACTION_DELETE)

    def test_missing_sensors_array_raises(self) -> None:
        with self.assertRaises(CalibrationValidationError):
            validate({"version": "1.0"}, CALIBRATION_ACTION_DELETE)


class TestValidateUnknownAction(unittest.TestCase):
    def test_unknown_action_raises(self) -> None:
        with self.assertRaises(CalibrationValidationError) as ctx:
            validate(_good_full_calibration(), "unknown-action")
        self.assertIn("unknown calibration action", str(ctx.exception))


class TestErrorMessageContainsMultipleViolations(unittest.TestCase):
    """The validator caps the raised summary at 5 violations and notes overflow."""

    def test_many_violations_truncated_with_overflow_hint(self) -> None:
        # Build a payload with many sensor-level violations to trigger the cap.
        bad = _good_full_calibration()
        bad["sensors"] = [
            {
                # Strip 6 required fields -> 6 violations from this single sensor
                "id": "s",
            }
        ]
        with self.assertRaises(CalibrationValidationError) as ctx:
            validate(bad, CALIBRATION_ACTION_UPSERT_ALL)
        msg = str(ctx.exception)
        self.assertIn("more", msg)


if __name__ == "__main__":
    unittest.main()
