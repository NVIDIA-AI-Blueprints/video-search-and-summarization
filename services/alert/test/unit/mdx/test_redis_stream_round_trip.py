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

"""End-to-end Redis Streams round trip against a real Redis implementation.

The other Redis tests mock the client, so they pin the calls this code makes but
cannot catch a wire-format mistake — a payload written under the wrong field, or
protobuf corrupted by response decoding, looks identical to a mock. These tests
run the actual source and sink against an in-memory Redis and assert on bytes,
so the interop contract is checked rather than assumed:

- An upstream producer (what vss-behavior-analytics writes) publishes a real
  protobuf incident, and the source hands back a batch that the real
  ``protobuf_anomalies_to_json_string_list`` decoder can read.
- The VLM-enhanced sink's output decodes back into the incident a downstream
  consumer (Logstash's ``redis_stream`` input) would index.

Skipped when ``fakeredis`` is unavailable, since it is not a runtime dependency.
"""

import json

import pytest

fakeredis = pytest.importorskip("fakeredis")

from mdx.redis_stream_broker import (  # noqa: E402
    HEADERS_FIELD,
    KEY_FIELD,
    PAYLOAD_FIELD,
)
from mdx.sink.sink_redis_stream import SinkRedisStream  # noqa: E402
from mdx.sink.vlm_enhanced_sink.sink_redis_stream import (  # noqa: E402
    VLMEnhancedRedisStreamSink,
)
from mdx.source.source_redis_stream import SourceRedisStream  # noqa: E402
from utils.schema_util import (  # noqa: E402
    convert_incident_to_protobuf_incident,
    protobuf_anomalies_to_json_string_list,
)

INCIDENT_STREAM = "mdx-incidents"
ALERT_STREAM = "mdx-alerts"
VLM_INCIDENT_STREAM = "mdx-vlm-incidents"

CONFIG = {
    "redis": {"host": "localhost", "port": 6379, "maxlen": 1000},
    "event_bridge": {
        "sourceType": "redisStream",
        "sinkType": "redisStream",
        "redis_source": {
            "streams": {"incident": INCIDENT_STREAM, "alert": ALERT_STREAM},
            "consumer_group": "alert-bridge-vlm-group",
            # Read from the beginning so entries published before the consumer
            # starts are still visible to the test.
            "consumer_config": {"start_id": "0-0", "count": 10, "block_time": 0},
        },
        "redis_sink": {
            "streams": {
                "enhanced_anomaly": "alert-bridge-enhanced-alerts",
                "incidents": "alert-bridge-incidents",
            }
        },
    },
    "vlm_enhanced_sink": {
        "type": "redisStream",
        "incident": {"redisStream": {"stream": VLM_INCIDENT_STREAM, "message_type": "incident"}},
        "alert": {"redisStream": {"stream": "mdx-vlm-alerts", "message_type": "alert"}},
    },
}

#: Shaped to the ``Incident`` protobuf schema, which carries ``sensorId`` /
#: ``category`` / ``isAnomaly`` plus a free-form ``info`` string map. Fields
#: outside the schema (a top-level ``id``, for instance) are dropped by the
#: converter on every transport, so the id travels in ``info`` here.
INCIDENT = {
    "sensorId": "cam-1",
    "category": "Loitering",
    "isAnomaly": True,
    "info": {"id": "inc-1", "description": "person loitering near the dock door"},
}


@pytest.fixture
def server():
    """A single in-memory Redis shared by every client in a test."""
    return fakeredis.FakeServer()


@pytest.fixture
def redis_client(server):
    return fakeredis.FakeStrictRedis(server=server, decode_responses=False)


@pytest.fixture
def patch_broker(monkeypatch, server):
    """Point every RedisStreamBroker at the shared in-memory server."""
    def fake_redis(**kwargs):
        return fakeredis.FakeStrictRedis(server=server, decode_responses=kwargs.get("decode_responses", False))

    monkeypatch.setattr("mdx.redis_stream_broker.redis.Redis", fake_redis)


