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
"""Unit tests for vss_agents.utils.incident_db.

No live database is used: the Supabase async client is mocked throughout.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

from postgrest import APIError
import pytest
import pytest_asyncio

from vss_agents.utils import incident_db
from vss_agents.utils.incident_db import IncidentDB
from vss_agents.utils.incident_db import revision_summary_text


def _make_mock_client() -> MagicMock:
    """Create a mock Supabase async client with chainable query builder."""
    client = MagicMock()
    table_mock = MagicMock()

    # Chainable methods for select/upsert/update/delete
    select_mock = MagicMock()
    select_mock.eq = MagicMock(return_value=select_mock)
    select_mock.order = MagicMock(return_value=select_mock)
    select_mock.maybe_single = MagicMock(return_value=select_mock)
    select_mock.execute = AsyncMock(return_value=MagicMock(data=None))
    select_mock.head = True
    select_mock.count = "exact"

    upsert_mock = MagicMock()
    upsert_mock.on_conflict = MagicMock(return_value=upsert_mock)
    upsert_mock.execute = AsyncMock()

    update_mock = MagicMock()
    update_mock.eq = MagicMock(return_value=update_mock)
    update_mock.execute = AsyncMock()

    delete_mock = MagicMock()
    delete_mock.eq = MagicMock(return_value=delete_mock)
    delete_mock.execute = AsyncMock()

    insert_mock = MagicMock()
    insert_mock.execute = AsyncMock()

    rpc_builder = MagicMock()
    rpc_builder.execute = AsyncMock()
    rpc_mock = MagicMock(return_value=rpc_builder)

    table_mock.select = MagicMock(return_value=select_mock)
    table_mock.upsert = MagicMock(return_value=upsert_mock)
    table_mock.update = MagicMock(return_value=update_mock)
    table_mock.delete = MagicMock(return_value=delete_mock)
    table_mock.insert = MagicMock(return_value=insert_mock)

    client.table = MagicMock(return_value=table_mock)
    client.rpc = rpc_mock

    return client


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    monkeypatch.delenv("INCIDENT_SUPABASE_URL", raising=False)
    monkeypatch.delenv("INCIDENT_SUPABASE_SERVICE_ROLE_KEY", raising=False)


@pytest_asyncio.fixture(autouse=True)
async def _reset_singleton():
    await incident_db.reset_cache()
    yield
    await incident_db.reset_cache()


def test_is_configured_false_when_unset(monkeypatch):
    monkeypatch.delenv("INCIDENT_SUPABASE_URL", raising=False)
    monkeypatch.delenv("INCIDENT_SUPABASE_SERVICE_ROLE_KEY", raising=False)
    assert incident_db.is_configured() is False


def test_is_configured_false_when_only_url(monkeypatch):
    monkeypatch.setenv("INCIDENT_SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.delenv("INCIDENT_SUPABASE_SERVICE_ROLE_KEY", raising=False)
    assert incident_db.is_configured() is False


def test_is_configured_false_when_only_key(monkeypatch):
    monkeypatch.delenv("INCIDENT_SUPABASE_URL", raising=False)
    monkeypatch.setenv("INCIDENT_SUPABASE_SERVICE_ROLE_KEY", "secret-key")
    assert incident_db.is_configured() is False


def test_is_configured_true_when_both_set(monkeypatch):
    monkeypatch.setenv("INCIDENT_SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("INCIDENT_SUPABASE_SERVICE_ROLE_KEY", "secret-key")
    assert incident_db.is_configured() is True


@pytest.mark.asyncio
async def test_connect_raises_when_missing(monkeypatch):
    monkeypatch.delenv("INCIDENT_SUPABASE_URL", raising=False)
    monkeypatch.delenv("INCIDENT_SUPABASE_SERVICE_ROLE_KEY", raising=False)
    with pytest.raises(ValueError, match="INCIDENT_SUPABASE_URL"):
        await IncidentDB.connect()


@pytest.mark.asyncio
async def test_connect_creates_client():
    mock_client = _make_mock_client()
    with patch("vss_agents.utils.incident_db.acreate_client", new=AsyncMock(return_value=mock_client)):
        db = await IncidentDB.connect(url="https://example.supabase.co", key="secret-key")
    assert db.client is mock_client


@pytest.mark.asyncio
async def test_connect_reads_from_env(monkeypatch):
    monkeypatch.setenv("INCIDENT_SUPABASE_URL", "https://from-env.supabase.co")
    monkeypatch.setenv("INCIDENT_SUPABASE_SERVICE_ROLE_KEY", "from-env-key")
    mock_client = _make_mock_client()
    mock_acreate = AsyncMock(return_value=mock_client)
    with patch("vss_agents.utils.incident_db.acreate_client", new=mock_acreate):
        await IncidentDB.connect()
        mock_acreate.assert_awaited_once_with("https://from-env.supabase.co", "from-env-key")


@pytest.mark.asyncio
async def test_close_is_noop():
    mock_client = _make_mock_client()
    db = IncidentDB(mock_client)
    await db.close()  # Should not raise


@pytest.mark.asyncio
async def test_async_context_manager():
    mock_client = _make_mock_client()
    async with IncidentDB(mock_client) as db:
        assert isinstance(db, IncidentDB)


@pytest.mark.asyncio
async def test_transaction_yields_a_writer_for_sequential_calls():
    """No real atomicity over PostgREST, but the async-with call shape must work
    (the mock backend's Analyze route groups several writes this way)."""
    mock_client = _make_mock_client()
    db = IncidentDB(mock_client)
    async with db.transaction() as tx:
        assert tx is db
        await tx.update_video("vid-1", filepath="r2/key.mp4")
    mock_client.table.assert_called_with("videos")


@pytest.mark.asyncio
async def test_healthcheck_ok():
    mock_client = _make_mock_client()
    mock_client.table().select().execute = AsyncMock(return_value=MagicMock(data=[{"id": "1"}]))
    db = IncidentDB(mock_client)
    ok, message = await db.healthcheck()
    assert ok is True
    assert message == "ok"


@pytest.mark.asyncio
async def test_healthcheck_failure_returns_message():
    mock_client = _make_mock_client()
    mock_client.table().select().execute = AsyncMock(side_effect=Exception("connection refused"))
    db = IncidentDB(mock_client)
    ok, message = await db.healthcheck()
    assert ok is False
    assert "connection refused" in message


@pytest.mark.asyncio
async def test_upsert_video_calls_upsert():
    mock_client = _make_mock_client()
    db = IncidentDB(mock_client)
    result = await db.upsert_video("vid-1", filepath="a.mp4", duration=30, source="upload")
    assert result == "vid-1"
    # Verify upsert was called with correct data
    mock_client.table.assert_called_with("videos")
    upsert_call = mock_client.table().upsert
    upsert_call.assert_called_once()
    args, kwargs = upsert_call.call_args
    assert args[0]["id"] == "vid-1"
    assert args[0]["filepath"] == "a.mp4"
    assert args[0]["duration"] == 30
    assert args[0]["source"] == "upload"
    assert "uploaded_datetime" in args[0]
    assert kwargs["on_conflict"] == "id"


@pytest.mark.asyncio
async def test_get_video_returns_none_when_missing():
    mock_client = _make_mock_client()
    mock_client.table().select().maybe_single().execute = AsyncMock(return_value=MagicMock(data=None))
    db = IncidentDB(mock_client)
    assert await db.get_video("missing") is None


@pytest.mark.asyncio
async def test_get_video_returns_none_when_postgrest_returns_no_response():
    """supabase-py's maybe_single().execute() returns None, not a response, when no row matches."""
    mock_client = _make_mock_client()
    mock_client.table().select().maybe_single().execute = AsyncMock(return_value=None)
    db = IncidentDB(mock_client)
    assert await db.get_video("missing") is None


@pytest.mark.asyncio
async def test_get_video_returns_dict_when_found():
    mock_client = _make_mock_client()
    mock_client.table().select().maybe_single().execute = AsyncMock(
        return_value=MagicMock(data={"id": "vid-1", "filepath": "a.mp4"})
    )
    db = IncidentDB(mock_client)
    row = await db.get_video("vid-1")
    assert row == {"id": "vid-1", "filepath": "a.mp4"}


@pytest.mark.asyncio
async def test_get_video_for_update_raises_not_implemented():
    mock_client = _make_mock_client()
    db = IncidentDB(mock_client)
    with pytest.raises(NotImplementedError, match="for_update is not supported"):
        await db.get_video("vid-1", for_update=True)


@pytest.mark.asyncio
async def test_list_videos_maps_all_rows():
    mock_client = _make_mock_client()
    mock_client.table().select().order().execute = AsyncMock(
        return_value=MagicMock(data=[{"id": "vid-1"}, {"id": "vid-2"}])
    )
    db = IncidentDB(mock_client)
    videos = await db.list_videos()
    assert [v["id"] for v in videos] == ["vid-1", "vid-2"]


@pytest.mark.asyncio
async def test_delete_video_calls_delete():
    mock_client = _make_mock_client()
    db = IncidentDB(mock_client)
    await db.delete_video("vid-1")
    mock_client.table.assert_called_with("videos")
    delete_call = mock_client.table().delete()
    delete_call.eq.assert_called_with("id", "vid-1")
    delete_call.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_video_calls_update():
    mock_client = _make_mock_client()
    db = IncidentDB(mock_client)
    await db.update_video("vid-1", filepath="r2/key.mp4")
    mock_client.table.assert_called_with("videos")
    update_call = mock_client.table().update({"filepath": "r2/key.mp4"})
    update_call.eq.assert_called_with("id", "vid-1")
    update_call.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_insert_model_run_upserts():
    mock_client = _make_mock_client()
    db = IncidentDB(mock_client)
    result = await db.insert_model_run("run-1", model_name="m", model_version="v1")
    assert result == "run-1"
    mock_client.table.assert_called_with("model_runs")
    upsert_call = mock_client.table().upsert
    upsert_call.assert_called_once()
    args, kwargs = upsert_call.call_args
    assert args[0]["id"] == "run-1"
    assert args[0]["model_name"] == "m"
    assert args[0]["model_version"] == "v1"
    assert kwargs["on_conflict"] == "id"


@pytest.mark.asyncio
async def test_get_model_run_returns_none_when_missing():
    mock_client = _make_mock_client()
    mock_client.table().select().maybe_single().execute = AsyncMock(return_value=MagicMock(data=None))
    db = IncidentDB(mock_client)
    assert await db.get_model_run("missing") is None


@pytest.mark.asyncio
async def test_insert_model_run_if_absent_never_overwrites_an_existing_row():
    mock_client = _make_mock_client()
    db = IncidentDB(mock_client)
    assert await db.insert_model_run_if_absent("run-1", model_name="m", prompt_version="v3") == "run-1"
    mock_client.table.assert_called_with("model_runs")
    args, kwargs = mock_client.table().upsert.call_args
    assert args[0]["id"] == "run-1" and args[0]["notes"] is None and args[0]["prompt_version"] == "v3"
    assert kwargs == {"on_conflict": "id", "ignore_duplicates": True}


def _revision_client(*, last_revision_nos, insert_results=None):
    """Client whose ``report_revisions`` select returns one max ``revision_no`` per call, then insert results."""
    client = MagicMock()
    table = MagicMock()
    select_chain = MagicMock()
    select_chain.eq.return_value = select_chain
    select_chain.order.return_value = select_chain
    select_chain.limit.return_value = select_chain
    select_chain.execute = AsyncMock(
        side_effect=[MagicMock(data=[{"revision_no": n}] if n else []) for n in last_revision_nos]
    )
    table.select.return_value = select_chain
    insert_chain = MagicMock()
    insert_chain.execute = AsyncMock(side_effect=insert_results or [MagicMock(data=[{"id": 11}])])
    table.insert.return_value = insert_chain
    client.table.return_value = table
    return client, table


REPORT_PAYLOAD = {
    "incident": {
        "type": "burglary",
        "severity_level": 2,
        "start_timestamp": 1.4,
        "end_timestamp": 6,
    },
    "entities": [],
}


@pytest.mark.asyncio
async def test_insert_ai_revision_stores_the_summary_embedding_in_the_same_insert():
    client, table = _revision_client(last_revision_nos=[0])
    await IncidentDB(client).insert_ai_revision(
        "v1", model_run_id="r", payload=REPORT_PAYLOAD, evidence=[], embedding=[0.25]
    )
    assert table.insert.call_args.args[0]["embedding"] == [0.25]


@pytest.mark.asyncio
async def test_insert_ai_revision_defaults_the_embedding_to_null():
    client, table = _revision_client(last_revision_nos=[0])
    await IncidentDB(client).insert_ai_revision("v1", model_run_id="r", payload=REPORT_PAYLOAD, evidence=[])
    assert table.insert.call_args.args[0]["embedding"] is None


@pytest.mark.asyncio
async def test_search_revisions_calls_the_rpc_and_returns_rows():
    client = MagicMock()
    rpc_chain = MagicMock()
    rpc_chain.execute = AsyncMock(return_value=MagicMock(data=[{"revision_id": 4, "similarity_score": 0.9}]))
    client.rpc.return_value = rpc_chain
    rows = await IncidentDB(client).search_revisions([0.1, 0.2], top_k=3, min_similarity=0.5)
    client.rpc.assert_called_once_with(
        "search_report_revisions",
        {"p_query_embedding": [0.1, 0.2], "p_top_k": 3, "p_min_similarity": 0.5},
    )
    assert rows == [{"revision_id": 4, "similarity_score": 0.9}]


def test_revision_summary_text_joins_title_and_description_and_tolerates_gaps():
    assert (
        revision_summary_text({"incident": {"title": " Fight ", "description": "Two people."}}) == "Fight\nTwo people."
    )
    assert revision_summary_text({"incident": {"title": None, "description": "Only this."}}) == "Only this."
    assert revision_summary_text({"incident": {}}) == ""
    assert revision_summary_text({"entities": []}) == ""


@pytest.mark.asyncio
async def test_insert_ai_revision_appends_the_next_revision_no_with_facets():
    client, table = _revision_client(last_revision_nos=[2])
    row = await IncidentDB(client).insert_ai_revision(
        "v1", model_run_id="run-1", payload=REPORT_PAYLOAD, evidence=[{"item_id": "E1"}], note="n"
    )
    assert row == {"id": 11}
    inserted = table.insert.call_args.args[0]
    assert inserted["video_id"] == "v1" and inserted["revision_no"] == 3
    assert inserted["kind"] == "ai" and inserted["model_run_id"] == "run-1"
    assert inserted["parent_id"] is None and inserted["created_by"] is None and inserted["note"] == "n"
    assert inserted["payload"] is REPORT_PAYLOAD and inserted["evidence"] == [{"item_id": "E1"}]
    assert (inserted["incident_type"], inserted["severity_level"]) == ("burglary", 2)
    assert (inserted["start_seconds"], inserted["end_seconds"]) == (1, 6)
    assert "+" not in inserted["created_at"]  # naive UTC


@pytest.mark.asyncio
async def test_first_revision_of_a_video_is_number_one():
    client, table = _revision_client(last_revision_nos=[0])
    await IncidentDB(client).insert_ai_revision("v1", model_run_id="r", payload=REPORT_PAYLOAD, evidence=[])
    assert table.insert.call_args.args[0]["revision_no"] == 1


@pytest.mark.asyncio
async def test_insert_ai_revision_retries_a_revision_no_taken_by_a_concurrent_writer():
    clash = APIError({"message": "duplicate key", "code": "23505", "details": "", "hint": ""})
    client, table = _revision_client(
        last_revision_nos=[2, 3], insert_results=[clash, MagicMock(data=[{"id": 12, "revision_no": 4}])]
    )
    row = await IncidentDB(client).insert_ai_revision("v1", model_run_id="r", payload=REPORT_PAYLOAD, evidence=[])
    assert row["id"] == 12
    assert [call.args[0]["revision_no"] for call in table.insert.call_args_list] == [3, 4]


@pytest.mark.asyncio
async def test_insert_ai_revision_three_concurrent_inserts_succeed_with_distinct_revision_nos():
    """3+ concurrent inserts against unique (video_id, revision_no) all succeed with distinct numbers.

    With 5 attempts and jittered backoff, concurrent writers retry on SQLSTATE 23505 and eventually
    each gets a distinct revision_no instead of all aborting after 3 attempts.
    """
    clash = APIError({"message": "duplicate key", "code": "23505", "details": "", "hint": ""})
    client, _table = _revision_client(
        last_revision_nos=[2, 3, 4, 5, 6, 7],
        insert_results=[
            clash,
            MagicMock(data=[{"id": 1, "revision_no": 3}]),
            clash,
            MagicMock(data=[{"id": 2, "revision_no": 5}]),
            clash,
            MagicMock(data=[{"id": 3, "revision_no": 7}]),
        ],
    )
    rows = await asyncio.gather(
        IncidentDB(client).insert_ai_revision("v1", model_run_id="r1", payload=REPORT_PAYLOAD, evidence=[]),
        IncidentDB(client).insert_ai_revision("v1", model_run_id="r2", payload=REPORT_PAYLOAD, evidence=[]),
        IncidentDB(client).insert_ai_revision("v1", model_run_id="r3", payload=REPORT_PAYLOAD, evidence=[]),
    )
    assert len(rows) == 3
    revision_nos = [row["revision_no"] for row in rows]
    assert len(set(revision_nos)) == 3
    assert set(revision_nos) == {3, 5, 7}


@pytest.mark.asyncio
async def test_insert_ai_revision_gives_up_after_repeated_clashes():
    clash = APIError({"message": "duplicate key", "code": "23505", "details": "", "hint": ""})
    client, _ = _revision_client(last_revision_nos=[1, 2, 3, 4, 5], insert_results=[clash] * 5)
    with pytest.raises(RuntimeError, match="revision_no"):
        await IncidentDB(client).insert_ai_revision("v1", model_run_id="r", payload=REPORT_PAYLOAD, evidence=[])


@pytest.mark.asyncio
async def test_insert_ai_revision_does_not_retry_other_errors():
    boom = APIError({"message": "violates foreign key", "code": "23503", "details": "", "hint": ""})
    client, table = _revision_client(last_revision_nos=[1], insert_results=[boom])
    with pytest.raises(APIError):
        await IncidentDB(client).insert_ai_revision("v1", model_run_id="r", payload=REPORT_PAYLOAD, evidence=[])
    assert table.insert.call_count == 1


@pytest.mark.asyncio
async def test_get_revision_returns_none_when_missing():
    mock_client = _make_mock_client()
    db = IncidentDB(mock_client)
    assert await db.get_revision(99) is None
    mock_client.table.assert_called_with("report_revisions")


@pytest.mark.asyncio
async def test_get_revision_returns_the_row():
    mock_client = _make_mock_client()
    mock_client.table().select().maybe_single().execute = AsyncMock(return_value=MagicMock(data={"id": 5}))
    assert await IncidentDB(mock_client).get_revision(5) == {"id": 5}


@pytest.mark.asyncio
async def test_list_revisions_orders_by_revision_no():
    mock_client = _make_mock_client()
    mock_client.table().select().execute = AsyncMock(return_value=MagicMock(data=[{"id": 1}, {"id": 2}]))
    assert await IncidentDB(mock_client).list_revisions("v1") == [{"id": 1}, {"id": 2}]
    mock_client.table().select().order.assert_called_with("revision_no")


@pytest.mark.asyncio
async def test_get_revisions_reads_many_ids_in_one_request_and_none_without_a_request():
    mock_client = _make_mock_client()
    select = mock_client.table().select()
    select.in_ = MagicMock(return_value=select)
    select.execute = AsyncMock(return_value=MagicMock(data=[{"id": 1}, {"id": 2}]))
    db = IncidentDB(mock_client)
    assert await db.get_revisions([1, 2]) == [{"id": 1}, {"id": 2}]
    select.in_.assert_called_once_with("id", [1, 2])
    assert await db.get_revisions([]) == [] and select.execute.await_count == 1


@pytest.mark.asyncio
async def test_newest_revision_ids_picks_the_highest_revision_no_per_video():
    mock_client = _make_mock_client()
    select = mock_client.table().select()
    select.in_ = MagicMock(return_value=select)
    rows = [
        {"id": 7, "video_id": "v1", "revision_no": 1},
        {"id": 9, "video_id": "v1", "revision_no": 3},
        {"id": 8, "video_id": "v1", "revision_no": 2},
        {"id": 4, "video_id": "v2", "revision_no": 1},
    ]
    select.execute = AsyncMock(return_value=MagicMock(data=rows))
    db = IncidentDB(mock_client)
    assert await db.newest_revision_ids(["v1", "v2", "v3"]) == {"v1": 9, "v2": 4}
    mock_client.table().select.assert_called_with("id,video_id,revision_no")
    select.in_.assert_called_once_with("video_id", ["v1", "v2", "v3"])
    assert await db.newest_revision_ids([]) == {} and select.execute.await_count == 1


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (REPORT_PAYLOAD, {"incident_type": "burglary", "severity_level": 2, "start_seconds": 1, "end_seconds": 6}),
        ({}, {"incident_type": None, "severity_level": None, "start_seconds": None, "end_seconds": None}),
        (
            {"incident": {"type": "", "severity_level": True, "start_timestamp": "00:00:01", "end_timestamp": None}},
            {"incident_type": None, "severity_level": None, "start_seconds": None, "end_seconds": None},
        ),
        (
            {"incident": {"start_timestamp": float("nan"), "end_timestamp": 7.6}},
            {"incident_type": None, "severity_level": None, "start_seconds": None, "end_seconds": 8},
        ),
    ],
)
def test_revision_facets_read_the_payload_and_tolerate_malformed_values(payload, expected):
    assert incident_db.revision_facets(payload) == expected


