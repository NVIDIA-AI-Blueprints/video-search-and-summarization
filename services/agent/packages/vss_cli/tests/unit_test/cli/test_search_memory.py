# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""CLI search persistence against an in-process memory store."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from pydantic import BaseModel
import pytest

from vss_cli import config as config_mod
from vss_cli.exits import Exit
from vss_cli.group import Context
from vss_cli.memory import Memory
from vss_cli.search_group import SearchGroup
from vss_core.memory.backends.in_memory import InMemoryStore
from vss_core.memory.service import MemoryService
from vss_core.memory.store import MemoryQuery
from vss_core.search_core.models.search import SearchOutput
from vss_core.search_core.models.search import SearchResult


class _EmbedInputs(BaseModel):
    query: str
    video_sources: list[str]
    top_k: int | None = None


def _inputs() -> _EmbedInputs:
    return _EmbedInputs(query="forklift", video_sources=["warehouse-camera"], top_k=10)


def _deployment() -> config_mod.Deployment:
    return config_mod.Deployment(
        base_url="http://h:7777",
        services={
            "elasticsearch": config_mod.Service(url="http://h:7777/elasticsearch", indices=["mdx-embed-filtered-1"]),
            "rt_embed": config_mod.Service(url="http://h:7777/cosmos-embed", models=["cosmos-embed"]),
        },
    )


def _search_output(n: int = 2) -> SearchOutput:
    return SearchOutput(
        data=[
            SearchResult(
                video_name="warehouse",
                description=f"hit {i}",
                start_time=f"2026-07-22T12:3{i}:04Z",
                end_time=f"2026-07-22T12:3{i}:14Z",
                sensor_id="warehouse-camera",
                screenshot_url=f"https://x/{i}.mp4",
                similarity=0.9 - i * 0.01,
                object_ids=[f"object-{i}"],
            )
            for i in range(1, n + 1)
        ],
        search_messages=[],
    )


@pytest.fixture
def search_group(monkeypatch: pytest.MonkeyPatch) -> SearchGroup:
    group = SearchGroup()
    monkeypatch.setattr(
        "vss_cli.search_group._runtime_from",
        lambda *_args, **_kwargs: MagicMock(),
    )

    async def _critic(_deployment: Any) -> tuple[None, None]:
        return None, None

    monkeypatch.setattr("vss_cli.search_group._critic_from", _critic)

    class _VSS:
        async def __aenter__(self) -> Any:
            return self

        async def __aexit__(self, *_args: Any) -> None:
            return None

        async def search(self, **_kwargs: Any) -> SearchOutput:
            return _search_output(2)

        @classmethod
        def from_runtime(cls, *_args: Any, **_kwargs: Any) -> Any:
            return cls()

    monkeypatch.setattr("vss_core.search_core.host.VSSSearch", _VSS)
    return group


def test_search_succeeds_without_memory_soft_persist(search_group: SearchGroup) -> None:
    # Deployment without Elasticsearch → Memory.build raises; default persist soft-skips.
    dep = config_mod.Deployment(
        base_url="http://h:7777",
        services={"rt_embed": config_mod.Service(url="http://h:7777/cosmos-embed", models=["m"])},
    )
    ctx = Context(deployment=dep)
    result = search_group.run("embed", _inputs(), ctx)
    assert result.exit == Exit.SUCCESS
    assert result.body["persisted"] is False
    assert len(result.body["data"]) == 2
    assert result.job_id.startswith("search-")


def test_no_persist_writes_nothing(search_group: SearchGroup) -> None:
    store = InMemoryStore()
    ctx = Context(
        deployment=_deployment(),
        memory=Memory(MemoryService(store), index="vss-memory"),
        extra={"persist": False},
    )
    result = search_group.run("embed", _inputs(), ctx)
    assert result.exit == Exit.SUCCESS
    assert result.body["persisted"] is False
    assert store._records == {}


def test_persisted_search_parent_and_children(search_group: SearchGroup) -> None:
    store = InMemoryStore()
    service = MemoryService(store)
    ctx = Context(
        deployment=_deployment(),
        memory=Memory(service, index="vss-memory"),
        extra={"persist": True},
    )
    result = search_group.run("embed", _inputs(), ctx)
    assert result.exit == Exit.SUCCESS
    assert result.body["persisted"] is True
    parent = service.get(result.job_id)
    assert parent.job.group == "search"
    assert parent.job.record_id is None
    dump = parent.model_dump_memory()
    assert "results" not in dump.get("output", {}).get("ext", {})
    children = service.query(MemoryQuery(job_id=result.job_id, record_type="search_hit", limit=10))
    assert len(children) == 2
    assert all(c.job.record_type == "search_hit" for c in children)
    assert children[0].output is not None
    assert children[0].output.ext is not None
    assert "rank" in children[0].output.ext
    assert result.body["data"][0]["description"] == "hit 1"


def test_child_write_failure_preserves_search_result(search_group: SearchGroup) -> None:
    class _Flaky(InMemoryStore):
        def upsert(self, record: Any) -> Any:
            if getattr(record.job, "record_type", None) == "search_hit":
                raise RuntimeError("child write failed")
            return super().upsert(record)

    store = _Flaky()
    ctx = Context(
        deployment=_deployment(),
        memory=Memory(MemoryService(store), index="vss-memory"),
        extra={"persist": True},
    )
    result = search_group.run("embed", _inputs(), ctx)
    assert result.exit == Exit.PARTIAL
    assert result.body["persisted"] is False
    assert len(result.body["data"]) == 2
    assert result.body["data"][0]["description"] == "hit 1"


def test_search_get_status_list_parent_oriented(search_group: SearchGroup) -> None:
    store = InMemoryStore()
    service = MemoryService(store)
    ctx = Context(
        deployment=_deployment(),
        memory=Memory(service, index="vss-memory"),
        extra={"persist": True},
    )
    run = search_group.run("embed", _inputs(), ctx)
    got = search_group.get(run.job_id, ctx)
    assert got.body["job"]["job_id"] == run.job_id
    assert "record_id" not in got.body["job"]
    status = search_group.status(run.job_id, ctx)
    assert status.body["job"]["status"] == "completed"
    listed = search_group.list({}, ctx)
    assert len(listed.body) == 1
    assert "record_id" not in listed.body[0]["job"]
