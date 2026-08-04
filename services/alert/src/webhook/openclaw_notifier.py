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

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)


class OpenClawNotifier:
    """Fire-and-forget webhook notifier for VLM-verified incidents.

    Reads ``webhook.openclaw`` from the Alert Bridge config dict.
    When disabled (the default) every public method is a no-op.
    """

    def __init__(self, config: Dict[str, Any]) -> None:
        cfg = (config.get("webhook") or {}).get("openclaw") or {}
        self._enabled: bool = bool(cfg.get("enabled", False))
        self._url: str = str(cfg.get("url", "") or "")
        self._topic: str = str(cfg.get("topic", "") or "")
        self._timeout: int = int(cfg.get("timeout_seconds", 5))

        self._pool: ThreadPoolExecutor | None = None
        if not self._enabled:
            return

        parsed = urlparse(self._url)
        if parsed.scheme not in ("http", "https"):
            raise ValueError(
                "webhook.openclaw is enabled but 'url' is missing or uses "
                "an unsupported scheme; provide an http:// or https:// URL"
            )
        if not parsed.hostname:
            raise ValueError(
                "webhook.openclaw is enabled but 'url' has no host; "
                "provide a full URL like https://host/path"
            )

        max_pending = int(cfg.get("max_pending", 100))
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="openclaw")
        self._backpressure = threading.BoundedSemaphore(max_pending)
        safe_origin = f"{parsed.scheme}://{parsed.hostname}"
        if parsed.port:
            safe_origin += f":{parsed.port}"
        logger.info(
            "OpenClaw webhook enabled → %s (topic=%s)",
            safe_origin,
            self._topic or "<any>",
        )

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def topic(self) -> str:
        return self._topic

    def notify(self, incident: Dict[str, Any]) -> None:
        """Submit the webhook POST to a background thread.

        The caller (main enhancer loop) returns immediately; delivery
        happens asynchronously so a slow/unreachable endpoint never
        stalls anomaly ingestion.  At most ``max_pending`` (config)
        submissions may be queued; excess incidents are dropped so a
        slow or unreachable receiver cannot exhaust process memory.
        """
        if not self._enabled:
            return
        if not self._backpressure.acquire(blocking=False):
            logger.warning("OpenClaw webhook backpressure: dropping incident")
            return
        try:
            self._pool.submit(self._safe_deliver, incident)  # type: ignore[union-attr]
        except RuntimeError:
            self._backpressure.release()

    def _safe_deliver(self, incident: Dict[str, Any]) -> None:
        """Wrapper that guarantees the backpressure semaphore is released."""
        try:
            self._deliver(incident)
        finally:
            self._backpressure.release()

    def _deliver(self, incident: Dict[str, Any]) -> None:
        """Synchronous POST executed in the thread pool."""
        sensor_id = incident.get("sensorId", "N/A")
        category = incident.get("category", "N/A")

        try:
            resp = requests.post(
                self._url,
                json=incident,
                timeout=self._timeout,
            )
            if resp.ok:
                logger.info(
                    "OpenClaw webhook sent [sensor=%s category=%s] status=%d",
                    sensor_id,
                    category,
                    resp.status_code,
                )
            else:
                logger.warning(
                    "OpenClaw webhook failed [sensor=%s category=%s] status=%d",
                    sensor_id,
                    category,
                    resp.status_code,
                )
        except Exception as exc:
            logger.warning(
                "OpenClaw webhook failed [sensor=%s category=%s]: %s",
                sensor_id,
                category,
                exc,
            )

    def close(self) -> None:
        """Shut down the thread pool, waiting for in-flight deliveries."""
        if self._pool is not None:
            self._pool.shutdown(wait=True)
            self._pool = None
