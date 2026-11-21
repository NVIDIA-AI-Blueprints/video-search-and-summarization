# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Finite fast JSON must retain native special-value and error behavior."""

import datetime
import json
from unittest.mock import AsyncMock
import uuid

from elasticsearch.serializer import NdjsonSerializer
import pytest

from vss_core.search_core.clients._frame_json import encode_frame_searches
from vss_core.search_core.clients.elastic import ElasticClient


@pytest.mark.parametrize("value", [float("nan"), float("inf"), 2**90, datetime.datetime(2025, 1, 1), uuid.UUID(int=0)])
def test_special_values_use_native_serialization(value):
    lines = [{"value": value}]
    assert encode_frame_searches(lines) == NdjsonSerializer().dumps(lines)


def test_shared_vectors_and_precision():
    vector = [0.12345678901234567, 1e-20, -0.0, 1.0]
    lines = [{}, {"params": {"vector": vector}}, {}, {"params": {"vector": vector}}]
    assert [json.loads(x) for x in encode_frame_searches(lines).splitlines()] == lines
    assert encode_frame_searches(lines).endswith(b"\n")


def test_numpy_uses_native_precision():
    np = pytest.importorskip("numpy")
    lines = [{"v": np.asarray([0.123456789], dtype=np.float32)}]
    assert encode_frame_searches(lines) == NdjsonSerializer().dumps(lines)


def test_cycles_preserve_native_error_class():
    value = []
    value.append(value)
    with pytest.raises(Exception) as expected:
        NdjsonSerializer().dumps([{"cycle": value}])
    with pytest.raises(type(expected.value)):
        encode_frame_searches([{"cycle": value}])


@pytest.mark.asyncio
async def test_only_msearch_receives_encoded_body():
    raw = AsyncMock()
    raw.msearch.return_value = {"responses": []}
    es = ElasticClient(endpoint="http://fixture", client=raw)
    await es.msearch(index="frames", searches=[{}, {"size": 1}], max_concurrent_searches=4)
    kwargs = raw.msearch.call_args.kwargs
    assert kwargs["searches"] == b'{}\n{"size":1}\n'
    assert kwargs["max_concurrent_searches"] == 4
