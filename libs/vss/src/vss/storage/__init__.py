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

"""Generic, JSON-only asynchronous storage with CAS and ownership claims."""

from .base import StorageAdapter
from .elasticsearch import ElasticsearchStorageAdapter
from .errors import (
    StorageBusyError,
    StorageClosedError,
    StorageConfigurationError,
    StorageConflictError,
    StorageCorruptError,
    StorageError,
    StorageIndeterminateError,
    StorageLimitError,
    StorageNotFoundError,
)
from .file import FileStorageAdapter
from .memory import InMemoryStorageAdapter
from .models import ElasticsearchConfig, StorageConfig, StorageSnapshot


def create_storage(config: StorageConfig) -> StorageAdapter:
    """Construct and return a caller-owned adapter selected by validated config."""
    if not isinstance(config, StorageConfig):
        raise StorageConfigurationError("config must be a StorageConfig")
    if config.backend == "memory":
        return InMemoryStorageAdapter(
            max_snapshot_bytes=config.max_snapshot_bytes,
            max_records=config.max_records,
            max_in_flight=config.max_in_flight,
        )
    if config.backend == "file":
        assert config.root is not None
        return FileStorageAdapter(
            root=config.root,
            max_snapshot_bytes=config.max_snapshot_bytes,
            max_records=config.max_records,
            max_in_flight=config.max_in_flight,
            lock_wait_timeout_s=config.lock_wait_timeout_s,
        )
    if config.backend == "elasticsearch":
        assert config.elasticsearch is not None
        return ElasticsearchStorageAdapter(
            config=config.elasticsearch,
            max_snapshot_bytes=config.max_snapshot_bytes,
            max_records=config.max_records,
            max_in_flight=config.max_in_flight,
        )
    raise StorageConfigurationError(f"unsupported storage backend: {config.backend}")


__all__ = [
    "StorageAdapter",
    "StorageConfig",
    "StorageSnapshot",
    "ElasticsearchConfig",
    "ElasticsearchStorageAdapter",
    "InMemoryStorageAdapter",
    "FileStorageAdapter",
    "create_storage",
    "StorageError",
    "StorageNotFoundError",
    "StorageConflictError",
    "StorageBusyError",
    "StorageCorruptError",
    "StorageConfigurationError",
    "StorageClosedError",
    "StorageLimitError",
    "StorageIndeterminateError",
]
