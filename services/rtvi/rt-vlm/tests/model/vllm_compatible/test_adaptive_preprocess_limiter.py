# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import asyncio

import pytest

from models.vllm_compatible.adaptive_preprocess_limiter import (
    AdaptivePreprocessConfig,
    AdaptivePreprocessLimiter,
    PreprocessAdmissionTimeout,
)


class _FreeMemory:
    def __init__(self, value_mb):
        self.value_mb = value_mb

    def __call__(self):
        return self.value_mb


class _Clock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class _GpuUtilization:
    def __init__(self, value_percent):
        self.value_percent = value_percent

    def __call__(self):
        return self.value_percent


def _config(**overrides):
    values = {
        "enabled": True,
        "shadow_mode": False,
        "min_workers": 1,
        "max_workers": 4,
        "gpu_headroom_mb": 100,
        "initial_estimated_request_mb": 500,
        "estimate_safety_factor": 1.0,
        "admission_timeout_seconds": 0.05,
        "healthy_completions_for_increase": 2,
        "calibration_samples_required": 1,
        "scale_up_cooldown_seconds": 10,
    }
    values.update(overrides)
    return AdaptivePreprocessConfig(**values)


async def _calibrate(limiter, workload_key="video-shape-a", observed_mb=500):
    admission = await limiter.acquire("calibration", workload_key, payload_mb=100)
    await limiter.release(admission, observed_allocation_mb=observed_mb)


def test_atomic_pending_reservations_prevent_snapshot_over_admission():
    async def run():
        memory = _FreeMemory(1200)
        limiter = AdaptivePreprocessLimiter(
            _config(min_workers=4, max_workers=4),
            memory,
        )
        await _calibrate(limiter)

        first = await limiter.acquire("first", "video-shape-a", payload_mb=100)
        second = await limiter.acquire("second", "video-shape-a", payload_mb=100)
        with pytest.raises(PreprocessAdmissionTimeout):
            await limiter.acquire("third", "video-shape-a", payload_mb=100)

        snapshot = limiter.snapshot()
        assert snapshot.active == 2
        assert snapshot.observed_peak_active == 2
        assert snapshot.pending_reserved_mb == 1000
        assert snapshot.timeouts == 1

        await limiter.release(first)
        await limiter.release(second)

    asyncio.run(run())


def test_duplicate_request_id_does_not_overwrite_reservation():
    async def run():
        limiter = AdaptivePreprocessLimiter(
            _config(min_workers=2, max_workers=2),
            _FreeMemory(10000),
        )
        admission = await limiter.acquire("duplicate", "video-shape-a", payload_mb=100)

        with pytest.raises(ValueError, match="already admitted"):
            await limiter.acquire("duplicate", "video-shape-a", payload_mb=100)

        snapshot = limiter.snapshot()
        assert snapshot.active == 1
        assert snapshot.pending_reserved_mb == 500
        await limiter.release(admission)

    asyncio.run(run())


def test_cancelled_waiter_is_removed_from_queue():
    async def run():
        limiter = AdaptivePreprocessLimiter(
            _config(admission_timeout_seconds=1),
            _FreeMemory(100),
        )
        waiter = asyncio.create_task(limiter.acquire("cancelled", "video-shape-a", payload_mb=100))
        while limiter.snapshot().queued == 0:
            await asyncio.sleep(0)

        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter

        assert limiter.snapshot().queued == 0

    asyncio.run(run())


def test_effective_limit_increases_only_after_sustained_backpressure():
    async def run():
        clock = _Clock()
        limiter = AdaptivePreprocessLimiter(
            _config(healthy_completions_for_increase=1),
            _FreeMemory(10000),
            clock=clock,
        )

        first = await limiter.acquire("first", "video-shape-a", payload_mb=100)
        limiter.note_backpressure()
        await limiter.release(first, success=True)
        assert limiter.snapshot().effective_limit == 1

        clock.advance(10)
        second = await limiter.acquire("second", "video-shape-a", payload_mb=100)
        limiter.note_backpressure()
        await limiter.release(second, success=True)
        assert limiter.snapshot().effective_limit == 2

        clock.advance(10)
        for request_id in ("third", "fourth"):
            admission = await limiter.acquire(request_id, "video-shape-a", payload_mb=100)
            limiter.note_backpressure()
            await limiter.release(admission, success=True)

        assert limiter.snapshot().effective_limit == 3

    asyncio.run(run())


