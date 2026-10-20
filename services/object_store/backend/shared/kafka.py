import json
from typing import Any

from aiokafka import AIOKafkaProducer

from backend.config import settings


class KafkaService:
    def __init__(self) -> None:
        self._producer = AIOKafkaProducer(
            bootstrap_servers=settings.kafka_bootstrap_servers,
        )

    async def start(self) -> None:
        await self._producer.start()

    async def stop(self) -> None:
        await self._producer.stop()

    async def send(
        self,
        payload: dict[str, Any],
        topic: str | None = None,
        key: str | None = None,
        partition: int | None = None,
    ) -> None:
        await self._producer.send_and_wait(
            topic or settings.kafka_topic,
            json.dumps(payload).encode(),
            key=key.encode() if key is not None else None,
            partition=partition,
        )
