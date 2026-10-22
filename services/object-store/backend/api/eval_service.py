"""Eval-set planning + fan-out — BE side (design Steps 0-2), track 11.2/11.4.

POST /v1/results resolves a server-generated eval_id, opens ONE shared PIT,
compiles the selection into an ES query, writes an initial status.json, and
publishes one work item per slice to the eval-slices topic (slice i -> partition
i). The sliced search_after export runs in the consumer group (11.3), not here.

Response shapes follow the agreed AOI Storage Backend API contract
(shaunakg/aoi-api): CreateEvaluationSetResponse + EvaluationSetStats.
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from backend.api.eval_schemas import EvalRequest
from backend.api.snapshot_service import SORT_SPEC
from backend.config import settings
from backend.shared import eval_layout
from backend.shared.elasticsearch import ElasticsearchService
from backend.shared.kafka import KafkaService
from backend.shared.storage import StorageService
from deft_common import Status

logger = logging.getLogger(__name__)

# The job lifecycle uses the contract SetStatus values directly (creating while
# exporting, then ready/failed), so no API-boundary mapping is needed.
_TIMESTAMP_FIELD = "metadata.content_metadata.timestamp"


class EvalFanoutError(Exception):
    """One or more slice work items could not be published; the eval job cannot
    proceed and its PIT has been closed. The caller should retry."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


_CROP_INFO_PATH = "metadata.content_metadata.cropInfo"


def _crop_model_match(model: str, version: str) -> dict[str, Any]:
    """Match records that have a crop whose old_model OR new_model == (model, version).

    cropInfo is a `nested` array in aoi-inspection-template.json — each element
    cropInfo[i] is indexed as its own hidden document — so the whole match runs
    inside ONE nested query on the field. That scopes every term to the SAME
    element i: model_name and version must come from the same crop's same model
    slot, and the old_model/new_model OR is evaluated per crop (a crop[i] matches
    if its own old_model or its own new_model matches), not across the array.

    NB the field lives at metadata.content_metadata.cropInfo (nested inside
    content_metadata), so both the nested `path` and the term fields use that
    full prefix — a bare `cropInfo` path throws "failed to find nested object".
    """
    def slot(prefix: str) -> dict[str, Any]:
        return {
            "bool": {
                "filter": [
                    {"term": {f"{_CROP_INFO_PATH}.inference.{prefix}.model_name": model}},
                    {"term": {f"{_CROP_INFO_PATH}.inference.{prefix}.version": version}},
                ]
            }
        }

    return {
        "nested": {
            "path": _CROP_INFO_PATH,
            "query": {
                "bool": {
                    "should": [slot("old_model"), slot("new_model")],
                    "minimum_should_match": 1,
                }
            },
        }
    }


def compile_eval_selection(selection: dict[str, Any]) -> dict[str, Any]:
    """Contract EvaluationSetSelection -> ES bool query.

    model/version match a single crop (cropInfo[i]) whose old_model OR new_model
    equals (model, version); role is an exact-match term filter; from/to bound
    the record timestamp (gte / lt).
    """
    filters: list[dict[str, Any]] = [
        _crop_model_match(selection["model"], selection["version"]),
        {"term": {"metadata.content_metadata.inferenceSummary.deployment_type": selection["role"]}},
    ]
    rng: dict[str, Any] = {}
    if selection.get("from") is not None:
        rng["gte"] = selection["from"]
    if selection.get("to") is not None:
        rng["lt"] = selection["to"]
    if rng:
        filters.append({"range": {_TIMESTAMP_FIELD: rng}})
    return {"bool": {"filter": filters}}


def build_work_item(
    eval_id: str, pit_id: str, query: dict[str, Any], selection: dict[str, Any],
    slice_id: int, n: int, request: EvalRequest,
) -> dict[str, Any]:
    return {
        "eval_id": eval_id,
        "pit_id": pit_id,
        "query": query,
        "selection": selection,
        "slice": {"id": slice_id, "max": n},
        "sort": SORT_SPEC,
        "keep_alive": settings.pit_keep_alive,
        "results_topic": settings.eval_results_topic,
        "n": n,
        "reason": request.reason,
        "metadata": request.metadata,
    }


