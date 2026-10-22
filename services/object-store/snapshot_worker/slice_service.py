"""Slice-consumer service entrypoint (design Steps 3-5).

Runs only the SliceConsumer loop: read slices, write manifests, report to
snapshot-summary. Deployed as its own container, scaled up to the slice count.
"""

import asyncio
import logging

from snapshot_worker.config import settings
from snapshot_worker.core.slice_consumer import SliceConsumer
from snapshot_worker.runtime import wait_for_termination
from snapshot_worker.shared.elasticsearch import ElasticsearchService
from snapshot_worker.shared.kafka import SummaryProducer
from snapshot_worker.shared.storage import StorageService

logging.basicConfig(level=settings.log_level)
logger = logging.getLogger(__name__)


async def run() -> None:
    es = ElasticsearchService()
    storage = StorageService()
    producer = SummaryProducer()
    await producer.start()

    consumer = SliceConsumer(es, storage, producer)
    await consumer.start()
    logger.info("slice consumer started")

    await wait_for_termination()

    logger.info("slice consumer stopping")
    await consumer.stop()
    await producer.stop()
    await es.close()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
