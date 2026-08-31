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

"""Envelope-key / sensorId alignment classifier, shared by every source."""

import json

import pytest

from mdx.source.source_utils import classify_key_alignment


class TestKeyAlignmentClassifier:
    def test_incident_top_level_sensorid_aligned(self):
        v = json.dumps({"sensorId": "cam-1"}).encode()
        assert classify_key_alignment(b"cam-1", v) == "yes"

    def test_anomaly_nested_sensor_id_aligned(self):
        # Alert/anomaly payloads carry the id under sensor.id.
        v = json.dumps({"sensor": {"id": "cam-7"}}).encode()
        assert classify_key_alignment(b"cam-7", v) == "yes"

    def test_mismatched_key_is_no(self):
        v = json.dumps({"sensorId": "cam-1"}).encode()
        assert classify_key_alignment(b"cam-2", v) == "no"

    def test_missing_key_is_unknown(self):
        v = json.dumps({"sensorId": "cam-1"}).encode()
        assert classify_key_alignment(None, v) == "unknown"

    def test_non_json_protobuf_is_unknown_not_error(self):
        # Protobuf bytes are not JSON → classified unknown, never raises.
        assert classify_key_alignment(b"cam-1", b"\x08\x96\x01protobuf") == "unknown"

    def test_string_key_and_str_value(self):
        assert classify_key_alignment("cam-3", json.dumps({"sensorId": "cam-3"})) == "yes"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
