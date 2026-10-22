# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""E01–E06: pre-implementation ES contract and same-host ownership regressions.

The stateful double models ES create/CAS/realtime GET and injected failures;
native acceptance must additionally exercise an installed ES server/client.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import importlib
import json
import os
import subprocess
import sys
import threading
import traceback
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError
from test_checkpoint_pipeline import pipeline as pipeline
from test_checkpoint_pipeline import submit

SECRET = "fabricated-es-credential-do-not-log"


class APIError(Exception):
    def __init__(self, status: int):
        self.status_code = status
        super().__init__(SECRET)


class State:
    def __init__(self):
        self.docs: dict[tuple[str, str], dict[str, Any]] = {}
        self.mapping: dict[str, Any] | None = None
        self.durability = "request"
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.error: Exception | None = None
        self.fail_after_commit = False
        self.failed_shards = False
        self.missing_concurrency = False
        self.entered = asyncio.Event()
        self.release: asyncio.Event | None = None
        self.active = 0
        self.peak = 0
        self.default_pipeline = "_none"
        self.final_pipeline = "_none"
        self.source_mode = "stored"
        self.index_mode = "standard"
        self.defaults_only = False
        self.get_barrier: asyncio.Barrier | None = None


class Indices:
    def __init__(self, state: State):
        self.state = state

    async def get_mapping(self, *, index, **kwargs):
        self.state.calls.append(("mapping", {"index": index, **kwargs}))
        if self.state.mapping is None:
            raise APIError(404)
        return {index: {"mappings": copy.deepcopy(self.state.mapping)}}

    async def create(self, *, index, mappings, settings, **kwargs):
        self.state.calls.append(
            (
                "create-index",
                {"index": index, "mappings": mappings, "settings": settings},
            )
        )
        if self.state.mapping is not None:
            raise APIError(400)  # resource_already_exists_exception race
        self.state.mapping = copy.deepcopy(mappings)
        return {"acknowledged": True, "shards_acknowledged": True}

    async def get_settings(self, *, index, **kwargs):
        self.state.calls.append(("settings", {"index": index, **kwargs}))
        result = {
            index: {
                "settings": {
                    "index.translog.durability": self.state.durability,
                    "index.default_pipeline": self.state.default_pipeline,
                    "index.final_pipeline": self.state.final_pipeline,
                    "index.mapping.source.mode": self.state.source_mode,
                    "index.mode": self.state.index_mode,
                },
                "defaults": {"index.translog.durability": "request"},
            }
        }
        if self.state.defaults_only:
            result[index]["defaults"] = result[index]["settings"]
            result[index]["settings"] = {}
        return result


class Client:
    def __init__(self, state: State | None = None):
        self.state = state or State()
        self.indices = Indices(self.state)
        self.closed = False

    def options(self, **kwargs):
        self.state.calls.append(("options", kwargs))
        assert kwargs["max_retries"] == 0
        assert kwargs["retry_on_timeout"] is False
        configured = copy.copy(self)
        configured._configured = True
        return configured

    async def get(self, *, index, id, **kwargs):
        self.state.calls.append(("get", {"index": index, "id": id, **kwargs}))
        identity = index, id
        if identity not in self.state.docs:
            raise APIError(404)
        stored = self.state.docs[identity]
        result = {"_source": copy.deepcopy(stored["document"])}
        if not self.state.missing_concurrency:
            result.update(_seq_no=stored["seq"], _primary_term=stored.get("term", 1))
        if self.state.get_barrier is not None:
            await self.state.get_barrier.wait()
        return result

    async def index(self, *, index, id, document, **kwargs):
        assert getattr(self, "_configured", False), "adapter ignored configured client"
        state = self.state
        state.calls.append(("index", {"index": index, "id": id, **kwargs}))
        state.active += 1
        state.peak = max(state.peak, state.active)
        state.entered.set()
        try:
            if state.release is not None:
                await state.release.wait()
            if state.error is not None and not state.fail_after_commit:
                raise state.error
            identity = index, id
            previous = state.docs.get(identity)
            if kwargs.get("op_type") == "create":
                if previous is not None:
                    raise APIError(409)
            else:
                assert kwargs.get("if_seq_no") is not None
                assert kwargs.get("if_primary_term") is not None
                if (
                    previous is None
                    or previous["seq"] != kwargs["if_seq_no"]
                    or previous.get("term", 1) != kwargs["if_primary_term"]
                ):
                    raise APIError(409)
            seq = previous["seq"] + 1 if previous else 0
            state.docs[identity] = {"document": copy.deepcopy(document), "seq": seq}
            if state.error is not None:
                raise state.error
            return {
                "_seq_no": seq,
                "_primary_term": 1,
                "_shards": {
                    "total": 1,
                    "successful": 1,
                    "failed": int(state.failed_shards),
                },
            }
        finally:
            state.active -= 1

    async def close(self):
        self.closed = True


