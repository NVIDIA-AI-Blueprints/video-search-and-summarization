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

"""The protobuf-decode to normalization boundary for alert payloads.

``POST /alerts`` with ``Content-Type: application/x-protobuf`` republishes the
client's bytes to the alert topic verbatim, so whatever the client left unset
reaches the consumer unset. The consumer decodes with
``MessageToJson(always_print_fields_with_no_presence=True)``, and that flag
prints scalars, maps and repeated fields at their defaults but *omits*
presence-tracked submessages entirely. A sparse ``Behavior`` therefore decodes
to a dict that is simply missing keys, and ``normalize_alert_message`` runs on
it before any validation guard.

The unit tests in ``test_event_utils`` cover normalization against handwritten
dicts. These cover it against dicts that a real protobuf round-trip produces,
which is what the endpoint actually delivers. ``SUBMESSAGE_FIELDS`` is derived
from the descriptor rather than hardcoded so a submessage added to the proto
later is covered here automatically instead of reintroducing the defect.
"""

import json

import pytest

from mdx.protobuf import Behavior as nvSchemaBehavior
from utils.event_utils import normalize_alert_message
from utils.schema_util import protobuf_anomaly_to_json_string

# Singular submessage fields — the ones that vanish from the decoded JSON when
# the producer leaves them unset. Repeated and map fields carry no presence, so
# the decoder always emits them and they cannot go missing.
SUBMESSAGE_FIELDS = [
    field.name
    for field in nvSchemaBehavior.DESCRIPTOR.fields
    if field.message_type is not None and field.has_presence
]


def decode(behavior: nvSchemaBehavior) -> dict:
    """Round-trip through the wire format exactly as the consumer does."""
    return json.loads(
        protobuf_anomaly_to_json_string(behavior.SerializeToString(), "Behavior")
    )


class TestDecodedPayloadShape:
    """What the decoder actually hands to normalization."""

    def test_unset_submessages_are_absent_from_the_decoded_payload(self):
        decoded = decode(nvSchemaBehavior())

        assert not set(SUBMESSAGE_FIELDS) & set(decoded)

    def test_unset_scalars_survive_at_their_defaults(self):
        """Contrast with the above: scalars are printed, so they never vanish."""
        decoded = decode(nvSchemaBehavior())

        assert decoded["id"] == ""
        assert decoded["speed"] == 0.0
        assert decoded["direction"] == ""


class TestNormalisingDecodedPayloads:
    @pytest.mark.parametrize("field", SUBMESSAGE_FIELDS)
    def test_a_payload_carrying_only_one_submessage_normalises(self, field):
        """Every single-block payload must survive, not just the common ones."""
        behavior = nvSchemaBehavior()
        getattr(behavior, field).SetInParent()

        normalize_alert_message(decode(behavior))

    def test_analytics_module_without_sensor_normalises(self):
        """The reported shape: an alert whose producer never set ``sensor``."""
        behavior = nvSchemaBehavior()
        behavior.id = "evt-1"
        behavior.analyticsModule.id = "Intrusion"

        result = normalize_alert_message(decode(behavior))

        assert "sensor" not in result
        assert result["category"] == "Intrusion"
        assert result["notification_type"] == "alert"

    def test_sensor_without_analytics_module_normalises(self):
        behavior = nvSchemaBehavior()
        behavior.sensor.id = "cam-1"

        result = normalize_alert_message(decode(behavior))

        assert result["sensorId"] == "cam-1"
        assert "category" not in result

    def test_object_only_payload_yields_object_ids(self):
        behavior = nvSchemaBehavior()
        behavior.object.id = "obj-1"

        result = normalize_alert_message(decode(behavior))

        assert result["objectIds"] == ["obj-1"]

    def test_a_fully_populated_payload_is_normalised_as_before(self):
        behavior = nvSchemaBehavior()
        behavior.sensor.id = "cam-1"
        behavior.analyticsModule.id = "Intrusion"
        behavior.object.id = "obj-1"

        result = normalize_alert_message(decode(behavior))

        assert result["sensorId"] == "cam-1"
        assert result["category"] == "Intrusion"
        assert result["objectIds"] == ["obj-1"]
        assert set(result["_normalized_added_fields"]) == {
            "sensorId",
            "category",
            "objectIds",
        }
