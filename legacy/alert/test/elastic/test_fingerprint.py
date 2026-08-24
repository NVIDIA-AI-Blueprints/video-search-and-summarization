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

"""Unit tests for Elasticsearch fingerprint generation aligned with Logstash."""

import pytest

from mdx.anomaly.utils.elastic_ready import generate_incident_fingerprint


class TestIncidentFingerprint:
    """Tests for generate_incident_fingerprint alignment with Logstash."""

    def test_fingerprint_uses_primary_object_id(self):
        """Test that fingerprint uses info.primaryObjectId per Logstash logic."""
        event_with_primary = {
            "timestamp": "2025-04-13T10:00:00.000Z",
            "category": "collision",
            "sensorId": "HWY_20",
            "info": {"primaryObjectId": "obj_123"}
        }
        event_without_primary = {
            "timestamp": "2025-04-13T10:00:00.000Z",
            "category": "collision",
            "sensorId": "HWY_20",
        }

        fp_with = generate_incident_fingerprint(event_with_primary)
        fp_without = generate_incident_fingerprint(event_without_primary)

        assert fp_with is not None
        assert fp_without is not None
        assert fp_with != fp_without

    def test_fingerprint_is_deterministic(self):
        """Test that fingerprint generation is deterministic."""
        event = {
            "timestamp": "2025-04-13T10:00:00.000Z",
            "category": "collision",
            "sensorId": "HWY_20",
            "info": {"primaryObjectId": "obj_123"}
        }

        fp1 = generate_incident_fingerprint(event)
        fp2 = generate_incident_fingerprint(event)

        assert fp1 == fp2

    def test_different_primary_ids_produce_different_fingerprints(self):
        """Test that different primaryObjectId values produce different fingerprints."""
        base = {
            "timestamp": "2025-04-13T10:00:00.000Z",
            "category": "collision",
            "sensorId": "HWY_20",
        }

        event1 = {**base, "info": {"primaryObjectId": "obj_111"}}
        event2 = {**base, "info": {"primaryObjectId": "obj_222"}}

        fp1 = generate_incident_fingerprint(event1)
        fp2 = generate_incident_fingerprint(event2)

        assert fp1 != fp2
