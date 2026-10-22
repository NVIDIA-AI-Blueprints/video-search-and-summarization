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
    model_name: str | None = None


@dataclass
class CropScore:
    old_model: ModelScore | None
    new_model: ModelScore | None
    score: float
    verdict: str
    threshold: float


@dataclass
class BatchOutcome:
    batch: Batch
    scores: list[CropScore] | None
    deployment_type: str | None = None
    model_name: str | None = None
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

        old_block = body.get("old_model")
        new_block = body.get("new_model")
        if old_block is not None and (not isinstance(old_block, dict) or not old_block):
            return None
        if new_block is not None and (not isinstance(new_block, dict) or not new_block):
            return None

        threshold = body.get("threshold")
        if threshold is None:
            return None

        deployment_type = body.get("deployment_type")
        if not isinstance(deployment_type, str):
            return None

        old_version = old_block.get("version") if old_block else None
        new_version = new_block.get("version") if new_block else None
        if old_block and not isinstance(old_version, str):
            return None
        if new_block and not isinstance(new_version, str):
            return None
        old_model_name = old_block.get("model_name") if old_block else None
        new_model_name = new_block.get("model_name") if new_block else None

        old_results = old_block.get("results") if old_block else None
        new_results = new_block.get("results") if new_block else None
        if old_block and old_results is None:
            return None
        if new_block and new_results is None:
            return None
        if old_results is not None and (not isinstance(old_results, list) or len(old_results) != len(results)):
            return None
        if new_results is not None and (not isinstance(new_results, list) or len(new_results) != len(results)):
            return None

        scores: list[CropScore] = []
        for i, primary in enumerate(results):
            score = primary.get("score")
            verdict = primary.get("verdict")
            if not isinstance(score, (int, float)) or not isinstance(verdict, str):
                return None
            old_r = old_results[i] if old_results else {}
            new_r = new_results[i] if new_results else {}
            old_score = old_r.get("score")
            old_verdict = old_r.get("verdict")
            new_score = new_r.get("score")
            new_verdict = new_r.get("verdict")
            if old_results and (not isinstance(old_score, (int, float)) or not isinstance(old_verdict, str)):
                return None
            if new_results and (not isinstance(new_score, (int, float)) or not isinstance(new_verdict, str)):
                return None
            scores.append(CropScore(
                old_model=ModelScore(version=old_version or "", score=float(old_score or 0), verdict=old_verdict or "", model_name=old_model_name) if old_block else None,
                new_model=ModelScore(version=new_version or "", score=float(new_score or 0), verdict=new_verdict or "", model_name=new_model_name) if new_block else None,
                score=float(score),
                verdict=verdict,
                threshold=threshold,
            ))
        model_name = body.get("model_name")
        return scores, deployment_type, model_name  # type: ignore[return-value]  # None only if results is empty

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
                        scores, deployment_type, model_name = parsed
                        return BatchOutcome(batch, scores, deployment_type, model_name)
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
