"""Kafka consumer for the Smart City archival-selection topic.

DeepStream publishes selected frames to ``selected-frames`` as VSS Selected
Image Schema records, image bytes included. This consumer reads them and hands
each frame to an injected handler.

Nothing here imports the backend. The handler is supplied by whoever runs the
consumer, so this module stays a transport concern and the backend keeps
depending on the archiver rather than the other way round. Configuration is
passed in for the same reason: ``al_archiver.config`` builds its Settings at
import time from a .env beside it, which is not what the backend should pick up
by importing this file.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiokafka import AIOKafkaConsumer

logger = logging.getLogger(__name__)

FrameHandler = Callable[[dict[str, Any]], Awaitable[None]]


class ScFrameConsumer:
    """Consume Selected Image Schema records and pass them to ``handler``."""

    def __init__(
        self,
        *,
        bootstrap_servers: str,
        topic: str,
        group_id: str,
        handler: FrameHandler,
        max_partition_fetch_bytes: int,
        consumer: AIOKafkaConsumer | None = None,
    ) -> None:
        self._handler = handler
        self._topic = topic
        self._consumer = consumer or AIOKafkaConsumer(
            topic,
            bootstrap_servers=bootstrap_servers,
            group_id=group_id,
            enable_auto_commit=False,
            auto_offset_reset="earliest",
            # A frame carries its image as base64, so a record runs to roughly
            # 1.37x the JPEG and the 1 MiB default would refuse the larger ones.
            # This has to match the producer cap and the broker's
            # message.max.bytes: raising only some of the three lets a record be
            # written that no consumer can fetch, which stalls rather than errors.
            max_partition_fetch_bytes=max_partition_fetch_bytes,
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
        logger.info("sc frame consumer started on topic %s", self._topic)

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
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
            logger.exception("sc frame consume loop crashed")
            self._started = False

    async def _handle(self, msg: Any) -> None:
        try:
            frame = json.loads(msg.value.decode())
        except (ValueError, UnicodeDecodeError):
            logger.error(
                "skipping undecodable message on %s at offset %s",
                self._topic, msg.offset,
            )
            return
        if not isinstance(frame, dict):
            logger.error(
                "skipping non-object message on %s at offset %s",
                self._topic, msg.offset,
            )
            return

        try:
            await self._handler(frame)
        except Exception:
            # The offset is committed by the caller either way. Holding it back
            # would stall the partition on one bad frame, so the frame is
            # dropped and reported instead; a dead-letter path is the proper
            # fix and is not built yet.
            logger.exception(
                "dropping frame sensorId=%s id=%s at offset %s: handler failed",
                frame.get("sensorId"), frame.get("id"), msg.offset,
            )