def test_idle_completions_do_not_increase_effective_limit():
    async def run():
        clock = _Clock()
        limiter = AdaptivePreprocessLimiter(
            _config(healthy_completions_for_increase=1),
            _FreeMemory(10000),
            clock=clock,
        )
        clock.advance(60)

        for index in range(10):
            admission = await limiter.acquire(
                f"request-{index}",
                "video-shape-a",
                payload_mb=100,
            )
            await limiter.release(admission, success=True)

        assert limiter.snapshot().effective_limit == 1

    asyncio.run(run())


def test_gpu_saturation_defers_scale_up_until_utilization_recovers():
    async def run():
        clock = _Clock()
        gpu_utilization = _GpuUtilization(95)
        limiter = AdaptivePreprocessLimiter(
            _config(healthy_completions_for_increase=1),
            _FreeMemory(10000),
            gpu_utilization,
            clock=clock,
        )
        clock.advance(10)

        saturated = await limiter.acquire("saturated", "video-shape-a", payload_mb=100)
        limiter.note_backpressure()
        await limiter.release(saturated, success=True)

        snapshot = limiter.snapshot()
        assert snapshot.effective_limit == 1
        assert snapshot.last_gpu_utilization_percent == 95

        gpu_utilization.value_percent = 50
        clock.advance(10)
        recovered = await limiter.acquire("recovered", "video-shape-a", payload_mb=100)
        limiter.note_backpressure()
        await limiter.release(recovered, success=True)

        snapshot = limiter.snapshot()
        assert snapshot.effective_limit == 2
        assert snapshot.last_gpu_utilization_percent == 50

    asyncio.run(run())


def test_memory_pressure_halves_effective_limit_once_per_pressure_episode():
    async def run():
        memory = _FreeMemory(10000)
        limiter = AdaptivePreprocessLimiter(
            _config(min_workers=1, max_workers=4),
            memory,
        )
        limiter.set_effective_limit_for_test(4)
        memory.value_mb = 500

        with pytest.raises(PreprocessAdmissionTimeout):
            await limiter.acquire("blocked", "video-shape-a", payload_mb=100)

        snapshot = limiter.snapshot()
        assert snapshot.effective_limit == 2
        assert snapshot.memory_pressure_events == 1
        assert snapshot.backpressure_observed is False

    asyncio.run(run())


def test_successful_admission_does_not_clear_memory_pressure_episode():
    async def run():
        clock = _Clock()
        memory = _FreeMemory(10000)
        limiter = AdaptivePreprocessLimiter(
            _config(
                min_workers=1,
                max_workers=4,
                admission_timeout_seconds=0.01,
                healthy_completions_for_increase=1,
            ),
            memory,
            clock=clock,
        )
        limiter.set_effective_limit_for_test(4)
        memory.value_mb = 500

        with pytest.raises(PreprocessAdmissionTimeout):
            await limiter.acquire("blocked-first", "video-shape-a", payload_mb=100)

        assert limiter.snapshot().effective_limit == 2
        assert limiter.snapshot().memory_pressure_events == 1

        memory.value_mb = 10000
        admitted = await limiter.acquire("recovery", "video-shape-a", payload_mb=100)
        await limiter.release(admitted, success=True)

        memory.value_mb = 500
        with pytest.raises(PreprocessAdmissionTimeout):
            await limiter.acquire("blocked-again", "video-shape-a", payload_mb=100)

        snapshot = limiter.snapshot()
        assert snapshot.effective_limit == 2
        assert snapshot.memory_pressure_events == 1

    asyncio.run(run())


