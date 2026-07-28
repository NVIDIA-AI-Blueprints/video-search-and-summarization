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

import asyncio

from clients.elastic import ElasticClient


class _FakeSyncES:
    def __init__(self):
        self.indexed = []

    def index(self, **kwargs):
        self.indexed.append(kwargs)
        return {"result": "created"}


class _FakeAsyncES:
    def __init__(self):
        self.indexed = []

    async def index(self, **kwargs):
        self.indexed.append(kwargs)
        return {"result": "created"}


def _make_client(fake_sync, fake_async):
    client = ElasticClient.__new__(ElasticClient)
    client.client = fake_sync
    client._async_client = fake_async
    client._client_kwargs = {}
    client._index_cache = {"pre-cached"}
    return client


def _make_message():
    return {
        "id": "evt-9",
        "sensorId": "sensor-9",
        "category": "intrusion",
        "timestamp": "2026-01-01T00:00:00.000Z",
        "end": "2026-01-01T00:00:10.000Z",
        "info": {"verdict": "confirmed", "verificationResponseCode": 200},
    }


class TestWriteEventResponseParity:
    def test_sync_and_async_paths_produce_identical_documents(self):
        fake_sync = _FakeSyncES()
        fake_async = _FakeAsyncES()
        client = _make_client(fake_sync, fake_async)
        # Pre-cache the daily index so neither path touches indices APIs
        daily_index, _, _ = client._prepare_event_document(_make_message(), "mdx-vlm-incidents")
        client._index_cache.add(daily_index)

        client.write_event_response(
            _make_message(), {"verdict": "confirmed"}, "prompt", "mdx-vlm-incidents",
        )
        asyncio.run(client.write_event_response_async(
            _make_message(), {"verdict": "confirmed"}, "prompt", "mdx-vlm-incidents",
        ))

        assert len(fake_sync.indexed) == 1
        assert len(fake_async.indexed) == 1
        assert fake_sync.indexed[0] == fake_async.indexed[0]
        assert fake_sync.indexed[0]["index"] == daily_index
        assert fake_sync.indexed[0]["id"], "fingerprint doc_id must be set"

    def test_category_mapping_applied_identically(self):
        fake_sync = _FakeSyncES()
        fake_async = _FakeAsyncES()
        client = _make_client(fake_sync, fake_async)
        mapping = {"intrusion": "Perimeter Breach"}
        daily_index, _, _ = client._prepare_event_document(
            _make_message(), "mdx-vlm-incidents", category_mapping=mapping,
        )
        client._index_cache.add(daily_index)

        client.write_event_response(
            _make_message(), {}, "p", "mdx-vlm-incidents", category_mapping=mapping,
        )
        asyncio.run(client.write_event_response_async(
            _make_message(), {}, "p", "mdx-vlm-incidents", category_mapping=mapping,
        ))

        sync_doc = fake_sync.indexed[0]["document"]
        async_doc = fake_async.indexed[0]["document"]
        assert sync_doc == async_doc
        assert sync_doc["category"] == "Perimeter Breach"
        # Fingerprint must be computed from the ORIGINAL category in both paths
        assert fake_sync.indexed[0]["id"] == fake_async.indexed[0]["id"]
