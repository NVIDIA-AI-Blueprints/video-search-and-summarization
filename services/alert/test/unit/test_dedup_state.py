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

"""Unit tests for the in-process DedupStateHandler (Redis removal).

Covers:
- TTL dedup (system-time collision suppression) including expiry
- VLM rate limit gating by end-time category
- ES-backed confirmed-verdict protection (mark / is_confirmed / expiry)
- The _TTLCache primitive
"""

import os
import tempfile

import pytest
import yaml

from clients.dedup_state import DedupStateHandler, _TTLCache


class FakeClock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


def _write_config(**redis_source) -> str:
    cfg = {"event_bridge": {"redis_source": {"host": "localhost", "port": 6379, "db": 0, **redis_source}}}
    fd, path = tempfile.mkstemp(suffix=".yaml")
    with os.fdopen(fd, "w") as f:
        yaml.dump(cfg, f)
    return path


def _incident(sensor="cam-1", ts="2024-01-15T10:30:00Z", ids=(1, 2), category="collision", end="2024-01-15T10:30:05Z"):
    return {
        "sensorId": sensor,
        "timestamp": ts,
        "end": end,
        "objectIds": list(ids),
        "category": category,
        "analyticsModule": {"id": "vst"},
    }


# ── _TTLCache ────────────────────────────────────────────────────────


class TestTTLCache:
    def test_set_and_get(self):
        c = _TTLCache(clock=FakeClock())
        c.set("k", 1, ttl=10)
        assert c.get("k") == 1

    def test_expiry(self):
        clock = FakeClock()
        c = _TTLCache(clock=clock)
        c.set("k", 1, ttl=5)
        clock.advance(5)
        assert c.get("k") is None

    def test_set_if_absent(self):
        clock = FakeClock()
        c = _TTLCache(clock=clock)
        assert c.set_if_absent("k", 1, ttl=5) is True
        assert c.set_if_absent("k", 1, ttl=5) is False  # still live
        clock.advance(5)
        assert c.set_if_absent("k", 1, ttl=5) is True  # expired → re-created

    def test_no_ttl_never_expires(self):
        clock = FakeClock()
        c = _TTLCache(clock=clock)
        c.set("k", 1, ttl=None)
        clock.advance(10_000)
        assert c.get("k") == 1


# ── TTL dedup ────────────────────────────────────────────────────────


class TestDedup:
    def test_duplicate_within_ttl_dropped(self):
        path = _write_config(dedup_ttl_seconds=5)
        try:
            clock = FakeClock()
            h = DedupStateHandler(config_file=path, clock=clock)
            msg = _incident()
            kept1 = h.filter_new_events([dict(msg)])
            kept2 = h.filter_new_events([dict(msg)])
            assert len(kept1) == 1
            assert len(kept2) == 0  # duplicate within TTL
        finally:
            os.unlink(path)

    def test_duplicate_after_ttl_allowed(self):
        path = _write_config(dedup_ttl_seconds=5)
        try:
            clock = FakeClock()
            h = DedupStateHandler(config_file=path, clock=clock)
            msg = _incident()
            assert len(h.filter_new_events([dict(msg)])) == 1
            clock.advance(6)  # TTL elapsed
            assert len(h.filter_new_events([dict(msg)])) == 1
        finally:
            os.unlink(path)

    def test_distinct_events_both_kept(self):
        path = _write_config(dedup_ttl_seconds=300)
        try:
            h = DedupStateHandler(config_file=path, clock=FakeClock())
            a = _incident(ids=(1, 2))
            b = _incident(ids=(3, 4))
            kept = h.filter_new_events([a, b])
            assert len(kept) == 2
        finally:
            os.unlink(path)

    def test_verify_only_finished_skips_incomplete(self):
        path = _write_config(dedup_ttl_seconds=300)
        try:
            h = DedupStateHandler(config_file=path, clock=FakeClock())
            incomplete = _incident()
            incomplete["info"] = {"isComplete": False}
            kept = h.filter_new_events([incomplete], verify_only_finished_events=True)
            assert kept == []
        finally:
            os.unlink(path)


# ── Rate limit ───────────────────────────────────────────────────────


class TestRateLimit:
    def test_rate_limit_skips_category_without_end_requirement(self):
        # No end_time categories configured → rate-limit is a pass-through.
        path = _write_config(dedup_ttl_seconds=300)
        try:
            h = DedupStateHandler(config_file=path, rate_limit=5, clock=FakeClock())
            msg = _incident(category="collision")
            assert h.process_event(dict(msg), rate_limit=True) is True
            assert h.process_event(dict(msg), rate_limit=True) is True  # not deduped
        finally:
            os.unlink(path)

    def test_rate_limit_applies_for_end_time_category(self):
        path = _write_config(
            dedup_ttl_seconds=300,
            end_time_in_dedup_key_categories=["collision"],
        )
        try:
            h = DedupStateHandler(config_file=path, rate_limit=5, clock=FakeClock())
            msg = _incident(category="collision")
            assert h.process_event(dict(msg), rate_limit=True) is True
            assert h.process_event(dict(msg), rate_limit=True) is False  # rate-limited
        finally:
            os.unlink(path)


