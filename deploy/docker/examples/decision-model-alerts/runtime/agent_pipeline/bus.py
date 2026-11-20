# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""At-least-once delivery. Remove outbox records only after broker acknowledgement."""
import logging
import os
import threading
from dataclasses import dataclass
from confluent_kafka import Consumer, Producer

DECISIONS = "d1.decisions.v1"
EVENTS = "d1.events.v1"
REVIEWS = "d1.reviews.v1"
LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class ConsumerPosition:
    """The broker position, never an offset supplied by a message payload."""
    topic: str
    partition: int
    offset: int

    def __post_init__(self):
        if not self.topic or self.partition < 0 or self.offset < 0:
            raise ValueError("A decision requires a valid Kafka topic, partition and offset")

    @classmethod
    def from_message(cls, message):
        return cls(message.topic(), message.partition(), message.offset())


def kafka_config():
    # Optional JSON client config file supports SASL/TLS without secrets in source.
    import json
    from pathlib import Path
    path = os.getenv("KAFKA_CONFIG_FILE")
    config = json.loads(Path(path).read_text()) if path else {}
    config["bootstrap.servers"] = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "127.0.0.1:9092")
    return config


def consumer(group, topics):
    client = Consumer({**kafka_config(), "group.id": group, "enable.auto.commit": False,
                       "auto.offset.reset": "earliest", "max.poll.interval.ms": 900000})
    client.subscribe(topics)
    return client


def drain_outbox(store, stop):
    producer = Producer({**kafka_config(), "enable.idempotence": True, "acks": "all", "delivery.timeout.ms": 30000})
    while not stop.is_set():
        rows = store.rows("SELECT * FROM outbox ORDER BY rowid LIMIT 1")
        if not rows:
            stop.wait(0.2)
            continue
        row = rows[0]
        finished = threading.Event()
        result = []

        def delivered(error, _message):
            result.append(error)
            finished.set()

        try:
            producer.produce(row["topic"], key=row["key"], value=row["body"], on_delivery=delivered)
            while not finished.is_set():
                producer.poll(0.2)
            if result[0] is not None:
                LOG.warning("Kafka delivery failed; event retained in outbox")
                stop.wait(2)
                continue
            with store.transaction() as db:
                db.execute("DELETE FROM outbox WHERE id=?", (row["id"],))
        except Exception:
            LOG.exception("Outbox delivery failed")
            stop.wait(2)
