# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for transient Elasticsearch shard-allocation retries."""

from unittest.mock import MagicMock

import pytest

from elasticsearch_shard_retry import (
    PrimaryShardUnavailable,
    is_shard_unavailable_error,
    retry_shard_operation,
    wait_for_primary_shard,
)


@pytest.mark.parametrize(
    "message",
    [
        "NoShardAvailableActionException: all shards failed",
        "no_shard_available_action_exception",
        "primary shard is not active",
    ],
)
def test_identifies_shard_unavailable_errors(message):
    assert is_shard_unavailable_error(RuntimeError(message))


def test_retries_until_shard_operation_succeeds(monkeypatch):
    monkeypatch.setenv("VSS_CTX_RAG_ES_SHARD_RETRY_ATTEMPTS", "4")
    monkeypatch.setenv("VSS_CTX_RAG_ES_SHARD_RETRY_BACKOFF_SECS", "0.25")
    operation = MagicMock(
        side_effect=[
            RuntimeError("NoShardAvailableActionException"),
            RuntimeError("all shards failed"),
            {"hits": []},
        ]
    )
    sleep = MagicMock()

    result = retry_shard_operation(
        operation,
        operation_name="retrieve_docs",
        index_name="default_video_1",
        logger=MagicMock(),
        sleep=sleep,
    )

    assert result == {"hits": []}
    assert operation.call_count == 3
    assert [call.args[0] for call in sleep.call_args_list] == [0.25, 0.5]


def test_surfaces_shard_error_after_bounded_attempts(monkeypatch):
    monkeypatch.setenv("VSS_CTX_RAG_ES_SHARD_RETRY_ATTEMPTS", "3")
    operation = MagicMock(side_effect=RuntimeError("all shards failed"))

    with pytest.raises(RuntimeError, match="all shards failed"):
        retry_shard_operation(
            operation,
            operation_name="retrieve_docs",
            index_name="default_video_1",
            logger=MagicMock(),
            sleep=MagicMock(),
        )

    assert operation.call_count == 3


def test_does_not_retry_non_shard_error(monkeypatch):
    monkeypatch.setenv("VSS_CTX_RAG_ES_SHARD_RETRY_ATTEMPTS", "5")
    operation = MagicMock(side_effect=ValueError("invalid query"))

    with pytest.raises(ValueError, match="invalid query"):
        retry_shard_operation(
            operation,
            operation_name="retrieve_docs",
            index_name="default_video_1",
            logger=MagicMock(),
            sleep=MagicMock(),
        )

    operation.assert_called_once_with()


def test_waits_for_yellow_index_health():
    es_client = MagicMock()
    es_client.cluster.health.return_value = {"timed_out": False, "status": "yellow"}

    wait_for_primary_shard(es_client, "default_video_1")

    es_client.cluster.health.assert_called_once_with(
        index="default_video_1",
        wait_for_status="yellow",
        wait_for_active_shards="1",
        timeout="1s",
    )


def test_primary_shard_timeout_is_503():
    es_client = MagicMock()
    es_client.cluster.health.return_value = {"timed_out": True}

    with pytest.raises(PrimaryShardUnavailable) as exc_info:
        wait_for_primary_shard(es_client, "default_video_1")

    assert exc_info.value.status_code == 503


@pytest.mark.parametrize("status_location", ["status_code", "meta"])
def test_http_408_cluster_health_timeout_is_503(status_location):
    class ClusterHealthTimeout(Exception):
        pass

    error = ClusterHealthTimeout("cluster health wait timed out")
    if status_location == "status_code":
        error.status_code = 408
    else:
        error.meta = type("Meta", (), {"status": 408})()
    es_client = MagicMock()
    es_client.cluster.health.side_effect = error

    with pytest.raises(PrimaryShardUnavailable) as exc_info:
        wait_for_primary_shard(es_client, "default_video_1")

    assert exc_info.value.status_code == 503
    assert exc_info.value.__cause__ is error


def test_non_timeout_cluster_health_error_is_not_reclassified():
    error = RuntimeError("authentication failed")
    error.status_code = 401
    es_client = MagicMock()
    es_client.cluster.health.side_effect = error

    with pytest.raises(RuntimeError, match="authentication failed"):
        wait_for_primary_shard(es_client, "default_video_1")
