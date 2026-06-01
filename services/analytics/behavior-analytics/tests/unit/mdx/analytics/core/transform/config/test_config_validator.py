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

"""Unit tests for :func:`validate`, :func:`validate_envelope`, and :class:`ValidationResult`."""

import unittest
from datetime import datetime, timezone

from mdx.analytics.core.transform.config.config_validator import (
    ALLOWED_APP_KEYS,
    ALLOWED_SENSOR_KEYS,
    validate,
    validate_envelope,
    ValidationResult,
)


def _kv(name: str, value: str) -> dict[str, str]:
    """Shorthand for the on-the-wire key-value dict."""
    return {"name": name, "value": value}


# Convenience aliases for "this name is in the corresponding allowlist". Using
# real allowlisted names in success-path tests keeps the tests honest about
# what the validator actually accepts.
APP_KEY_A = "behaviorWatermarkSec"
APP_KEY_B = "behaviorStateTimeout"
APP_KEY_C = "in3dMode"
SENSOR_KEY_A = "tripwireMinPoints"
SENSOR_KEY_B = "sensorMinFrames"
# A name that is well-known in the codebase (captured at process __init__,
# so a runtime upsert would not take effect without a restart) but explicitly
# NOT allowlisted for runtime updates.
NON_ALLOWLISTED_APP_KEY = "numProcesses"
NON_ALLOWLISTED_SENSOR_KEY = "anomalyCollisionDetection"


class TestConfigValidatorShape(unittest.TestCase):
    """Step 1 of the ladder -- the payload itself must be a JSON object."""

    def test_non_dict_payload_is_failure(self) -> None:
        for bad in [None, [], [1, 2, 3], "config", 42, 3.14]:
            with self.subTest(bad=bad):
                v = validate(bad)
                self.assertEqual(v.status, "failure")
                self.assertIn("not a JSON object", v.error)
                self.assertEqual(v.applied_app, [])
                self.assertEqual(v.applied_sensors, [])

    def test_empty_dict_is_success_noop(self) -> None:
        """An empty ``{}`` is a legitimate no-op success -- the operator
        explicitly said "no changes" and we did nothing, which is what
        they asked for. ``error`` stays ``null``."""
        v = validate({})
        self.assertEqual(v.status, "success")
        self.assertEqual(v.applied_app, [])
        self.assertEqual(v.applied_sensors, [])
        self.assertIsNone(v.error)

    def test_empty_app_and_sensors_lists_is_success_noop(self) -> None:
        """Explicit empty arrays -> same success no-op as ``{}``."""
        v = validate({"app": [], "sensors": []})
        self.assertEqual(v.status, "success")
        self.assertEqual(v.applied_app, [])
        self.assertEqual(v.applied_sensors, [])
        self.assertIsNone(v.error)


class TestConfigValidatorScope(unittest.TestCase):
    """Step 2 of the ladder -- read-only sections become per-section rejections."""

    def test_kafka_section_with_valid_app_is_partial_success(self) -> None:
        v = validate({"app": [_kv(APP_KEY_A, "1")], "kafka": {"brokers": "x"}})
        self.assertEqual(v.status, "partial-success")
        self.assertEqual(v.applied_app, [_kv(APP_KEY_A, "1")])
        self.assertIn("kafka", v.error)
        self.assertIn("read-only", v.error)
        self.assertIn("applied 1", v.error)

    def test_only_forbidden_sections_is_failure(self) -> None:
        """Pure forbidden payload (no app/sensors) -> failure, nothing applied."""
        v = validate({"inference": {"url": "http://x"}})
        self.assertEqual(v.status, "failure")
        self.assertEqual(v.applied_app, [])
        self.assertIn("inference", v.error)
        self.assertIn("read-only", v.error)

    def test_multiple_forbidden_sections_listed_sorted(self) -> None:
        v = validate({"redisStream": {}, "mqtt": {}})
        self.assertEqual(v.status, "failure")
        self.assertIn("mqtt", v.error)
        self.assertIn("redisStream", v.error)
        # Sorted: mqtt comes before redisStream.
        self.assertLess(v.error.index("mqtt"), v.error.index("redisStream"))

    def test_forbidden_with_partial_app_still_partial_success(self) -> None:
        v = validate(
            {
                "kafka": {"brokers": "x"},
                "app": [_kv(APP_KEY_A, "1"), {"name": "", "value": "bad"}],
            }
        )
        self.assertEqual(v.status, "partial-success")
        self.assertEqual(v.applied_app, [_kv(APP_KEY_A, "1")])
        self.assertIn("kafka", v.error)
        self.assertIn("app[1]", v.error)


