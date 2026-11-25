"""Transport behaviour of the Smart City frame consumer."""

import asyncio
import json

from al_archiver.core.sc_consumer import ScFrameConsumer


class Msg:
    def __init__(self, value: bytes, offset: int = 0):
        self.value = value
        self.offset = offset


class FakeKafkaConsumer:
    """Yields a fixed list of messages, then blocks like a live consumer."""

    def __init__(self, messages):
        self._messages = list(messages)
        self.started = False
        self.stopped = False
        self.commits = 0

    async def start(self):
        self.started = True

    async def stop(self):
        self.stopped = True

    async def commit(self):
        self.commits += 1

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._messages:
            return self._messages.pop(0)
        await asyncio.sleep(3600)  # a real consumer waits rather than ending


def build(messages, handler):
    return ScFrameConsumer(
        bootstrap_servers="unused:9092",
        topic="selected-frames",
        group_id="test",
        handler=handler,
        max_partition_fetch_bytes=4 * 1024 * 1024,
        consumer=FakeKafkaConsumer(messages),
    )


async def drain(consumer, expected_commits, timeout=2.0):
    await consumer.start()
    deadline = asyncio.get_event_loop().time() + timeout
    while consumer._consumer.commits < expected_commits:
        if asyncio.get_event_loop().time() > deadline:
            break
        await asyncio.sleep(0.01)
    await consumer.stop()


async def test_valid_frames_reach_the_handler():
    seen = []
    frames = [{"sensorId": "cam1", "id": "f1"}, {"sensorId": "cam1", "id": "f2"}]
    messages = [Msg(json.dumps(f).encode(), i) for i, f in enumerate(frames)]

    async def handler(frame):
        seen.append(frame)

    c = build(messages, handler)
    await drain(c, expected_commits=2)

    assert seen == frames
    assert c._consumer.commits == 2
    assert c._consumer.stopped


async def test_undecodable_message_is_skipped_and_still_committed():
    """Holding the offset back would stall the partition on one bad record."""
    seen = []

    async def handler(frame):
        seen.append(frame)

    c = build([Msg(b"{not json"), Msg(json.dumps({"id": "f1"}).encode(), 1)], handler)
    await drain(c, expected_commits=2)

    assert seen == [{"id": "f1"}]
    assert c._consumer.commits == 2


async def test_non_object_message_is_skipped():
    seen = []

    async def handler(frame):
        seen.append(frame)

    c = build([Msg(b'["a list"]'), Msg(json.dumps({"id": "f1"}).encode(), 1)], handler)
    await drain(c, expected_commits=2)
    assert seen == [{"id": "f1"}]


async def test_a_failing_handler_does_not_stop_the_loop():
    """One frame the handler cannot process must not block every later frame."""
    seen = []

    async def handler(frame):
        if frame["id"] == "bad":
            raise RuntimeError("boom")
        seen.append(frame)

    messages = [
        Msg(json.dumps({"id": "bad"}).encode(), 0),
        Msg(json.dumps({"id": "good"}).encode(), 1),
    ]
    c = build(messages, handler)
    await drain(c, expected_commits=2)

    assert seen == [{"id": "good"}]
    assert c._consumer.commits == 2


async def test_stop_is_idempotent_and_clears_readiness():
    async def handler(frame):
        pass

    c = build([], handler)
    await c.start()
    assert c.is_ready
    await c.stop()
    assert not c.is_ready
    await c.stop()


async def test_fetch_size_reaches_the_kafka_client(monkeypatch):
    """A base64 frame approaches 1 MiB, so the consumer cap has to be lifted with
    the producer and broker ones. Assert it actually reaches the Kafka client
    rather than only being accepted as an argument."""
    captured = {}

    def fake_client(*topics, **kwargs):
        captured["topics"] = topics
        captured.update(kwargs)
        return FakeKafkaConsumer([])

    monkeypatch.setattr("al_archiver.core.sc_consumer.AIOKafkaConsumer", fake_client)

    async def handler(frame):
        pass

    ScFrameConsumer(
        bootstrap_servers="broker:9092",
        topic="selected-frames",
        group_id="sc-archiver",
        handler=handler,
        max_partition_fetch_bytes=4 * 1024 * 1024,
    )

    assert captured["topics"] == ("selected-frames",)
    assert captured["max_partition_fetch_bytes"] == 4 * 1024 * 1024
    assert captured["group_id"] == "sc-archiver"
    # Offsets are committed by the loop only after a message is handled.
    assert captured["enable_auto_commit"] is False
    assert captured["auto_offset_reset"] == "earliest"
