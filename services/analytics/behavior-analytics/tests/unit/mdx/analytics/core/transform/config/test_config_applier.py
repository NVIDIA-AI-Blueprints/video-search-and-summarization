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

"""
Unit tests for :class:`ConfigApplier` -- the dumb mutator.

Validation is exercised in ``test_config_validator.py``; here we only verify
that already-validated items make it through the setters and that caches are
invalidated.
"""

import unittest

from mdx.analytics.core.schema.config import AppConfig, AppSensorConfig, KeyValuePair
from mdx.analytics.core.transform.config.config_applier import ApplyResult, ConfigApplier


def _kv(name: str, value: str) -> dict[str, str]:
    """Shorthand for the on-the-wire key-value dict."""
    return {"name": name, "value": value}


class TestConfigApplierApply(unittest.TestCase):
    """:meth:`ConfigApplier.apply` merges already-validated items in place."""

    def setUp(self) -> None:
        self.config = AppConfig()
        self.applier = ConfigApplier(self.config)

    def test_apply_app_items(self) -> None:
        self.applier.apply([_kv("foo", "1"), _kv("bar", "2")], [])
        self.assertEqual(self.config.get_app_config("foo"), "1")
        self.assertEqual(self.config.get_app_config("bar"), "2")

    def test_apply_sensor_items(self) -> None:
        self.applier.apply([], [{"id": "cam1", "configs": [_kv("k", "v")]}])
        self.assertEqual(self.config.get_sensor_config(key="k", sensor_id="cam1"), "v")

    def test_apply_both_app_and_sensors(self) -> None:
        self.applier.apply(
            [_kv("foo", "1")],
            [{"id": "cam1", "configs": [_kv("k", "v")]}],
        )
        self.assertEqual(self.config.get_app_config("foo"), "1")
        self.assertEqual(self.config.get_sensor_config(key="k", sensor_id="cam1"), "v")

    def test_apply_overwrites_existing_value(self) -> None:
        self.config.app.append(KeyValuePair(name="foo", value="old"))
        self.applier.apply([_kv("foo", "new")], [])
        self.assertEqual(self.config.get_app_config("foo"), "new")

    def test_apply_is_additive_for_sensors(self) -> None:
        """A sensor pre-existing in config is preserved when not in the patch."""
        self.config.sensors.append(
            AppSensorConfig(id="legacy", configs=[KeyValuePair(name="k", value="v")])
        )
        self.applier.apply([], [{"id": "new", "configs": [_kv("kk", "vv")]}])
        # Old sensor still present.
        self.assertEqual(self.config.get_sensor_config(key="k", sensor_id="legacy"), "v")
        # New sensor added.
        self.assertEqual(self.config.get_sensor_config(key="kk", sensor_id="new"), "vv")

    def test_apply_empty_lists_is_noop(self) -> None:
        """Empty inputs -- no setters called, no exception."""
        self.applier.apply([], [])
        # Nothing to assert beyond "did not raise"; cache invalidation still runs.

    def test_apply_invalidates_cached_property(self) -> None:
        """Reading a cached_property before apply, then applying, flips its value."""
        self.assertFalse(self.config.in_3d_mode)  # primes cache
        self.applier.apply([_kv("in3dMode", "true")], [])
        self.assertTrue(self.config.in_3d_mode)


class TestApplyResult(unittest.TestCase):
    """Sanity checks on the wire-payload dataclass."""

    def test_default_failure(self) -> None:
        """Default config and error are None for a bare 'failure' instance."""
        result = ApplyResult(status="failure")
        self.assertIsNone(result.config)
        self.assertIsNone(result.error)

    def test_full_construction(self) -> None:
        result = ApplyResult(
            status="partial-success",
            config={"app": [_kv("a", "1")], "sensors": []},
            error="rejected: foo",
        )
        self.assertEqual(result.status, "partial-success")
        self.assertEqual(result.config["app"], [_kv("a", "1")])
        self.assertEqual(result.error, "rejected: foo")


if __name__ == "__main__":
    unittest.main()