class TestConfigValidatorAppItems(unittest.TestCase):
    """Step 3 -- per-item validation under ``app`` (shape-level rules)."""

    def test_app_must_be_a_list(self) -> None:
        v = validate({"app": "not-a-list"})
        self.assertEqual(v.status, "failure")
        self.assertIn("app", v.error)
        self.assertIn("must be a list", v.error)

    def test_app_item_not_a_dict_rejects(self) -> None:
        v = validate({"app": ["not-a-dict"]})
        self.assertEqual(v.status, "failure")
        self.assertIn("must be a dict", v.error)

    def test_app_item_missing_name_rejects(self) -> None:
        v = validate({"app": [{"value": "x"}]})
        self.assertEqual(v.status, "failure")
        self.assertIn("name", v.error)

    def test_app_item_empty_name_rejects(self) -> None:
        v = validate({"app": [{"name": "", "value": "x"}]})
        self.assertEqual(v.status, "failure")

    def test_app_item_non_string_value_rejects(self) -> None:
        v = validate({"app": [{"name": APP_KEY_A, "value": 5}]})
        self.assertEqual(v.status, "failure")
        self.assertIn("value", v.error)

    def test_all_app_items_allowlisted_is_success(self) -> None:
        v = validate({"app": [_kv(APP_KEY_A, "1"), _kv(APP_KEY_B, "2")]})
        self.assertEqual(v.status, "success")
        self.assertEqual(v.applied_app, [_kv(APP_KEY_A, "1"), _kv(APP_KEY_B, "2")])
        self.assertIsNone(v.error)

    def test_partial_success_keeps_good_drops_bad(self) -> None:
        v = validate(
            {"app": [_kv(APP_KEY_A, "30"), {"name": "", "value": "x"}]}
        )
        self.assertEqual(v.status, "partial-success")
        self.assertEqual(v.applied_app, [_kv(APP_KEY_A, "30")])
        self.assertIn("app[1]", v.error)
        self.assertIn("applied 1", v.error)


class TestConfigValidatorSensorItems(unittest.TestCase):
    """Step 3 -- per-item validation under ``sensors[*].configs`` (shape-level)."""

    def test_sensors_must_be_a_list(self) -> None:
        v = validate({"sensors": "not-a-list"})
        self.assertEqual(v.status, "failure")
        self.assertIn("sensors", v.error)

    def test_sensor_entry_not_a_dict_rejects(self) -> None:
        v = validate({"sensors": ["not-a-dict"]})
        self.assertEqual(v.status, "failure")
        self.assertIn("sensors[0]", v.error)
        self.assertIn("must be a dict", v.error)

    def test_sensor_missing_id_rejects(self) -> None:
        v = validate(
            {"sensors": [{"configs": [_kv(SENSOR_KEY_A, "5")]}]}
        )
        self.assertEqual(v.status, "failure")
        self.assertIn("sensors[0].id", v.error)

    def test_sensor_empty_id_rejects(self) -> None:
        v = validate(
            {"sensors": [{"id": "", "configs": [_kv(SENSOR_KEY_A, "5")]}]}
        )
        self.assertEqual(v.status, "failure")
        self.assertIn("sensors[0].id", v.error)

    def test_sensor_configs_must_be_a_list(self) -> None:
        v = validate({"sensors": [{"id": "cam1", "configs": "nope"}]})
        self.assertEqual(v.status, "failure")
        self.assertIn("sensors[0].configs", v.error)

    def test_sensor_partial_drops_whole_entry(self) -> None:
        v = validate(
            {
                "sensors": [
                    {"id": "cam1", "configs": [_kv(SENSOR_KEY_A, "5"), {"name": "", "value": "x"}]},
                    {"id": "cam2", "configs": [_kv(SENSOR_KEY_B, "5")]},
                ]
            }
        )
        self.assertEqual(v.status, "partial-success")
        # cam1 dropped wholesale because of one bad item.
        self.assertEqual(len(v.applied_sensors), 1)
        self.assertEqual(v.applied_sensors[0]["id"], "cam2")
        self.assertIn("cam1", v.error)

    def test_all_sensor_items_allowlisted_is_success(self) -> None:
        v = validate(
            {"sensors": [{"id": "cam1", "configs": [_kv(SENSOR_KEY_A, "5")]}]}
        )
        self.assertEqual(v.status, "success")
        self.assertEqual(
            v.applied_sensors, [{"id": "cam1", "configs": [_kv(SENSOR_KEY_A, "5")]}]
        )
        self.assertIsNone(v.error)

    def test_sensor_with_empty_configs_is_failure(self) -> None:
        """An empty ``configs: []`` is ambiguous (no-op vs wipe-all) so
        the validator rejects it. Operator must either omit the sensor
        entry entirely (no-op) or use a future explicit delete event."""
        v = validate({"sensors": [{"id": "cam1", "configs": []}]})
        self.assertEqual(v.status, "failure")
        self.assertEqual(v.applied_sensors, [])
        self.assertIn("empty sensor configs not allowed", v.error)
        self.assertIn("cam1", v.error)

    def test_partial_sensors_empty_is_rejection_non_empty_applies(self) -> None:
        """Mixed list: one sensor with items, one with empty configs.
        The empty one becomes a per-item rejection; the non-empty one
        applies; status is ``partial-success``."""
        v = validate(
            {"sensors": [
                {"id": "cam1", "configs": []},
                {"id": "cam2", "configs": [_kv(SENSOR_KEY_A, "5")]},
            ]}
        )
        self.assertEqual(v.status, "partial-success")
        self.assertEqual(
            v.applied_sensors,
            [{"id": "cam2", "configs": [_kv(SENSOR_KEY_A, "5")]}],
        )
        self.assertIn("empty sensor configs not allowed", v.error)
        self.assertIn("cam1", v.error)


