# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Public storage API, configuration, and optional-import contracts."""

from __future__ import annotations

import asyncio
import importlib
import inspect
import json
import os
import subprocess
import sys
import textwrap
from collections.abc import Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

STORAGE_EXPORTS = {
    "StorageAdapter",
    "StorageConfig",
    "StorageSnapshot",
    "ElasticsearchConfig",
    "ElasticsearchStorageAdapter",
    "InMemoryStorageAdapter",
    "FileStorageAdapter",
    "create_storage",
    "StorageNotFoundError",
    "StorageConflictError",
    "StorageBusyError",
    "StorageClosedError",
}


def test_storage_public_exports_are_importable_and_introspectable() -> None:
    storage = importlib.import_module("vss.storage")

    assert STORAGE_EXPORTS <= set(storage.__all__)
    assert STORAGE_EXPORTS <= set(dir(storage))
    for name in STORAGE_EXPORTS:
        exported = getattr(storage, name)
        assert exported is not None
        assert inspect.getdoc(exported), f"{name} needs a public docstring"

    assert inspect.isabstract(storage.StorageAdapter)
    assert inspect.isclass(storage.StorageConfig)
    assert inspect.isclass(storage.StorageSnapshot)
    assert callable(storage.create_storage)


def test_storage_config_is_frozen_versioned_and_json_serializable(
    tmp_path: Path,
) -> None:
    from vss.storage import StorageConfig

    config = StorageConfig(backend="file", root=tmp_path)
    encoded = config.model_dump_json()
    decoded = json.loads(encoded)
    assert decoded["schema_version"] == "1.0"
    assert decoded["backend"] == "file"
    assert decoded["root"] == str(tmp_path)
    assert decoded["max_snapshot_bytes"] == 64 * 1024 * 1024
    assert decoded["max_records"] == 100_000
    assert decoded["max_in_flight"] == 4
    assert StorageConfig.model_validate_json(encoded) == config

    with pytest.raises(ValidationError):
        config.backend = "memory"


@pytest.mark.parametrize(
    "payload",
    [
        {"backend": "es"},
        {"backend": "unknown"},
        {"schema_version": "2.0"},
        {"unexpected": True},
        {"backend": "memory", "root": "/tmp/storage"},
        {"max_snapshot_bytes": True},
        {"max_records": True},
        {"max_in_flight": True},
        {"max_snapshot_bytes": 0},
        {"max_records": -1},
        {"max_in_flight": 0},
    ],
)
def test_storage_config_rejects_invalid_or_unsupported_values(
    payload: dict[str, Any],
) -> None:
    from vss.storage import StorageConfig

    with pytest.raises(ValidationError):
        StorageConfig.model_validate(payload)


def test_storage_config_rejects_malformed_json() -> None:
    from vss.storage import StorageConfig

    with pytest.raises(ValidationError):
        StorageConfig.model_validate_json('{"backend":')


@pytest.mark.parametrize("entry", ["config", "adapter", "caption_store"])
def test_file_storage_rejects_empty_string_root(entry):
    from vss.storage import FileStorageAdapter, StorageConfig
    from vss.summarization import FileChunkStore

    with pytest.raises(ValidationError):
        if entry == "config":
            StorageConfig(backend="file", root="")
        elif entry == "adapter":
            FileStorageAdapter(root="")
        else:
            FileChunkStore(root="")


def test_create_storage_selects_public_adapters_and_caller_owns_them(
    tmp_path: Path,
) -> None:
    from vss.storage import (
        FileStorageAdapter,
        InMemoryStorageAdapter,
        StorageConfig,
        create_storage,
    )

    memory = create_storage(StorageConfig(backend="memory"))
    assert isinstance(memory, InMemoryStorageAdapter)
    assert memory.persistent is False

    file_storage = create_storage(StorageConfig(backend="file", root=tmp_path))
    assert isinstance(file_storage, FileStorageAdapter)
    assert file_storage.persistent is True

    async def close_call_owned_adapters() -> None:
        async with memory:
            pass
        async with file_storage:
            pass

    asyncio.run(close_call_owned_adapters())


