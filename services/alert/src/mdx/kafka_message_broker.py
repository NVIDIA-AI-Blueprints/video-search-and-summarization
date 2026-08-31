# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import threading
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from confluent_kafka import Consumer, KafkaError, KafkaException, Producer

# Configure centralized logging from config.yaml
from utils.logging_config import setup_logging, get_logger
setup_logging()
logger = get_logger(__name__)


EAGER_ASSIGNORS = ("range", "roundrobin")


def _require_eager_assignor(configured: Optional[str]) -> str:
    """Return the eager strategy string, refusing any cooperative request."""
    pinned = ",".join(EAGER_ASSIGNORS)
    if configured is None:
        return pinned
    requested = [name.strip() for name in str(configured).split(",") if name.strip()]
    if not requested:
        # A templated value that never got substituted. Passing "" through
        # would leave the choice to the client's default, which is the thing
        # this function exists to stop being implicit.
        return pinned
    unsupported = [name for name in requested if name not in EAGER_ASSIGNORS]
    if unsupported:
        raise ValueError(
            f"kafka.partition_assignment_strategy={configured!r} is not supported: "
            f"{', '.join(unsupported)} rebalance incrementally, while partition "
            f"ownership and readiness here are tracked from whole-assignment "
            f"callbacks. Use {pinned}."
        )
    return ",".join(requested)


