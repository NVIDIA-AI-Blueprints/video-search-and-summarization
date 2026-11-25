import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager
from functools import partial
from typing import Any, AsyncGenerator

from al_archiver.core.sc_consumer import ScFrameConsumer
from fastapi import FastAPI

from backend.api.eval_router import router as eval_router
from backend.api.inference_router import router as inference_router
from backend.api.kpi_router import router as kpi_router
from backend.api.presign_router import router as presign_router
from backend.api.router import router
from backend.api.sc_gc import gc_loop
from backend.api.sc_lifecycle import sync_lifecycle_rule
from backend.api.sc_service import archive_frame
from backend.api.snapshot_router import router as snapshot_router
from backend.config import settings
from backend.shared.aoi_client import AoiClientService
from backend.shared.elasticsearch import ElasticsearchService
from backend.shared.kafka import KafkaService
from backend.shared.storage import StorageService

logger = logging.getLogger(__name__)


async def _archive(app: FastAPI, frame: dict[str, Any]) -> None:
    """Bind the archival handler to this app's storage and Kafka services."""
    await archive_frame(app.state.storage, app.state.kafka, frame)


async def _sync_lifecycle(storage: StorageService) -> None:
    """Every replica syncs the rule; the result is the same. A failure leaves
    the bucket's lifecycle as it was."""
    try:
        outcome = await sync_lifecycle_rule(storage)
    except Exception:  # noqa: BLE001 — the backstop must never take the app down
        logger.exception("ITS lifecycle rule sync failed")
        return
    # WARNING for a change, like the retention pass's report: the app sets up no
    # logging, so INFO from here never reaches the container log.
    if outcome.startswith("refused"):
        logger.error("ITS lifecycle rule %s", outcome)
    elif outcome.startswith(("set", "removed")):
        logger.warning("ITS lifecycle rule %s", outcome)
    elif outcome != "off":
        logger.info("ITS lifecycle rule %s", outcome)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    app.state.storage = StorageService()
    app.state.kafka = KafkaService()
    app.state.aoi_client = AoiClientService()
    app.state.elasticsearch = ElasticsearchService()
    await app.state.kafka.start()

    # The Smart City archival consumer runs inside the backend rather than as a
    # separate service, so it starts and stops with the app. Backend replicas
    # share one consumer group, so archival throughput scales up to the
    # partition count of selected-frames; producers key by sensorId, which keeps
    # a sensor's frames on one partition and therefore in order.
    app.state.sc_consumer = None
    if settings.sc_consumer_enabled:
        app.state.sc_consumer = ScFrameConsumer(
            bootstrap_servers=settings.kafka_bootstrap_servers,
            topic=settings.sc_frames_topic,
            group_id=settings.sc_consumer_group,
            handler=partial(_archive, app),
            max_partition_fetch_bytes=settings.sc_max_partition_fetch_bytes,
        )
        await app.state.sc_consumer.start()

    # The bucket lifecycle rule that backs up the retention pass, synced in the
    # background so startup never waits on the object store. A backend that
    # archives nothing for ITS (AOI only) leaves the bucket's lifecycle alone.
    app.state.sc_lifecycle_task = None
    if settings.sc_lifecycle_enabled or settings.sc_consumer_enabled or settings.sc_gc_enabled:
        app.state.sc_lifecycle_task = asyncio.create_task(
            _sync_lifecycle(app.state.storage), name="sc-lifecycle"
        )

    # Retention for archived Smart City frames. Deletions are not coordinated
    # between replicas, so enable it on one backend only.
    app.state.sc_gc_task = None
    if settings.sc_gc_enabled:
        app.state.sc_gc_task = asyncio.create_task(
            gc_loop(app.state.elasticsearch, app.state.storage), name="sc-gc"
        )

    yield

    if app.state.sc_lifecycle_task is not None:
        app.state.sc_lifecycle_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await app.state.sc_lifecycle_task
    if app.state.sc_gc_task is not None:
        app.state.sc_gc_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await app.state.sc_gc_task
    if app.state.sc_consumer is not None:
        await app.state.sc_consumer.stop()
    await app.state.kafka.stop()
    await app.state.aoi_client.close()
    await app.state.elasticsearch.close()


def create_app() -> FastAPI:
    app = FastAPI(title="DEFT AOI Object Store", lifespan=lifespan)
    app.include_router(router)
    app.include_router(inference_router)
    app.include_router(snapshot_router)
    app.include_router(presign_router)
    app.include_router(eval_router)
    app.include_router(kpi_router)
    return app


app = create_app()