def test_custom_storage_adapter_can_be_implemented_and_injected() -> None:
    from vss.storage import StorageAdapter, StorageSnapshot

    class CustomAdapter(StorageAdapter):
        @property
        def persistent(self) -> bool:
            return True

        async def read(self, namespace: str, key: str) -> StorageSnapshot:
            return StorageSnapshot(revision=1, records=())

        async def write(
            self,
            namespace: str,
            key: str,
            records: Sequence[dict[str, Any]],
            *,
            expected_revision: int | None,
        ) -> StorageSnapshot:
            return StorageSnapshot(revision=1, records=tuple(records))

        @asynccontextmanager
        async def exclusive(self, namespace: str, key: str):
            yield

        async def aclose(self) -> None:
            return None

    adapter = CustomAdapter()
    assert isinstance(adapter, StorageAdapter)
    assert adapter.persistent is True
    assert inspect.iscoroutinefunction(adapter.read)
    assert inspect.iscoroutinefunction(adapter.write)
    assert inspect.iscoroutinefunction(adapter.aclose)


def _child_import_env() -> dict[str, str]:
    env = dict(os.environ)
    root = Path(__file__).resolve().parents[2]
    source_roots = (
        root / "src",
        root / "packages/ingestion/src",
        root / "packages/summarization/src",
    )
    env["PYTHONPATH"] = os.pathsep.join(
        filter(None, (*(str(path) for path in source_roots), env.get("PYTHONPATH")))
    )
    return env


def _run_isolated_import(script: str) -> None:
    result = subprocess.run(
        [
            sys.executable,
            *(["-I"] if sys.flags.isolated else []),
            "-c",
            textwrap.dedent(script),
        ],
        env=_child_import_env(),
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("existing_path", [None, "/caller/import-path"])
def test_import_subprocess_resolves_source_checkout_without_editable_install(
    monkeypatch, existing_path
):
    if existing_path is None:
        monkeypatch.delenv("PYTHONPATH", raising=False)
    else:
        monkeypatch.setenv("PYTHONPATH", existing_path)
    import vss.storage as storage_module

    expected = (
        Path(storage_module.__file__).resolve()
        if sys.flags.isolated
        else Path(__file__).resolve().parents[2] / "src/vss/storage/__init__.py"
    )
    script = (
        "from pathlib import Path\nimport vss.storage\n"
        f"assert Path(vss.storage.__file__).resolve() == Path({str(expected)!r})\n"
    )
    if existing_path:
        script += (
            "import os\n"
            f"assert {existing_path!r} in os.environ['PYTHONPATH'].split(os.pathsep)\n"
        )
    _run_isolated_import(script)


def test_storage_and_ingestion_contract_imports_need_no_optional_runtime_deps() -> None:
    _run_isolated_import(
        """
        import importlib.abc
        import sys

        blocked = ('httpx', 'httpx_sse', 'openai', 'cv2', 'PyNvVideoCodec',
                   'elasticsearch', 'elastic_transport', 'aiohttp')

        class BlockOptionalDependencies(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if any(fullname == name or fullname.startswith(name + '.')
                       for name in blocked):
                    raise ModuleNotFoundError(
                        f'{fullname} intentionally unavailable', name=fullname
                    )

        sys.meta_path.insert(0, BlockOptionalDependencies())
        import vss.storage
        from vss.ingestion.contracts import RequestSource, VlmChunkResponse

        assert RequestSource(source_id='source').source_id == 'source'
        assert VlmChunkResponse().chunk is None
        assert not any(name in sys.modules for name in blocked)
        """
    )


def test_summary_contract_import_does_not_load_ingestion_transports() -> None:
    _run_isolated_import(
        """
        import importlib.abc
        import sys

        blocked = ('httpx_sse', 'cv2', 'PyNvVideoCodec')

        class BlockIngestionOnlyDependencies(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if any(fullname == name or fullname.startswith(name + '.')
                       for name in blocked):
                    raise ModuleNotFoundError(
                        f'{fullname} intentionally unavailable', name=fullname
                    )

        sys.meta_path.insert(0, BlockIngestionOnlyDependencies())
        import vss.summarization
        from vss.ingestion.contracts import VlmChunkResponse

        assert VlmChunkResponse().chunk is None
        assert 'vss.ingestion.service' not in sys.modules
        assert 'vss.ingestion.backend' not in sys.modules
        assert 'vss.ingestion.vlm' not in sys.modules
        assert 'vss.video_processing' not in sys.modules
        assert 'vss.ingestion.rtvi' not in sys.modules
        assert not any(name in sys.modules for name in blocked)
        """
    )
