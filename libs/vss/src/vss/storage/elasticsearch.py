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

"""Elasticsearch snapshots with local, same-host ownership claims.

The backend stores complete snapshots in one dedicated, versioned index. Each
write is a single Elasticsearch document operation guarded by CAS metadata;
full snapshots are sent for every revision. Claims use local OS locks and only
coordinate processes on the same host that share the configured claim root.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import inspect
import json
import os
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from .base import (
    StorageAdapter,
    _wait_settled,
    detach_records,
    validate_expected_revision,
    validate_identity,
)
from .errors import (
    StorageBusyError,
    StorageConfigurationError,
    StorageConflictError,
    StorageCorruptError,
    StorageError,
    StorageIndeterminateError,
    StorageLimitError,
    StorageNotFoundError,
)
from .file import FileStorageAdapter
from .models import ElasticsearchConfig, StorageSnapshot

_MAPPING: dict[str, Any] = {
    "_meta": {"vss_storage_format": "1.0"},
    "dynamic": "strict",
    "properties": {
        "namespace": {"type": "keyword"},
        "key": {"type": "keyword"},
        "revision": {"type": "long"},
        "schema_version": {"type": "keyword"},
        "record_count": {"type": "integer"},
        "checksum": {"type": "keyword"},
        "records": {"type": "object", "enabled": False},
    },
}
_SETTINGS: dict[str, Any] = {"index": {"translog": {"durability": "request"}}}


class ElasticsearchStorageAdapter(StorageAdapter):
    """Persist CAS snapshots in a dedicated Elasticsearch index.

    Elasticsearch's translog must retain ``request`` durability. A private
    ``FileStorageAdapter`` supplies non-expiring claims only among processes on
    this host sharing endpoint, index, claim root and OS user. The backend does
    not provide distributed ownership across hosts.
    """

    @property
    def persistent(self) -> bool:
        """Elasticsearch commits survive adapter and process shutdown."""
        return True

    def __init__(
        self,
        *,
        config: ElasticsearchConfig,
        client: Any | None = None,
        max_snapshot_bytes: int = 64 * 1024 * 1024,
        max_records: int = 100_000,
        max_in_flight: int = 4,
    ) -> None:
        """Create an adapter; injected clients remain caller-owned.

        The optional ``elasticsearch[async]`` package is imported only when this
        constructor must create its own client. Owned clients read the API key
        from ``VSS_STORAGE_ES_API_KEY``; credentials are never stored in config.
        """
        from .models import StorageConfig

        if not isinstance(config, ElasticsearchConfig):
            raise StorageConfigurationError("config must be an ElasticsearchConfig")
        limits = StorageConfig(
            backend="memory",
            max_snapshot_bytes=max_snapshot_bytes,
            max_records=max_records,
            max_in_flight=max_in_flight,
        )
        self.config = config
        self.max_snapshot_bytes = limits.max_snapshot_bytes
        self.max_records = limits.max_records
        self._owns_client = client is None
        self._client_closed = False
        self._resource_close_task: asyncio.Task[None] | None = None
        self._client: Any
        if client is None:
            try:
                elasticsearch_module = importlib.import_module("elasticsearch")
                async_elasticsearch = elasticsearch_module.AsyncElasticsearch
            except (ImportError, AttributeError):
                raise StorageConfigurationError(
                    "install the Elasticsearch client with `pip install 'vss[storage]'`"
                ) from None
            options: dict[str, Any] = {
                "request_timeout": config.request_timeout,
                "max_retries": 0,
                "retry_on_timeout": False,
            }
            if config.ca_certs is not None:
                options["ca_certs"] = str(config.ca_certs)
            api_key = os.environ.get("VSS_STORAGE_ES_API_KEY")
            if api_key:
                options["api_key"] = api_key
            try:
                self._client = async_elasticsearch(config.url, **options)
            except Exception:
                raise StorageConfigurationError(
                    "Elasticsearch client initialization failed"
                ) from None
        else:
            self._client = self._client_without_retries(client)

        claim_root = config.claim_root or (Path.home() / ".vss" / "es-claims")
        scope = hashlib.sha256(
            f"{self._canonical_endpoint(config.url)}\0{config.index}".encode()
        ).hexdigest()
        self._coordinator = FileStorageAdapter(
            root=claim_root / scope,
            max_in_flight=limits.max_in_flight,
        )
        self._requests = asyncio.Semaphore(limits.max_in_flight)
        self._index_lock = asyncio.Lock()
        self._index_ready = False
        self._init_runtime(limits.max_in_flight)

    @staticmethod
    def _canonical_endpoint(url: str) -> str:
        """Normalize URL spelling for same-host claim namespacing."""
        from urllib.parse import urlsplit

        parsed = urlsplit(url)
        scheme = parsed.scheme.lower()
        hostname = (parsed.hostname or "").lower()
        if ":" in hostname:
            hostname = f"[{hostname}]"
        port = parsed.port
        if port is not None and not (
            (scheme == "https" and port == 443) or (scheme == "http" and port == 80)
        ):
            hostname = f"{hostname}:{port}"
        path = parsed.path.rstrip("/")
        return f"{scheme}://{hostname}{path}"

    def _client_without_retries(self, client: Any) -> Any:
        """Require injected clients to expose an explicit no-retry view."""
        options = getattr(client, "options", None)
        if not callable(options):
            raise StorageConfigurationError(
                "injected Elasticsearch clients must support options()"
            )
        try:
            adjusted = options(
                max_retries=0,
                retry_on_timeout=False,
                request_timeout=self.config.request_timeout,
            )
        except Exception:
            raise StorageConfigurationError(
                "injected Elasticsearch client retry policy could not be set"
            ) from None
        if adjusted is None:
            raise StorageConfigurationError(
                "injected Elasticsearch client options() returned no client"
            )
        if inspect.isawaitable(adjusted):
            raise StorageConfigurationError(
                "injected Elasticsearch client options() must be synchronous"
            )
        return adjusted

    async def _request(self, method: Any, **kwargs: Any) -> Any:
        """Call one async SDK operation with bounded admission and settle cancel."""
        await self._requests.acquire()
        try:
            call_kwargs = dict(kwargs)
            try:
                result = method(**call_kwargs)
                if inspect.isawaitable(result):
                    task = asyncio.ensure_future(result)
                    try:
                        return await asyncio.shield(task)
                    except asyncio.CancelledError:
                        while not task.done():
                            try:
                                await asyncio.shield(task)
                            except asyncio.CancelledError:
                                continue
                            except BaseException:
                                break
                        if task.done() and not task.cancelled():
                            try:
                                task.exception()
                            except BaseException:
                                pass
                        raise
                return result
            except asyncio.CancelledError:
                raise
        finally:
            self._requests.release()

    @staticmethod
    def _body(value: Any) -> Any:
        body = getattr(value, "body", None)
        return body if body is not None else value

    @staticmethod
    def _status(exc: BaseException) -> int | None:
        for value in (
            getattr(exc, "status_code", None),
            getattr(getattr(exc, "meta", None), "status", None),
            getattr(exc, "status", None),
        ):
            if type(value) is int:
                return value
        return None

    @staticmethod
    def _is_missing(exc: BaseException) -> bool:
        return ElasticsearchStorageAdapter._status(exc) == 404

    async def _ensure_index(self) -> None:
        if self._index_ready:
            return
        async with self._index_lock:
            if self._index_ready:
                return
            indices = self._client.indices
            try:
                response = self._body(
                    await self._request(indices.get_mapping, index=self.config.index)
                )
            except BaseException as exc:
                if isinstance(exc, asyncio.CancelledError):
                    raise
                if not self._is_missing(exc):
                    raise StorageError(
                        "Elasticsearch index validation failed"
                    ) from None
                try:
                    await self._request(
                        indices.create,
                        index=self.config.index,
                        mappings=_MAPPING,
                        settings=_SETTINGS,
                    )
                except BaseException as create_exc:
                    if isinstance(create_exc, asyncio.CancelledError):
                        raise
                    # Concurrent creators race; re-read and validate below.
                    if self._status(create_exc) not in (400, 409):
                        raise StorageError(
                            "Elasticsearch index creation failed"
                        ) from None
                try:
                    response = self._body(
                        await self._request(
                            indices.get_mapping, index=self.config.index
                        )
                    )
                except BaseException as verify_exc:
                    if isinstance(verify_exc, asyncio.CancelledError):
                        raise
                    raise StorageConfigurationError(
                        "Elasticsearch dedicated index could not be verified"
                    ) from None
            mappings = self._mapping_from_response(response)
            self._validate_mapping(mappings)
            await self._validate_durability(indices)
            self._index_ready = True

    def _mapping_from_response(self, response: Any) -> dict[str, Any]:
        if not isinstance(response, dict) or set(response) != {self.config.index}:
            raise StorageConfigurationError(
                "configured Elasticsearch name is not a dedicated index"
            )
        entry = response[self.config.index]
        if not isinstance(entry, dict):
            raise StorageConfigurationError("Elasticsearch mapping is invalid")
        mapping = entry.get("mappings")
        if not isinstance(mapping, dict):
            raise StorageConfigurationError("Elasticsearch mapping is invalid")
        return mapping

    @staticmethod
    def _validate_mapping(mapping: dict[str, Any]) -> None:
        if set(mapping) - {"_meta", "dynamic", "properties", "_source"}:
            raise StorageConfigurationError(
                "Elasticsearch mapping contains unsupported features"
            )
        metadata = mapping.get("_meta", {})
        if (
            not isinstance(metadata, dict)
            or metadata.get("vss_storage_format") != "1.0"
        ):
            raise StorageConfigurationError(
                "Elasticsearch index has an unsupported storage mapping"
            )
        if mapping.get("dynamic") != "strict":
            raise StorageConfigurationError("Elasticsearch mapping is not strict")
        source = mapping.get("_source", {})
        if not isinstance(source, dict):
            raise StorageConfigurationError("Elasticsearch source mapping is invalid")
        if (
            source.get("enabled") is False
            or source.get("includes")
            or source.get("excludes")
            or source.get("mode") not in (None, "stored")
        ):
            raise StorageConfigurationError(
                "Elasticsearch mapping must preserve complete stored _source"
            )
        props = mapping.get("properties")
        if not isinstance(props, dict):
            raise StorageConfigurationError("Elasticsearch mapping is invalid")
        required = {
            "namespace": {"type": "keyword"},
            "key": {"type": "keyword"},
            "revision": {"type": "long"},
            "schema_version": {"type": "keyword"},
            "record_count": {"type": "integer"},
            "checksum": {"type": "keyword"},
            "records": {"type": "object", "enabled": False},
        }
        if set(props) != set(required):
            raise StorageConfigurationError("Elasticsearch mapping fields differ")
        for name, expected in required.items():
            actual = props[name]
            if not isinstance(actual, dict) or any(
                actual.get(field) != value for field, value in expected.items()
            ):
                raise StorageConfigurationError(
                    "Elasticsearch mapping has incompatible field definitions"
                )

    async def _validate_durability(self, indices: Any) -> None:
        try:
            response = self._body(
                await self._request(
                    indices.get_settings,
                    index=self.config.index,
                    flat_settings=True,
                    include_defaults=True,
                )
            )
        except BaseException as exc:
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise StorageConfigurationError(
                "Elasticsearch translog durability could not be verified"
            ) from None
        if not isinstance(response, dict) or self.config.index not in response:
            raise StorageConfigurationError("Elasticsearch settings are unavailable")
        settings = response[self.config.index]
        if not isinstance(settings, dict):
            raise StorageConfigurationError("Elasticsearch settings are invalid")
        actual = settings.get("settings", {})
        if not isinstance(actual, dict):
            raise StorageConfigurationError("Elasticsearch settings are invalid")
        index_settings = actual.get("index", {})
        if not isinstance(index_settings, dict):
            index_settings = {}
        translog = index_settings.get("translog", {})
        durability = translog.get("durability") if isinstance(translog, dict) else None
        if durability is None:
            durability = actual.get("index.translog.durability")
        if durability is None:
            defaults = settings.get("defaults", {})
            if isinstance(defaults, dict):
                durability = defaults.get("index.translog.durability")
        if not isinstance(durability, str) or durability.lower() != "request":
            raise StorageConfigurationError(
                "Elasticsearch translog durability must be request"
            )
        defaults = settings.get("defaults", {})
        if not isinstance(defaults, dict):
            defaults = {}
        for setting in ("default_pipeline", "final_pipeline"):
            value = index_settings.get(setting)
            if value is None:
                value = actual.get(f"index.{setting}")
            if value is None:
                value = defaults.get(f"index.{setting}")
            if value is not None and (
                not isinstance(value, str) or value.lower() not in ("", "_none")
            ):
                raise StorageConfigurationError(
                    "Elasticsearch ingest pipelines are not allowed"
                )
        source_mode = actual.get("index.mapping.source.mode")
        if source_mode is None:
            mapping_settings = index_settings.get("mapping", {})
            if isinstance(mapping_settings, dict):
                source = mapping_settings.get("source", {})
                if isinstance(source, dict):
                    source_mode = source.get("mode")
        if source_mode is None:
            source_mode = defaults.get("index.mapping.source.mode")
        if not isinstance(source_mode, str) or source_mode.lower() != "stored":
            raise StorageConfigurationError(
                "Elasticsearch source mode must preserve stored documents"
            )

    @staticmethod
    def _canonical(value: Any) -> bytes:
        try:
            return json.dumps(
                value,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        except UnicodeEncodeError:
            raise StorageConfigurationError(
                "snapshot text must be valid UTF-8"
            ) from None

    def _document(
        self,
        namespace: str,
        key: str,
        revision: int,
        records: tuple[dict[str, Any], ...],
    ) -> dict[str, Any]:
        checksum_fields = {
            "schema_version": "1.0",
            "namespace": namespace,
            "key": key,
            "revision": revision,
            "record_count": len(records),
            "records": records,
        }
        checksum = hashlib.sha256(self._canonical(checksum_fields)).hexdigest()
        return {**checksum_fields, "checksum": checksum}

    def _decode_source(self, source: Any, namespace: str, key: str) -> StorageSnapshot:
        if not isinstance(source, dict) or set(source) != {
            "schema_version",
            "namespace",
            "key",
            "revision",
            "record_count",
            "records",
            "checksum",
        }:
            raise StorageCorruptError("Elasticsearch snapshot fields are invalid")
        try:
            schema_version = source["schema_version"]
            found_namespace = source["namespace"]
            found_key = source["key"]
            revision = source["revision"]
            count = source["record_count"]
            records = source["records"]
            checksum = source["checksum"]
            if (
                schema_version != "1.0"
                or found_namespace != namespace
                or found_key != key
            ):
                raise ValueError("snapshot identity or schema mismatch")
            if type(revision) is not int or revision <= 0:
                raise ValueError("invalid revision")
            if type(count) is not int or count < 0 or count != len(records):
                raise ValueError("invalid record count")
            if count > self.max_records:
                raise StorageLimitError("stored record count exceeds configured limit")
            if type(checksum) is not str:
                raise ValueError("invalid checksum")
            detached = detach_records(records, self.max_records)
            document = self._document(namespace, key, revision, detached)
            if document["checksum"] != checksum:
                raise ValueError("snapshot checksum mismatch")
            if len(self._canonical(document)) > self.max_snapshot_bytes:
                raise StorageLimitError("stored snapshot exceeds configured byte limit")
            return StorageSnapshot(revision=revision, records=detached)
        except StorageLimitError:
            raise
        except (KeyError, TypeError, ValueError, RecursionError):
            raise StorageCorruptError("Elasticsearch snapshot is corrupt") from None

    def _document_id(self, namespace: str, key: str) -> str:
        return hashlib.sha256(self._canonical([namespace, key])).hexdigest()

    async def _get_document(self, namespace: str, key: str) -> tuple[Any, Any, Any]:
        await self._ensure_index()
        try:
            response = self._body(
                await self._request(
                    self._client.get,
                    index=self.config.index,
                    id=self._document_id(namespace, key),
                    realtime=True,
                )
            )
        except BaseException as exc:
            if isinstance(exc, asyncio.CancelledError):
                raise
            if self._is_missing(exc):
                raise StorageNotFoundError(key) from None
            raise StorageError("Elasticsearch snapshot read failed") from None
        if not isinstance(response, dict) or "_source" not in response:
            raise StorageCorruptError("Elasticsearch response omitted snapshot source")
        return (
            response["_source"],
            response.get("_seq_no"),
            response.get("_primary_term"),
        )

    async def read(self, namespace: str, key: str) -> StorageSnapshot:
        """Read and verify one realtime snapshot."""
        namespace, key = validate_identity(namespace, key)
        self._operation_started()
        try:
            source, _, _ = await self._get_document(namespace, key)
            return await self._run(
                self._decode_source, source, namespace, key, _admitted=True
            )
        finally:
            self._operation_finished()

    async def write(
        self,
        namespace: str,
        key: str,
        records: Sequence[dict[str, Any]],
        *,
        expected_revision: int | None,
    ) -> StorageSnapshot:
        """Create or CAS-replace a full immutable snapshot document."""
        namespace, key = validate_identity(namespace, key)
        expected_revision = validate_expected_revision(expected_revision)
        self._operation_started()
        try:
            detached = await self._run(
                detach_records, records, self.max_records, _admitted=True
            )
            doc, encoded_size = await self._run(
                self._prepare_document,
                namespace,
                key,
                detached,
                1,
                _admitted=True,
            )
            if encoded_size > self.max_snapshot_bytes:
                raise StorageLimitError("snapshot exceeds configured byte limit")
            await self._ensure_index()
            kwargs: dict[str, Any] = {
                "index": self.config.index,
                "id": self._document_id(namespace, key),
                "document": doc,
            }
            if expected_revision is None:
                kwargs["op_type"] = "create"
            else:
                try:
                    source, seq_no, primary_term = await self._get_document(
                        namespace, key
                    )
                except StorageNotFoundError:
                    raise StorageConflictError("snapshot revision changed") from None
                current = await self._run(
                    self._decode_source, source, namespace, key, _admitted=True
                )
                if current.revision != expected_revision:
                    raise StorageConflictError("snapshot revision changed")
                if (
                    type(seq_no) is not int
                    or seq_no < 0
                    or type(primary_term) is not int
                    or primary_term <= 0
                ):
                    raise StorageCorruptError(
                        "Elasticsearch response omitted concurrency metadata"
                    )
                doc, encoded_size = await self._run(
                    self._prepare_document,
                    namespace,
                    key,
                    detached,
                    current.revision + 1,
                    _admitted=True,
                )
                if encoded_size > self.max_snapshot_bytes:
                    raise StorageLimitError("snapshot exceeds configured byte limit")
                kwargs["document"] = doc
                kwargs["if_seq_no"] = seq_no
                kwargs["if_primary_term"] = primary_term
            try:
                response = await self._request(self._client.index, **kwargs)
            except BaseException as exc:
                if isinstance(exc, asyncio.CancelledError):
                    raise
                if self._status(exc) == 409:
                    raise StorageConflictError("snapshot revision changed") from None
                status = self._status(exc)
                # A response may fail parsing/product validation after a commit,
                # even with HTTP 200. Only known request rejections are definite;
                # transport timeouts and other outcomes require reconciliation.
                if status not in {400, 401, 403, 404, 405, 406, 413, 415, 422, 429}:
                    raise StorageIndeterminateError(
                        "Elasticsearch write outcome is uncertain"
                    ) from None
                raise StorageError("Elasticsearch snapshot write failed") from None
            result = self._body(response)
            shards = result.get("_shards") if isinstance(result, dict) else None
            if (
                not isinstance(shards, dict)
                or type(shards.get("total")) is not int
                or shards["total"] <= 0
                or type(shards.get("successful")) is not int
                or shards["successful"] <= 0
                or type(shards.get("failed")) is not int
                or shards["failed"] != 0
            ):
                raise StorageIndeterminateError(
                    "Elasticsearch write reported shard failures"
                )
            revision = doc["revision"]
            return await self._run(
                self._make_snapshot, revision, detached, _admitted=True
            )
        finally:
            self._operation_finished()

    def _prepare_document(
        self,
        namespace: str,
        key: str,
        records: tuple[dict[str, Any], ...],
        revision: int,
    ) -> tuple[dict[str, Any], int]:
        document = self._document(namespace, key, revision, records)
        return document, len(self._canonical(document))

    @staticmethod
    def _make_snapshot(
        revision: int, records: tuple[dict[str, Any], ...]
    ) -> StorageSnapshot:
        return StorageSnapshot(revision=revision, records=records)

    @asynccontextmanager
    async def exclusive(self, namespace: str, key: str) -> AsyncIterator[None]:
        """Acquire a nonblocking same-host claim for a namespace/key."""
        namespace, key = validate_identity(namespace, key)
        self._claim_started()
        try:
            async with self._coordinator.exclusive(namespace, key):
                yield
        finally:
            self._claim_finished()

    async def aclose(self) -> None:
        """Drain requests and close only resources owned by this adapter."""
        cancelled = False
        failure: BaseException | None = None
        try:
            await self._close_runtime()
        except StorageBusyError:
            raise
        except asyncio.CancelledError as exc:
            cancelled = True
            failure = exc
        except BaseException as exc:
            failure = exc
        if self._state == "closed":
            if self._resource_close_task is None:
                self._resource_close_task = asyncio.create_task(
                    self._close_owned_resources()
                )
            cancelled |= await _wait_settled(self._resource_close_task)
        if failure is not None:
            if isinstance(failure, asyncio.CancelledError):
                raise asyncio.CancelledError
            raise failure
        if cancelled:
            raise asyncio.CancelledError

    async def _close_owned_resources(self) -> None:
        failure: BaseException | None = None
        if self._owns_client and not self._client_closed:
            self._client_closed = True
            try:
                close = getattr(self._client, "close", None)
                if callable(close):
                    result = close()
                    if inspect.isawaitable(result):
                        await result
            except BaseException as exc:
                failure = exc
        try:
            if self._coordinator._state != "closed":
                await self._coordinator.aclose()
        except BaseException as exc:
            if failure is None:
                failure = exc
        if failure is not None:
            if isinstance(failure, asyncio.CancelledError):
                raise failure
            raise StorageError("Elasticsearch resources could not be closed") from None
