# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""GPU-memory-aware admission control for multimodal preprocessing."""

from __future__ import annotations

import asyncio
import math
import threading
import time
from dataclasses import dataclass
from typing import Callable


class PreprocessAdmissionTimeout(TimeoutError):
    """Raised when GPU memory does not become available before the deadline."""


@dataclass(frozen=True)
class AdaptivePreprocessConfig:
    enabled: bool = False
    shadow_mode: bool = True
    min_workers: int = 1
    max_workers: int = 16
    gpu_headroom_mb: int = 1024
    initial_estimated_request_mb: int = 512
    estimate_safety_factor: float = 1.25
    admission_timeout_seconds: float = 30.0
    healthy_completions_for_increase: int = 3
    scale_up_cooldown_seconds: float = 30.0
    scale_up_gpu_utilization_threshold_percent: float = 90.0
    calibration_samples_required: int = 3
    estimate_ewma_alpha: float = 0.25
    poll_interval_seconds: float = 0.05

    def __post_init__(self):
        if self.min_workers < 1:
            raise ValueError("min_workers must be greater than or equal to 1")
        if self.max_workers < self.min_workers:
            raise ValueError("max_workers must be greater than or equal to min_workers")
        if self.gpu_headroom_mb < 0:
            raise ValueError("gpu_headroom_mb must be greater than or equal to 0")
        if self.initial_estimated_request_mb < 1:
            raise ValueError("initial_estimated_request_mb must be greater than or equal to 1")
        if self.estimate_safety_factor < 1:
            raise ValueError("estimate_safety_factor must be greater than or equal to 1")
        if self.admission_timeout_seconds <= 0:
            raise ValueError("admission_timeout_seconds must be greater than 0")
        if self.healthy_completions_for_increase < 1:
            raise ValueError("healthy_completions_for_increase must be greater than or equal to 1")
        if self.scale_up_cooldown_seconds < 0:
            raise ValueError("scale_up_cooldown_seconds must be greater than or equal to 0")
        if not 0 <= self.scale_up_gpu_utilization_threshold_percent <= 100:
            raise ValueError(
                "scale_up_gpu_utilization_threshold_percent must be in the interval [0, 100]"
            )
        if self.calibration_samples_required < 1:
            raise ValueError("calibration_samples_required must be greater than or equal to 1")
        if not 0 < self.estimate_ewma_alpha <= 1:
            raise ValueError("estimate_ewma_alpha must be in the interval (0, 1]")
        if self.poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be greater than 0")


@dataclass(frozen=True)
class PreprocessAdmission:
    request_id: str
    workload_key: str
    payload_mb: int
    estimated_mb: int
    free_memory_before_mb: int | None
    admitted_at: float
    enforced: bool
    policy_would_admit: bool
    exclusive: bool
    wait_seconds: float


@dataclass(frozen=True)
class AdaptivePreprocessSnapshot:
    active: int
    queued: int
    effective_limit: int
    pending_reserved_mb: int
    admissions: int
    policy_denials: int
    shadow_denials: int
    timeouts: int
    memory_pressure_events: int
    memory_pressure_active: bool
    backpressure_observed: bool
    healthy_completions: int
    scale_up_cooldown_remaining_seconds: float
    last_gpu_utilization_percent: float | None
    observed_peak_active: int
    estimated_mb_by_workload: dict[str, int]
    calibrated_workloads: int
    last_free_memory_mb: int | None


