# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""A failed frame backend is distinct from an absent/malformed frame document."""

from datetime import UTC
from datetime import datetime

import pytest

from vss_core.search_core.errors import BackendUnreachableError
from vss_core.search_core.primitives import _attribute_helpers as helpers


@pytest.mark.asyncio
@pytest.mark.parametrize("batched", [False, True])
async def test_backend_failure_propagates_from_every_frame_transport(batched):
    failure = BackendUnreachableError("elasticsearch", "offline")

    class Backend:
        async def search(self, **kwargs):
            raise failure

    class BatchedBackend(Backend):
        async def msearch(self, **kwargs):
            raise failure

    backend = BatchedBackend() if batched else Backend()
    with pytest.raises(BackendUnreachableError) as raised:
        await helpers._perform_frame_lookups(
            [{"_source": {"sensor": {"id": "sensor"}, "object": {"id": "object"}}}],
            [1.0, 0.0],
            "frames",
            datetime(2025, 1, 1, tzinfo=UTC),
            datetime(2025, 1, 2, tzinfo=UTC),
            backend,
        )
    assert raised.value is failure


@pytest.mark.asyncio
async def test_missing_frame_is_still_a_successful_empty_lookup():
    class Backend:
        async def search(self, **kwargs):
            return {"hits": {"hits": []}}

    assert await helpers._get_frame_from_behavior(
        "frames", "sensor", "object", "2025-01-01", "2025-01-02", [1.0, 0.0], Backend()
    ) == (None, None, None, None)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 404, 408, 429, 503])
async def test_systemic_msearch_item_failure_is_not_a_missing_frame(status):
    class Backend:
        async def msearch(self, **kwargs):
            return {"responses": [{"status": status, "error": {"reason": "backend unavailable"}}]}

    with pytest.raises(BackendUnreachableError):
        await helpers._perform_frame_lookups(
            [{"_source": {"sensor": {"id": "sensor"}, "object": {"id": "object"}}}],
            [1.0, 0.0],
            "frames",
            datetime(2025, 1, 1, tzinfo=UTC),
            datetime(2025, 1, 2, tzinfo=UTC),
            Backend(),
        )
