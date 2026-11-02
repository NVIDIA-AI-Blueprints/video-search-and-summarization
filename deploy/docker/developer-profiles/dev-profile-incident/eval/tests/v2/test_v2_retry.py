# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Retry policies keep V1's semantics; model outputs are never retried."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from evalv2.core.retry import POLICIES, Failure, classify_http_status, run_with_retry


def test_policies_mirror_v1_exactly():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    import eval_vlm_client as v1

    for name, v1_policy in v1.P1_RETRY_POLICIES.items():
        policy = POLICIES[name]
        assert policy.retry_on == tuple(v1_policy["retry_on"])
        assert policy.backoff_s == tuple(v1_policy["backoff_s"])
        assert policy.inter_call_delay_s == v1_policy["inter_call_delay_s"]
        assert policy.cooldown_s == v1_policy["cooldown_s"]
        assert policy.honor_retry_after == v1_policy["honor_retry_after"]
        assert policy.max_retries == v1_policy["max_rate_limit_retries"]


def _calls(*outcomes):
    queue = list(outcomes)
    return lambda attempt: queue.pop(0)


def test_infrastructure_failures_are_retried_until_success():
    sleeps = []
    log = run_with_retry(_calls((None, Failure("timeout")), ("ok", None)), POLICIES["retry-v2"], sleep=sleeps.append)
    assert log.succeeded and [a.number for a in log.attempts] == [1, 2] and sleeps == [60.0]
    assert log.attempts[1].waited_s == 60.0


def test_a_model_output_is_never_retried():
    log = run_with_retry(_calls((None, Failure("invalid_output"))), POLICIES["retry-v2"], sleep=lambda s: None)
    assert not log.succeeded and len(log.attempts) == 1


def test_retries_stop_at_the_policy_limit():
    sleeps = []
    failures = [(None, Failure("transport_error"))] * 5
    log = run_with_retry(_calls(*failures), POLICIES["retry-v2"], sleep=sleeps.append)
    assert len(log.attempts) == 3 and sleeps == [60.0, 180.0]


def test_retry_v1_retries_rate_limits_only_and_honours_retry_after():
    sleeps = []
    log = run_with_retry(_calls((None, Failure("rate_limited", retry_after_s=90.0)), ("ok", None)),
                         POLICIES["retry-v1"], sleep=sleeps.append)
    assert log.succeeded and sleeps == [90.0]
    log = run_with_retry(_calls((None, Failure("timeout"))), POLICIES["retry-v1"], sleep=sleeps.append)
    assert len(log.attempts) == 1


def test_the_none_policy_never_retries():
    log = run_with_retry(_calls((None, Failure("timeout"))), POLICIES["none"], sleep=lambda s: None)
    assert len(log.attempts) == 1


@pytest.mark.parametrize(("status", "kind"), [(429, "rate_limited"), (504, "timeout"), (524, "timeout"),
                                              (522, "timeout"), (500, "http_error"), (400, "http_error")])
def test_http_status_classification_matches_v1(status, kind):
    assert classify_http_status(status) == kind


def test_unknown_failure_kinds_are_refused():
    with pytest.raises(ValueError):
        Failure("flaky")
    assert Failure("timeout").infrastructure and not Failure("invalid_output").infrastructure