def test_pressure_recovery_requires_margin_and_cooldown():
    async def run():
        clock = _Clock()
        memory = _FreeMemory(10000)
        limiter = AdaptivePreprocessLimiter(
            _config(
                min_workers=1,
                max_workers=4,
                admission_timeout_seconds=0.01,
                healthy_completions_for_increase=1,
            ),
            memory,
            clock=clock,
        )
        limiter.set_effective_limit_for_test(4)
        memory.value_mb = 500
        with pytest.raises(PreprocessAdmissionTimeout):
            await limiter.acquire("pressure", "video-shape-a", payload_mb=100)

        memory.value_mb = 1099
        admission = await limiter.acquire("thin-margin", "video-shape-a", payload_mb=100)
        await limiter.release(admission, success=True)
        assert limiter.snapshot().memory_pressure_active is True

        memory.value_mb = 10000
        clock.advance(10)
        admission = await limiter.acquire("recovered", "video-shape-a", payload_mb=100)
        await limiter.release(admission, success=True)

        snapshot = limiter.snapshot()
        assert snapshot.memory_pressure_active is False
        assert snapshot.effective_limit == 2

    asyncio.run(run())


def test_scale_up_requires_memory_for_current_request_and_recovery_margin():
    async def run():
        clock = _Clock()
        memory = _FreeMemory(1099)
        limiter = AdaptivePreprocessLimiter(
            _config(healthy_completions_for_increase=1),
            memory,
            clock=clock,
        )
        clock.advance(10)

        admission = await limiter.acquire("request", "video-shape-a", payload_mb=100)
        limiter.note_backpressure()
        await limiter.release(admission, success=True)

        assert limiter.snapshot().effective_limit == 1

    asyncio.run(run())


def test_non_memory_failure_does_not_reduce_effective_limit():
    async def run():
        limiter = AdaptivePreprocessLimiter(
            _config(min_workers=1, max_workers=4),
            _FreeMemory(10000),
        )
        limiter.set_effective_limit_for_test(4)
        admission = await limiter.acquire("failed", "video-shape-a", payload_mb=100)

        await limiter.release(admission, success=False, memory_pressure=False)

        snapshot = limiter.snapshot()
        assert snapshot.effective_limit == 4
        assert snapshot.memory_pressure_events == 0

    asyncio.run(run())


def test_cuda_memory_pressure_failure_reduces_effective_limit():
    async def run():
        limiter = AdaptivePreprocessLimiter(
            _config(min_workers=1, max_workers=4),
            _FreeMemory(10000),
        )
        limiter.set_effective_limit_for_test(4)
        admission = await limiter.acquire("oom", "video-shape-a", payload_mb=100)

        await limiter.release(admission, success=False, memory_pressure=True)

        snapshot = limiter.snapshot()
        assert snapshot.effective_limit == 2
        assert snapshot.memory_pressure_events == 1
        assert snapshot.estimated_mb_by_workload == {}
        assert snapshot.calibrated_workloads == 0

    asyncio.run(run())


def test_shadow_mode_records_denial_without_blocking():
    async def run():
        limiter = AdaptivePreprocessLimiter(
            _config(shadow_mode=True, min_workers=1, max_workers=1),
            _FreeMemory(100),
        )

        admission = await limiter.acquire("shadow", "video-shape-a", payload_mb=2000)

        assert admission.enforced is False
        assert admission.policy_would_admit is False
        assert limiter.snapshot().shadow_denials == 1
        await limiter.release(admission)

    asyncio.run(run())


def test_exclusive_observation_updates_workload_estimate():
    async def run():
        limiter = AdaptivePreprocessLimiter(
            _config(min_workers=1, max_workers=1),
            _FreeMemory(10000),
        )
        admission = await limiter.acquire("sample", "video-shape-a", payload_mb=100)

        await limiter.release(admission, observed_allocation_mb=1400, success=True)

        snapshot = limiter.snapshot()
        assert snapshot.estimated_mb_by_workload["video-shape-a"] == 1400
        assert snapshot.calibrated_workloads == 1

    asyncio.run(run())


def test_peak_sampler_captures_transient_allocation():
    async def run():
        memory = _FreeMemory(10000)
        limiter = AdaptivePreprocessLimiter(
            _config(min_workers=1, max_workers=1),
            memory,
        )
        admission = await limiter.acquire("sample", "video-shape-a", payload_mb=100)
        memory.value_mb = 8200
        limiter.sample_active_memory("sample")
        memory.value_mb = 10000

        await limiter.release(admission, success=True)

        assert limiter.snapshot().estimated_mb_by_workload == {"video-shape-a": 1800}

    asyncio.run(run())


