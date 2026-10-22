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

"""Validated public configuration and snapshot models."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ElasticsearchConfig(BaseModel):
    """Nonsecret settings for one dedicated ES snapshot index.

    ``url`` is an HTTP(S) endpoint without URL credentials/query/fragment.
    ``index`` is a concrete lowercase index name, not a wildcard or alias.
    ``claim_root`` defaults to ~/.vss/es-claims and coordinates SAME-HOST
    processes using the same endpoint/index/root. Credentials and clients are
    intentionally absent; owned adapters read VSS_STORAGE_ES_API_KEY, and
    injected preauthenticated clients retain caller ownership. Injected client
    routing must match this endpoint; TLS/auth on those clients are caller-owned.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    schema_version: Literal["1.0"] = "1.0"
    url: str
    index: str = "vss-storage"
    claim_root: Path | None = None
    request_timeout: float = 30.0
    ca_certs: Path | None = None

    @field_validator("url", mode="before")
    @classmethod
    def validate_url(cls, value: Any) -> str:
        if type(value) is not str or not value or value != value.strip():
            raise ValueError("ES endpoint must be an HTTP(S) URL")
        try:
            parts = urlsplit(value)
            if (
                parts.scheme not in {"http", "https"}
                or not parts.hostname
                or parts.username is not None
                or parts.password is not None
                or parts.query
                or parts.fragment
                or "?" in value
                or "#" in value
                or any(char.isspace() for char in value)
            ):
                raise ValueError
            hostname = parts.hostname.lower()
            if ":" in hostname:
                hostname = f"[{hostname}]"
            port = parts.port
            authority = hostname + (
                f":{port}"
                if port is not None
                and (parts.scheme, port) not in {("http", 80), ("https", 443)}
                else ""
            )
            return urlunsplit((parts.scheme, authority, parts.path.rstrip("/"), "", ""))
        except ValueError:
            raise ValueError(
                "ES endpoint must omit credentials/query/fragment"
            ) from None

    @field_validator("index", mode="before")
    @classmethod
    def validate_index(cls, value: Any) -> str:
        import re

        if type(value) is not str or not re.fullmatch(
            r"[a-z0-9][a-z0-9_-]{0,254}", value
        ):
            raise ValueError("ES index must be a concrete lowercase index name")
        return value

    @field_validator("claim_root", "ca_certs", mode="before")
    @classmethod
    def validate_path(cls, value: Any) -> Any:
        if value is not None and not isinstance(value, (Path, str)):
            raise ValueError("ES filesystem setting must be a path")
        if isinstance(value, str) and not value:
            raise ValueError("ES filesystem setting must not be empty")
        return value

    @field_validator("request_timeout", mode="before")
    @classmethod
    def validate_timeout(cls, value: Any) -> float:
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise ValueError("ES timeout must be a finite positive number")
        return float(value)


class StorageConfig(BaseModel):
    """Frozen JSON-serializable configuration for a built-in storage backend.

    ``memory`` is transient and forbids a root. ``file`` stores complete,
    checksummed JSONL snapshots under ``root``; when root is omitted or ``None``,
    the file backend uses ``Path.home() / '.vss'``. The byte, record, and worker
    limits are strictly positive integers; defaults are 64 MiB, 100,000 records,
    and four in-flight operations. File operation lock contention waits up to
    ``lock_wait_timeout_s`` (30 seconds by default), a finite positive number.
    Custom lock waits apply only to the file backend. Ownership claims fail
    promptly for an owned key but may briefly retry registry contention.
    Reusable lock slots are the only file lock protocol; unsupported pre-release
    per-key lock roots are rejected rather than converted automatically.
    """

    model_config = ConfigDict(
        frozen=True, extra="forbid", validate_assignment=True, hide_input_in_errors=True
    )

    schema_version: Literal["1.0"] = "1.0"
    backend: Literal["memory", "file", "elasticsearch"] = "memory"
    root: Path | None = None
    elasticsearch: ElasticsearchConfig | None = None
    max_snapshot_bytes: int = 64 * 1024 * 1024
    max_records: int = 100_000
    max_in_flight: int = 4
    lock_wait_timeout_s: float = 30.0

    @field_validator("lock_wait_timeout_s", mode="before")
    @classmethod
    def validate_lock_wait(cls, value: Any) -> float:
        if type(value) not in (int, float):
            raise ValueError("lock_wait_timeout_s must be finite and positive")
        try:
            seconds = float(value)
        except OverflowError:
            raise ValueError(
                "lock_wait_timeout_s must be finite and positive"
            ) from None
        if not math.isfinite(seconds) or seconds <= 0:
            raise ValueError("lock_wait_timeout_s must be finite and positive")
        return seconds

    @field_validator("root", mode="before")
    @classmethod
    def validate_root(cls, value: Any) -> Any:
        if value is not None and not isinstance(value, (Path, str)):
            raise ValueError("root must be a filesystem path")
        if isinstance(value, str) and not value:
            raise ValueError("root must not be empty")
        return value

    @field_validator(
        "max_snapshot_bytes", "max_records", "max_in_flight", mode="before"
    )
    @classmethod
    def strict_positive_int(cls, value: Any) -> int:
        if type(value) is not int or value <= 0:
            raise ValueError("limit must be a positive integer")
        return value

    @model_validator(mode="after")
    def backend_root_consistency(self) -> StorageConfig:
        if self.backend != "file" and self.lock_wait_timeout_s != 30.0:
            raise ValueError("custom lock waits are only valid for the file backend")
        if self.backend != "file" and self.root is not None:
            raise ValueError("root is only valid for the file backend")
        if self.backend == "elasticsearch" and self.elasticsearch is None:
            raise ValueError("Elasticsearch backend requires explicit ES settings")
        if self.backend != "elasticsearch" and self.elasticsearch is not None:
            raise ValueError("ES settings are only valid for the Elasticsearch backend")
        if self.backend == "file" and self.root is None:
            object.__setattr__(self, "root", Path.home() / ".vss")
        return self


class StorageSnapshot(BaseModel):
    """Frozen versioned snapshot with a positive CAS revision and JSON records.

    ``schema_version`` describes this generic envelope independently of
    application record versions. Returned records are detached from adapter and
    caller-owned input data.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    revision: int = Field(strict=True, gt=0)
    records: tuple[dict[str, Any], ...]

    @field_validator("records", mode="before")
    @classmethod
    def require_records(cls, value: Any) -> Any:
        if type(value) not in (tuple, list):
            raise ValueError("records must be a sequence of objects")
        detached: list[dict[str, Any]] = []

        def validate(item: Any, seen: set[int], depth: int = 0) -> None:
            if depth > 256:
                raise ValueError("JSON nesting exceeds 256 levels")
            if item is None or type(item) in (str, bool, int):
                return
            if type(item) is float:
                if not math.isfinite(item):
                    raise ValueError("JSON numbers must be finite")
                return
            if type(item) not in (list, dict):
                raise ValueError("snapshot records must contain finite JSON values")
            identity = id(item)
            if identity in seen:
                raise ValueError("snapshot records cannot contain cycles")
            seen.add(identity)
            try:
                if type(item) is dict:
                    for key, child in item.items():
                        if type(key) is not str:
                            raise ValueError("JSON object keys must be strings")
                        validate(child, seen, depth + 1)
                else:
                    for child in item:
                        validate(child, seen, depth + 1)
            finally:
                seen.remove(identity)

        for record in value:
            if type(record) is not dict:
                raise ValueError("every record must be an object")
            validate(record, set())
            detached.append(
                json.loads(json.dumps(record, ensure_ascii=False, allow_nan=False))
            )
        return tuple(detached)