class TestConfigValidatorAllowlist(unittest.TestCase):
    """Step 3b -- names not in the allowlist are rejected per-item."""

    def test_non_allowlisted_app_key_alone_is_failure(self) -> None:
        v = validate({"app": [_kv(NON_ALLOWLISTED_APP_KEY, "8")]})
        self.assertEqual(v.status, "failure")
        self.assertEqual(v.applied_app, [])
        self.assertIn("app[0]", v.error)
        self.assertIn("not allowlisted", v.error)
        self.assertIn(NON_ALLOWLISTED_APP_KEY, v.error)

    def test_allowlisted_alongside_non_allowlisted_is_partial_success(self) -> None:
        v = validate(
            {"app": [_kv(APP_KEY_A, "30"), _kv(NON_ALLOWLISTED_APP_KEY, "8")]}
        )
        self.assertEqual(v.status, "partial-success")
        self.assertEqual(v.applied_app, [_kv(APP_KEY_A, "30")])
        self.assertIn("not allowlisted", v.error)
        self.assertIn("applied 1", v.error)

    def test_non_allowlisted_sensor_key_alone_is_failure(self) -> None:
        v = validate(
            {"sensors": [{"id": "cam1", "configs": [_kv(NON_ALLOWLISTED_SENSOR_KEY, "{}")]}]}
        )
        self.assertEqual(v.status, "failure")
        self.assertEqual(v.applied_sensors, [])
        self.assertIn(NON_ALLOWLISTED_SENSOR_KEY, v.error)
        self.assertIn("not allowlisted", v.error)

    def test_app_keys_not_allowlisted_for_sensor_section(self) -> None:
        """An ``app``-level key under sensors[*].configs is rejected (different allowlist)."""
        v = validate(
            {"sensors": [{"id": "cam1", "configs": [_kv(APP_KEY_A, "30")]}]}
        )
        self.assertEqual(v.status, "failure")
        self.assertIn("not allowlisted", v.error)

    def test_sensor_keys_not_allowlisted_for_app_section(self) -> None:
        """A sensor-level key under app[] is rejected (different allowlist)."""
        v = validate({"app": [_kv(SENSOR_KEY_A, "5")]})
        self.assertEqual(v.status, "failure")
        self.assertIn("not allowlisted", v.error)

    def test_allowlists_are_disjoint(self) -> None:
        """No name should appear in both allowlists -- confusion would obscure intent."""
        self.assertEqual(ALLOWED_APP_KEYS & ALLOWED_SENSOR_KEYS, frozenset())

    def test_excluded_tier3_keys_are_not_allowlisted(self) -> None:
        """Spot-check a few known restart-required names to lock them out of the allowlist."""
        for name in [
            "numProcesses",
            "numWorkersForBehaviorCreation",
            "sourceType",
            "sinkType",
            "embedDownsamplerType",
            "spaceAnalyticsGridSize",
            "playbackLoop",
            "robotApiBaseUrl",
            "cameraFontSize",
        ]:
            with self.subTest(name=name):
                self.assertNotIn(name, ALLOWED_APP_KEYS)

    def test_excluded_tier3_sensor_keys_are_not_allowlisted(self) -> None:
        # anomalyCollisionDetection is the one anomaly* key captured at __init__.
        self.assertNotIn("anomalyCollisionDetection", ALLOWED_SENSOR_KEYS)

    def test_invalid_value_for_allowlisted_app_key_is_rejection(self) -> None:
        """Allowlisted name + invalid value -> per-item rejection with 'invalid:' prefix."""
        v = validate({"app": [_kv("behaviorMaxPoints", "0")]})
        self.assertEqual(v.status, "failure")
        self.assertIn("invalid", v.error)
        self.assertIn("behaviorMaxPoints", v.error)
        # Reason from the value validator carries through.
        self.assertIn(">= 1", v.error)

    def test_invalid_value_alongside_valid_is_partial_success(self) -> None:
        v = validate(
            {"app": [_kv("behaviorMaxPoints", "0"), _kv("behaviorWatermarkSec", "30")]}
        )
        self.assertEqual(v.status, "partial-success")
        self.assertEqual(v.applied_app, [_kv("behaviorWatermarkSec", "30")])
        self.assertIn("behaviorMaxPoints", v.error)
        self.assertIn("invalid", v.error)

    def test_invalid_value_for_allowlisted_sensor_key_is_rejection(self) -> None:
        """Same value-rule path but on the sensor allowlist."""
        v = validate(
            {"sensors": [{"id": "cam1", "configs": [_kv("tripwireMinPoints", "0")]}]}
        )
        self.assertEqual(v.status, "failure")
        self.assertIn("tripwireMinPoints", v.error)
        self.assertIn(">= 1", v.error)