# ── Verdict protection (ES-backed) ───────────────────────────────────


class FakeES:
    def __init__(self):
        self.docs = {}

    def ensure_json_index(self, index):
        pass

    def write_json(self, index, doc, doc_id=None, **kwargs):
        self.docs[doc_id] = doc

    def get_document(self, index, doc_id):
        return self.docs.get(doc_id)


class TestVerdictProtection:
    def _handler(self, enabled=True, ttl=600):
        path = _write_config(
            dedup_ttl_seconds=300,
            protect_confirmed_verdicts={"enabled": enabled, "ttl_seconds": ttl},
        )
        try:
            h = DedupStateHandler(config_file=path)
        finally:
            os.unlink(path)
        return h

    def test_disabled_returns_false(self):
        h = self._handler(enabled=False)
        assert h.mark_verdict_confirmed("fp") is False
        assert h.is_verdict_confirmed("fp") is False

    def test_mark_then_confirmed(self, monkeypatch):
        import clients.dedup_state as ds

        fake_time = FakeClock(1000.0)
        monkeypatch.setattr(ds.time, "time", fake_time)
        h = self._handler(enabled=True, ttl=600)
        h._es_client = FakeES()  # inject fake ES, bypass lazy build

        assert h.is_verdict_confirmed("fp") is False
        assert h.mark_verdict_confirmed("fp") is True
        assert h.is_verdict_confirmed("fp") is True

    def test_confirmed_expires(self, monkeypatch):
        import clients.dedup_state as ds

        fake_time = FakeClock(1000.0)
        monkeypatch.setattr(ds.time, "time", fake_time)
        h = self._handler(enabled=True, ttl=600)
        h._es_client = FakeES()

        h.mark_verdict_confirmed("fp")
        assert h.is_verdict_confirmed("fp") is True
        fake_time.advance(601)  # past TTL
        assert h.is_verdict_confirmed("fp") is False

    def test_fail_open_when_es_unavailable(self):
        h = self._handler(enabled=True)
        # Simulate an ES client that is unavailable and in backoff cooldown
        # (no permanent disable latch; a far-future retry time keeps it
        # unavailable for the duration of the test).
        h._es_retry_after = 1e18
        assert h.mark_verdict_confirmed("fp") is False
        assert h.is_verdict_confirmed("fp") is False


# ── TTL validation (non-positive TTL rejected, not "never expire") ──────


class TestTTLValidation:
    def test_zero_dedup_ttl_rejected(self):
        path = _write_config(dedup_ttl_seconds=0)
        try:
            with pytest.raises(ValueError, match="dedup_ttl_seconds"):
                DedupStateHandler(config_file=path)
        finally:
            os.unlink(path)

    def test_negative_end_delta_ttl_rejected(self):
        path = _write_config(
            dedup_ttl_seconds=300,
            end_time_delta_filter={"enabled": True, "ttl_seconds": -5},
        )
        try:
            with pytest.raises(ValueError, match="end_time_delta_filter.ttl_seconds"):
                DedupStateHandler(config_file=path)
        finally:
            os.unlink(path)

    def test_ttlcache_non_positive_ttl_expires_immediately(self):
        clock = FakeClock()
        c = _TTLCache(clock=clock)
        # A mistaken 0 must NOT become a permanent entry.
        c.set("k", 1, ttl=0)
        assert c.get("k") is None


# ── end-delta uses the canonical cohort key ─────────────────────────────


class TestEndDeltaCanonicalKey:
    def _handler(self, threshold=5):
        path = _write_config(
            dedup_ttl_seconds=300,
            end_time_delta_filter={
                "enabled": True,
                "threshold_seconds": threshold,
                "ttl_seconds": 3600,
            },
        )
        try:
            return DedupStateHandler(config_file=path, clock=FakeClock())
        finally:
            os.unlink(path)

    def test_end_delta_key_derives_from_build_key(self):
        h = self._handler()
        msg = _incident()
        # end-delta key must be the canonical cohort key (rate_limit=True →
        # excludes end) namespaced under "enddelta:".
        expected = "enddelta:" + h._build_key(msg, rate_limit=True, is_last_chunk=False)
        # Drive one message so the key is stored, then confirm the stored key
        # matches the canonical derivation.
        h.filter_by_end_time_delta([dict(msg)])
        assert h._enddelta_cache.get(expected) is not None

    def test_incomplete_and_complete_are_distinct_cohorts(self):
        h = self._handler()
        incomplete = _incident()
        incomplete["info"] = {"isComplete": False}
        complete = _incident()
        complete["info"] = {"isComplete": True}
        # Different isComplete → different canonical key → independent state.
        k_incomplete = "enddelta:" + h._build_key(incomplete, rate_limit=True, is_last_chunk=False)
        k_complete = "enddelta:" + h._build_key(complete, rate_limit=True, is_last_chunk=True)
        assert k_incomplete != k_complete

    def test_end_delta_key_excludes_end(self):
        # Two messages of the same cohort differing only in `end` must map to
        # the SAME end-delta key (so the delta can be compared).
        h = self._handler(threshold=100)
        a = _incident(end="2024-01-15T10:30:05Z")
        b = _incident(end="2024-01-15T10:30:06Z")
        ka = "enddelta:" + h._build_key(a, rate_limit=True, is_last_chunk=False)
        kb = "enddelta:" + h._build_key(b, rate_limit=True, is_last_chunk=False)
        assert ka == kb