@pytest.mark.asyncio
async def test_get_db_returns_none_when_unconfigured(monkeypatch):
    monkeypatch.delenv("INCIDENT_SUPABASE_URL", raising=False)
    monkeypatch.delenv("INCIDENT_SUPABASE_SERVICE_ROLE_KEY", raising=False)
    assert await incident_db.get_db() is None


@pytest.mark.asyncio
async def test_get_db_returns_none_on_connect_failure(monkeypatch):
    monkeypatch.setenv("INCIDENT_SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("INCIDENT_SUPABASE_SERVICE_ROLE_KEY", "secret")
    with patch.object(IncidentDB, "connect", new=AsyncMock(side_effect=OSError("unreachable"))):
        assert await incident_db.get_db() is None


@pytest.mark.asyncio
async def test_get_db_returns_and_caches_instance(monkeypatch):
    monkeypatch.setenv("INCIDENT_SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("INCIDENT_SUPABASE_SERVICE_ROLE_KEY", "secret")
    mock_client = _make_mock_client()
    with patch("vss_agents.utils.incident_db.acreate_client", new=AsyncMock(return_value=mock_client)):
        first = await incident_db.get_db()
        second = await incident_db.get_db()
    assert first is second
    assert isinstance(first, IncidentDB)


@pytest.mark.asyncio
async def test_get_db_concurrent_callers_do_not_race(monkeypatch):
    """Two coroutines racing on the lazy singleton must share one client, not leak a second."""
    monkeypatch.setenv("INCIDENT_SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("INCIDENT_SUPABASE_SERVICE_ROLE_KEY", "secret")

    async def _slow_create_client(**_kwargs):
        await asyncio.sleep(0.05)
        return _make_mock_client()

    with patch("vss_agents.utils.incident_db.acreate_client", new=AsyncMock(side_effect=_slow_create_client)):
        first, second = await asyncio.gather(incident_db.get_db(), incident_db.get_db())
    assert first is second


def test_utcnow_returns_naive_utc():
    """Timestamps must be naive UTC for PostgREST compatibility."""
    now = incident_db._utcnow()
    assert now.tzinfo is None
    assert abs(_dt.datetime.now(_dt.UTC).replace(tzinfo=None) - now) < _dt.timedelta(minutes=1)