async def _write_status(storage: StorageService, status: dict[str, Any]) -> None:
    await storage.put_object(
        eval_layout.status_key(status["eval_id"]),
        json.dumps(status).encode(),
        content_type="application/json",
    )


async def create_eval(
    es: ElasticsearchService,
    kafka: KafkaService,
    storage: StorageService,
    request: EvalRequest,
) -> str:
    """Mint an eval_id, open the shared PIT, write status, and fan out N slices."""
    eval_id = uuid.uuid4().hex
    n = settings.eval_partitions
    selection = request.selection.model_dump(by_alias=True)
    query = compile_eval_selection(selection)

    # One PIT for the whole job — every slice reads the same frozen view (§10.1).
    pit_id = await es.open_pit()
    # Any failure after the PIT is open must close it, or it leaks until keep_alive.
    try:
        status = {
            "eval_id": eval_id,
            "status": Status.CREATING,
            "n": n,
            "completed_slices": 0,
            "failed_slices": 0,
            "reason": request.reason,
            "metadata": request.metadata,
            "created_at": _now(),
        }
        await _write_status(storage, status)

        # Fan out sequentially so a mid-batch Kafka failure leaves at most one
        # orphan slice message. Explicit partition=i so slice i lands on partition i.
        failed: list[tuple[int, Exception]] = []
        for i in range(n):
            try:
                await kafka.send(
                    build_work_item(eval_id, pit_id, query, selection, i, n, request),
                    topic=settings.eval_slices_topic,
                    key=str(i),
                    partition=i,
                )
            except Exception as exc:  # noqa: BLE001
                failed.append((i, exc))
                break

        if failed:
            for i, exc in failed:
                logger.error("eval %s: slice %d publish failed: %s", eval_id, i, exc)
            status["status"] = Status.FAILED
            status["error"] = f"{len(failed)}/{n} slice publishes failed"
            # Mark failed before closing PIT so slice consumers skip export promptly.
            try:
                await _write_status(storage, status)
            except Exception as st_exc:  # noqa: BLE001
                logger.error("eval %s: status write failed during cleanup: %s", eval_id, st_exc)
            await _close_pit_quietly(es, pit_id, eval_id)
            raise EvalFanoutError(f"eval {eval_id}: {len(failed)}/{n} slice publishes failed")
    except EvalFanoutError:
        raise  # its PIT was already closed above
    except Exception:
        # e.g. status.json PUT failed — close the PIT so it doesn't orphan.
        logger.exception("eval %s: create failed after PIT open; closing PIT", eval_id)
        await _close_pit_quietly(es, pit_id, eval_id)
        raise

    return eval_id


async def _close_pit_quietly(es: ElasticsearchService, pit_id: str, eval_id: str) -> None:
    try:
        await es.close_pit(pit_id)
    except Exception as exc:  # noqa: BLE001
        logger.error("eval %s: PIT close failed: %s", eval_id, exc)


async def get_eval_status(
    storage: StorageService, eval_id: str
) -> dict[str, Any] | None:
    """Return EvaluationSetStats (contract) for eval_id, or None if unknown.

    status.json holds the lifecycle + created_at; eval.json (written on
    finalise) holds total_records + the manifest list. Before finalisation the
    job is "creating" with 0 records and no manifests.
    """
    raw = await storage.get_object(eval_layout.status_key(eval_id))
    if raw is None:
        return None
    status: dict[str, Any] = json.loads(raw)
    summary_raw = await storage.get_object(eval_layout.summary_key(eval_id))
    summary: dict[str, Any] = json.loads(summary_raw) if summary_raw else {}
    return {
        "eval_id": eval_id,
        "status": status.get("status", Status.CREATING),
        "total_records": summary.get("total_records", 0),
        "manifests": summary.get("manifests", []),
        "created_at": status.get("created_at"),
    }