def adapter(tmp_path: Path, client: Client, **kwargs):
    from vss.storage import ElasticsearchConfig, ElasticsearchStorageAdapter

    config = ElasticsearchConfig(
        url="https://es.test:9200", index="vss-test", claim_root=tmp_path / "claims"
    )
    return ElasticsearchStorageAdapter(config=config, client=client, **kwargs)


async def test_es_domain_work_uses_adapter_admission_for_checkpoint_and_summary(
    tmp_path,
):
    from test_persistence_workers import _checkpoint_records

    import vss.ingestion.persistence as persistence
    from vss.storage import StorageBusyError

    store = adapter(tmp_path, Client(State()), max_in_flight=1)
    manifest = persistence.CheckpointManifest.model_validate(_checkpoint_records())
    entered, release = threading.Event(), threading.Event()
    original = persistence.checkpoint_records
    summary_started = threading.Event()

    def checkpoint_codec(*args):
        entered.set()
        if not release.wait(3):
            raise TimeoutError("test did not release checkpoint codec")
        return original(*args)

    def summary_codec(*args):
        summary_started.set()
        return persistence.observation_records(*args)

    first = asyncio.create_task(
        persistence.domain_work(store, checkpoint_codec, manifest, {})
    )
    second = None
    try:
        async with asyncio.timeout(2):
            while not entered.is_set():
                await asyncio.sleep(0.005)
        second = asyncio.create_task(
            persistence.domain_work(store, summary_codec, "job", [])
        )
        await asyncio.sleep(0.05)
        assert not summary_started.is_set(), "ES max_in_flight was bypassed"
        with pytest.raises(StorageBusyError):
            async with store.exclusive("jobs", "job"):
                await store.aclose()
    finally:
        release.set()
        await asyncio.gather(
            *([first, second] if second is not None else [first]),
            return_exceptions=True,
        )
        await store.aclose()
    assert first.result()[0]["record_type"] == "checkpoint"
    assert second is not None
    assert second.result()[0]["record_type"] == "observation"


@pytest.mark.parametrize("field", ["claim_root", "ca_certs"])
def test_es_config_rejects_empty_filesystem_paths(field):
    from vss.storage import ElasticsearchConfig

    with pytest.raises(ValidationError):
        ElasticsearchConfig.model_validate({"url": "https://es.test", field: ""})


@pytest.mark.parametrize("error_type", ["ConnectionError", "ConnectionTimeout"])
@pytest.mark.parametrize("exists", [False, True])
async def test_native_fixture_readiness_retries_only_startup_connections(
    monkeypatch, error_type, exists
):
    es = pytest.importorskip("elasticsearch")
    import test_elasticsearch_native as native

    attempts = []
    sleeps = []
    real_sleep = asyncio.sleep

    async def probe(**kwargs):
        attempts.append(kwargs)
        if len(attempts) < 3:
            raise getattr(es, error_type)("startup")
        return exists

    async def fast_sleep(delay):
        sleeps.append(delay)
        await real_sleep(0)

    monkeypatch.setattr(native.asyncio, "sleep", fast_sleep)
    client = SimpleNamespace(indices=SimpleNamespace(exists=probe))
    assert await native._initial_index_probe(client, "disposable-index") is exists
    assert attempts == [{"index": "disposable-index"}] * 3
    assert len(sleeps) == 2


async def test_native_fixture_unreachable_server_fails_clearly():
    es = pytest.importorskip("elasticsearch")
    import test_elasticsearch_native as native

    async def probe(**kwargs):
        raise es.ConnectionError("startup")

    client = SimpleNamespace(indices=SimpleNamespace(exists=probe))
    with pytest.raises(pytest.fail.Exception, match="did not become reachable"):
        await native._initial_index_probe(client, "disposable-index", timeout_s=0.01)