def test_overlapping_observations_do_not_train_memory_estimate():
    async def run():
        limiter = AdaptivePreprocessLimiter(
            _config(min_workers=2, max_workers=2),
            _FreeMemory(10000),
        )
        await _calibrate(limiter)
        first = await limiter.acquire("first", "video-shape-a", payload_mb=100)
        second = await limiter.acquire("second", "video-shape-a", payload_mb=100)

        await limiter.release(first, observed_allocation_mb=2000)
        await limiter.release(second, observed_allocation_mb=2000)

        assert limiter.snapshot().estimated_mb_by_workload == {"video-shape-a": 500}

    asyncio.run(run())


def test_can_accept_accounts_for_headroom_and_pending_reservations():
    async def run():
        memory = _FreeMemory(1200)
        limiter = AdaptivePreprocessLimiter(
            _config(min_workers=4, max_workers=4),
            memory,
        )
        await _calibrate(limiter)
        first = await limiter.acquire("first", "video-shape-a", payload_mb=100)
        second = await limiter.acquire("second", "video-shape-a", payload_mb=100)

        assert limiter.can_accept("video-shape-a", payload_mb=100) is False

        await limiter.release(first)
        assert limiter.can_accept("video-shape-a", payload_mb=100) is True
        await limiter.release(second)

    asyncio.run(run())


def test_unseen_workload_is_calibrated_without_overlap():
    async def run():
        limiter = AdaptivePreprocessLimiter(
            _config(min_workers=2, max_workers=2, admission_timeout_seconds=1),
            _FreeMemory(10000),
        )
        first = await limiter.acquire("first", "video-shape-a", payload_mb=100)
        second_task = asyncio.create_task(
            limiter.acquire("second", "video-shape-b", payload_mb=200)
        )
        while limiter.snapshot().queued == 0:
            await asyncio.sleep(0)

        assert second_task.done() is False
        await limiter.release(first, observed_allocation_mb=700)
        second = await second_task

        assert second.exclusive is True
        await limiter.release(second, observed_allocation_mb=1200)
        snapshot = limiter.snapshot()
        assert snapshot.estimated_mb_by_workload == {
            "video-shape-a": 700,
            "video-shape-b": 1200,
        }

    asyncio.run(run())


def test_workload_requires_configured_exclusive_calibration_samples():
    async def run():
        limiter = AdaptivePreprocessLimiter(
            _config(
                min_workers=2,
                max_workers=2,
                calibration_samples_required=2,
                admission_timeout_seconds=1,
            ),
            _FreeMemory(10000),
        )
        first = await limiter.acquire("first", "video-shape-a", payload_mb=100)
        await limiter.release(first, observed_allocation_mb=700)
        assert limiter.snapshot().calibrated_workloads == 0

        second = await limiter.acquire("second", "video-shape-a", payload_mb=100)
        waiter = asyncio.create_task(limiter.acquire("waiter", "video-shape-a", payload_mb=100))
        while limiter.snapshot().queued == 0:
            await asyncio.sleep(0)
        assert waiter.done() is False

        await limiter.release(second, observed_allocation_mb=700)
        admitted = await waiter
        assert limiter.snapshot().calibrated_workloads == 1
        await limiter.release(admitted)

    asyncio.run(run())


def test_calibration_is_scoped_to_each_model_process():
    async def run():
        first_model = AdaptivePreprocessLimiter(_config(), _FreeMemory(10000))
        await _calibrate(first_model, workload_key="shared-shape", observed_mb=1800)

        second_model = AdaptivePreprocessLimiter(_config(), _FreeMemory(10000))

        assert first_model.snapshot().estimated_mb_by_workload == {"shared-shape": 1800}
        assert second_model.snapshot().estimated_mb_by_workload == {}
        assert second_model.snapshot().calibrated_workloads == 0

    asyncio.run(run())
