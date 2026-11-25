"""Summary-consumer service entrypoint (design Steps 6-7, track 11.3, group B).

Runs only the SummaryConsumer loop: aggregate slice completions from
eval-results, write eval.json, flip status.json, and close the shared PIT.
Deployed as its own container (single instance — completions for a job are
keyed by eval_id onto one partition).

Run: python -m eval_set_worker.summary_service
"""

import asyncio
import logging

from eval_set_worker.config import settings
from eval_set_worker.core.summary_consumer import SummaryConsumer
from eval_set_worker.runtime import wait_for_termination
from eval_set_worker.shared.elasticsearch import ElasticsearchService
from eval_set_worker.shared.storage import StorageService

logging.basicConfig(level=settings.log_level)
logger = logging.getLogger(__name__)


async def run() -> None:
    es = ElasticsearchService()
    storage = StorageService()

    consumer = SummaryConsumer(storage, es)
    await consumer.start()
    logger.info("eval summary consumer started (group=%s)", settings.eval_summary_group)

    await wait_for_termination()

    logger.info("eval summary consumer stopping")
    await consumer.stop()
    await es.close()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
