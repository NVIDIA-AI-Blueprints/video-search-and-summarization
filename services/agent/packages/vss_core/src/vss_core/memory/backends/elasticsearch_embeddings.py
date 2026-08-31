# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Derived semantic-memory documents in a companion Elasticsearch index."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from typing import Literal

from elasticsearch import BadRequestError
from elasticsearch import Elasticsearch
from elasticsearch import NotFoundError as ESNotFoundError
from elasticsearch.exceptions import ConnectionError as ESConnectionError
from elasticsearch.exceptions import TransportError as ESTransportError

from vss_core._foundation.errors import BackendUnreachableError
from vss_core._foundation.errors import ConfigurationError
from vss_core._foundation.time import datetime_to_iso8601

from ..embeddings import SIMILARITY
from ..embeddings import EmbeddingProvider
from ..embeddings import canonical_searchable_text
from ..embeddings import content_hash
from ..embeddings import is_embedding_eligible
from ..models import EmbeddingRef
from ..models import MemoryOutput
from ..models import UnifiedMemoryRecord
from ..store import MemoryQuery
from ..store import MemoryStore
from ..store import coerce_utc_instant
from ..store import storage_id_for

EMBEDDING_SCHEMA = "nv.vss.memory.embedding/1.0"
IMPLEMENTATION_VERSION = 1

SyncAction = Literal["created", "reembedded", "reused", "deleted", "unchanged"]


@dataclass(frozen=True, slots=True)
class EmbeddingSyncResult:
    """Outcome of synchronizing one authoritative record."""

    storage_id: str
    index: str
    action: SyncAction
    record: UnifiedMemoryRecord


@dataclass(frozen=True, slots=True)
class EmbeddingDeleteResult:
    """Outcome of deleting one derived vector and its owned reference."""

    storage_id: str
    index: str
    deleted: bool
    record: UnifiedMemoryRecord


