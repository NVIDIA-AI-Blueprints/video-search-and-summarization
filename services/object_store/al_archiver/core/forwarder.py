"""Forwarder: POST each inference-result record to the BE with retry/backoff."""

import asyncio
import logging
import random
from typing import Any

import httpx

from al_archiver.config import settings

logger = logging.getLogger(__name__)

RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


def _backoff_delay(attempt: int) -> float:
    delay = min(settings.retry_base_delay_s * (2**attempt), settings.retry_max_delay_s)
    if settings.retry_jitter:
        delay *= 0.5 + random.random() * 0.5
    return delay


class Forwarder:
    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client or httpx.AsyncClient(
            base_url=settings.be_base_url,
            timeout=settings.forward_timeout_s,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def forward(self, record: dict[str, Any]) -> bool:
        """POST one record to the BE; return True once accepted."""
        last_error: str | None = None
        for attempt in range(settings.max_retries + 1):
            try:
                resp = await self._client.post(
                    settings.be_inference_result_path, json=record
                )
                if resp.status_code < 300:
                    return True
                if resp.status_code not in RETRYABLE_STATUS_CODES:
                    logger.error(
                        "BE rejected record %s: HTTP %d %s",
                        record.get("objStorePath"),
                        resp.status_code,
                        resp.text[:200],
                    )
                    return False
                last_error = f"HTTP {resp.status_code}"
            except httpx.HTTPError as exc:
                last_error = f"request error: {exc}"

            if attempt < settings.max_retries:
                await asyncio.sleep(_backoff_delay(attempt))

        logger.error(
            "failed to forward record %s after retries: %s",
            record.get("objStorePath"),
            last_error,
        )
        return False