async def test_native_fixture_does_not_retry_permission_errors():
    pytest.importorskip("elasticsearch")
    import test_elasticsearch_native as native

    attempts = []

    async def probe(**kwargs):
        attempts.append(kwargs)
        raise APIError(403)

    client = SimpleNamespace(indices=SimpleNamespace(exists=probe))
    with pytest.raises(APIError):
        await native._initial_index_probe(client, "disposable-index")
    assert attempts == [{"index": "disposable-index"}]


def test_es_configuration_roundtrip_keeps_persistence_opt_in(tmp_path):
    from vss.storage import ElasticsearchConfig, StorageConfig

    config = StorageConfig(
        backend="elasticsearch",
        elasticsearch=ElasticsearchConfig(
            url="https://es.test:9200", index="vss-test", claim_root=tmp_path
        ),
    )
    assert StorageConfig.model_validate_json(config.model_dump_json()) == config
    assert StorageConfig().backend == "memory"
    assert "api_key" not in config.model_dump_json()


@pytest.mark.parametrize(
    "payload",
    [
        {"backend": "elasticsearch"},
        {"backend": "elasticsearch", "elasticsearch": {"url": "ftp://es.test"}},
        {
            "backend": "elasticsearch",
            "elasticsearch": {"url": "https://user:pass@es.test"},
        },
        {
            "backend": "elasticsearch",
            "elasticsearch": {"url": "https://es.test/?key=secret"},
        },
        {
            "backend": "elasticsearch",
            "elasticsearch": {"url": "https://es.test/#secret"},
        },
        {
            "backend": "elasticsearch",
            "elasticsearch": {"url": "https://es.test", "index": "*"},
        },
        {
            "backend": "elasticsearch",
            "elasticsearch": {"url": "https://es.test", "index": "UPPER"},
        },
        {
            "backend": "elasticsearch",
            "elasticsearch": {"url": "https://es.test", "request_timeout": True},
        },
        {
            "backend": "elasticsearch",
            "elasticsearch": {"url": "https://es.test", "request_timeout": 0},
        },
        {
            "backend": "elasticsearch",
            "elasticsearch": {"url": "https://es.test", "api_key": SECRET},
        },
        {"backend": "file", "elasticsearch": {"url": "https://es.test"}},
        {
            "backend": "elasticsearch",
            "root": "/tmp",
            "elasticsearch": {"url": "https://es.test"},
        },
    ],
)
def test_invalid_es_config_fails_before_connecting(payload):
    from vss.storage import StorageConfig

    with pytest.raises(ValidationError):
        StorageConfig.model_validate(payload)


def test_rejected_nested_and_direct_config_does_not_render_credentials():
    from vss.storage import ElasticsearchConfig, StorageConfig

    unsafe = {"url": f"https://user:{SECRET}@es.test"}
    for model, value in (
        (ElasticsearchConfig, unsafe),
        (StorageConfig, {"backend": "elasticsearch", "elasticsearch": unsafe}),
    ):
        with pytest.raises(ValidationError) as caught:
            model.model_validate(value)
        assert SECRET not in str(caught.value)


def test_missing_es_extra_is_actionable_without_loading_optional_client(monkeypatch):
    from vss.storage import ElasticsearchConfig, ElasticsearchStorageAdapter

    original = importlib.import_module

    def missing(name, *args, **kwargs):
        if name == "elasticsearch":
            raise ImportError("optional runtime intentionally absent")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(importlib, "import_module", missing)
    from vss.storage import StorageConfigurationError

    with pytest.raises(StorageConfigurationError, match=r"vss\[storage\]"):
        ElasticsearchStorageAdapter(config=ElasticsearchConfig(url="https://es.test"))


