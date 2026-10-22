"""Publisher: publish one record per board to aoi-inference-results.

Records are keyed by objStorePath so boards stay separate through to the AL
Archiver. On a Kafka outage records are buffered locally with bounded retention and replayed on recovery.
"""

import asyncio
import json
import logging
from collections import deque
from typing import Any

from aiokafka import AIOKafkaProducer

from client.config import settings

logger = logging.getLogger(__name__)


class KafkaPublisher:
    def __init__(self, producer: AIOKafkaProducer | None = None) -> None:
        self._producer = producer or AIOKafkaProducer(
            bootstrap_servers=settings.kafka_bootstrap_servers,
        )
        self._buffer: deque[tuple[str, dict[str, Any]]] = deque(
            maxlen=settings.kafka_buffer_max
        )
        self._replay_task: asyncio.Task | None = None
        self._started = False

    async def start(self) -> None:
        await self._producer.start()
        self._started = True
        self._replay_task = asyncio.create_task(self._replay_loop())

    async def stop(self) -> None:
        if self._replay_task is not None:
            self._replay_task.cancel()
            try:
                await self._replay_task
            except asyncio.CancelledError:
                pass
        await self._producer.stop()
        self._started = False

    @property
    def is_ready(self) -> bool:
        return self._started

    async def publish(self, key: str, payload: dict[str, Any]) -> None:
        if not await self._send(key, payload):
            if len(self._buffer) == self._buffer.maxlen:
                logger.warning("kafka buffer full; dropping oldest buffered record")
            self._buffer.append((key, payload))

    async def _send(self, key: str, payload: dict[str, Any]) -> bool:
        try:
            await self._producer.send_and_wait(
                settings.kafka_topic,
                json.dumps(payload).encode(),
                key=key.encode(),
            )
            return True
        except Exception as exc:
            logger.warning("kafka publish failed for %s: %s", key, exc)
            return False

    async def _replay_loop(self) -> None:
        while True:
            await asyncio.sleep(settings.kafka_replay_interval_s)
            while self._buffer:
                key, payload = self._buffer[0]
                if await self._send(key, payload):
                    self._buffer.popleft()
                else:
                    break  # still down; try again next tick
