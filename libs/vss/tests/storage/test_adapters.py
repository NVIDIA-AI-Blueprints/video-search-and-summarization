# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""S01/S02: public storage conformance, independent of caption schemas.

These expectations precede the M6 implementation. Imports are intentionally lazy:
the suite collects while the public package is absent and fails explicitly on use.
"""

from __future__ import annotations

import asyncio
import importlib
import json
from collections.abc import AsyncIterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from pydantic import ValidationError


@pytest.fixture
def api() -> ModuleType:
    return importlib.import_module("vss.storage")


@pytest.fixture(params=("memory", "file"))
async def adapter(
    api: ModuleType, request: pytest.FixtureRequest, tmp_path: Path
) -> AsyncIterator[Any]:
    instance = (
        api.InMemoryStorageAdapter()
        if request.param == "memory"
        else api.FileStorageAdapter(root=tmp_path / "data")
    )
    async with instance:
        yield instance


async def test_create_read_cas_replaces_records_and_advances_revision(
    api: ModuleType, adapter: Any
) -> None:
    created = await adapter.write(
        "custom_events", "job", [{"value": 1}], expected_revision=None
    )
    assert isinstance(created, api.StorageSnapshot)
    assert created.schema_version == "1.0"
    assert created.revision == 1
    assert created.records == ({"value": 1},)
    assert isinstance(created.records, tuple)
    updated = await adapter.write(
        "custom_events", "job", [{"value": 2}, {"other": True}], expected_revision=1
    )
    assert updated.revision == 2
    assert (await adapter.read("custom_events", "job")) == updated
    assert updated.records == ({"value": 2}, {"other": True})
    with pytest.raises(ValidationError):
        updated.revision = 5


async def test_missing_is_keyerror_but_empty_is_committed_snapshot(
    api: ModuleType, adapter: Any
) -> None:
    with pytest.raises(api.StorageNotFoundError) as caught:
        await adapter.read("observations", "missing")
    assert isinstance(caught.value, KeyError)
    await adapter.write("observations", "empty", [], expected_revision=None)
    empty = await adapter.read("observations", "empty")
    assert empty.records == ()
    assert empty.revision == 1


async def test_namespace_and_opaque_keys_are_independent(adapter: Any) -> None:
    for namespace, key, value in (
        ("observations", "../摄像头/😺", "one"),
        ("checkpoints", "../摄像头/😺", "two"),
        ("observations", "other", "three"),
    ):
        await adapter.write(namespace, key, [{"value": value}], expected_revision=None)
    assert (await adapter.read("observations", "../摄像头/😺")).records == (
        {"value": "one"},
    )
    assert (await adapter.read("checkpoints", "../摄像头/😺")).records == (
        {"value": "two"},
    )
    assert (await adapter.read("observations", "other")).records == (
        {"value": "three"},
    )


async def test_json_types_unicode_and_application_versions_are_preserved(
    adapter: Any,
) -> None:
    record = {
        "schema_version": "owned-by-another-library-v27",
        "text": "café\n摄像头 😺",
        "values": [None, True, False, 0, 2**63 + 1, -3, 1.25, {"nested": []}],
    }
    await adapter.write("custom", "job", [record], expected_revision=None)
    result = await adapter.read("custom", "job")
    assert result.records == (record,)
    assert type(result.records[0]["values"][1]) is bool
    assert type(result.records[0]["values"][3]) is int
    assert json.loads(result.model_dump_json())["records"] == [record]


async def test_write_input_and_every_return_are_deep_detached(adapter: Any) -> None:
    records = [{"nested": {"list": [{"caption": "original"}]}}]
    written = await adapter.write("custom", "job", records, expected_revision=None)
    records[0]["nested"]["list"][0]["caption"] = "caller mutated"
    written.records[0]["nested"]["list"].append({"caption": "returned write mutated"})
    first = await adapter.read("custom", "job")
    assert first.records == ({"nested": {"list": [{"caption": "original"}]}},)
    first.records[0]["nested"]["list"][0]["caption"] = "read mutated"
    second = await adapter.read("custom", "job")
    assert second.records == ({"nested": {"list": [{"caption": "original"}]}},)


async def test_create_only_and_stale_cas_never_overwrite(
    api: ModuleType, adapter: Any
) -> None:
    await adapter.write("custom", "job", [{"value": "old"}], expected_revision=None)
    with pytest.raises(api.StorageConflictError):
        await adapter.write(
            "custom", "job", [{"value": "duplicate"}], expected_revision=None
        )
    await adapter.write("custom", "job", [{"value": "new"}], expected_revision=1)
    with pytest.raises(api.StorageConflictError):
        await adapter.write("custom", "job", [{"value": "stale"}], expected_revision=1)
    with pytest.raises(api.StorageConflictError):
        await adapter.write("custom", "missing", [{}], expected_revision=1)
    current = await adapter.read("custom", "job")
    assert current.revision == 2
    assert current.records == ({"value": "new"},)


@pytest.mark.parametrize("revision", [0, -1, True, False, 1.0, "1"])
async def test_revision_is_a_strict_positive_integer_or_none(
    api: ModuleType, adapter: Any, revision: Any
) -> None:
    with pytest.raises(api.StorageConfigurationError):
        await adapter.write("custom", "job", [{}], expected_revision=revision)


@pytest.mark.parametrize(
    "namespace",
    ["", "../x", "/tmp", "a/b", "a\\b", ".", "Upper", "1job", "é", "a" * 65],
)
async def test_invalid_namespaces_rejected_by_all_operations(
    api: ModuleType, adapter: Any, namespace: str
) -> None:
    with pytest.raises(api.StorageConfigurationError):
        await adapter.read(namespace, "key")
    with pytest.raises(api.StorageConfigurationError):
        await adapter.write(namespace, "key", [], expected_revision=None)
    with pytest.raises(api.StorageConfigurationError):
        async with adapter.exclusive(namespace, "key"):
            pytest.fail("invalid namespace acquired a claim")


@pytest.mark.parametrize(
    "key",
    ["", "a" * 4097, "😺" * 1025],
    ids=["empty", "ascii-over-limit", "multibyte-over-limit"],
)
async def test_key_limit_is_measured_in_utf8_bytes(
    api: ModuleType, adapter: Any, key: str
) -> None:
    with pytest.raises(api.StorageConfigurationError):
        await adapter.write("custom", key, [], expected_revision=None)
    with pytest.raises(api.StorageConfigurationError):
        await adapter.read("custom", key)
    with pytest.raises(api.StorageConfigurationError):
        async with adapter.exclusive("jobs", key):
            pytest.fail("invalid key acquired a claim")


async def test_key_exactly_at_limit_is_supported(adapter: Any) -> None:
    key = "😺" * 1024
    await adapter.write("custom", key, [{"ok": True}], expected_revision=None)
    assert (await adapter.read("custom", key)).records == ({"ok": True},)


@pytest.mark.parametrize(
    "value",
    [
        float("nan"),
        float("inf"),
        -float("inf"),
        b"bytes",
        {1, 2},
        object(),
        {1: "non-string-key"},
    ],
)
async def test_non_json_nested_values_fail_without_replacing_snapshot(
    api: ModuleType, adapter: Any, value: Any
) -> None:
    await adapter.write("custom", "job", [{"value": "old"}], expected_revision=None)
    with pytest.raises(api.StorageConfigurationError):
        await adapter.write("custom", "job", [{"nested": [value]}], expected_revision=1)
    assert (await adapter.read("custom", "job")).records == ({"value": "old"},)


@pytest.mark.parametrize("records", [[None], [1], [[]], ["text"], {"not": "records"}])
async def test_records_must_be_a_sequence_of_json_objects(
    api: ModuleType, adapter: Any, records: Any
) -> None:
    with pytest.raises(api.StorageConfigurationError):
        await adapter.write("custom", "job", records, expected_revision=None)


async def test_cyclic_payload_fails_as_typed_input_error(
    api: ModuleType, adapter: Any
) -> None:
    record: dict[str, Any] = {}
    record["cycle"] = record
    with pytest.raises(api.StorageConfigurationError):
        await adapter.write("custom", "job", [record], expected_revision=None)


@pytest.mark.parametrize("kind", ["memory", "file"])
async def test_size_and_record_limits_preserve_last_commit(
    api: ModuleType, tmp_path: Path, kind: str
) -> None:
    options = {"max_records": 2, "max_snapshot_bytes": 2048}
    store = (
        api.InMemoryStorageAdapter(**options)
        if kind == "memory"
        else api.FileStorageAdapter(root=tmp_path / "data", **options)
    )
    async with store:
        await store.write("custom", "job", [{"small": True}], expected_revision=None)
        for records in ([{}, {}, {}], [{"huge": "😺" * 2048}]):
            with pytest.raises(api.StorageLimitError):
                await store.write("custom", "job", records, expected_revision=1)
            current = await store.read("custom", "job")
            assert current.revision == 1
            assert current.records == ({"small": True},)
        await store.write("custom", "job", [{}, {}], expected_revision=1)


async def test_competing_cas_writers_have_one_winner(
    api: ModuleType, adapter: Any
) -> None:
    await adapter.write("custom", "job", [{"winner": -1}], expected_revision=None)
    outcomes = await asyncio.gather(
        *(
            adapter.write("custom", "job", [{"winner": n}], expected_revision=1)
            for n in range(12)
        ),
        return_exceptions=True,
    )
    assert sum(isinstance(result, api.StorageSnapshot) for result in outcomes) == 1
    assert (
        sum(isinstance(result, api.StorageConflictError) for result in outcomes) == 11
    )
    winner = next(
        result for result in outcomes if isinstance(result, api.StorageSnapshot)
    )
    assert await adapter.read("custom", "job") == winner


async def test_claim_excludes_same_key_but_not_other_jobs_or_snapshot_io(
    api: ModuleType, adapter: Any
) -> None:
    async with adapter.exclusive("jobs", "one"):
        with pytest.raises(api.StorageBusyError):
            async with adapter.exclusive("jobs", "one"):
                pytest.fail("same claim acquired twice")
        async with adapter.exclusive("jobs", "two"):
            await adapter.write(
                "checkpoints", "one", [{"value": 1}], expected_revision=None
            )
            assert (await adapter.read("checkpoints", "one")).revision == 1
    async with adapter.exclusive("jobs", "one"):
        pass


async def test_busy_close_leaves_adapter_open_until_claim_released(
    api: ModuleType, adapter: Any
) -> None:
    async with adapter.exclusive("jobs", "one"):
        with pytest.raises(api.StorageBusyError):
            await adapter.aclose()
        await adapter.write("checkpoints", "one", [{}], expected_revision=None)
        assert (await adapter.read("checkpoints", "one")).revision == 1
    await adapter.aclose()
    await adapter.aclose()
    with pytest.raises(api.StorageClosedError):
        await adapter.read("checkpoints", "one")
    with pytest.raises(api.StorageClosedError):
        await adapter.write("checkpoints", "two", [], expected_revision=None)
    with pytest.raises(api.StorageClosedError):
        async with adapter.exclusive("jobs", "two"):
            pytest.fail("closed adapter acquired claim")


async def test_claim_released_on_exception_and_cancellation(adapter: Any) -> None:
    class CallerFailure(Exception):
        pass

    with pytest.raises(CallerFailure):
        async with adapter.exclusive("jobs", "one"):
            raise CallerFailure
    entered = asyncio.Event()

    async def owner() -> None:
        async with adapter.exclusive("jobs", "one"):
            entered.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(owner())
    await asyncio.wait_for(entered.wait(), timeout=3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    async with adapter.exclusive("jobs", "one"):
        pass


def test_snapshot_and_config_are_versioned_frozen_validated(
    api: ModuleType, tmp_path: Path
) -> None:
    config = api.StorageConfig(backend="file", root=tmp_path)
    assert config.schema_version == "1.0"
    assert config.max_snapshot_bytes == 64 * 1024**2
    assert config.max_records == 100_000
    assert config.max_in_flight == 4
    assert api.StorageConfig.model_validate_json(config.model_dump_json()) == config
    with pytest.raises(ValidationError):
        config.backend = "memory"
    for revision in (0, -1, True, "1"):
        with pytest.raises(ValidationError):
            api.StorageSnapshot(revision=revision, records=())
    with pytest.raises(ValidationError):
        api.StorageSnapshot(schema_version="future", revision=1, records=())


@pytest.mark.parametrize(
    "options",
    [
        {"backend": "es"},
        {"backend": "file", "unknown": 1},
        {"backend": "memory", "root": "/tmp"},
        {"backend": "file", "max_records": True},
        {"backend": "file", "max_records": 0},
        {"backend": "file", "max_snapshot_bytes": -1},
        {"backend": "file", "max_in_flight": False},
        {"backend": "file", "max_in_flight": 1.5},
        {"backend": "file", "schema_version": "future"},
    ],
)
def test_invalid_configuration_fails(api: ModuleType, options: dict[str, Any]) -> None:
    with pytest.raises((ValidationError, api.StorageConfigurationError)):
        api.StorageConfig(**options)


async def test_factory_returns_owned_context_and_truthful_persistence(
    api: ModuleType, tmp_path: Path
) -> None:
    for kind in ("memory", "file"):
        options = {"backend": kind}
        if kind == "file":
            options["root"] = tmp_path / "data"
        adapter = api.create_storage(api.StorageConfig(**options))
        assert isinstance(adapter, api.StorageAdapter)
        assert adapter.persistent is (kind == "file")
        with pytest.raises((AttributeError, TypeError)):
            adapter.persistent = not adapter.persistent
        async with adapter:
            await adapter.write("custom", "job", [{}], expected_revision=None)
        with pytest.raises(api.StorageClosedError):
            await adapter.read("custom", "job")


def test_fresh_file_adapters_work_across_asyncio_run_loops(
    api: ModuleType, tmp_path: Path
) -> None:
    async def write() -> None:
        async with api.FileStorageAdapter(root=tmp_path) as store:
            await store.write("custom", "job", [{"value": 7}], expected_revision=None)

    async def read() -> None:
        async with api.FileStorageAdapter(root=tmp_path) as store:
            assert (await store.read("custom", "job")).records == ({"value": 7},)

    asyncio.run(write())
    asyncio.run(read())