async def test_es_create_cas_read_detachment_and_realtime(tmp_path):
    from vss.storage import StorageConflictError, StorageNotFoundError

    client = Client()
    async with adapter(tmp_path, client) as store:
        assert store.persistent
        with pytest.raises(StorageNotFoundError):
            await store.read("observations", "absent")
        values = [{"nested": {"unicode": "café", "nanos": 2_000_000_000_000_000_001}}]
        one = await store.write(
            "observations", "opaque/摄像头", values, expected_revision=None
        )
        assert one.revision == 1
        values[0]["nested"]["unicode"] = "changed"
        loaded = await store.read("observations", "opaque/摄像头")
        assert loaded == one
        loaded.records[0]["nested"]["unicode"] = "caller mutation"
        assert await store.read("observations", "opaque/摄像头") == one
        two = await store.write(
            "observations", "opaque/摄像头", [], expected_revision=1
        )
        assert two.revision == 2 and two.records == ()
        for revision in (None, 1):
            with pytest.raises(StorageConflictError):
                await store.write(
                    "observations", "opaque/摄像头", [], expected_revision=revision
                )
        with pytest.raises(StorageConflictError):
            await store.write("observations", "missing", [], expected_revision=1)
    assert not client.closed
    calls = [kwargs for op, kwargs in client.state.calls if op == "index"]
    assert calls[0]["op_type"] == "create"
    assert calls[1]["if_seq_no"] == 0 and calls[1]["if_primary_term"] == 1
    assert [kwargs for op, kwargs in client.state.calls if op == "options"]
    assert all(
        kwargs.get("realtime") is True
        for op, kwargs in client.state.calls
        if op == "get"
    )
    mapping = client.state.mapping
    assert mapping["_meta"]["vss_storage_format"] == "1.0"
    assert mapping["properties"]["records"]["enabled"] is False


async def test_two_clients_cas_has_only_one_winner(tmp_path):
    from vss.storage import StorageConflictError

    state = State()
    async with (
        adapter(tmp_path, Client(state)) as first,
        adapter(tmp_path, Client(state)) as second,
    ):
        await first.write("data", "job", [{"v": 0}], expected_revision=None)
        state.get_barrier = asyncio.Barrier(2)
        results = await asyncio.wait_for(
            asyncio.gather(
                first.write("data", "job", [{"v": 1}], expected_revision=1),
                second.write("data", "job", [{"v": 2}], expected_revision=1),
                return_exceptions=True,
            ),
            timeout=2,
        )
        state.get_barrier = None
        assert sum(isinstance(result, StorageConflictError) for result in results) == 1
        assert (await first.read("data", "job")).revision == 2


