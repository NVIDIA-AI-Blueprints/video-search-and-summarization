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

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from elastic.elastic import ConflictError, ElasticClient, ElasticConfig
from persistence.base import PersistenceStore
from persistence.exceptions import (
    ConcurrentModificationError,
    DocumentAlreadyExistsError,
    DocumentNotFoundError,
    PersistenceError,
)

logger = logging.getLogger(__name__)

DEFAULT_UPDATE_RETRIES = 3

# ES metadata fields the read paths echo into the returned document so
# callers can correlate ids and concurrency tokens. Must be stripped
# on the way back into ``create`` / ``update`` because ES 8.x rejects
# any underscore-prefixed metadata field inside the document body
# with ``document_parsing_exception``. Without this, any caller doing
# read-modify-write with the returned dict (e.g.
# ``AlertTypeConfigLoader.save_to_redis`` at startup, or
# ``AlertConfigService.update`` on PUT) trips on the asymmetry between
# read injection and write acceptance.
_ES_META_FIELDS = frozenset({"_id", "_seq_no", "_primary_term", "_index", "_version"})


class ElasticPersistenceStore(PersistenceStore):
    """Elasticsearch-backed persistence store.

    Uses a fixed index per collection (no daily rotation) since
    persisted configs/prompts are low-volume CRUD data, not time-series.

    Multi-replica safety is handled at the ES server side:
      - ``create`` uses ``op_type="create"`` for atomic create-only semantics.
      - ``update`` uses optimistic concurrency control via ``_seq_no`` and
        ``_primary_term``, with automatic retries on version conflicts.
    """

    def __init__(
        self,
        es_client: ElasticClient,
        index_prefix: str = "ab-",
        update_retries: int = DEFAULT_UPDATE_RETRIES,
        *,
        auto_create_indices: bool = True,
        index_shards: int = 1,
        index_replicas: int = 0,
    ) -> None:
        """
        Args:
            es_client: Pre-configured ElasticClient instance.
            index_prefix: Prefix for all persistence indices.
                          e.g. prefix "ab-" + collection "alert-configs" → index "ab-alert-configs"
            update_retries: Max retries on optimistic-concurrency conflicts.
            auto_create_indices: When ``True`` (default), ensure the target
                index exists on first write. Production deployments should
                pass ``False`` and pre-create indices via ES bootstrap
                templates so shard / replica counts stay under ops control.
            index_shards: Shard count for auto-created indices.
            index_replicas: Replica count for auto-created indices.
        """
        self._es = es_client
        self._index_prefix = index_prefix
        self._update_retries = update_retries
        self._auto_create_indices = auto_create_indices
        self._index_shards = index_shards
        self._index_replicas = index_replicas

    def _index_name(self, collection: str) -> str:
        return f"{self._index_prefix}{collection}"

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    def create(
        self,
        collection: str,
        doc_id: str,
        document: Dict[str, Any],
    ) -> Dict[str, Any]:
        index = self._index_name(collection)

        now = self._now_iso()
        # Strip ES metadata so callers that pass back a previously read
        # document (round-trip read → modify → write) don't push
        # reserved fields like ``_id`` into the body, which ES 8.x
        # rejects with ``document_parsing_exception``.
        doc = {k: v for k, v in document.items() if k not in _ES_META_FIELDS}
        doc.setdefault("created_at", now)
        doc["updated_at"] = now

        try:
            if self._auto_create_indices:
                # Cached in the ES client, so only the first write per
                # index per process actually touches the cluster.
                self._es.ensure_json_index(
                    index,
                    shards=self._index_shards,
                    replicas=self._index_replicas,
                )
            self._es.write_json(
                index, doc, doc_id=doc_id, refresh="wait_for", op_type="create"
            )
        except ConflictError:
            raise DocumentAlreadyExistsError(collection, doc_id)
        except Exception as exc:
            logger.error("Persistence create failed (collection=%s doc_id=%s)", collection, doc_id, exc_info=True)
            raise PersistenceError(f"Failed to create document '{doc_id}' in '{collection}'") from exc

        # Echo the document id on the way out so callers can correlate
        # subsequent update/delete calls without having to remember the
        # key they just used.
        doc["_id"] = doc_id
        return doc

    def read(
        self,
        collection: str,
        doc_id: str,
    ) -> Dict[str, Any]:
        index = self._index_name(collection)

        try:
            doc = self._es.get_document(index, doc_id)
        except Exception as exc:
            logger.error("Persistence read failed (collection=%s doc_id=%s)", collection, doc_id, exc_info=True)
            raise PersistenceError(f"Failed to read document '{doc_id}' from '{collection}'") from exc

        if doc is None:
            raise DocumentNotFoundError(collection, doc_id)

        # Attach the ES document id so callers can round-trip to
        # update/delete without having to remember it out of band.
        doc["_id"] = doc_id
        return doc

    def update(
        self,
        collection: str,
        doc_id: str,
        partial: Dict[str, Any],
    ) -> Dict[str, Any]:
        index = self._index_name(collection)

        last_conflict: Optional[Exception] = None
        for attempt in range(self._update_retries + 1):
            try:
                current = self._es.get_document_with_meta(index, doc_id)
            except Exception as exc:
                logger.error(
                    "Persistence update meta-read failed (collection=%s doc_id=%s)",
                    collection, doc_id, exc_info=True,
                )
                raise PersistenceError(
                    f"Failed to read document '{doc_id}' from '{collection}' before update"
                ) from exc

            if current is None:
                raise DocumentNotFoundError(collection, doc_id)

            # Missing concurrency metadata means ES cannot enforce OCC,
            # which would silently turn this into an unconditional write.
            # Fail loudly instead so the caller sees the broken guarantee
            # rather than a ghost last-writer-wins.
            seq_no = current.get("seq_no")
            primary_term = current.get("primary_term")
            if seq_no is None or primary_term is None:
                raise PersistenceError(
                    f"Elasticsearch returned no _seq_no/_primary_term for "
                    f"'{doc_id}' in '{collection}'; refusing unconditional update"
                )

            # Same metadata strip as ``create`` — callers that read,
            # mutate one field, and pass the dict back here would
            # otherwise round-trip the injected ``_id`` into ES.
            partial_with_ts = {
                k: v for k, v in partial.items() if k not in _ES_META_FIELDS
            }
            partial_with_ts["updated_at"] = self._now_iso()

            try:
                self._es.update_document(
                    index,
                    doc_id,
                    partial_with_ts,
                    refresh="wait_for",
                    if_seq_no=seq_no,
                    if_primary_term=primary_term,
                )
            except ConflictError as exc:
                last_conflict = exc
                logger.warning(
                    "Concurrent update conflict (collection=%s doc_id=%s attempt=%d/%d), retrying",
                    collection, doc_id, attempt + 1, self._update_retries + 1,
                )
                continue
            except Exception as exc:
                logger.error(
                    "Persistence update failed (collection=%s doc_id=%s)",
                    collection, doc_id, exc_info=True,
                )
                raise PersistenceError(
                    f"Failed to update document '{doc_id}' in '{collection}'"
                ) from exc

            merged = dict(current["source"])
            merged.update(partial_with_ts)
            merged["_id"] = doc_id
            return merged

        raise ConcurrentModificationError(collection, doc_id, self._update_retries) from last_conflict

    def delete(
        self,
        collection: str,
        doc_id: str,
    ) -> bool:
        index = self._index_name(collection)

        try:
            deleted = self._es.delete_document(index, doc_id, refresh="wait_for")
        except Exception as exc:
            logger.error("Persistence delete failed (collection=%s doc_id=%s)", collection, doc_id, exc_info=True)
            raise PersistenceError(f"Failed to delete document '{doc_id}' from '{collection}'") from exc

        if not deleted:
            raise DocumentNotFoundError(collection, doc_id)

        return True

    def list(
        self,
        collection: str,
        filters: Optional[Dict[str, Any]] = None,
        size: int = 100,
        from_: int = 0,
    ) -> Dict[str, Any]:
        index = self._index_name(collection)

        if filters:
            must_clauses = [
                {"term": {f"{key}.keyword": value}} if isinstance(value, str)
                else {"term": {key: value}}
                for key, value in filters.items()
            ]
            query = {"bool": {"must": must_clauses}}
        else:
            query = None

        try:
            result = self._es.search_documents(
                index, query=query, size=size, from_=from_,
                sort=[{"created_at": {"order": "desc", "unmapped_type": "date"}}],
            )
        except Exception as exc:
            logger.error("Persistence list failed (collection=%s)", collection, exc_info=True)
            raise PersistenceError(f"Failed to list documents in '{collection}'") from exc

        # Include the ES document id on every item — without it callers
        # cannot correlate list results back to subsequent update/delete
        # calls, forcing them to mirror doc_id into the document body.
        items = [
            {**hit["source"], "_id": hit["id"]}
            for hit in result.get("hits", [])
        ]
        return {"items": items, "total": result.get("total", 0)}

    def exists(
        self,
        collection: str,
        doc_id: str,
    ) -> bool:
        """Return True iff the document exists in ES.

        A truthy response means the backend is reachable AND the document
        is present. Backend failures (timeouts, auth errors, network
        blips) propagate as ``PersistenceError`` so callers relying on
        this method for control flow cannot mistake an outage for a
        confirmed miss.
        """
        index = self._index_name(collection)
        try:
            return self._es.get_document(index, doc_id) is not None
        except Exception as exc:
            logger.error(
                "Persistence exists() failed (collection=%s doc_id=%s)",
                collection, doc_id, exc_info=True,
            )
            raise PersistenceError(
                f"Failed to check existence of '{doc_id}' in '{collection}'"
            ) from exc

    def health(self) -> bool:
        return self._es.ping()
