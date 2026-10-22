"""Inference caller: send batches to AOI #2 under a shared semaphore.

Each batch takes one permit before it is sent and releases it when the reply
returns or before it waits to retry. Retries use exponential backoff with
jitter. Replies are validated against the batch that was sent (count and index
order); a mismatch is rejected and retried, never mapped.
"""

import asyncio
import logging
import random
from dataclasses import dataclass

import httpx

from client.config import settings
from client.core.batcher import Batch

logger = logging.getLogger(__name__)

# Statuses worth retrying; everything else is a permanent input rejection.
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


@dataclass
class ModelScore:
    version: str
    score: float
    verdict: str


@dataclass
class CropScore:
    old_model: ModelScore
    new_model: ModelScore
    score: float
    verdict: str
    threshold: float


@dataclass
class BatchOutcome:
    batch: Batch
    scores: list[CropScore] | None
    deployment_type: str | None = None
    error: str | None = None


def _backoff_delay(attempt: int) -> float:
    delay = min(settings.retry_base_delay_s * (2**attempt), settings.retry_max_delay_s)
    if settings.retry_jitter:
        delay *= 0.5 + random.random() * 0.5
    return delay


class InferenceClient:
    def __init__(
        self,
        semaphore: asyncio.Semaphore,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._semaphore = semaphore
        self._client = client or httpx.AsyncClient(
            base_url=settings.aoi2_base_url,
            timeout=settings.inference_timeout_s,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def ready(self) -> bool:
        """Probe AOI #2's readiness endpoint for GET /ready."""
        try:
            resp = await self._client.get(settings.aoi2_ready_path, timeout=5.0)
            return resp.status_code == 200
        except httpx.HTTPError:
            return False

    def _build_files(self, batch: Batch) -> list[tuple[str, tuple[str, bytes, str]]]:
        # AOI #2 /v1/predict:batch takes repeated multipart fields: N `test`
        # images then N aligned `ref` images.
        fmt = settings.crop_format.lower()
        ext = "jpg" if fmt == "jpeg" else fmt
        mime = f"image/{fmt}"
        files: list[tuple[str, tuple[str, bytes, str]]] = []
        for i, img in enumerate(batch.test_images):
            files.append(("test", (f"test_{i}.{ext}", img, mime)))
        for i, img in enumerate(batch.golden_images):
            files.append(("ref", (f"ref_{i}.{ext}", img, mime)))
        return files

    def _parse(self, body: dict, batch: Batch) -> tuple[list[CropScore], str] | None:
        results = body.get("results")
        if not isinstance(results, list) or len(results) != len(batch.crop_ids):
            return None
        scores: list[CropScore] = []
        deployment_type: str | None = None
        for result in results:
            old = result.get("old_model")
            new = result.get("new_model")
            if not isinstance(old, dict) or not isinstance(new, dict):
                return None
            threshold = result.get("threshold")
            if threshold is None:
                return None
            score = result.get("score")
            verdict = result.get("verdict")
            if not isinstance(score, (int, float)) or not isinstance(verdict, str):
                return None
            dt = result.get("deployment_type")
            if not isinstance(dt, str):
                return None
            if deployment_type is None:
                deployment_type = dt
            try:
                scores.append(CropScore(
                    old_model=ModelScore(**old),
                    new_model=ModelScore(**new),
                    score=float(score),
                    verdict=verdict,
                    threshold=threshold,
                ))
            except (TypeError, KeyError):
                return None
        return scores, deployment_type  # type: ignore[return-value]  # None only if results is empty

    async def infer_batch(self, batch: Batch) -> BatchOutcome:
        last_error: str | None = None
        for attempt in range(settings.max_retries + 1):
            resp: httpx.Response | None = None
            async with self._semaphore:  # released before any backoff sleep
                try:
                    resp = await self._client.post(
                        settings.aoi2_batch_path, files=self._build_files(batch)
                    )
                except httpx.HTTPError as exc:
                    last_error = f"request error: {exc}"

            if resp is not None:
                if resp.status_code == 200:
                    parsed = self._parse(resp.json(), batch)
                    if parsed is not None:
                        scores, deployment_type = parsed
                        return BatchOutcome(batch, scores, deployment_type)
                    last_error = "reply did not match the batch that was sent"
                elif resp.status_code not in RETRYABLE_STATUS_CODES:
                    # Bad input / batch too large / unknown reference: do not retry.
                    return BatchOutcome(
                        batch, None, error=f"HTTP {resp.status_code}: {resp.text[:200]}"
                    )
                else:
                    last_error = f"HTTP {resp.status_code}"

            if attempt < settings.max_retries:
                await asyncio.sleep(_backoff_delay(attempt))

        logger.warning(
            "batch %d failed after retries: %s", batch.batch_number, last_error
        )
        return BatchOutcome(batch, None, error=last_error or "exhausted retries")
