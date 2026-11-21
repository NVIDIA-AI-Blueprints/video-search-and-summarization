# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Batch transport must preserve per-object evidence and isolate item errors."""

from datetime import UTC
from datetime import datetime

import pytest

from vss_core.search_core.primitives import _attribute_helpers as h


@pytest.mark.asyncio
@pytest.mark.parametrize("second_failed", [False, True])
async def test_nonempty_frames_keep_sensor_order_duplicates_and_item_failures(
    second_failed,
):
    def hit(sensor, frame, score):
        return {
            "hits": {
                "hits": [
                    {
                        "_score": score,
                        "_source": {
                            "id": frame,
                            "sensorId": sensor,
                            "timestamp": f"2025-01-01T00:00:0{frame // 10}Z",
                            "objects": [
                                {
                                    "id": "0",
                                    "bbox": {
                                        "leftX": frame,
                                        "rightX": frame + 1,
                                        "topY": 2,
                                        "bottomY": 3,
                                    },
                                }
                            ],
                        },
                    }
                ]
            }
        }

    class ES:
        calls = 0

        async def msearch(self, **kwargs):
            self.calls += 1
            assert len(kwargs["searches"]) == 4
            assert kwargs["max_concurrent_searches"] == 16
            return {
                "responses": [
                    hit("a", 10, 0.8),
                    {"error": {"reason": "fixture"}} if second_failed else hit("b", 20, 0.2),
                ]
            }

    def candidate(sensor):
        return {"_source": {"sensor": {"id": sensor}, "object": {"id": "0"}}}

    es = ES()
    rows = [candidate("a"), candidate("b"), candidate("a"), {"_source": {}}]
    out = await h._perform_frame_lookups(
        rows,
        [1.0, 0.0],
        "frames",
        datetime(2025, 1, 1, tzinfo=UTC),
        datetime(2025, 1, 2, tzinfo=UTC),
        es,
    )
    assert es.calls == 1
    assert len(out) == 4
    assert out[0] == out[2]
    assert out[0] == (
        10,
        {"leftX": 10, "rightX": 11, "topY": 2, "bottomY": 3},
        0.8,
        "2025-01-01T00:00:01Z",
    )
    assert out[3] is None
    if second_failed:
        assert out[1] is None
    else:
        assert out[1][0] == 20 and out[1][2] == 0.2
