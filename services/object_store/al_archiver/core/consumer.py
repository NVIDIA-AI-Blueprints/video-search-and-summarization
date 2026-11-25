import asyncio
import json
import logging

from aiokafka import AIOKafkaConsumer
from pydantic import ValidationError

from al_archiver.config import settings
from al_archiver.core.forwarder import Forwarder
from client.api.schemas import InferenceResultRecord

logger = logging.getLogger(__name__)


class ArchiverConsumer:
    def __init__(
        self,
        forwarder: Forwarder,
        consumer: AIOKafkaConsumer | None = None,
    ) -> None:
        self._forwarder = forwarder
        self._consumer = consumer or AIOKafkaConsumer(
            settings.kafka_topic,
            bootstrap_servers=settings.kafka_bootstrap_servers,
            group_id=settings.kafka_group_id,
            enable_auto_commit=False,
            auto_offset_reset="earliest",
        )
        self._task: asyncio.Task | None = None
        self._started = False

    @property
    def is_ready(self) -> bool:
        return self._started

    async def start(self) -> None:
        await self._consumer.start()
        self._started = True
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        await self._consumer.stop()
        self._started = False

    async def _run(self) -> None:
        try:
            async for msg in self._consumer:
                await self._handle(msg)
                await self._consumer.commit()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("archiver consume loop crashed")
            self._started = False
            raise

    async def _handle(self, msg) -> None:
        try:
            raw = json.loads(msg.value.decode())
        except (ValueError, UnicodeDecodeError):
            logger.error("skipping undecodable message at offset %s", msg.offset)
            return

        try:
            record = InferenceResultRecord.model_validate(raw)
        except ValidationError as exc:
            logger.error(
                "skipping schema-invalid message at offset %s: %s",
                msg.offset,
                exc,
            )
            return

        await self._forwarder.forward(record.model_dump(exclude_none=True))