class ElasticsearchEmbeddingStore:
    """Synchronize canonical memory into a versioned companion vector index."""

    def __init__(
        self,
        *,
        endpoint: str,
        index: str,
        provider: EmbeddingProvider,
        authoritative_store: MemoryStore,
        client: Elasticsearch | None = None,
        request_timeout: int = 30,
    ) -> None:
        if not index:
            raise ConfigurationError("Elasticsearch embedding store requires a configured companion index")
        if not endpoint and client is None:
            raise ConfigurationError("Elasticsearch embedding store requires an endpoint or injected client")
        if provider.dimensions <= 0:
            raise ConfigurationError("embedding dimensions must be positive")
        self._index = index
        self._provider = provider
        self._authoritative_store = authoritative_store
        self._owned = client is None
        self._client = client or Elasticsearch(endpoint, request_timeout=request_timeout)

    @property
    def index(self) -> str:
        return self._index

    @property
    def mapping(self) -> dict[str, Any]:
        """Return the exact mapping installed for this provider/index version."""
        return {
            "dynamic": "strict",
            "_meta": {
                "model": self._provider.model,
                "dimensions": self._provider.dimensions,
                "schema": EMBEDDING_SCHEMA,
                "implementation_version": IMPLEMENTATION_VERSION,
            },
            "properties": {
                "storage_id": {"type": "keyword"},
                "schema": {"type": "keyword"},
                "content_hash": {"type": "keyword"},
                "model": {"type": "keyword"},
                "dimensions": {"type": "integer"},
                "content": {"type": "text"},
                "vector": {
                    "type": "dense_vector",
                    "dims": self._provider.dimensions,
                    "index": True,
                    "similarity": SIMILARITY,
                },
                "job_id": {"type": "keyword"},
                "record_id": {"type": "keyword"},
                "record_type": {"type": "keyword"},
                "group": {"type": "keyword"},
                "status": {"type": "keyword"},
                "is_child": {"type": "boolean"},
                "created_at": {"type": "date"},
                "updated_at": {"type": "date"},
                "sensor_ids": {"type": "keyword"},
                "window_start": {"type": "date"},
                "window_end": {"type": "date"},
            },
        }

    def close(self) -> None:
        if self._owned:
            self._client.close()

    def sync_record(self, record: UnifiedMemoryRecord) -> EmbeddingSyncResult:
        """Synchronize a vector, then attach only this index's canonical reference."""
        doc_id = storage_id_for(record)
        if not is_embedding_eligible(record):
            deleted = self.delete_record(record)
            return EmbeddingSyncResult(
                storage_id=doc_id,
                index=self._index,
                action="deleted" if deleted.deleted else "unchanged",
                record=deleted.record,
            )

        self._ensure_compatible_index()
        text = canonical_searchable_text(record)
        digest = content_hash(text)
        existing = self._get_metadata(doc_id)
        reusable = (
            existing is not None
            and existing.get("content_hash") == digest
            and existing.get("model") == self._provider.model
            and existing.get("dimensions") == self._provider.dimensions
        )
        metadata = self._document(record, text=text, digest=digest)
        try:
            if reusable:
                if existing != metadata:
                    self._client.update(
                        index=self._index,
                        id=doc_id,
                        doc=metadata,
                        refresh="wait_for",
                    )
                action: SyncAction = "reused"
            else:
                vector = self._provider.embed_passages([text])[0]
                if len(vector) != self._provider.dimensions:
                    raise ConfigurationError(
                        f"embedding provider returned {len(vector)} dimensions; configured dimensions are "
                        f"{self._provider.dimensions}"
                    )
                metadata["vector"] = vector
                self._client.index(index=self._index, id=doc_id, document=metadata, refresh="wait_for")
                action = "reembedded" if existing is not None else "created"
        except (ESConnectionError, ESTransportError) as error:
            raise BackendUnreachableError(
                "elasticsearch", f"embedding sync failed for {doc_id}", cause=error
            ) from error

        referenced = self._write_reference(record)
        return EmbeddingSyncResult(storage_id=doc_id, index=self._index, action=action, record=referenced)

    def delete_record(self, record: UnifiedMemoryRecord) -> EmbeddingDeleteResult:
        """Delete one derived document and remove only this index's reference."""
        doc_id = storage_id_for(record)
        deleted = True
        try:
            self._client.delete(index=self._index, id=doc_id, refresh="wait_for")
        except ESNotFoundError:
            deleted = False
        except (ESConnectionError, ESTransportError) as error:
            raise BackendUnreachableError(
                "elasticsearch", f"embedding delete failed for {doc_id}", cause=error
            ) from error
        unreferenced = self._write_reference(record, remove=True)
        return EmbeddingDeleteResult(storage_id=doc_id, index=self._index, deleted=deleted, record=unreferenced)

    def semantic_search(
        self,
        query: MemoryQuery,
        query_vector: list[float],
        candidate_count: int,
    ) -> list[str]:
        """Return filtered kNN hits as ordered authoritative storage IDs."""
        if candidate_count <= 0:
            return []
        if len(query_vector) != self._provider.dimensions:
            raise ConfigurationError(
                f"query embedding has {len(query_vector)} dimensions; expected {self._provider.dimensions}"
            )
        self._ensure_compatible_index()
        body = {
            "size": candidate_count,
            "_source": ["storage_id"],
            "knn": {
                "field": "vector",
                "query_vector": query_vector,
                "k": candidate_count,
                "num_candidates": candidate_count,
                "filter": {"bool": self._semantic_filter(query)},
            },
        }
        try:
            response = self._client.search(index=self._index, body=body)
        except ESNotFoundError:
            return []
        except (ESConnectionError, ESTransportError) as error:
            raise BackendUnreachableError("elasticsearch", "embedding search failed", cause=error) from error
        storage_ids: list[str] = []
        for hit in response.get("hits", {}).get("hits", []):
            if not isinstance(hit, dict):
                continue
            source = hit.get("_source")
            storage_id = source.get("storage_id") if isinstance(source, dict) else None
            if not isinstance(storage_id, str):
                storage_id = hit.get("_id")
            if isinstance(storage_id, str):
                storage_ids.append(storage_id)
        return storage_ids

    @staticmethod
    def _semantic_filter(query: MemoryQuery) -> dict[str, list[dict[str, Any]]]:
        filters: list[dict[str, Any]] = []
        must_not: list[dict[str, Any]] = []
        if query.job_id:
            filters.append({"term": {"job_id": query.job_id}})
        if query.group:
            filters.append({"term": {"group": query.group}})
        if query.status:
            filters.append({"term": {"status": query.status}})
        if query.sensor_id:
            filters.append({"term": {"sensor_ids": query.sensor_id}})
        if query.record_type:
            filters.append({"term": {"record_type": query.record_type}})
        if query.record_id:
            filters.append({"term": {"record_id": query.record_id}})
        if query.parents_only or not query.include_children:
            filters.append({"term": {"is_child": False}})

        since = coerce_utc_instant(query.since)
        until = coerce_utc_instant(query.until)
        if since is not None or until is not None:
            if query.time_field == "window":
                if until is not None:
                    filters.append({"range": {"window_start": {"lte": datetime_to_iso8601(until)}}})
                if since is not None:
                    filters.append(
                        {
                            "bool": {
                                "should": [
                                    {"range": {"window_end": {"gte": datetime_to_iso8601(since)}}},
                                    {
                                        "bool": {
                                            "must_not": [{"exists": {"field": "window_end"}}],
                                            "filter": [
                                                {"range": {"window_start": {"gte": datetime_to_iso8601(since)}}}
                                            ],
                                        }
                                    },
                                ],
                                "minimum_should_match": 1,
                            }
                        }
                    )
            else:
                bounds: dict[str, str] = {}
                if since is not None:
                    bounds["gte"] = datetime_to_iso8601(since)
                if until is not None:
                    bounds["lte"] = datetime_to_iso8601(until)
                filters.append({"range": {"created_at": bounds}})
        return {"filter": filters, "must_not": must_not}

    def _ensure_compatible_index(self) -> None:
        try:
            exists = bool(self._client.indices.exists(index=self._index))
            if not exists:
                try:
                    self._client.indices.create(index=self._index, mappings=self.mapping)
                except BadRequestError as error:
                    if not _is_already_exists(error):
                        raise
            response = self._client.indices.get_mapping(index=self._index)
        except (ESConnectionError, ESTransportError) as error:
            raise BackendUnreachableError("elasticsearch", "embedding index validation failed", cause=error) from error
        mapping = _mapping_for(response, self._index)
        vector = mapping.get("properties", {}).get("vector")
        metadata = mapping.get("_meta")
        expected_meta = self.mapping["_meta"]
        if (
            not isinstance(vector, dict)
            or vector.get("type") != "dense_vector"
            or vector.get("dims") != self._provider.dimensions
            or vector.get("similarity") != SIMILARITY
            or metadata != expected_meta
        ):
            raise ConfigurationError(
                f"companion embedding index {self._index!r} has an incompatible mapping; "
                "configure a new versioned embedding index and backfill it"
            )

    def _get_metadata(self, doc_id: str) -> dict[str, Any] | None:
        try:
            response = self._client.get(index=self._index, id=doc_id, source_excludes=["vector"])
        except ESNotFoundError:
            return None
        except (ESConnectionError, ESTransportError) as error:
            raise BackendUnreachableError(
                "elasticsearch", f"embedding metadata read failed for {doc_id}", cause=error
            ) from error
        source = response.get("_source")
        return source if isinstance(source, dict) else None

    def _document(self, record: UnifiedMemoryRecord, *, text: str, digest: str) -> dict[str, Any]:
        memory_input = record.input
        window = memory_input.window if memory_input is not None else None
        sensors = memory_input.sensors if memory_input is not None else None
        sensor_ids = list(
            dict.fromkeys(
                value
                for sensor in sensors or []
                for value in (sensor.id, sensor.info.get("name") if sensor.info is not None else None)
                if isinstance(value, str) and value
            )
        )
        document: dict[str, Any] = {
            "storage_id": storage_id_for(record),
            "schema": EMBEDDING_SCHEMA,
            "content_hash": digest,
            "model": self._provider.model,
            "dimensions": self._provider.dimensions,
            "content": text,
            "job_id": record.job.job_id,
            "group": record.job.group,
            "status": record.job.status,
            "is_child": record.job.is_child,
            "created_at": datetime_to_iso8601(record.job.created_at),
            "sensor_ids": sensor_ids,
        }
        if record.job.record_id is not None:
            document["record_id"] = record.job.record_id
        if record.job.record_type is not None:
            document["record_type"] = record.job.record_type
        if record.job.updated_at is not None:
            document["updated_at"] = datetime_to_iso8601(record.job.updated_at)
        if window is not None:
            document["window_start"] = datetime_to_iso8601(window.start.timestamp)
            if window.end is not None:
                document["window_end"] = datetime_to_iso8601(window.end.timestamp)
        return document

    def _write_reference(self, record: UnifiedMemoryRecord, *, remove: bool = False) -> UnifiedMemoryRecord:
        if remove and record.output is None:
            return record
        output = record.output or MemoryOutput()
        doc_id = storage_id_for(record)
        reference_path = f"{self._index}/{doc_id}"
        unrelated = [
            reference for reference in output.embedding or [] if reference.es_ref not in {self._index, reference_path}
        ]
        if not remove:
            unrelated.append(
                EmbeddingRef(
                    es_ref=reference_path,
                    doc_ids=[doc_id],
                    kind="text",
                    info={
                        "model": self._provider.model,
                        "dimensions": self._provider.dimensions,
                        "content_hash": content_hash(record),
                    },
                )
            )
        updated_output = output.model_copy(update={"embedding": unrelated or None})
        updated = record.model_copy(update={"output": updated_output})
        if updated == record:
            return record
        # Deliberately cross the raw authoritative-store boundary. Calling a
        # MemoryService here would recursively trigger semantic synchronization.
        return self._authoritative_store.upsert(updated)


def _mapping_for(response: Any, index: str) -> dict[str, Any]:
    if not isinstance(response, dict):
        return {}
    entry = response.get(index)
    if not isinstance(entry, dict):
        return {}
    mapping = entry.get("mappings")
    return mapping if isinstance(mapping, dict) else {}


def _is_already_exists(error: BadRequestError) -> bool:
    body = getattr(error, "body", None)
    if not isinstance(body, dict):
        return False
    detail = body.get("error")
    if isinstance(detail, dict):
        return detail.get("type") == "resource_already_exists_exception"
    return "resource_already_exists_exception" in str(detail)


__all__ = [
    "EMBEDDING_SCHEMA",
    "IMPLEMENTATION_VERSION",
    "ElasticsearchEmbeddingStore",
    "EmbeddingDeleteResult",
    "EmbeddingSyncResult",
    "SyncAction",
]
