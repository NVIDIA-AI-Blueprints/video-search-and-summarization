# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Retry-proof, separate budget ledgers."""

from __future__ import annotations

import pytest

from evalv2.core.budget import LEDGERS, BudgetExceeded, BudgetLedger, Cap


def test_every_attempt_is_charged_before_it_is_sent():
    ledger = BudgetLedger([Cap("inference", max_attempts=2)])
    ledger.reserve("inference", subject="s", task="incident", tokens_bound=10)
    ledger.reserve("inference", subject="s", task="incident", tokens_bound=10)
    with pytest.raises(BudgetExceeded, match="attempts cap 2"):
        ledger.reserve("inference", subject="s", task="incident", tokens_bound=10)
    assert ledger.totals("inference").attempts == 2  # the refused attempt charged nothing


def test_tokens_use_the_bound_until_settled_with_measured_usage():
    ledger = BudgetLedger([Cap("inference", max_tokens=100)])
    first = ledger.reserve("inference", subject="s", task="t", tokens_bound=60)
    with pytest.raises(BudgetExceeded, match="tokens cap 100"):
        ledger.reserve("inference", subject="s", task="t", tokens_bound=60)
    ledger.settle(first, tokens_used=20)
    ledger.reserve("inference", subject="s", task="t", tokens_bound=60)
    assert ledger.totals("inference").tokens == 80


def test_an_attempt_without_reported_usage_keeps_its_pessimistic_bound():
    ledger = BudgetLedger([Cap("judge", max_tokens=1000)])
    reservation = ledger.reserve("judge", subject="j", task="t", tokens_bound=400)
    ledger.settle(reservation, tokens_used=None)
    assert ledger.totals("judge").tokens == 400
    with pytest.raises(ValueError, match="already settled"):
        ledger.settle(reservation, tokens_used=1)


def test_ledgers_are_separate_and_caps_can_be_scoped():
    ledger = BudgetLedger([Cap("inference", max_attempts=1, subject="nano"), Cap("zoom", max_attempts=3)])
    ledger.reserve("inference", subject="nano", task="incident", tokens_bound=0)
    ledger.reserve("inference", subject="super", task="incident", tokens_bound=0)  # nano's cap does not apply
    with pytest.raises(BudgetExceeded):
        ledger.reserve("inference", subject="nano", task="incident", tokens_bound=0)
    for _ in range(3):
        ledger.reserve("zoom", subject="nano", task="crops", tokens_bound=0)
    with pytest.raises(BudgetExceeded):
        ledger.reserve("zoom", subject="super", task="crops", tokens_bound=0)


def test_a_resumed_ledger_restores_counts_including_unsettled_attempts(tmp_path):
    log = tmp_path / "budget.jsonl"
    first = BudgetLedger([Cap("inference", max_attempts=3, max_tokens=300)], log)
    settled = first.reserve("inference", subject="s", task="t", tokens_bound=100)
    first.settle(settled, tokens_used=40)
    first.reserve("inference", subject="s", task="t", tokens_bound=100)  # the process died before the response
    resumed = BudgetLedger([Cap("inference", max_attempts=3, max_tokens=300)], log)
    assert (resumed.totals("inference").attempts, resumed.totals("inference").tokens) == (2, 140)
    resumed.reserve("inference", subject="s", task="t", tokens_bound=100)
    with pytest.raises(BudgetExceeded):
        resumed.reserve("inference", subject="s", task="t", tokens_bound=1)


def test_invalid_caps_and_ledgers_are_refused():
    with pytest.raises(ValueError, match="unknown ledger"):
        Cap("gpu", max_attempts=1)
    with pytest.raises(ValueError, match="limits nothing"):
        Cap("inference")
    with pytest.raises(ValueError):
        BudgetLedger([]).reserve("gpu", subject="s", task="t", tokens_bound=0)
    assert "generation" in LEDGERS and "object_tracking" in LEDGERS


def test_snapshot_reports_every_cap():
    ledger = BudgetLedger([Cap("inference", max_attempts=5, max_tokens=50)])
    ledger.reserve("inference", subject="s", task="t", tokens_bound=7)
    assert ledger.snapshot()["caps"] == [{"ledger": "inference", "subject": "*", "task": "*", "attempts": 1,
                                          "max_attempts": 5, "tokens": 7, "max_tokens": 50}]
