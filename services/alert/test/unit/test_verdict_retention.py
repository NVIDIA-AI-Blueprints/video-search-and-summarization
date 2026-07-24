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

"""Confirmed-verdict marker retention (delete-by-query reaper)."""

import os
import tempfile
import time

import pytest
import yaml

from clients.dedup_state import DedupStateHandler
from clients.verdict_retention import VerdictRetentionJob


class FakeES:
    def __init__(self):
        self.dbq_calls = []
        self.deleted = 7

    def ensure_json_index(self, index):
        pass

    def delete_by_query(self, index, query, **kwargs):
        self.dbq_calls.append((index, query, kwargs))
        return {"deleted": self.deleted}


def _handler(enabled=True):
    cfg = {
        "alert_agent": {
            "event_filters": {
                "dedup_ttl_seconds": 300,
                "protect_confirmed_verdicts": {"enabled": enabled, "ttl_seconds": 600},
            }
        }
    }
    fd, path = tempfile.mkstemp(suffix=".yaml")
    with os.fdopen(fd, "w") as f:
        yaml.dump(cfg, f)
    try:
        h = DedupStateHandler(config_file=path)
    finally:
        os.unlink(path)
    return h


class TestPurgeExpiredVerdicts:
    def test_issues_throttled_delete_by_query_for_expired(self):
        h = _handler(enabled=True)
        h._es_client = FakeES()
        deleted = h.purge_expired_verdicts(requests_per_second=25.0)
        assert deleted == 7
        index, query, kwargs = h._es_client.dbq_calls[0]
        assert index == h.verdict_index
        # deletes only expired markers (expires_at < now)
        assert "range" in query and "expires_at" in query["range"]
        assert "lt" in query["range"]["expires_at"]
        # throttled + conflict-tolerant + sliced
        assert kwargs.get("requests_per_second") == 25.0
        assert kwargs.get("conflicts") == "proceed"
        assert kwargs.get("slices") == "auto"

    def test_disabled_is_noop(self):
        h = _handler(enabled=False)
        h._es_client = FakeES()
        assert h.purge_expired_verdicts() == 0
        assert h._es_client.dbq_calls == []

    def test_es_unavailable_returns_zero(self):
        h = _handler(enabled=True)
        h._es_retry_after = 1e18  # unavailable / in backoff
        assert h.purge_expired_verdicts() == 0

    def test_delete_by_query_error_is_swallowed(self):
        h = _handler(enabled=True)

        class BoomES(FakeES):
            def delete_by_query(self, index, query, **kwargs):
                raise RuntimeError("es down")

        h._es_client = BoomES()
        # Must not raise — the scheduler relies on this.
        assert h.purge_expired_verdicts() == 0


class TestVerdictRetentionJob:
    def test_run_once_delegates_to_handler(self):
        h = _handler(enabled=True)
        h._es_client = FakeES()
        job = VerdictRetentionJob(h, interval_seconds=3600, requests_per_second=10.0)
        assert job.run_once() == 7
        assert h._es_client.dbq_calls[0][2]["requests_per_second"] == 10.0

    def test_start_runs_and_stop_terminates(self):
        h = _handler(enabled=True)
        h._es_client = FakeES()
        # Tiny interval + run_on_start so the first pass fires immediately.
        job = VerdictRetentionJob(
            h, interval_seconds=0.05, requests_per_second=10.0, run_on_start=True,
        )
        job.start()
        # Give the daemon a moment to execute at least one pass.
        deadline = time.time() + 2.0
        while not h._es_client.dbq_calls and time.time() < deadline:
            time.sleep(0.02)
        job.stop(timeout=2.0)
        assert h._es_client.dbq_calls, "retention job did not run a pass"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
