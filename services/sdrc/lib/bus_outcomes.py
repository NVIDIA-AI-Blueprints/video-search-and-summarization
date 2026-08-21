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

"""Bus event outcome helpers for Redis/Kafka commit decisions.

Safe policy (always on):
- OK / NOOP / TERMINAL -> commit (progress the bus)
- RETRYABLE -> do not commit until WDM_EVENT_RETRY_LIMIT, then promote to TERMINAL
- TERMINAL failures are always logged at ERROR (log-based DLQ)
"""

from __future__ import annotations

import json
from typing import Any, MutableMapping, Optional, Tuple

# Event handled successfully (stream added/removed/configured as requested).
EVENT_OK = "OK"
# Nothing to do (ignored, filtered, or duplicate); still safe to ack.
EVENT_NOOP = "NOOP"
# Permanent failure — retrying this same message cannot succeed; log + commit.
EVENT_TERMINAL = "TERMINAL"
# Temporary failure — keep the message pending and try again later.
EVENT_RETRYABLE = "RETRYABLE"

# Permanent / poison-style failures: retrying the same record cannot help.
_TERMINAL_EXC_TYPES = (
    KeyError,
    TypeError,
    ValueError,
    json.JSONDecodeError,
    AttributeError,
    IndexError,
    UnicodeDecodeError,
)

# In-process retry counters keyed by bus message id (reset on process restart).
_bus_retry_attempts: MutableMapping[str, int] = {}


def reset_retry_attempts_for_tests() -> None:
    """Clear retry counters (unit tests only)."""
    _bus_retry_attempts.clear()


def _is_retryable_infra_exception(exc: BaseException) -> bool:
    """True for transient transport / infra client failures."""
    # Built-in connectivity / timeout (ConnectionError covers BrokenPipeError, etc.).
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True

    try:
        import requests

        if isinstance(exc, requests.RequestException):
            return True
    except Exception:
        pass

    try:
        import redis

        # redis.RedisError covers ConnectionError, TimeoutError, BusyLoadingError, etc.
        if isinstance(exc, redis.RedisError):
            return True
    except Exception:
        pass

    try:
        from kubernetes.client.rest import ApiException

        if isinstance(exc, ApiException):
            return True
    except Exception:
        pass

    return False


def classify_exception(exc: BaseException) -> str:
    """Classify an exception as TERMINAL or RETRYABLE.

    Poison / permanent shape errors are TERMINAL. Transient HTTP, Redis, and
    Kubernetes client failures are RETRYABLE. Unrecognized exceptions stay
    TERMINAL so a single unexpected bug cannot stall a partition forever.
    """
    if isinstance(exc, _TERMINAL_EXC_TYPES):
        return EVENT_TERMINAL

    # Pre-add health wait timed out — keep the event pending and retry later.
    try:
        from lib.podprovisioner.healthwatcher import WorkloadUnhealthyError
    except ImportError:
        WorkloadUnhealthyError = ()  # type: ignore
    if WorkloadUnhealthyError and isinstance(exc, WorkloadUnhealthyError):
        return EVENT_RETRYABLE

    if _is_retryable_infra_exception(exc):
        return EVENT_RETRYABLE

    # Unknown errors: prefer progress (commit) over stalling a partition forever.
    return EVENT_TERMINAL

def bump_retry_attempt(message_key: str) -> int:
    """Increment and return the 1-based attempt count for a bus message."""
    attempt = int(_bus_retry_attempts.get(message_key, 0)) + 1
    _bus_retry_attempts[message_key] = attempt
    return attempt


def clear_retry_attempt(message_key: str) -> None:
    _bus_retry_attempts.pop(message_key, None)


def decide_commit(
    outcome: str,
    message_key: str,
    retry_limit: int,
) -> Tuple[bool, str, int]:
    """Return (should_commit, final_outcome, attempt).

    RETRYABLE outcomes are promoted to TERMINAL when attempt >= retry_limit.
    """
    limit = max(1, int(retry_limit))
    attempt = 0
    final = outcome if outcome in {
        EVENT_OK, EVENT_NOOP, EVENT_TERMINAL, EVENT_RETRYABLE
    } else EVENT_TERMINAL

    if final == EVENT_RETRYABLE:
        attempt = bump_retry_attempt(message_key)
        if attempt >= limit:
            final = EVENT_TERMINAL
        else:
            return False, final, attempt

    clear_retry_attempt(message_key)
    return True, final, attempt