class TestConfigValidatorMixedItems(unittest.TestCase):
    """Cross-section interactions."""

    def test_app_good_sensors_bad_partial_success(self) -> None:
        v = validate(
            {
                "app": [_kv(APP_KEY_A, "1")],
                "sensors": [{"id": "cam1", "configs": [{"name": "", "value": "x"}]}],
            }
        )
        # cam1 dropped (bad sub-item), but app[0] is valid -> partial-success.
        self.assertEqual(v.status, "partial-success")
        self.assertEqual(v.applied_app, [_kv(APP_KEY_A, "1")])
        self.assertEqual(v.applied_sensors, [])

    def test_all_rejected_is_failure(self) -> None:
        v = validate(
            {
                "app": [{"name": "", "value": "x"}],
                "sensors": [{"id": "", "configs": []}],
            }
        )
        self.assertEqual(v.status, "failure")
        self.assertIn("app[0]", v.error)
        self.assertIn("sensors[0].id", v.error)


class TestValidationResultDefaults(unittest.TestCase):
    """The dataclass defaults for the all-failure path."""

    def test_default_failure_has_empty_lists(self) -> None:
        v = ValidationResult(status="failure")
        self.assertEqual(v.applied_app, [])
        self.assertEqual(v.applied_sensors, [])
        self.assertIsNone(v.error)


