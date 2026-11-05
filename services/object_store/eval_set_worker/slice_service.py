"""Slice-consumer service entrypoint (design Steps 3-6, track 11.3, group A).

Runs only the SliceConsumer loop: read slices, write manifests, publish
completions to eval-results. Deployed as its own container, scaled up to the
slice count.

Run: python -m eval_set_worker.slice_service
"""

import asyncio
import logging

from eval_set_worker.config import settings
from eval_set_worker.core.slice_consumer import SliceConsumer
from eval_set_worker.runtime import wait_for_termination
from eval_set_worker.shared.elasticsearch import ElasticsearchService
from eval_set_worker.shared.kafka import ResultsProducer
from eval_set_worker.shared.storage import StorageService

logging.basicConfig(level=settings.log_level)
logger = logging.getLogger(__name__)


async def run() -> None:
    es = ElasticsearchService()
    storage = StorageService()
    producer = ResultsProducer()
    await producer.start()

    consumer = SliceConsumer(es, storage, producer)
    await consumer.start()
    logger.info("eval slice consumer started (group=%s)", settings.eval_slices_group)

    await wait_for_termination()

    logger.info("eval slice consumer stopping")
    await consumer.stop()
    await producer.stop()
    await es.close()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