def _kafka_record_field(msg: Any, name: str) -> Any:
    """Read topic/partition/offset from kafka-python or confluent-style records."""
    val = getattr(msg, name, None)
    if callable(val):
        try:
            return val()
        except TypeError:
            return val
    return val


def _kafka_topic_partition(topic: Any, partition: Any) -> Any:
    try:
        from kafka import TopicPartition

        return TopicPartition(topic, int(partition))
    except Exception:
        from collections import namedtuple

        return namedtuple("TopicPartition", ["topic", "partition"])(
            topic, int(partition)
        )


def _kafka_offset_and_metadata(offset: int) -> Any:
    try:
        from kafka.structs import OffsetAndMetadata

        try:
            return OffsetAndMetadata(offset, "")
        except TypeError:
            # kafka-python >= 2.0.2 adds leader_epoch
            return OffsetAndMetadata(offset, "", -1)
    except Exception:
        from collections import namedtuple

        return namedtuple("OffsetAndMetadata", ["offset", "metadata"])(offset, "")


def kafka_message_key(msg: Any) -> str:
    """Build a stable-ish key for Kafka retry accounting."""
    topic = _kafka_record_field(msg, "topic")
    partition = _kafka_record_field(msg, "partition")
    offset = _kafka_record_field(msg, "offset")
    if topic is not None and partition is not None and offset is not None:
        return f"kafka:{topic}:{partition}:{offset}"
    return f"kafka:{id(msg)}"


def kafka_rewind_to_message(consumer: Any, msg: Any) -> bool:
    """Seek back to ``msg`` so a later/post-handler commit cannot skip it.

    flask-kafka always calls ``consumer.commit()`` after the handler returns.
    With ``enable_auto_commit=False``, skipping our own commit is not enough:
    the iterator has already advanced, so the next successful commit would
    acknowledge this offset and permanently drop the lifecycle update.
    Rewinding before return makes that commit re-park the group at this offset.
    """
    try:
        topic = _kafka_record_field(msg, "topic")
        partition = _kafka_record_field(msg, "partition")
        offset = _kafka_record_field(msg, "offset")
        if topic is None or partition is None or offset is None:
            return False
        consumer.seek(_kafka_topic_partition(topic, partition), int(offset))
        return True
    except Exception:
        return False


def kafka_park_offset_on_next_commit(consumer: Any, msg: Any) -> bool:
    """Make the next ``consumer.commit()`` park the group at ``msg``'s offset.

    Used when ``seek`` fails: flask-kafka still commits after the handler, so
    replace ``commit`` once to write this offset (next fetch = this message)
    instead of the already-advanced position.
    """
    try:
        topic = _kafka_record_field(msg, "topic")
        partition = _kafka_record_field(msg, "partition")
        offset = _kafka_record_field(msg, "offset")
        if topic is None or partition is None or offset is None:
            return False

        tp = _kafka_topic_partition(topic, partition)
        park = {tp: _kafka_offset_and_metadata(int(offset))}
        original_commit = consumer.commit

        def _park_commit(*_args: Any, **_kwargs: Any) -> Any:
            try:
                return original_commit(park)
            finally:
                consumer.commit = original_commit

        consumer.commit = _park_commit
        return True
    except Exception:
        return False


def redis_message_key(msgid: Any) -> str:
    return f"redis:{msgid}"


def truncate_payload(payload: Any, max_chars: int = 2048) -> str:
    try:
        text = payload if isinstance(payload, str) else json.dumps(payload, default=str)
    except Exception:
        text = repr(payload)
    if len(text) > max_chars:
        return text[:max_chars] + "...(truncated)"
    return text


def log_terminal_failure(
    logger: Any,
    *,
    bus: str,
    message_id: str,
    error: Any,
    change: Optional[str] = None,
    stream_id: Optional[str] = None,
    workload: Optional[str] = None,
    attempt: Optional[int] = None,
    payload: Any = None,
    reason: Optional[str] = None,
) -> None:
    """Log-based DLQ entry for a terminal bus failure."""
    logger.error(
        "bus terminal failure bus=%s message_id=%s workload=%s change=%s "
        "stream_id=%s attempt=%s reason=%s error=%s payload=%s",
        bus,
        message_id,
        workload,
        change,
        stream_id,
        attempt,
        reason or "terminal",
        repr(error),
        truncate_payload(payload) if payload is not None else None,
    )
