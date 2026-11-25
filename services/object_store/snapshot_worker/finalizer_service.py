"""Finalizer service entrypoint (design Steps 6-7).

Runs only the Finalizer loop: aggregate slice reports from snapshot-summary,
write snapshot.json, and close the shared PIT. Deployed as its own container.
"""

import asyncio
import logging

from snapshot_worker.config import settings
from snapshot_worker.core.finalizer import Finalizer
from snapshot_worker.runtime import wait_for_termination
from snapshot_worker.shared.elasticsearch import ElasticsearchService
from snapshot_worker.shared.storage import StorageService

logging.basicConfig(level=settings.log_level)
logger = logging.getLogger(__name__)


async def run() -> None:
    es = ElasticsearchService()
    storage = StorageService()

    finalizer = Finalizer(es, storage)
    await finalizer.start()
    logger.info("finalizer started")

    await wait_for_termination()

    logger.info("finalizer stopping")
    await finalizer.stop()
    await es.close()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