@pytest.mark.parametrize(
    "field,value",
    [
        ("revision", True),
        ("record_count", True),
        ("schema_version", "9"),
        ("namespace", "wrong"),
        ("key", "wrong"),
    ],
)
async def test_checksum_valid_schema_damage_is_rejected(tmp_path, field, value):
    from vss.storage import StorageCorruptError

    client = Client()
    async with adapter(tmp_path, client) as store:
        await store.write("data", "job", [{"v": 1}], expected_revision=None)
        document = next(iter(client.state.docs.values()))["document"]
        document[field] = value
        fields = {key: item for key, item in document.items() if key != "checksum"}
        document["checksum"] = hashlib.sha256(
            json.dumps(
                fields,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        with pytest.raises(StorageCorruptError):
            await store.read("data", "job")


async def test_read_side_es_limits_are_enforced(tmp_path):
    from vss.storage import StorageLimitError

    client = Client()
    async with adapter(tmp_path, client) as store:
        await store.write("data", "job", [{"v": "x" * 600}, {}], expected_revision=None)
    for limits in ({"max_records": 1}, {"max_snapshot_bytes": 512}):
        async with adapter(tmp_path, client, **limits) as smaller:
            with pytest.raises(StorageLimitError):
                await smaller.read("data", "job")


@pytest.mark.parametrize(
    "field,value",
    [
        ("revision", True),
        ("schema_version", "9"),
        ("key", "wrong"),
        ("records", [{"changed": True}]),
    ],
)
async def test_es_corruption_fails_closed(tmp_path, field, value):
    from vss.storage import StorageCorruptError

    client = Client()
    async with adapter(tmp_path, client) as store:
        await store.write("data", "job", [{"v": 0}], expected_revision=None)
        next(iter(client.state.docs.values()))["document"][field] = value
        with pytest.raises(StorageCorruptError):
            await store.read("data", "job")


async def test_missing_es_concurrency_metadata_never_unconditionally_updates(tmp_path):
    from vss.storage import StorageCorruptError

    client = Client()
    async with adapter(tmp_path, client) as store:
        await store.write("data", "job", [], expected_revision=None)
        client.state.missing_concurrency = True
        with pytest.raises(StorageCorruptError):
            await store.write("data", "job", [], expected_revision=1)
    assert len([op for op, _ in client.state.calls if op == "index"]) == 1


async def test_es_limits_and_invalid_json_fail_before_indexing(tmp_path):
    from vss.storage import StorageConfigurationError, StorageLimitError

    client = Client()
    async with adapter(
        tmp_path, client, max_records=1, max_snapshot_bytes=512
    ) as store:
        with pytest.raises(StorageLimitError):
            await store.write("data", "job", [{}, {}], expected_revision=None)
        with pytest.raises(StorageLimitError):
            await store.write(
                "data", "job", [{"v": "x" * 1000}], expected_revision=None
            )
        with pytest.raises(StorageConfigurationError):
            await store.write(
                "data", "job", [{"v": float("nan")}], expected_revision=None
            )
    assert not [op for op, _ in client.state.calls if op == "index"]


@pytest.mark.parametrize(
    "unsafe",
    [
        "mapping",
        "durability",
        "source-disabled",
        "source-pruned",
        "synthetic",
        "default-pipeline",
        "final-pipeline",
    ],
)
async def test_existing_unsafe_es_index_is_rejected(tmp_path, unsafe):
    from vss.storage import StorageConfigurationError

    client = Client()
    async with adapter(tmp_path, client) as store:
        await store.write("data", "seed", [], expected_revision=None)
    if unsafe == "mapping":
        client.state.mapping["_meta"]["vss_storage_format"] = "unrelated"
    elif unsafe == "durability":
        client.state.durability = "async"
    elif unsafe == "source-disabled":
        client.state.mapping["_source"] = {"enabled": False}
    elif unsafe == "source-pruned":
        client.state.mapping["_source"] = {"excludes": ["records"]}
    elif unsafe == "synthetic":
        client.state.source_mode = "synthetic"
    elif unsafe == "default-pipeline":
        client.state.default_pipeline = "transform-snapshots"
    else:
        client.state.final_pipeline = "transform-snapshots"
    before = len([op for op, _ in client.state.calls if op == "index"])
    async with adapter(tmp_path, client) as store:
        with pytest.raises(StorageConfigurationError):
            await store.write("data", "job", [], expected_revision=None)
    assert len([op for op, _ in client.state.calls if op == "index"]) == before


async def test_native_uppercase_default_enums_are_accepted_without_weakening_safety(
    tmp_path,
):
    client = Client()
    client.state.defaults_only = True
    client.state.source_mode = "STORED"
    client.state.durability = "REQUEST"
    async with adapter(tmp_path, client) as store:
        assert (
            await store.write("data", "job", [], expected_revision=None)
        ).revision == 1


@pytest.mark.parametrize("committed", [False, True])
async def test_write_timeout_is_indeterminate_safe_and_never_retried(
    tmp_path, committed
):
    from vss.storage import StorageIndeterminateError

    client = Client()
    client.state.error = TimeoutError(SECRET)
    client.state.fail_after_commit = committed
    async with adapter(tmp_path, client) as store:
        with pytest.raises(StorageIndeterminateError) as caught:
            await store.write("data", "job", [], expected_revision=None)
        assert SECRET not in "".join(traceback.format_exception(caught.value))
        assert bool(client.state.docs) is committed
    assert len([op for op, _ in client.state.calls if op == "index"]) == 1


async def test_delayed_timed_out_cas_cannot_overwrite_reconciled_snapshot(tmp_path):
    from vss.storage import StorageIndeterminateError

    class DeferredClient(Client):
        async def index(self, **kwargs):
            if getattr(self.state, "defer", False):
                self.state.defer = False
                self.state.delayed = copy.deepcopy(kwargs)
                raise TimeoutError(SECRET)
            return await super().index(**kwargs)

    client = DeferredClient()
    async with adapter(tmp_path, client) as store:
        await store.write("data", "job", [{"v": "initial"}], expected_revision=None)
        client.state.defer = True
        with pytest.raises(StorageIndeterminateError):
            await store.write("data", "job", [{"v": "old-worker"}], expected_revision=1)
        # Reconciliation commits first. The old server request may still arrive.
        await store.write("data", "job", [{"v": "reconciled"}], expected_revision=1)
        with pytest.raises(APIError) as caught:
            await (
                Client(client.state)
                .options(max_retries=0, retry_on_timeout=False)
                .index(**client.state.delayed)
            )
        assert caught.value.status_code == 409
        assert (await store.read("data", "job")).records == ({"v": "reconciled"},)


async def test_partial_shard_failure_is_indeterminate(tmp_path):
    from vss.storage import StorageIndeterminateError

    client = Client()
    client.state.failed_shards = True
    async with adapter(tmp_path, client) as store:
        with pytest.raises(StorageIndeterminateError):
            await store.write("data", "job", [], expected_revision=None)


async def test_unrecognized_response_failure_after_commit_is_indeterminate(tmp_path):
    from vss.storage import StorageIndeterminateError

    client = Client()
    client.state.error = RuntimeError(SECRET)
    client.state.fail_after_commit = True
    async with adapter(tmp_path, client) as store:
        with pytest.raises(StorageIndeterminateError) as caught:
            await store.write("data", "job", [], expected_revision=None)
        assert client.state.docs  # server committed before response failed
        assert SECRET not in "".join(traceback.format_exception(caught.value))


@pytest.mark.parametrize("status", [200, 302, 408, 499])
async def test_non_rejection_http_failure_after_commit_is_indeterminate(
    tmp_path, status
):
    from vss.storage import StorageIndeterminateError

    client = Client()
    client.state.error = APIError(status)
    client.state.fail_after_commit = True
    async with adapter(tmp_path, client) as store:
        with pytest.raises(StorageIndeterminateError):
            await store.write("data", "job", [], expected_revision=None)
        assert client.state.docs


async def test_official_sdk_serialization_failure_after_commit_is_indeterminate(
    tmp_path,
):
    transport = pytest.importorskip("elastic_transport")
    from vss.storage import StorageIndeterminateError

    client = Client()
    client.state.error = transport.SerializationError(SECRET)
    client.state.fail_after_commit = True
    async with adapter(tmp_path, client) as store:
        with pytest.raises(StorageIndeterminateError):
            await store.write("data", "job", [], expected_revision=None)
        assert client.state.docs


async def test_final_snapshot_validation_runs_off_event_loop(
    tmp_path, monkeypatch, worker_gate
):
    module = importlib.import_module("vss.storage.elasticsearch")
    original = module.StorageSnapshot

    def blocked(**kwargs):
        worker_gate.block()
        return original(**kwargs)

    monkeypatch.setattr(module, "StorageSnapshot", blocked)
    async with adapter(tmp_path, Client()) as store:
        write = asyncio.create_task(
            store.write("data", "job", [], expected_revision=None)
        )
        try:
            await worker_gate.wait_until_blocked(write)
            worker_gate.release.set()
            result = await asyncio.wait_for(write, 10)
            assert result.revision == 1 and result.records == ()
        finally:
            worker_gate.release.set()
            await asyncio.gather(write, return_exceptions=True)


async def test_non_utf8_record_text_is_typed_input_failure(tmp_path):
    from vss.storage import StorageConfigurationError

    client = Client()
    async with adapter(tmp_path, client) as store:
        with pytest.raises(StorageConfigurationError):
            await store.write(
                "data", "job", [{"text": "\ud800"}], expected_revision=None
            )
    assert not [op for op, _ in client.state.calls if op == "index"]


@pytest.mark.parametrize("status", [401, 403, 500])
async def test_provider_errors_are_typed_and_sanitized(tmp_path, status):
    from vss.storage import StorageError, StorageIndeterminateError

    client = Client()
    client.state.error = APIError(status)
    async with adapter(tmp_path, client) as store:
        with pytest.raises(StorageError) as caught:
            await store.write("data", "job", [], expected_revision=None)
        if status == 500:
            assert isinstance(caught.value, StorageIndeterminateError)
        assert SECRET not in "".join(traceback.format_exception(caught.value))


async def test_same_host_claims_prevent_duplicate_owners_and_close_is_busy(tmp_path):
    from vss.storage import StorageBusyError

    state = State()
    async with (
        adapter(tmp_path, Client(state)) as first,
        adapter(tmp_path, Client(state)) as second,
    ):
        async with first.exclusive("jobs", "job"):
            with pytest.raises(StorageBusyError):
                async with second.exclusive("jobs", "job"):
                    pytest.fail("duplicate owner")
            with pytest.raises(StorageBusyError):
                await first.aclose()
            async with second.exclusive("jobs", "other"):
                await second.write("data", "other", [], expected_revision=None)
        async with second.exclusive("jobs", "job"):
            await second.write("data", "job", [], expected_revision=None)


async def test_cancelled_write_settles_before_releasing_claim(tmp_path):
    from vss.storage import StorageBusyError

    state = State()
    state.release = asyncio.Event()
    async with (
        adapter(tmp_path, Client(state)) as first,
        adapter(tmp_path, Client(state)) as second,
    ):

        async def work():
            async with first.exclusive("jobs", "job"):
                await first.write("data", "job", [], expected_revision=None)

        task = asyncio.create_task(work())
        try:
            await asyncio.wait_for(state.entered.wait(), 2)
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            with pytest.raises(StorageBusyError):
                async with second.exclusive("jobs", "job"):
                    pytest.fail("claim released while request was in flight")
        finally:
            state.release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        async with second.exclusive("jobs", "job"):
            assert (await second.read("data", "job")).revision == 1


async def test_close_drains_network_and_admission_is_bounded(tmp_path):
    from vss.storage import StorageClosedError

    state = State()
    state.release = asyncio.Event()
    store = adapter(tmp_path, Client(state), max_in_flight=2)
    tasks = [
        asyncio.create_task(store.write("data", str(i), [], expected_revision=None))
        for i in range(4)
    ]
    closing = None
    try:
        await asyncio.wait_for(state.entered.wait(), 2)
        closing = asyncio.create_task(store.aclose())
        await asyncio.sleep(0.02)
        assert not closing.done()
        with pytest.raises(StorageClosedError):
            await store.read("data", "new")
        assert state.peak <= 2
    finally:
        state.release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        if closing is not None:
            await closing
        else:
            await store.aclose()
    assert state.peak <= 2


async def test_owned_client_close_settles_cancellation_and_closes_once(
    tmp_path, monkeypatch
):
    from vss.storage import ElasticsearchConfig, ElasticsearchStorageAdapter

    entered, release = asyncio.Event(), asyncio.Event()
    closes = []
    kwargs_seen = []

    class Owned(Client):
        async def close(self):
            closes.append(True)
            entered.set()
            await release.wait()
            self.closed = True

    client = Owned()

    def factory(url, **kwargs):
        kwargs_seen.append(kwargs)
        client._configured = True
        return client

    original = importlib.import_module
    monkeypatch.setattr(
        importlib,
        "import_module",
        lambda name: (
            SimpleNamespace(AsyncElasticsearch=factory)
            if name == "elasticsearch"
            else original(name)
        ),
    )
    monkeypatch.setenv("VSS_STORAGE_ES_API_KEY", SECRET)
    store = ElasticsearchStorageAdapter(
        config=ElasticsearchConfig(url="https://es.test", claim_root=tmp_path)
    )
    assert kwargs_seen[0]["api_key"] == SECRET
    assert kwargs_seen[0]["max_retries"] == 0
    assert SECRET not in repr(store.config)
    first = asyncio.create_task(store.aclose())
    second = None
    try:
        await asyncio.wait_for(entered.wait(), 2)
        first.cancel()
        second = asyncio.create_task(store.aclose())
        await asyncio.sleep(0)
        first.cancel()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await first
    if second is not None:
        await second
    assert client.closed and len(closes) == 1
    assert store._coordinator._state == "closed"


async def test_owned_client_close_failure_still_releases_coordinator(
    tmp_path, monkeypatch
):
    from vss.storage import (
        ElasticsearchConfig,
        ElasticsearchStorageAdapter,
        StorageError,
    )

    class Broken(Client):
        async def close(self):
            raise RuntimeError(SECRET)

    original = importlib.import_module
    monkeypatch.setattr(
        importlib,
        "import_module",
        lambda name: (
            SimpleNamespace(AsyncElasticsearch=lambda *args, **kwargs: Broken())
            if name == "elasticsearch"
            else original(name)
        ),
    )
    store = ElasticsearchStorageAdapter(
        config=ElasticsearchConfig(url="https://es.test", claim_root=tmp_path)
    )
    with pytest.raises(StorageError) as caught:
        await store.aclose()
    assert SECRET not in "".join(traceback.format_exception(caught.value))
    assert store._coordinator._state == "closed"


async def test_subprocess_es_claim_releases_on_process_death(tmp_path):
    from vss.storage import StorageBusyError

    ready = tmp_path / "ready"
    script = """
import asyncio, pathlib, sys
from vss.storage import ElasticsearchConfig, ElasticsearchStorageAdapter
class Client:
 def options(self, **kwargs): return self
async def main():
 config=ElasticsearchConfig(url='https://es.test:9200',index='vss-test',claim_root=pathlib.Path(sys.argv[1])/'claims')
 async with ElasticsearchStorageAdapter(config=config,client=Client()) as store:
  async with store.exclusive('jobs','job'):
   pathlib.Path(sys.argv[2]).write_text('ready')
   await asyncio.Event().wait()
asyncio.run(main())
"""
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(tmp_path), str(ready)],
        env=dict(
            os.environ,
            PYTHONPATH=str(Path("src").resolve()),
        ),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:
        async with adapter(tmp_path, Client()) as store:

            async def wait_ready():
                while not ready.exists():
                    if process.poll() is not None:
                        raise AssertionError("claim child exited before readiness")
                    await asyncio.sleep(0.01)

            await asyncio.wait_for(wait_ready(), 5)
            with pytest.raises(StorageBusyError):
                async with store.exclusive("jobs", "job"):
                    pytest.fail("OS claim not shared across processes")
            process.kill()
            await asyncio.to_thread(process.wait, timeout=5)
            async with store.exclusive("jobs", "job"):
                pass
    finally:
        if process.poll() is None:
            process.kill()
        await asyncio.to_thread(process.wait, timeout=5)
        if process.stderr is not None:
            process.stderr.close()


def test_environment_es_selection_excludes_credentials_and_config_overrides_env(
    monkeypatch,
):
    from vss.ingestion.persistence import environment_storage_config

    monkeypatch.setenv("VSS_STORAGE_BACKEND", "elasticsearch")
    monkeypatch.setenv("VSS_STORAGE_ES_URL", "https://es.test")
    monkeypatch.setenv("VSS_STORAGE_ES_API_KEY", SECRET)
    monkeypatch.delenv("VSS_STORAGE_ROOT", raising=False)
    config = environment_storage_config()
    assert config.backend == "elasticsearch"
    assert config.elasticsearch.url == "https://es.test"
    assert SECRET not in config.model_dump_json()


async def test_es_captions_survive_new_adapter_without_wire_changes(tmp_path):
    from vss.ingestion.contracts import ChunkInfo, VlmChunkResponse
    from vss.summarization import AdapterChunkStore

    state = State()
    value = VlmChunkResponse(
        chunk=ChunkInfo(sourceId="original", start_pts=0, end_pts=1_000_000_000),
        vlm_response="A car moves.",
    )
    async with adapter(tmp_path, Client(state)) as first:
        await AdapterChunkStore(first).store_chunks("job", [value])
    async with adapter(tmp_path, Client(state)) as second:
        assert await AdapterChunkStore(second).get_chunks("job") == [value]


async def test_es_public_ingestion_resume_and_deferred_summary(
    tmp_path, pipeline, monkeypatch
):
    import vss.ingestion.vlm as service
    from vss.ingestion import VLMBackendError
    from vss.summarization import summarize_from_store
    from vss.summarization.generators import GeneratedSummary, SummaryGenerator

    class Generator(SummaryGenerator):
        async def generate(self, events, *, prompt=None, model=None):
            assert len(events) == 1  # existing aggregation merges adjacent motion
            assert events[0]["start_time"] == 0
            assert events[0]["end_time"] == 2.5
            assert events[0]["description"] == (
                "Chunk 0 moves | Chunk 1 moves | Chunk 2 moves"
            )
            return GeneratedSummary(text="A car moves.", model="test")

    source, requests, pipeline_state = pipeline
    pipeline_state["fail_chunk"] = 2
    state = State()
    async with adapter(tmp_path, Client(state)) as first:
        job = submit(source, first)
        with pytest.raises(VLMBackendError):
            await job
        await job.wait_closed()
    async with adapter(tmp_path, Client(state)) as second:
        resumed = submit(source, second, job_id=job.id, resume=True)
        chunks = (await resumed)["vlm"]
        await resumed.wait_closed()
        assert [request["index"] for request in requests] == [0, 1, 2, 2]
        manifest = (await second.read("observations", str(job.id))).records[0]
        assert manifest["original_source_id"] == source.source_id

        def forbidden(*args, **kwargs):
            raise AssertionError("Completed replay must not decode")

        monkeypatch.setattr(service, "probe_video", forbidden)
        monkeypatch.setattr(service, "subsample_frames", forbidden)
        replay = submit(source, second, job_id=job.id, resume=True)
        assert (await replay)["vlm"] == chunks
        await replay.wait_closed()
        assert len(requests) == 4
    Path(source.url).unlink()
    async with adapter(tmp_path, Client(state)) as third:
        result = await summarize_from_store(
            str(job.id), storage=third, generator=Generator()
        )
        assert (
            json.loads(result.choices[0].message.content)["video_summary"]
            == "A car moves."
        )
    assert len(requests) == 4