class AdaptivePreprocessLimiter:
    """Admit preprocessing work without overcommitting one free-memory snapshot.

    The vLLM executor remains fixed at ``config.max_workers``. This controller
    provides the runtime-effective limit and reserves memory atomically before
    an admitted coroutine starts allocating CUDA tensors.
    """

    def __init__(
        self,
        config: AdaptivePreprocessConfig,
        free_memory_mb: Callable[[], int],
        gpu_utilization_percent: Callable[[], float] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.config = config
        self._free_memory_mb = free_memory_mb
        self._gpu_utilization_percent = gpu_utilization_percent
        self._clock = clock
        self._condition = asyncio.Condition()
        self._state_lock = threading.Lock()
        self._effective_limit = config.min_workers
        self._active = 0
        self._queued = 0
        self._pending_reserved_mb = 0
        self._admissions: dict[str, PreprocessAdmission] = {}
        self._minimum_free_mb_by_request: dict[str, int] = {}
        self._overlapped_request_ids: set[str] = set()
        self._estimated_mb_by_workload: dict[str, int] = {}
        self._calibration_samples_by_workload: dict[str, int] = {}
        self._calibrated_workloads: set[str] = set()
        self._healthy_completions = 0
        self._memory_pressure_active = False
        self._backpressure_observed = False
        self._next_scale_up_at = self._clock() + config.scale_up_cooldown_seconds
        self._admission_count = 0
        self._policy_denials = 0
        self._shadow_denials = 0
        self._timeouts = 0
        self._memory_pressure_events = 0
        self._observed_peak_active = 0
        self._last_free_memory_mb: int | None = None
        self._last_gpu_utilization_percent: float | None = None
        self._wait_histogram = None
        self._observable_gauges = []

    def register_otel_metrics(self, meter_name: str = "rtvi-vllm-preprocess") -> None:
        """Expose controller state without requiring telemetry during unit tests."""
        try:
            from opentelemetry import metrics

            meter = metrics.get_meter_provider().get_meter(meter_name)

            def observe(field):
                return lambda options: [metrics.Observation(getattr(self.snapshot(), field))]

            for name, field, description, unit in (
                (
                    "rtvi_vlm_preprocess_active_requests",
                    "active",
                    "Requests admitted to multimodal preprocessing",
                    "{request}",
                ),
                (
                    "rtvi_vlm_preprocess_queued_requests",
                    "queued",
                    "Requests waiting for multimodal preprocessing admission",
                    "{request}",
                ),
                (
                    "rtvi_vlm_preprocess_effective_limit",
                    "effective_limit",
                    "Current adaptive multimodal preprocessing concurrency limit",
                    "{worker}",
                ),
                (
                    "rtvi_vlm_preprocess_reserved_memory",
                    "pending_reserved_mb",
                    "Memory reserved for admitted multimodal preprocessing requests",
                    "MiBy",
                ),
                (
                    "rtvi_vlm_preprocess_admission_timeouts",
                    "timeouts",
                    "Cumulative multimodal preprocessing admission timeouts",
                    "{timeout}",
                ),
                (
                    "rtvi_vlm_preprocess_memory_pressure_events",
                    "memory_pressure_events",
                    "Cumulative adaptive preprocessing memory-pressure events",
                    "{event}",
                ),
                (
                    "rtvi_vlm_preprocess_observed_peak_active_requests",
                    "observed_peak_active",
                    "Peak concurrently admitted multimodal preprocessing requests",
                    "{request}",
                ),
            ):
                self._observable_gauges.append(
                    meter.create_observable_gauge(
                        name,
                        callbacks=[observe(field)],
                        description=description,
                        unit=unit,
                    )
                )
            self._wait_histogram = meter.create_histogram(
                "rtvi_vlm_preprocess_admission_wait_duration",
                description="Time spent waiting for multimodal preprocessing admission",
                unit="s",
            )
        except Exception:
            self._wait_histogram = None

    def _sample_free_memory_locked(self) -> int | None:
        try:
            value = max(0, int(self._free_memory_mb()))
        except Exception:
            value = None
        self._last_free_memory_mb = value
        return value

    def _has_scale_up_gpu_headroom_locked(self) -> bool:
        if self._gpu_utilization_percent is None:
            return True
        try:
            utilization = float(self._gpu_utilization_percent())
        except Exception:
            utilization = None
        if utilization is None or not math.isfinite(utilization):
            self._last_gpu_utilization_percent = None
            return False
        self._last_gpu_utilization_percent = min(100.0, max(0.0, utilization))
        return (
            self._last_gpu_utilization_percent
            < self.config.scale_up_gpu_utilization_threshold_percent
        )

    def _request_estimate_locked(self, workload_key: str, payload_mb: int) -> int:
        learned_mb = self._estimated_mb_by_workload.get(workload_key)
        baseline_mb = (
            learned_mb
            if learned_mb is not None
            else max(self.config.initial_estimated_request_mb, payload_mb)
        )
        return math.ceil(baseline_mb * self.config.estimate_safety_factor)

    def _policy_decision_locked(
        self,
        workload_key: str,
        payload_mb: int,
        *,
        require_calibration: bool = True,
    ) -> tuple[bool, int, int | None]:
        estimate_mb = self._request_estimate_locked(workload_key, payload_mb)
        free_mb = self._sample_free_memory_locked()
        worker_available = self._active < self._effective_limit
        # Calibrate a previously unseen workload without overlap. This avoids
        # carrying assumptions from one model processor or media shape to another.
        calibration_available = (
            not require_calibration
            or workload_key in self._calibrated_workloads
            or self._active == 0
        )
        memory_available = (
            free_mb is not None
            and free_mb - self._pending_reserved_mb >= self.config.gpu_headroom_mb + estimate_mb
        )
        return (
            worker_available and calibration_available and memory_available,
            estimate_mb,
            free_mb,
        )

    def _record_memory_pressure_locked(self, *, force: bool = False):
        if self._memory_pressure_active and not force:
            return
        self._effective_limit = max(
            self.config.min_workers,
            math.ceil(self._effective_limit / 2),
        )
        self._healthy_completions = 0
        self._memory_pressure_active = True
        self._backpressure_observed = False
        self._next_scale_up_at = self._clock() + self.config.scale_up_cooldown_seconds
        self._memory_pressure_events += 1

    def _has_scale_up_memory_margin_locked(self, admission: PreprocessAdmission) -> bool:
        free_mb = self._sample_free_memory_locked()
        if free_mb is None:
            return False
        # Keep one additional request estimate beyond the normal admission
        # reserve so a transient rebound cannot immediately reopen a worker.
        required_mb = self.config.gpu_headroom_mb + 2 * admission.estimated_mb
        return free_mb - self._pending_reserved_mb >= required_mb

    def _update_limit_after_release_locked(
        self,
        admission: PreprocessAdmission,
        *,
        success: bool,
        memory_pressure: bool,
    ) -> None:
        if memory_pressure:
            self._record_memory_pressure_locked(force=True)
            return
        if not success:
            self._healthy_completions = 0
            return

        now = self._clock()
        if self._memory_pressure_active:
            has_memory_margin = self._has_scale_up_memory_margin_locked(admission)
            if not has_memory_margin or now < self._next_scale_up_at:
                self._healthy_completions = 0
                return
            self._healthy_completions += 1
            if self._healthy_completions >= self.config.healthy_completions_for_increase:
                self._memory_pressure_active = False
                self._healthy_completions = 0
                self._next_scale_up_at = now + self.config.scale_up_cooldown_seconds
            return

        if not self._backpressure_observed or self._effective_limit >= self.config.max_workers:
            self._healthy_completions = 0
            return

        self._healthy_completions += 1
        required_completions = self.config.healthy_completions_for_increase * self._effective_limit
        if self._healthy_completions < required_completions or now < self._next_scale_up_at:
            return

        if (
            not self._has_scale_up_memory_margin_locked(admission)
            or not self._has_scale_up_gpu_headroom_locked()
        ):
            self._healthy_completions = 0
            self._next_scale_up_at = now + self.config.scale_up_cooldown_seconds
            return

        self._effective_limit += 1
        self._healthy_completions = 0
        self._backpressure_observed = False
        self._next_scale_up_at = now + self.config.scale_up_cooldown_seconds

    async def acquire(
        self,
        request_id: str,
        workload_key: str,
        payload_mb: int,
    ) -> PreprocessAdmission:
        if not workload_key:
            raise ValueError("workload_key must not be empty")
        if payload_mb < 1:
            raise ValueError("payload_mb must be greater than or equal to 1")
        loop = asyncio.get_running_loop()
        started_at = self._clock()
        deadline = loop.time() + self.config.admission_timeout_seconds
        queued = False

        try:
            while True:
                async with self._condition:
                    with self._state_lock:
                        if request_id in self._admissions:
                            raise ValueError(f"request {request_id} is already admitted")
                        would_admit, estimate_mb, free_mb = self._policy_decision_locked(
                            workload_key,
                            payload_mb,
                        )
                        if self.config.shadow_mode or would_admit:
                            if queued:
                                self._queued -= 1
                                queued = False
                            if self.config.shadow_mode and not would_admit:
                                self._shadow_denials += 1
                            if self._active:
                                self._overlapped_request_ids.update(self._admissions)
                                self._overlapped_request_ids.add(request_id)
                            self._active += 1
                            self._observed_peak_active = max(
                                self._observed_peak_active,
                                self._active,
                            )
                            self._pending_reserved_mb += estimate_mb
                            self._admission_count += 1
                            admission = PreprocessAdmission(
                                request_id=request_id,
                                workload_key=workload_key,
                                payload_mb=payload_mb,
                                estimated_mb=estimate_mb,
                                free_memory_before_mb=free_mb,
                                admitted_at=started_at,
                                enforced=not self.config.shadow_mode,
                                policy_would_admit=would_admit,
                                exclusive=self._active == 1,
                                wait_seconds=self._clock() - started_at,
                            )
                            self._admissions[request_id] = admission
                            if free_mb is not None:
                                self._minimum_free_mb_by_request[request_id] = free_mb
                            if self._wait_histogram is not None:
                                self._wait_histogram.record(
                                    admission.wait_seconds,
                                    {
                                        "enforced": str(admission.enforced).lower(),
                                        "policy_would_admit": str(would_admit).lower(),
                                    },
                                )
                            return admission

                        self._policy_denials += 1
                        if free_mb is None or (
                            free_mb - self._pending_reserved_mb
                            < self.config.gpu_headroom_mb + estimate_mb
                        ):
                            self._record_memory_pressure_locked()
                        if not queued:
                            self._queued += 1
                            queued = True

                    remaining = deadline - loop.time()
                    if remaining <= 0:
                        with self._state_lock:
                            if queued:
                                self._queued -= 1
                                queued = False
                            self._timeouts += 1
                        raise PreprocessAdmissionTimeout(
                            f"Timed out waiting for multimodal preprocessing admission for "
                            f"request {request_id}"
                        )
                    try:
                        await asyncio.wait_for(
                            self._condition.wait(),
                            timeout=min(remaining, self.config.poll_interval_seconds),
                        )
                    except asyncio.TimeoutError:
                        pass
        finally:
            if queued:
                async with self._condition:
                    with self._state_lock:
                        self._queued -= 1
                    self._condition.notify_all()

    async def release(
        self,
        admission: PreprocessAdmission,
        *,
        observed_allocation_mb: int | None = None,
        success: bool = True,
        memory_pressure: bool = False,
    ) -> None:
        async with self._condition:
            with self._state_lock:
                current = self._admissions.pop(admission.request_id, None)
                if current is None:
                    return
                minimum_free_mb = self._minimum_free_mb_by_request.pop(
                    admission.request_id,
                    None,
                )
                had_overlap = admission.request_id in self._overlapped_request_ids
                self._overlapped_request_ids.discard(admission.request_id)
                self._active -= 1
                self._pending_reserved_mb = max(
                    0,
                    self._pending_reserved_mb - current.estimated_mb,
                )

                if (
                    not had_overlap
                    and observed_allocation_mb is None
                    and current.free_memory_before_mb is not None
                ):
                    if minimum_free_mb is not None:
                        observed_allocation_mb = max(
                            0,
                            current.free_memory_before_mb - minimum_free_mb,
                        )

                if (
                    success
                    and not memory_pressure
                    and not had_overlap
                    and observed_allocation_mb is not None
                ):
                    observed_mb = max(
                        self.config.initial_estimated_request_mb,
                        current.payload_mb,
                        observed_allocation_mb,
                    )
                    previous = self._estimated_mb_by_workload.get(
                        current.workload_key,
                        max(self.config.initial_estimated_request_mb, current.payload_mb),
                    )
                    if observed_mb >= previous:
                        updated = observed_mb
                    else:
                        alpha = self.config.estimate_ewma_alpha
                        updated = math.ceil((1 - alpha) * previous + alpha * observed_mb)
                    self._estimated_mb_by_workload[current.workload_key] = updated
                    sample_count = (
                        self._calibration_samples_by_workload.get(current.workload_key, 0) + 1
                    )
                    self._calibration_samples_by_workload[current.workload_key] = sample_count
                    if sample_count >= self.config.calibration_samples_required:
                        self._calibrated_workloads.add(current.workload_key)

                self._update_limit_after_release_locked(
                    current,
                    success=success,
                    memory_pressure=memory_pressure,
                )
            self._condition.notify_all()

    def note_backpressure(self) -> None:
        """Record that upstream work is waiting on the effective worker limit."""
        with self._state_lock:
            self._backpressure_observed = True

    def sample_active_memory(self, request_id: str) -> None:
        """Record the lowest free-memory sample while one admission is active."""
        with self._state_lock:
            if request_id not in self._admissions:
                return
            free_mb = self._sample_free_memory_locked()
            if free_mb is None:
                return
            previous = self._minimum_free_mb_by_request.get(request_id, free_mb)
            self._minimum_free_mb_by_request[request_id] = min(previous, free_mb)

    def can_accept(
        self,
        workload_key: str = "unclassified",
        payload_mb: int = 1,
    ) -> bool:
        if not workload_key or payload_mb < 1:
            return False
        with self._state_lock:
            would_admit, _, _ = self._policy_decision_locked(
                workload_key,
                payload_mb,
                require_calibration=False,
            )
            return would_admit

    def queue_capacity(self) -> int:
        if self.config.shadow_mode:
            return 1
        with self._state_lock:
            return self._effective_limit

    def snapshot(self) -> AdaptivePreprocessSnapshot:
        with self._state_lock:
            return AdaptivePreprocessSnapshot(
                active=self._active,
                queued=self._queued,
                effective_limit=self._effective_limit,
                pending_reserved_mb=self._pending_reserved_mb,
                admissions=self._admission_count,
                policy_denials=self._policy_denials,
                shadow_denials=self._shadow_denials,
                timeouts=self._timeouts,
                memory_pressure_events=self._memory_pressure_events,
                memory_pressure_active=self._memory_pressure_active,
                backpressure_observed=self._backpressure_observed,
                healthy_completions=self._healthy_completions,
                scale_up_cooldown_remaining_seconds=max(
                    0.0,
                    self._next_scale_up_at - self._clock(),
                ),
                last_gpu_utilization_percent=self._last_gpu_utilization_percent,
                observed_peak_active=self._observed_peak_active,
                estimated_mb_by_workload=dict(self._estimated_mb_by_workload),
                calibrated_workloads=len(self._calibrated_workloads),
                last_free_memory_mb=self._last_free_memory_mb,
            )

    def set_effective_limit_for_test(self, value: int) -> None:
        if not self.config.min_workers <= value <= self.config.max_workers:
            raise ValueError("test limit must be within the configured worker range")
        with self._state_lock:
            self._effective_limit = value