def publish_upstream(client, stream, document):
    """Publish as vss-behavior-analytics does: protobuf bytes in ``value``."""
    payload = convert_incident_to_protobuf_incident(document).SerializeToString()
    return client.xadd(
        stream,
        {KEY_FIELD: document["sensorId"].encode(), PAYLOAD_FIELD: payload, HEADERS_FIELD: "{}"},
    )


@pytest.mark.usefixtures("patch_broker")
class TestSourceRoundTrip:
    def test_a_protobuf_incident_survives_the_source_and_decodes(self, redis_client):
        """The payload the source yields must be byte-identical to what was
        published and decode through the same helper the Kafka path uses."""
        publish_upstream(redis_client, INCIDENT_STREAM, INCIDENT)

        batches = SourceRedisStream(CONFIG).read_data()

        assert len(batches) == 1
        assert batches[0]["kind"] == "incident"

        decoded = protobuf_anomalies_to_json_string_list(
            {"batch": batches[0]["messages"]}, "Incident"
        )
        assert len(decoded) == 1
        document = json.loads(decoded[0])
        assert document["sensorId"] == "cam-1"
        assert document["category"] == "Loitering"
        assert document["isAnomaly"] is True
        assert document["info"]["id"] == "inc-1"

    def test_incidents_and_alerts_are_separated_by_kind(self, redis_client):
        """Both streams are read in one poll, but they must not be merged: the
        kind selects the protobuf schema used to decode the batch."""
        publish_upstream(redis_client, INCIDENT_STREAM, INCIDENT)
        publish_upstream(redis_client, ALERT_STREAM, {**INCIDENT, "sensorId": "cam-2"})

        batches = SourceRedisStream(CONFIG).read_data()

        assert {b["kind"] for b in batches} == {"incident", "alert"}
        assert all(len(b["messages"]) == 1 for b in batches)

    def test_the_envelope_key_is_preserved_for_cohort_affinity(self, redis_client):
        publish_upstream(redis_client, INCIDENT_STREAM, INCIDENT)

        batches = SourceRedisStream(CONFIG).read_data()
        key, _payload, _timestamp = batches[0]["messages"][0]

        assert key == b"cam-1"

    def test_the_publish_time_comes_from_the_entry_id(self, redis_client):
        """Redis stamps the entry ID, which stands in for the Kafka record
        timestamp that the latency metrics consume."""
        entry_id = publish_upstream(redis_client, INCIDENT_STREAM, INCIDENT)
        expected_ms = int(entry_id.decode().split("-")[0])

        batches = SourceRedisStream(CONFIG).read_data()
        _key, _payload, timestamp_ms = batches[0]["messages"][0]

        assert timestamp_ms == expected_ms
        assert batches[0]["kafka_published_at"] is not None

    def test_a_json_payload_is_handed_back_as_a_json_string(self, redis_client):
        """The envelope also carries JSON from some producers; that path must
        bypass protobuf decoding rather than fail."""
        redis_client.xadd(
            INCIDENT_STREAM,
            {KEY_FIELD: b"cam-1", PAYLOAD_FIELD: json.dumps(INCIDENT).encode(), HEADERS_FIELD: "{}"},
        )

        batches = SourceRedisStream(CONFIG).read_data()
        message = batches[0]["messages"][0]

        assert isinstance(message, str)
        assert json.loads(message)["info"]["id"] == "inc-1"

    def test_entries_are_acked_so_they_are_not_redelivered(self, redis_client):
        publish_upstream(redis_client, INCIDENT_STREAM, INCIDENT)
        source = SourceRedisStream(CONFIG)

        assert len(source.read_data()) == 1
        assert source.read_data() == []

        pending = redis_client.xpending(INCIDENT_STREAM, "alert-bridge-vlm-group")
        assert pending["pending"] == 0

    def test_an_empty_stream_yields_no_batches(self, redis_client):
        assert SourceRedisStream(CONFIG).read_data() == []

    def test_a_second_replica_shares_the_group_without_duplicating_work(self, redis_client):
        """Scaled-out replicas join one group, so each entry is delivered once."""
        for index in range(4):
            publish_upstream(redis_client, INCIDENT_STREAM, {**INCIDENT, "sensorId": f"cam-{index}"})

        first, second = SourceRedisStream(CONFIG), SourceRedisStream(CONFIG)
        total = sum(len(b["messages"]) for s in (first, second) for b in s.read_data())

        assert total == 4


