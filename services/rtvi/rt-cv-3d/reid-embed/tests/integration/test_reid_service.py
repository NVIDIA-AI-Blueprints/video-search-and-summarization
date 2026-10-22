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

"""Checks against one reid-embed image.

The fixture starts that image with Milvus and Kafka. Embeddings go in on the
mv3dt topic as a protobuf Frame, the service default. The assertions use only
the public HTTP API.
"""

import time

import pytest
import requests

pytestmark = [pytest.mark.integration, pytest.mark.timeout(600)]

# Must match DIMENSION in compose.reid-test.yml.
DIMENSION = 1280
TOPIC = "mv3dt"
OBJECT_ID = "1001"
_RESULT_FIELDS = {
    "object_id",
    "confidence",
    "exp_confidence",
    "distance",
    "group_count",
    "distance_to_centroid",
    "cluster_variance",
    "cluster_count",
}


def _query_vector():
    vector = [0.0] * DIMENSION
    vector[0] = 1.0
    return vector


def _produce_embedding(bootstrap, object_id, vector):
    from kafka import KafkaProducer

    import proto_utils.ds_schema_pb2 as schema_pb2

    frame = schema_pb2.Frame()
    frame.id = "1"
    frame.sensorId = "cam-1"
    obj = frame.objects.add()
    obj.id = object_id
    obj.embedding.vector.extend(vector)

    producer = KafkaProducer(bootstrap_servers=bootstrap)
    producer.send(TOPIC, frame.SerializeToString())
    producer.flush(10)
    producer.close()


def test_liveness(reid_stack):
    response = requests.get(f"{reid_stack.service_url}/health/live", timeout=10)

    assert response.status_code == 200
    assert response.json() == {"status": "alive"}


def test_readiness(reid_stack):
    response = requests.get(f"{reid_stack.service_url}/health/ready", timeout=10)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["checks"]


def test_search_finds_the_kafka_embedding(reid_stack):
    vector = _query_vector()
    _produce_embedding(reid_stack.kafka_bootstrap, OBJECT_ID, vector)

    deadline = time.monotonic() + 90
    matches = []
    while time.monotonic() < deadline:
        response = requests.post(
            f"{reid_stack.service_url}/search",
            json={"vector": vector, "top_k": 5, "max_group_count": 8},
            timeout=30,
        )
        assert response.status_code == 200
        matches = response.json()
        if any(match["object_id"] == OBJECT_ID for match in matches):
            break
        time.sleep(2)
    else:
        pytest.fail(f"{OBJECT_ID} was not returned by /search; last body={matches}")

    match = next(item for item in matches if item["object_id"] == OBJECT_ID)
    assert _RESULT_FIELDS <= set(match)
    assert match["distance"] >= 0.0
    assert 0.0 < match["confidence"] <= 1.0


def test_search_rejects_wrong_vector_dimension(reid_stack):
    response = requests.post(
        f"{reid_stack.service_url}/search",
        json={"vector": [0.0] * (DIMENSION - 1)},
        timeout=10,
    )

    assert response.status_code == 422