class TestValidateEnvelope(unittest.TestCase):
    """``validate_envelope`` is the pydantic shape gate.

    Constructs :class:`ConfigMessage` from a deserialized wire dict and
    catches :class:`pydantic.ValidationError` so the listener can emit a
    structured ``failure`` ack instead of silently dropping the message.
    """

    def _envelope(self, **overrides) -> dict:
        env = {
            "event_type": "upsert",
            "reference_id": "video-analytics-api-abc",
            "timestamp": datetime.now(timezone.utc),
            "config": {"app": []},
            "status": None,
            "error": None,
        }
        env.update(overrides)
        return env

    def test_well_formed_envelope_constructs_config_message(self) -> None:
        msg, err = validate_envelope(self._envelope())
        self.assertIsNotNone(msg)
        self.assertIsNone(err)
        self.assertEqual(msg.event_type, "upsert")
        self.assertEqual(msg.reference_id, "video-analytics-api-abc")
        self.assertEqual(msg.config, {"app": []})

    def test_non_string_status_returns_shape_error(self) -> None:
        """``ConfigMessage.status`` is typed ``str | None``; ``status: 42``
        trips pydantic at construction. ``validate_envelope`` catches the
        error so the listener can ack ``failure`` instead of silently
        dropping the message."""
        msg, err = validate_envelope(self._envelope(status=42))
        self.assertIsNone(msg)
        self.assertIsNotNone(err)
        self.assertIn("invalid envelope shape", err)

    def test_non_dict_config_is_accepted(self) -> None:
        """``config: Any`` -- non-dict values flow through pydantic; the
        per-payload :func:`validate` is what tags them as ``failure``."""
        msg, err = validate_envelope(self._envelope(config=[]))
        self.assertIsNotNone(msg)
        self.assertIsNone(err)
        self.assertEqual(msg.config, [])

    def test_missing_required_field_returns_shape_error(self) -> None:
        env = self._envelope()
        env.pop("event_type")
        msg, err = validate_envelope(env)
        self.assertIsNone(msg)
        self.assertIn("invalid envelope shape", err)

    def test_unrecognized_envelope_key_returns_failure(self) -> None:
        """A producer-side typo / contract drift on the envelope: top-level
        key the validator doesn't know about. Surface it explicitly so the
        ack lists the offending name(s)."""
        env = self._envelope()
        env["bogus"] = "x"
        msg, err = validate_envelope(env)
        self.assertIsNone(msg)
        self.assertIn("unrecognized envelope keys", err)
        self.assertIn("bogus", err)

    def test_upsert_with_null_config_returns_failure(self) -> None:
        """An upsert with ``config=null`` is a producer error (not a
        no-op): reject with a clear "no config to update" string."""
        msg, err = validate_envelope(self._envelope(event_type="upsert", config=None))
        self.assertIsNone(msg)
        self.assertEqual(err, "no config to update")

    def test_upsert_with_missing_config_returns_failure(self) -> None:
        """Same path as null -- absence and explicit-null collapse to the
        same operator-visible failure string."""
        env = self._envelope(event_type="upsert")
        env.pop("config")
        msg, err = validate_envelope(env)
        self.assertIsNone(msg)
        self.assertEqual(err, "no config to update")

    def test_upsert_all_with_null_config_is_accepted(self) -> None:
        """The bootstrap-failure signal from web-api: ``upsert-all`` with
        ``config=null`` is legitimate (handled by ``_handle_upsert_all``)
        -- the validator must let it through."""
        msg, err = validate_envelope(self._envelope(event_type="upsert-all", config=None))
        self.assertIsNotNone(msg)
        self.assertIsNone(err)
        self.assertIsNone(msg.config)

    def test_missing_status_is_accepted(self) -> None:
        """``ConfigMessage.status`` defaults to ``None``, so a body that
        omits ``status`` is not a producer error -- the field is just
        absent and pydantic supplies the default."""
        env = self._envelope()
        env.pop("status")
        msg, err = validate_envelope(env)
        self.assertIsNotNone(msg)
        self.assertIsNone(err)
        self.assertIsNone(msg.status)

    def test_missing_error_is_accepted(self) -> None:
        """Same as ``status`` -- ``error`` defaults to ``None``."""
        env = self._envelope()
        env.pop("error")
        msg, err = validate_envelope(env)
        self.assertIsNotNone(msg)
        self.assertIsNone(err)
        self.assertIsNone(msg.error)

    def test_error_as_object_returns_shape_error(self) -> None:
        """``error`` is typed ``str | None`` -- a dict (e.g.
        ``{"message": "bad"}``) is a producer-side typing mistake and the
        envelope gate rejects it as ``invalid envelope shape``."""
        msg, err = validate_envelope(self._envelope(error={"message": "bad"}))
        self.assertIsNone(msg)
        self.assertIn("invalid envelope shape", err)


if __name__ == "__main__":
    unittest.main()
