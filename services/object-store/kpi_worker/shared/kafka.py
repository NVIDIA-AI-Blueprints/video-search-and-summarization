"""Kafka helpers: a consumer factory and the summary producer."""

import json
from typing import Any

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer

from kpi_worker.config import settings


def make_consumer(topic: str, group_id: str) -> AIOKafkaConsumer:
    # Manual commit + earliest offset: slices/reports must be processed
    # at-least-once, and offsets are committed only after handling.
    return AIOKafkaConsumer(
        topic,
        bootstrap_servers=settings.kafka_bootstrap_servers,
        group_id=group_id,
        enable_auto_commit=False,
        auto_offset_reset="earliest",
    )


class SummaryProducer:
    def __init__(self, producer: AIOKafkaProducer | None = None) -> None:
        self._producer = producer or AIOKafkaProducer(
            bootstrap_servers=settings.kafka_bootstrap_servers
        )

    async def start(self) -> None:
        await self._producer.start()

    async def stop(self) -> None:
        await self._producer.stop()

    async def send(self, payload: dict[str, Any], key: str) -> None:
        # Keyed by kpi_id so every slice report for a KPI job lands on one
        # partition — a single finalizer instance then aggregates them.
        await self._producer.send_and_wait(
            settings.kpi_summary_topic,
            json.dumps(payload).encode(),
            key=key.encode(),
        )
