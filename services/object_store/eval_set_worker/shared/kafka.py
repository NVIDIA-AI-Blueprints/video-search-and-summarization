"""Kafka helpers: a consumer factory and the completion (results) producer."""

import json
from typing import Any

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer

from eval_set_worker.config import settings


def make_consumer(topic: str, group_id: str) -> AIOKafkaConsumer:
    # Manual commit + earliest offset: slices/completions must be processed
    # at-least-once, and offsets are committed only after handling.
    return AIOKafkaConsumer(
        topic,
        bootstrap_servers=settings.kafka_bootstrap_servers,
        group_id=group_id,
        enable_auto_commit=False,
        auto_offset_reset="earliest",
    )


class ResultsProducer:
    def __init__(self, producer: AIOKafkaProducer | None = None) -> None:
        self._producer = producer or AIOKafkaProducer(
            bootstrap_servers=settings.kafka_bootstrap_servers
        )

    async def start(self) -> None:
        await self._producer.start()

    async def stop(self) -> None:
        await self._producer.stop()

    async def send(self, topic: str, key: str, payload: dict[str, Any]) -> None:
        # Keyed by eval_id so every completion for a job lands on one partition —
        # a single summary consumer instance then aggregates them.
        await self._producer.send_and_wait(
            topic, json.dumps(payload).encode(), key=key.encode()
        )