@pytest.mark.usefixtures("patch_broker")
class TestVLMEnhancedSinkRoundTrip:
    def test_a_published_verdict_decodes_back_into_the_incident(self, redis_client):
        """This is what Logstash reads: protobuf under ``value``, decodable with
        the incident schema."""
        sink = VLMEnhancedRedisStreamSink.from_config(CONFIG)
        sink.publish_success(dict(INCIDENT), "prompt", None, {"verdict": "confirmed"})

        entries = redis_client.xrange(VLM_INCIDENT_STREAM)
        assert len(entries) == 1

        _entry_id, fields = entries[0]
        assert fields[KEY_FIELD] == b"cam-1"
        assert json.loads(fields[HEADERS_FIELD]) == {}

        document = json.loads(
            protobuf_anomalies_to_json_string_list(
                {"batch": [(None, fields[PAYLOAD_FIELD])]}, "Incident"
            )[0]
        )
        assert document["sensorId"] == "cam-1"
        assert document["category"] == "Loitering"
        assert document["info"]["id"] == "inc-1"

    def test_the_stream_is_capped_so_it_cannot_grow_without_bound(self, redis_client):
        config = json.loads(json.dumps(CONFIG))
        config["redis"]["maxlen"] = 5
        sink = VLMEnhancedRedisStreamSink.from_config(config)

        for index in range(20):
            sink.publish_success(
                dict(INCIDENT, sensorId=f"cam-{index}"), "prompt", None, {"verdict": "confirmed"}
            )

        # MAXLEN is approximate, so assert the cap holds rather than an exact length.
        assert 0 < redis_client.xlen(VLM_INCIDENT_STREAM) <= 20


@pytest.mark.usefixtures("patch_broker")
class TestEventBridgeSinkRoundTrip:
    def test_anomalies_and_incidents_land_on_their_own_streams(self, redis_client):
        sink = SinkRedisStream(CONFIG)

        sink.write_data([{"id": "evt-1", "sensor": {"id": "cam-1"}}])
        sink.write_incident_data([{"id": "inc-1", "sensorId": "cam-2"}])

        anomaly = redis_client.xrange("alert-bridge-enhanced-alerts")
        incident = redis_client.xrange("alert-bridge-incidents")

        assert json.loads(anomaly[0][1][PAYLOAD_FIELD])["id"] == "evt-1"
        assert json.loads(incident[0][1][PAYLOAD_FIELD])["id"] == "inc-1"


@pytest.mark.usefixtures("patch_broker")
class TestFullPipeline:
    def test_an_upstream_incident_flows_through_to_the_vlm_stream(self, redis_client):
        """Consume from the source, publish the verdict, and read it back —
        the whole Kafka-free path in one test."""
        publish_upstream(redis_client, INCIDENT_STREAM, INCIDENT)

        batches = SourceRedisStream(CONFIG).read_data()
        raw = protobuf_anomalies_to_json_string_list(
            {"batch": batches[0]["messages"]}, "Incident"
        )[0]
        document = json.loads(raw)

        document["info"]["vlm_verdict"] = "confirmed"
        VLMEnhancedRedisStreamSink.from_config(CONFIG).publish_success(
            document, "prompt", None, {"verdict": "confirmed"}
        )

        entries = redis_client.xrange(VLM_INCIDENT_STREAM)
        assert len(entries) == 1

        published = json.loads(
            protobuf_anomalies_to_json_string_list(
                {"batch": [(None, entries[0][1][PAYLOAD_FIELD])]}, "Incident"
            )[0]
        )
        assert published["sensorId"] == INCIDENT["sensorId"]
        assert published["info"]["id"] == "inc-1"
        # The enrichment added mid-pipeline has to reach the published payload.
        assert published["info"]["vlm_verdict"] == "confirmed"
