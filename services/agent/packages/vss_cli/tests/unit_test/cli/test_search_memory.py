# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""CLI search persistence against an in-process memory store."""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any
from unittest.mock import MagicMock

from pydantic import BaseModel
import pytest

from vss_cli import config as config_mod
from vss_cli.exits import Exit
from vss_cli.group import Context
from vss_cli.memory import Memory
from vss_cli.search.group import SearchGroup
from vss_core._foundation.errors import BackendUnreachableError
from vss_core.memory.backends.in_memory import InMemoryStore
from vss_core.memory.service import MemoryService
from vss_core.memory.store import MemoryQuery
from vss_core.search_core.models.search import SearchOutput
from vss_core.search_core.models.search import SearchResult

if TYPE_CHECKING:
    from pathlib import Path


class _EmbedInputs(BaseModel):
    query: str
    video_sources: list[str]
    top_k: int | None = None


def _inputs() -> _EmbedInputs:
    return _EmbedInputs(query="forklift", video_sources=["warehouse-camera"], top_k=10)


def _deployment(
    *,
    persist: bool = True,
    markdown_workspace: str | None = None,
    note_default: bool = False,
) -> config_mod.Deployment:
    return config_mod.Deployment(
        base_url="http://h:7777",
        services={
            "elasticsearch": config_mod.Service(url="http://h:7777/elasticsearch", indices=["mdx-embed-filtered-1"]),
            "rt_embed": config_mod.Service(url="http://h:7777/cosmos-embed", models=["cosmos-embed"]),
        },
        memory=config_mod.MemoryConfig(
            persist_by_default=persist,
            markdown=config_mod.MarkdownMemoryConfig(
                enabled=markdown_workspace is not None,
                workspace=markdown_workspace,
                write_by_default=note_default,
            ),
        ),
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


def test_search_exposes_only_the_safe_persistence_opt_out() -> None:
    options = {option for param in SearchGroup.extra_params for option in (*param.opts, *param.secondary_opts)}
    assert "--no-persist" in options
    assert "--persist" not in options
    assert {"--write-memory-note", "--no-write-memory-note"} <= options


@pytest.fixture
def search_group(monkeypatch: pytest.MonkeyPatch) -> SearchGroup:
    group = SearchGroup()
    monkeypatch.setattr(
        "vss_cli.search.group._runtime_from",
        lambda *_args, **_kwargs: MagicMock(),
    )

    async def _critic(_deployment: Any) -> tuple[None, None]:
        return None, None

    monkeypatch.setattr("vss_cli.search.group._critic_from", _critic)

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


def test_search_succeeds_without_memory_configuration(
    search_group: SearchGroup,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A deployment route is enough to search; absent memory policy means stdout-only.
    dep = config_mod.Deployment(
        base_url="http://h:7777",
        services={"rt_embed": config_mod.Service(url="http://h:7777/cosmos-embed", models=["m"])},
    )
    monkeypatch.setattr("vss_cli.memory.build", lambda *_args, **_kwargs: pytest.fail("memory initialized"))
    ctx = Context(deployment=dep)
    result = search_group.run("embed", _inputs(), ctx)
    assert result.exit == Exit.SUCCESS
    assert result.body["persisted"] is False
    assert len(result.body["data"]) == 2
    assert result.job_id.startswith("search-")
    assert result.body["record"] == "absent"
    assert result.extra["marker"]["persisted"] is False


@pytest.mark.parametrize(
    "memory_config",
    [
        config_mod.MemoryConfig(enabled=False, persist_by_default=False),
        config_mod.MemoryConfig(enabled=True, persist_by_default=False),
    ],
)
def test_search_static_policy_can_disable_automatic_persistence(
    search_group: SearchGroup,
    monkeypatch: pytest.MonkeyPatch,
    memory_config: config_mod.MemoryConfig,
) -> None:
    deployment = _deployment()
    deployment = config_mod.Deployment(
        base_url=deployment.base_url,
        services=deployment.services,
        memory=memory_config,
    )
    monkeypatch.setattr("vss_cli.memory.build", lambda *_args, **_kwargs: pytest.fail("memory initialized"))
    result = search_group.run("embed", _inputs(), Context(deployment=deployment))
    assert result.exit == Exit.SUCCESS
    assert result.body["persisted"] is False
    assert result.body["record"] == "absent"
    assert result.extra["marker"]["persisted"] is False


def test_no_persist_never_initializes_memory(
    search_group: SearchGroup,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("vss_cli.memory.build", lambda *_args, **_kwargs: pytest.fail("memory initialized"))
    ctx = Context(
        deployment=_deployment(),
        extra={"no_persist": True},
    )
    result = search_group.run("embed", _inputs(), ctx)
    assert result.exit == Exit.SUCCESS
    assert result.body["persisted"] is False
    assert result.body["record"] == "absent"


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
    assert result.body["record"] == "closed"
    assert result.extra["marker"]["persisted"] is True
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


def test_search_explicit_note_opt_in_writes_daily_note(
    search_group: SearchGroup,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    service = MemoryService(InMemoryStore())
    result = search_group.run(
        "embed",
        _inputs(),
        Context(
            deployment=_deployment(markdown_workspace=str(workspace)),
            memory=Memory(service, index="vss-memory"),
            extra={"write_memory_note": True},
        ),
    )
    assert result.exit == Exit.SUCCESS
    assert result.body["memory_note"]["written"] is True
    text = workspace.joinpath(result.body["memory_note"]["path"]).read_text(encoding="utf-8")
    assert result.job_id in text
    assert "forklift" in text


def test_search_markdown_failure_keeps_results_and_es_persistence(
    search_group: SearchGroup,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from vss_cli import memory_notes

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    service = MemoryService(InMemoryStore())

    def fail(*_args: Any, **_kwargs: Any) -> Any:
        raise OSError("disk full")

    monkeypatch.setattr(memory_notes, "write", fail)
    result = search_group.run(
        "embed",
        _inputs(),
        Context(
            deployment=_deployment(markdown_workspace=str(workspace), note_default=True),
            memory=Memory(service, index="vss-memory"),
        ),
    )
    assert result.exit == Exit.PARTIAL
    assert len(result.body["data"]) == 2
    assert result.body["persisted"] is True
    assert result.body["memory_note"]["written"] is False
    assert result.extra["marker"]["persisted"] is True
    assert service.get(result.job_id).job.status == "completed"


def test_search_es_failure_prevents_markdown_write(
    search_group: SearchGroup,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from vss_cli import memory_notes

    class _Flaky(InMemoryStore):
        def upsert(self, record: Any) -> Any:
            if getattr(record.job, "record_type", None) == "search_hit":
                raise RuntimeError("child write failed")
            return super().upsert(record)

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(memory_notes, "write", lambda *_args, **_kwargs: pytest.fail("note write attempted"))
    result = search_group.run(
        "embed",
        _inputs(),
        Context(
            deployment=_deployment(markdown_workspace=str(workspace), note_default=True),
            memory=Memory(MemoryService(_Flaky()), index="vss-memory"),
        ),
    )
    assert result.exit == Exit.PARTIAL
    assert result.body["persisted"] is False


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
    assert result.body["record"] == "closed"
    assert result.extra["marker"]["status"] == "partial"
    assert result.extra["marker"]["persisted"] is False


def test_submitted_write_failure_keeps_results_but_exits_partial(search_group: SearchGroup) -> None:
    class _Refusing(InMemoryStore):
        def upsert(self, record: Any) -> Any:
            if record.job.status == "submitted":
                raise BackendUnreachableError("elasticsearch", "read only")
            return super().upsert(record)

    ctx = Context(
        deployment=_deployment(),
        memory=Memory(MemoryService(_Refusing()), index="vss-memory"),
        extra={"persist": True},
    )
    result = search_group.run("embed", _inputs(), ctx)
    assert result.exit == Exit.PARTIAL
    assert len(result.body["data"]) == 2
    assert result.body["persisted"] is False
    assert result.body["record"] == "absent"


def test_search_failure_closes_submitted_parent(search_group: SearchGroup, monkeypatch: pytest.MonkeyPatch) -> None:
    class _FailingVSS:
        async def __aenter__(self) -> Any:
            return self

        async def __aexit__(self, *_args: Any) -> None:
            return None

        async def search(self, **_kwargs: Any) -> SearchOutput:
            raise BackendUnreachableError("search", "offline")

        @classmethod
        def from_runtime(cls, *_args: Any, **_kwargs: Any) -> Any:
            return cls()

    monkeypatch.setattr("vss_core.search_core.host.VSSSearch", _FailingVSS)
    service = MemoryService(InMemoryStore())
    ctx = Context(
        deployment=_deployment(),
        memory=Memory(service, index="vss-memory"),
        extra={"persist": True},
    )
    result = search_group.run("embed", _inputs(), ctx)
    assert result.exit == Exit.BACKEND_UNREACHABLE
    assert result.body["status"] == "failed"
    assert result.body["record"] == "closed"
    assert service.get(result.job_id).job.status == "failed"
    assert result.extra["marker"]["status"] == "failed"


def test_terminal_conversion_failure_keeps_results_and_closes_partial(
    search_group: SearchGroup, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "vss_cli.search.group._search_terminal_bundle",
        lambda **_kwargs: (_ for _ in ()).throw(ValueError("cannot map hits")),
    )
    service = MemoryService(InMemoryStore())
    ctx = Context(
        deployment=_deployment(),
        memory=Memory(service, index="vss-memory"),
        extra={"persist": True},
    )
    result = search_group.run("embed", _inputs(), ctx)
    assert result.exit == Exit.PARTIAL
    assert len(result.body["data"]) == 2
    assert result.body["record"] == "closed"
    assert service.get(result.job_id).job.status == "partial"


def test_terminal_and_close_refusal_marks_handle_stale(search_group: SearchGroup) -> None:
    class _RefusingTerminal(InMemoryStore):
        def upsert(self, record: Any) -> Any:
            if record.job.status in {"completed", "partial"}:
                raise BackendUnreachableError("elasticsearch", "read only")
            return super().upsert(record)

    service = MemoryService(_RefusingTerminal())
    ctx = Context(
        deployment=_deployment(),
        memory=Memory(service, index="vss-memory"),
        extra={"persist": True},
    )
    result = search_group.run("embed", _inputs(), ctx)
    assert result.exit == Exit.PARTIAL
    assert result.body["record"] == "stale"
    assert service.get(result.job_id).job.status == "submitted"


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
    assert len(got.body["children"]) == 2
    assert [child["output"]["ext"]["rank"] for child in got.body["children"]] == [1, 2]
    status = search_group.status(run.job_id, ctx)
    assert status.body["job"]["status"] == "completed"
    assert "children" not in status.body
    listed = search_group.list({}, ctx)
    assert len(listed.body) == 1
    assert "record_id" not in listed.body[0]["job"]
    assert "children" not in listed.body[0]