# ── verdict check fails open on malformed / missing expires_at ──────────


class TestVerdictFailOpen:
    def _handler(self):
        path = _write_config(
            dedup_ttl_seconds=300,
            protect_confirmed_verdicts={"enabled": True, "ttl_seconds": 600},
        )
        try:
            h = DedupStateHandler(config_file=path)
        finally:
            os.unlink(path)
        h._es_client = FakeES()
        return h

    def test_missing_expires_at_fails_open(self):
        h = self._handler()
        h._es_client.docs["fp"] = {"fingerprint": "fp"}  # no expires_at
        assert h.is_verdict_confirmed("fp") is False

    def test_non_numeric_expires_at_fails_open(self):
        h = self._handler()
        h._es_client.docs["fp"] = {"fingerprint": "fp", "expires_at": "soon"}
        assert h.is_verdict_confirmed("fp") is False

    def test_nan_expires_at_fails_open(self):
        h = self._handler()
        h._es_client.docs["fp"] = {"fingerprint": "fp", "expires_at": float("nan")}
        assert h.is_verdict_confirmed("fp") is False

    def test_inf_expires_at_fails_open(self):
        h = self._handler()
        h._es_client.docs["fp"] = {"fingerprint": "fp", "expires_at": float("inf")}
        assert h.is_verdict_confirmed("fp") is False

    def test_valid_future_expires_at_confirms(self):
        import clients.dedup_state as ds

        h = self._handler()
        h._es_client.docs["fp"] = {"fingerprint": "fp", "expires_at": ds.time.time() + 600}
        assert h.is_verdict_confirmed("fp") is True


# ── ES client backoff is not a permanent disable ────────────────────────


class TestESClientBackoff:
    def test_backoff_expires_and_retries(self, monkeypatch):
        import clients.dedup_state as ds

        path = _write_config(
            dedup_ttl_seconds=300,
            protect_confirmed_verdicts={"enabled": True, "ttl_seconds": 600},
        )
        try:
            h = DedupStateHandler(config_file=path)
        finally:
            os.unlink(path)

        # No hosts configured → construction fails → backoff scheduled, NOT a
        # permanent latch.
        fake = FakeClock(1000.0)
        monkeypatch.setattr(ds.time, "time", fake)
        assert h._get_es_client() is None
        assert h._es_retry_after > 1000.0  # cooldown scheduled

        # While in cooldown, no retry.
        assert h._get_es_client() is None

        # After the cooldown, a retry is attempted again (proves it is not a
        # permanent disable). Inject a client so the retry succeeds.
        fake.advance(h._es_backoff_seconds + 1)
        h._injected_es_client = FakeES()
        assert h._get_es_client() is not None

    def test_injected_client_used_directly(self):
        path = _write_config(
            dedup_ttl_seconds=300,
            protect_confirmed_verdicts={"enabled": True, "ttl_seconds": 600},
        )
        try:
            h = DedupStateHandler(config_file=path, es_client=FakeES())
        finally:
            os.unlink(path)
        assert isinstance(h._get_es_client(), FakeES)

    def test_quoted_backoff_is_coerced_not_type_error(self):
        # A quoted numeric backoff in YAML must not make the fail-open path
        # raise TypeError (regression guard).
        path = _write_config(
            dedup_ttl_seconds=300,
            protect_confirmed_verdicts={
                "enabled": True, "ttl_seconds": 600, "es_retry_backoff_seconds": "30",
            },
        )
        try:
            h = DedupStateHandler(config_file=path)
        finally:
            os.unlink(path)
        assert h._es_backoff_seconds == 30.0
        # fail-open path must not raise even after a construction failure
        assert h.is_verdict_confirmed("fp") is False

    def test_non_positive_backoff_rejected(self):
        path = _write_config(
            dedup_ttl_seconds=300,
            protect_confirmed_verdicts={
                "enabled": True, "ttl_seconds": 600, "es_retry_backoff_seconds": 0,
            },
        )
        try:
            with pytest.raises(ValueError, match="es_retry_backoff_seconds"):
                DedupStateHandler(config_file=path)
        finally:
            os.unlink(path)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
