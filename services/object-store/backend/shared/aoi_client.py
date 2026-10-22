"""HTTP client for AOI Client POST /api/inference (Workflow 2 outbound)."""

import asyncio
import logging
import random
from typing import Any

import httpx

from backend.config import settings

logger = logging.getLogger(__name__)

RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
NON_RETRYABLE_STATUS_CODES = {413, 422}


def _backoff_delay(attempt: int) -> float:
    delay = min(
        settings.aoi_client_retry_base_delay_s * (2**attempt),
        settings.aoi_client_retry_max_delay_s,
    )
    return delay * (0.5 + random.random() * 0.5)


class AoiClientService:
    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client or httpx.AsyncClient(
            base_url=settings.aoi_client_base_url,
            timeout=settings.aoi_client_timeout_s,
        )
        # Fire-and-forget: keep strong refs (asyncio docs pattern); discard is O(1).
        self._tasks: set[asyncio.Task[None]] = set()

    async def close(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        await self._client.aclose()

    def schedule_trigger(self, payload: dict[str, Any], obj_store_path: str) -> None:
        task = asyncio.create_task(self._trigger(payload, obj_store_path))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _trigger(self, payload: dict[str, Any], obj_store_path: str) -> bool:
        last_error: str | None = None
        for attempt in range(settings.aoi_client_max_retries + 1):
            try:
                resp = await self._client.post("/api/inference", json=payload)
                if resp.status_code == 202:
                    try:
                        crop_count = resp.json().get("cropCount")
                    except ValueError:
                        crop_count = None  # 202 with empty/non-JSON body
                    logger.info(
                        "AOI Client accepted inference for %s (cropCount=%s)",
                        obj_store_path,
                        crop_count,
                    )
                    return True
                if resp.status_code in NON_RETRYABLE_STATUS_CODES:
                    logger.error(
                        "AOI Client rejected inference for %s: HTTP %d %s",
                        obj_store_path,
                        resp.status_code,
                        resp.text[:200],
                    )
                    return False
                if resp.status_code not in RETRYABLE_STATUS_CODES:
                    logger.error(
                        "AOI Client unexpected response for %s: HTTP %d %s",
                        obj_store_path,
                        resp.status_code,
                        resp.text[:200],
                    )
                    return False
                last_error = f"HTTP {resp.status_code}"
            except httpx.HTTPError as exc:
                last_error = f"request error: {exc}"

            if attempt < settings.aoi_client_max_retries:
                await asyncio.sleep(_backoff_delay(attempt))

        logger.error(
            "AOI Client trigger failed for %s after retries: %s",
            obj_store_path,
            last_error,
        )
        return False
