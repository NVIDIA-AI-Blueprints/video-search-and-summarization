# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Resumable checkpoints, idempotency keys, usage and timing instruments."""

from __future__ import annotations

import pytest

from evalv2.core.checkpoint import Checkpoint, CheckpointError, idempotency_key
from evalv2.core.instrument import Stopwatch, Usage
from evalv2.tasks.base import WorkItem


def test_idempotency_keys_are_stable_and_sensitive_to_every_setting():
    a = WorkItem("incident", "super", "Assault001", 1, {"prompt": "p1-v4-base"})
    assert a.key == WorkItem("incident", "super", "Assault001", 1, {"prompt": "p1-v4-base"}).key
    assert a.key != WorkItem("incident", "super", "Assault001", 2, {"prompt": "p1-v4-base"}).key
    assert a.key != WorkItem("incident", "super", "Assault001", 1, {"prompt": "p1-v4-e01-severity"}).key
    assert idempotency_key({"b": 1, "a": 2}) == idempotency_key({"a": 2, "b": 1})


def test_final_results_are_never_repeated_and_infrastructure_failures_stay_open(tmp_path):
    checkpoint = Checkpoint(tmp_path / "checkpoint.jsonl")
    checkpoint.record("k1", "timeout")
    assert not checkpoint.is_final("k1") and checkpoint.attempts("k1") == 1
    checkpoint.record("k1", "ok", ref="prediction:" + "a" * 64)
    assert checkpoint.is_final("k1") and checkpoint.attempts("k1") == 2
    with pytest.raises(CheckpointError, match="already has a final result"):
        checkpoint.record("k1", "ok")
    checkpoint.record("k2", "invalid_output")  # a model miss is final too
    assert checkpoint.final_keys() == {"k1", "k2"}


def test_a_resumed_checkpoint_skips_finished_items(tmp_path):
    path = tmp_path / "checkpoint.jsonl"
    Checkpoint(path).record("k1", "ok")
    Checkpoint(path).record("k2", "rate_limited")
    resumed = Checkpoint(path)
    assert resumed.is_final("k1") and not resumed.is_final("k2")
    with pytest.raises(CheckpointError):
        resumed.record("k1", "ok")


def test_a_malformed_checkpoint_is_refused(tmp_path):
    path = tmp_path / "checkpoint.jsonl"
    path.write_text('{"key": "k1"}\n')
    with pytest.raises(CheckpointError, match="malformed"):
        Checkpoint(path)


def test_usage_keeps_provider_figures_and_derives_nothing_it_was_not_given():
    usage = Usage.from_openai({"prompt_tokens": 9000, "completion_tokens": 700,
                               "completion_tokens_details": {"reasoning_tokens": 500}})
    assert (usage.prompt_tokens, usage.completion_tokens, usage.reasoning_tokens, usage.total_tokens) == \
        (9000, 700, 500, 9700)
    assert usage.provider_reported and usage.raw["prompt_tokens"] == 9000
    assert Usage.from_openai(None) == Usage() and not Usage().provider_reported


def test_the_stopwatch_separates_waiting_and_flags_a_host_suspend():
    mono = iter([0.0, 2.5])
    wall = iter([100.0, 102.5])
    with Stopwatch(monotonic=lambda: next(mono), wall=lambda: next(wall), waited_ms=60000.0) as watch:
        pass
    assert watch.timing.wall_ms == 2500.0 and watch.timing.waited_ms == 60000.0 and not watch.timing.host_suspended
    mono = iter([0.0, 2.0])
    wall = iter([0.0, 600.0])  # the laptop slept: wall time ran on, monotonic time did not
    with Stopwatch(monotonic=lambda: next(mono), wall=lambda: next(wall)) as watch:
        pass
    assert watch.timing.host_suspended
