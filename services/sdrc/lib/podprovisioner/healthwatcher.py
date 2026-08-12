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

"""Background HTTP health polling for workload pods.

Polls each pod's configurable health-check URL on a fixed interval and tracks
per-pod healthy/unhealthy state. Used to:

- Gate stream assignment to healthy pods only
- Drive PodErrorWatcher transitions (especially Docker, replacing socket status)
- Wait for readiness before issuing /add calls
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Callable, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)


class WorkloadUnhealthyError(RuntimeError):
    """The target workload pod failed its immediate pre-add health probe."""


def build_health_url(pod_info: dict, health_path: str) -> str:
    """Build health URL from docker/K8s inventory + configurable path only.

    Prefer ``provisioning_address`` from ``docker_cluster_config.json``
    (``host:port``). Otherwise fall back to ``podIp``/``podPort`` from the
    cluster inventory. Only ``health_path`` (``WDM_WL_HEALTH_CHECK_URL``) is
    configurable — host and port always come from pod inventory.
    """
    path = health_path if str(health_path).startswith("/") else f"/{health_path}"
    provisioning_address = pod_info.get("provisioning_address")
    if provisioning_address is not None and str(provisioning_address).strip():
        return f"http://{str(provisioning_address).strip()}{path}"

    host = (
        pod_info.get("podIp")
        or pod_info.get("poddns")
        or pod_info.get("podName")
        or ""
    )
    port = pod_info.get("podPort")
    if port is None or str(port).strip() == "":
        return f"http://{host}{path}"
    return f"http://{host}:{port}{path}"


def probe_pod_health(
    pod_info: dict,
    health_path: str,
    timeout: float,
) -> bool:
    """Return True only when GET on the pod health endpoint returns HTTP 200."""
    url = build_health_url(pod_info, health_path)
    try:
        response = requests.get(url, timeout=timeout)
    except requests.RequestException as exc:
        logger.debug(
            "Health probe failed for pod=%s url=%s: %s",
            pod_info.get("podName"),
            url,
            exc,
        )
        return False

    return response.status_code == 200


class WorkloadHealthWatcher:
    """Poll workload pods and publish healthy/unhealthy transitions."""

    def __init__(
        self,
        app_config: dict,
        resolve_pods: Callable[[], List[dict]],
        logger_override=None,
    ):
        self.app_config = app_config
        self.resolve_pods = resolve_pods
        self.log = logger_override or logger

        self._lock = threading.RLock()
        # podName -> bool; missing means not yet probed (treated as unhealthy)
        self._pod_healthy: Dict[str, bool] = {}
        self._pod_info: Dict[str, dict] = {}
        self._events: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._started = False

    @property
    def health_path(self) -> str:
        return (
            self.app_config.get("WDM_WL_HEALTH_CHECK_URL")
            or "/api/v1/stream/add"
        )

    @property
    def interval(self) -> float:
        return float(
            self.app_config.get("WDM_HEALTH_CHECK_INTERVAL", 2.0)
        )

    @property
    def timeout(self) -> float:
        return float(
            self.app_config.get("WDM_HEALTH_CHECK_TIMEOUT", 2.0)
        )

    def start(self) -> bool:
        """Start the background polling thread (idempotent)."""
        with self._lock:
            if self._started and self._thread is not None and self._thread.is_alive():
                return True
            self._stop.clear()
            # Warm the inventory immediately so assignment / ifPodDown do not
            # treat every pod as unknown until the first background tick.
            try:
                self.poll_once()
            except Exception:
                self.log.exception("Initial workload health poll failed")
            self._thread = threading.Thread(
                target=self._poll_loop,
                name="workload-health-watcher",
                daemon=True,
            )
            self._thread.start()
            self._started = True
            self.log.info(
                "Workload health watcher started (path=%s interval=%ss timeout=%ss)",
                self.health_path,
                self.interval,
                self.timeout,
            )
            return True

    def stop(self, join_timeout: float = 2.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=join_timeout)
        self._started = False

    def is_pod_healthy(self, pod_name: str) -> bool:
        """Return True only when the latest probe marked the pod healthy."""
        with self._lock:
            return bool(self._pod_healthy.get(pod_name, False))

    def is_pod_known(self, pod_name: str) -> bool:
        with self._lock:
            return pod_name in self._pod_healthy

    def healthy_count(self) -> int:
        with self._lock:
            return sum(1 for ok in self._pod_healthy.values() if ok)

    def unhealthy_pod_names(self) -> List[str]:
        with self._lock:
            return [name for name, ok in self._pod_healthy.items() if not ok]

    def healthy_pod_names(self) -> List[str]:
        with self._lock:
            return [name for name, ok in self._pod_healthy.items() if ok]

    def snapshot(self) -> Dict[str, bool]:
        with self._lock:
            return dict(self._pod_healthy)

    def check_pod(self, pod_info: dict) -> bool:
        """One-shot probe; also updates tracked state and may emit a transition."""
        healthy = probe_pod_health(
            pod_info,
            self.health_path,
            self.timeout,
        )
        self._apply_result(pod_info, healthy)
        return healthy

    def wait_until_healthy(
        self,
        pod_info: dict,
        timeout_sec: Optional[float] = None,
        poll_interval: Optional[float] = None,
    ) -> bool:
        """Block until ``pod_info`` is healthy or ``timeout_sec`` elapses.

        ``timeout_sec == -1`` waits forever. ``None`` uses
        ``WDM_ADD_HEALTH_CHECK_TIMEOUT`` when set, otherwise
        ``WDM_API_WAIT_MAX_RETRIES_IN_SEC`` (default 30). ``0`` means a
        single probe with no additional wait.
        """
        if timeout_sec is None:
            if "WDM_ADD_HEALTH_CHECK_TIMEOUT" in self.app_config:
                timeout_sec = float(
                    self.app_config.get("WDM_ADD_HEALTH_CHECK_TIMEOUT")
                )
            else:
                timeout_sec = float(
                    self.app_config.get("WDM_API_WAIT_MAX_RETRIES_IN_SEC", 30)
                )
        if poll_interval is None:
            poll_interval = min(self.interval, 1.0)

        pod_name = pod_info.get("podName")
        timeout_val = float(timeout_sec)
        forever = timeout_val == -1
        deadline = None if forever else time.time() + max(0.0, timeout_val)
        while forever or time.time() <= deadline:
            if self.check_pod(pod_info):
                self.log.info(
                    "Pod %s health check passed (%s)",
                    pod_name,
                    build_health_url(pod_info, self.health_path),
                )
                return True
            if forever:
                time.sleep(poll_interval)
                continue
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            time.sleep(min(poll_interval, remaining))

        self.log.info(
            "Pod %s health check did not become ready within %ss",
            pod_name,
            timeout_sec,
        )
        return False

    def wait_until_any_healthy(
        self,
        timeout_sec: Optional[float] = None,
        poll_interval: Optional[float] = None,
    ) -> bool:
        """Block until at least one inventoried pod is healthy."""
        if timeout_sec is None:
            timeout_sec = float(
                self.app_config.get("WDM_API_WAIT_MAX_RETRIES_IN_SEC", 30)
            )
        if poll_interval is None:
            poll_interval = min(self.interval, 1.0)

        deadline = time.time() + max(0.0, float(timeout_sec))
        while time.time() <= deadline:
            self.poll_once()
            if self.healthy_count() > 0:
                return True
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            time.sleep(min(poll_interval, remaining))
        return False

    def iter_transitions(self):
        """Blocking generator of ``(is_down, pod_name, generate_name)`` events.

        Compatible with ``PodErrorWatcher`` / ``watchPodState`` consumers.
        """
        while not self._stop.is_set():
            try:
                event = self._events.get(timeout=0.5)
            except queue.Empty:
                continue
            yield event

    def poll_once(self) -> Dict[str, bool]:
        """Probe all currently resolved pods once and return the snapshot."""
        try:
            pods = self.resolve_pods() or []
        except Exception:
            self.log.exception("Failed resolving pods for health poll")
            pods = []

        seen = set()
        for pod in pods:
            pod_name = pod.get("podName")
            if not pod_name:
                continue
            seen.add(pod_name)
            healthy = probe_pod_health(
                pod,
                self.health_path,
                self.timeout,
            )
            self._apply_result(pod, healthy)

        # Pods that disappeared from inventory are treated as down.
        with self._lock:
            missing = [name for name in list(self._pod_healthy) if name not in seen]
        for name in missing:
            stale = {"podName": name}
            with self._lock:
                stale = self._pod_info.get(name, stale)
            self._apply_result(stale, False)
            with self._lock:
                self._pod_healthy.pop(name, None)
                self._pod_info.pop(name, None)

        return self.snapshot()

    def _poll_loop(self) -> None:
        self.log.info("Workload health poll loop running")
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception:
                self.log.exception("Unhandled error in workload health poll")
            self._stop.wait(self.interval)
        self.log.info("Workload health poll loop stopped")

    def _apply_result(self, pod_info: dict, healthy: bool) -> None:
        pod_name = pod_info.get("podName")
        if not pod_name:
            return

        with self._lock:
            previous = self._pod_healthy.get(pod_name)
            self._pod_healthy[pod_name] = healthy
            self._pod_info[pod_name] = dict(pod_info)

        if previous is None:
            # First observation: emit down when starting unhealthy so watchers
            # and downpodsArray stay aligned before any add/assignment.
            if not healthy:
                self._emit(True, pod_name)
            return

        if previous and not healthy:
            self.log.info("Pod %s marked unhealthy by health check", pod_name)
            self._emit(True, pod_name)
        elif (not previous) and healthy:
            self.log.info("Pod %s recovered (health check passed)", pod_name)
            self._emit(False, pod_name)

    def _emit(self, is_down: bool, pod_name: str) -> None:
        # generate_name mirrors docker watchPodState (pod name used for both).
        self._events.put((is_down, pod_name, pod_name))
