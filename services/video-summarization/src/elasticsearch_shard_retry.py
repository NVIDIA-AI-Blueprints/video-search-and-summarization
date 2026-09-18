# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Retry helpers for transient Elasticsearch shard-allocation races."""

import os
import time
from collections.abc import Callable
from typing import Any

_SHARD_UNAVAILABLE_MARKERS = (
    "noshardavailableactionexception",
    "no_shard_available_action_exception",
    "no shard available",
    "all shards failed",
    "primary shard is not active",
)


class PrimaryShardUnavailable(RuntimeError):
    """An Elasticsearch primary shard did not become active in time."""

    status_code = 503


def is_shard_unavailable_error(error: BaseException) -> bool:
    """Return whether an Elasticsearch error represents an unavailable shard."""
    message = str(error).lower()
    return any(marker in message for marker in _SHARD_UNAVAILABLE_MARKERS)


def _retry_settings() -> tuple[int, float]:
    try:
        attempts = int(os.environ.get("VSS_CTX_RAG_ES_SHARD_RETRY_ATTEMPTS", "5"))
    except (TypeError, ValueError):
        attempts = 5
    try:
        backoff = float(os.environ.get("VSS_CTX_RAG_ES_SHARD_RETRY_BACKOFF_SECS", "0.1"))
    except (TypeError, ValueError):
        backoff = 0.1
    return max(1, attempts), max(0.0, backoff)


def retry_shard_operation(
    operation: Callable[[], Any],
    *,
    operation_name: str,
    index_name: str,
    logger,
    sleep: Callable[[float], None] = time.sleep,
):
    """Retry only transient shard-unavailable failures with bounded backoff."""
    attempts, delay = _retry_settings()
    for attempt in range(1, attempts + 1):
        try:
            return operation()
        except Exception as error:
            if not is_shard_unavailable_error(error) or attempt == attempts:
                raise
            logger.warning(
                "Elasticsearch %s for index '%s' hit an unavailable shard "
                "(attempt %d/%d); retrying in %.3fs: %s",
                operation_name,
                index_name,
                attempt,
                attempts,
                delay,
                error,
            )
            sleep(delay)
            delay = min(delay * 2, 1.0)


def wait_for_primary_shard(es_client, index_name: str) -> None:
    """Wait briefly until the index has one active primary shard."""
    try:
        health = es_client.cluster.health(
            index=index_name,
            wait_for_status="yellow",
            wait_for_active_shards="1",
            timeout="1s",
        )
    except Exception as error:
        # Elasticsearch 9 reports a timed-out cluster-health wait as HTTP 408,
        # so the Python client raises before returning the response body.
        status = getattr(error, "status_code", None)
        if status is None:
            status = getattr(getattr(error, "meta", None), "status", None)
        if status == 408:
            raise PrimaryShardUnavailable(
                f"primary shard is not active for index '{index_name}'"
            ) from error
        raise
    if health.get("timed_out"):
        raise PrimaryShardUnavailable(f"primary shard is not active for index '{index_name}'")