class KafkaMessageBroker:
    """
    Module for Kafka message broker abstraction using Confluent Kafka
    """

    def __init__(self, kafkaConfig: dict) -> None:
        self.config = kafkaConfig
        self.batch_commit = bool(kafkaConfig.get('kafka', {}).get('batch_commit', False))
        # Messages that arrived while waiting for the first assignment, keyed
        # by consumer. Kafka only delivers to an assigned member, so anything
        # the wait poll returns is real traffic and has to reach the caller.
        self._prefetched: Dict[int, List[Any]] = {}
        # Assignment state per consumer. ``decided`` says the coordinator has
        # answered at least once, which is what readiness turns on; ``owned``
        # is the current set, which a revoke empties.
        self._assignment_lock = threading.Lock()
        self._assignment_decided: Set[int] = set()
        self._owned: Dict[int, Set[Tuple[str, int]]] = {}
        # What the most recent revoke took, kept until those
        # partitions are handed back, so a stranded record can be
        # told from one this member still owns.
        self._revoked: Dict[int, Set[Tuple[str, int]]] = {}
        # Records read into the batch currently being assembled, per
        # partition. A revoke delivered by the poll that fills that batch
        # cannot see them any other way: they are in a local of
        # ``get_consumed_messages`` until it returns.
        self._in_batch: Dict[int, Dict[Tuple[str, int], int]] = {}

    def get_consumer(
        self,
        topic: str,
        group_id: str,
        on_revoke: Optional[Callable[[Set[Tuple[str, int]]], None]] = None,
        on_assignment_change: Optional[Callable[[], None]] = None,
    ) -> Consumer:
        """
        Creates a Confluent Kafka consumer.

        :param topic: The topic to subscribe to.
        :param group_id: The consumer group ID.
        :return: Configured Kafka consumer.
        :rtype: Consumer
        """
        consumer_config = {
            'bootstrap.servers': self.config['kafka']['bootstrap_servers'],
            'group.id': group_id,
            'auto.offset.reset': self.config['kafka']['auto_offset_reset'],
            'enable.auto.commit': self.config['kafka']['enable_auto_commit'],
            'max.poll.interval.ms': self.config['kafka']['max_poll_interval_ms'],
            'session.timeout.ms': self.config['kafka'].get('session_timeout_ms', 300000), 
            'heartbeat.interval.ms': self.config['kafka'].get('heartbeat_interval_ms', 300000),
            # Pinned, not defaulted. The rebalance callbacks below are
            # written for the eager protocol: ``revoked`` clears the whole
            # owned set and ``assigned`` calls ``assign`` with the complete
            # one. Under a cooperative assignor both are incremental, so the
            # owned set would collapse to the increment and readiness would
            # drop on a partial revoke. Leaving it overridable would let a
            # deployment select that quietly, so a request for anything else
            # is refused at startup instead.
            'partition.assignment.strategy': _require_eager_assignor(
                self.config['kafka'].get('partition_assignment_strategy')
            ),
        }
        consumer = Consumer(consumer_config)
        self._subscribe_with_rebalance_hooks(consumer, topic, on_revoke, on_assignment_change)
        return consumer

    def _subscribe_with_rebalance_hooks(
        self,
        consumer: Consumer,
        topic: str,
        on_revoke: Optional[Callable[[Set[Tuple[str, int]]], None]],
        on_assignment_change: Optional[Callable[[], None]] = None,
    ) -> None:
        """Subscribe and record what the coordinator decides.

        Membership and assignment are different questions. A member that has
        joined may still be waiting to be told what it owns, and a member that
        owned partitions a moment ago may own none now. Tracking the callbacks
        is the only way to answer either without polling for a side effect.
        """
        def assigned(_consumer, partitions):
            owned = {(p.topic, p.partition) for p in partitions}
            with self._assignment_lock:
                self._assignment_decided.add(id(consumer))
                self._owned[id(consumer)] = owned
                # Anything handed back was never stranded.
                self._revoked[id(consumer)] = self._revoked.get(id(consumer), set()) - owned
            logger.info(
                "Assignment for %s: %d partition(s) %s",
                topic, len(owned), sorted(p for _, p in owned),
            )
            if on_assignment_change is not None:
                on_assignment_change()
            # Assign explicitly rather than relying on the client to do it
            # after the callback returns: the contract for that differs
            # between rebalance protocols and this must not depend on it.
            _consumer.assign(partitions)

        def revoked(_consumer, partitions):
            losing = {(p.topic, p.partition) for p in partitions}
            with self._assignment_lock:
                # Undecided again until the coordinator answers: this member
                # holds nothing and does not yet know what it will hold.
                #
                # This discard has to stay ahead of the on_assignment_change
                # below. That hook republishes assignment state, and it clears
                # the rebalance drain budget when the source reads as ready --
                # so with the two reordered, the budget shutdown hands to the
                # close-time revoke would be wiped before the revoke uses it,
                # and the revoke would mint a fresh one. Load-bearing, and no
                # test covers the ordering.
                self._assignment_decided.discard(id(consumer))
                self._owned[id(consumer)] = set()
                # Unioned, not replaced: a second revoke before the
                # coordinator answers arrives with an empty current
                # assignment, and overwriting there erased the record of what
                # the first one took -- exactly the cascading case where the
                # stranded residual is largest.
                self._revoked[id(consumer)] = (
                    self._revoked.get(id(consumer), set()) | losing
                )
                # Anything buffered while waiting for the assignment goes with
                # them. It was never committed, so the incoming owner reads it
                # again from the unchanged offset; keeping it would hand this
                # member work on partitions it no longer owns, which is the
                # overlap the drain exists to prevent.
                self._prefetched.pop(id(consumer), None)
            logger.info("Revoking %d partition(s) of %s", len(losing), topic)
            if on_assignment_change is not None:
                # Before the drain: readiness has to drop the moment the
                # partitions are taken, not after the work on them finishes.
                on_assignment_change()
            if on_revoke is not None:
                # Runs before the rebalance completes, which is the only point
                # at which this member can still finish what it started on a
                # partition another member is about to own.
                on_revoke(losing)

        consumer.subscribe([topic], on_assign=assigned, on_revoke=revoked, on_lost=revoked)

    def buffered_for(self, partitions) -> int:
        """Records already read for ``partitions`` and not yet handed on.

        Summed across consumers, because a revoke callback is told which
        partitions moved and not which consumer read them. Used to report a
        drain honestly: it cannot wait for these -- the thread it would block
        is the one that has to return them -- but it must not claim there was
        nothing outstanding either.
        """
        wanted = set(partitions)
        with self._assignment_lock:
            return sum(
                count
                for counts in self._in_batch.values()
                for key, count in counts.items()
                if key in wanted
            )

    def was_revoked(self, consumer: Consumer, topic: str, partition: int) -> bool:
        """Whether this partition was taken since the batch began.

        Compared against what the last revoke actually took, not against what
        is owned now. Under the eager protocol a member surrenders everything
        and is usually handed most of it straight back, so "not currently
        owned" counted every rebalance as a stranding whether or not another
        member ever touched the records.
        """
        with self._assignment_lock:
            if (topic, partition) in self._owned.get(id(consumer), ()):
                return False
            return (topic, partition) in self._revoked.get(id(consumer), ())

    def assignment_decided(self, consumer: Consumer) -> bool:
        """Whether the coordinator has told ``consumer`` what it owns.

        Not "owns something": with more group members than partitions on a
        topic, a member legitimately owns none of it and would never become
        ready under that reading.
        """
        with self._assignment_lock:
            return id(consumer) in self._assignment_decided

    def owned_partitions(self, consumer: Consumer) -> Set[Tuple[str, int]]:
        with self._assignment_lock:
            return set(self._owned.get(id(consumer), ()))

    def await_assignment(self, consumer: Consumer, timeout: float) -> bool:
        """Wait for one consumer, polling only it. See ``await_assignments``."""
        return self.await_assignments([consumer], timeout)

    def await_assignments(self, consumers: List[Consumer], timeout: float) -> bool:
        """Poll every consumer until the coordinator has decided what each owns.

        ``subscribe`` only starts the join; the assignment lands on a later
        poll. Until it does the member holds nothing, so a producer that
        starts in the meantime writes past a ``latest`` offset no member has
        reached and those records are never delivered.

        Every consumer is polled on every pass, including ones already
        assigned. Members of a group share a rebalance: a consumer joining
        later forces one, and it does not complete until *all* members poll.
        Waiting on the newcomer alone starves the earlier ones and the
        rebalance never finishes, which deadlocks the wait it was meant to
        satisfy.

        Waiting on the assignment rather than on membership is the difference
        between "the group knows about me" and "I know what to read".
        """
        deadline = time.monotonic() + timeout
        while True:
            if all(self.assignment_decided(c) for c in consumers):
                return True
            if time.monotonic() >= deadline:
                return all(self.assignment_decided(c) for c in consumers)
            for consumer in consumers:
                message = consumer.poll(timeout=0.1)
                if message is not None and message.error() is None:
                    self._prefetched.setdefault(id(consumer), []).append(message)

    def get_producer(self) -> Producer:
        """
        Creates a Confluent Kafka producer.

        :return: Configured Kafka producer.
        :rtype: Producer
        """
        producer_config = {
            'bootstrap.servers': self.config['kafka']['bootstrap_servers']
        }
        return Producer(producer_config)

    def _commit_pending(self, consumer: Consumer, pending: Dict[Tuple[str, int], Any]) -> None:
        """Commit the highest offset seen for each partition in the batch."""
        for msg in pending.values():
            try:
                consumer.commit(msg)
            except KafkaException as ke:
                logger.error(f"Failed to commit batched offset: {ke}")

    def get_consumed_messages(self, consumer: Consumer, batch_size: Optional[int] = None) -> Dict[str, List[Tuple[str, str]]]:
        """
        Consumes a batch of messages from a Kafka topic and manually commits the offsets.

        With ``kafka.batch_commit`` enabled the commit is deferred to the end of
        the batch instead of being issued per message. Offsets are monotonic
        within a partition and every intermediate message is already in the
        returned batch, so committing the highest offset per partition is
        equivalent to committing each in turn.

        The flush below happens before this returns, so callers never receive
        uncommitted messages: batching does not make the pipeline
        at-least-once, it opens a redelivery window of one poll batch, entered
        only when a crash lands inside the loop (see README "Crash and replay
        semantics").

        :param consumer: The Confluent Kafka consumer.
        :param batch_size: The number of messages to consume in a single batch. Defaults to kafka.max_poll_records.
        :return: A dictionary with partition keys and lists of consumed messages (key, value).
        :rtype: Dict[str, List[Tuple[str, str]]]
        """
        messages = {}
        pending_commits: Dict[Tuple[str, int], Any] = {}
        # Anything the group-join wait pulled off the wire is delivered here
        # first, ahead of a fresh poll, so waiting never drops a message.
        prefetched = self._prefetched.pop(id(consumer), [])
        with self._assignment_lock:
            self._in_batch[id(consumer)] = {}
        try:
            # Resolve effective batch size from argument or configuration
            effective_batch_size = batch_size if batch_size is not None else self.config['kafka'].get('max_poll_records', 10)

            for _ in range(effective_batch_size):  # Loop to fetch up to `batch_size` messages
                if prefetched:
                    msg = prefetched.pop(0)
                else:
                    msg = consumer.poll(
                        timeout=self.config['kafka']['poll_timeout'] / 1000
                    )
                if msg is None:
                    break  # No message available, stop polling this topic

                if msg.error():
                    if msg.error().code() == KafkaError._PARTITION_EOF:
                        logger.debug(
                            "End of partition reached, continuing polling.")
                        continue  # End of partition, keep polling
                    else:
                        raise KafkaException(msg.error())
                else:
                    partition_key = f"{msg.topic()}-{msg.partition()}"
                    if partition_key not in messages:
                        messages[partition_key] = []
                    # msg.timestamp() returns (timestamp_type, timestamp_ms)
                    # timestamp_type: 0=not available, 1=create time, 2=log append time
                    ts_type, kafka_timestamp_ms = msg.timestamp()
                    if ts_type == 0:
                        logger.debug("Kafka message has no timestamp available")
                        kafka_timestamp_ms = None
                    messages[partition_key].append((msg.key(), msg.value(), kafka_timestamp_ms))
                    with self._assignment_lock:
                        counts = self._in_batch.setdefault(id(consumer), {})
                        key = (msg.topic(), msg.partition())
                        counts[key] = counts.get(key, 0) + 1
                    if self.batch_commit:
                        pending_commits[(msg.topic(), msg.partition())] = msg
                    else:
                        # Manually commit the message offset
                        try:
                            consumer.commit(msg)
                        except KafkaException as ke:
                            logger.error(f"Failed to commit offset: {ke}")

        except KafkaException as e:
            logger.error(f"Kafka error: {e}")
        finally:
            if prefetched:
                # More arrived during the wait than one batch can carry.
                self._prefetched[id(consumer)] = prefetched
            if pending_commits:
                self._commit_pending(consumer, pending_commits)
            # In the finally, not after it: anything other than a
            # KafkaException -- from the poll, the timestamp handling, the
            # commit -- would otherwise leave the batch counts behind for a
            # later drain to read as a stranding that had already been handled.
            with self._assignment_lock:
                self._in_batch.pop(id(consumer), None)

        return messages
