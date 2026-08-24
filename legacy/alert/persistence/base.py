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

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class PersistenceStore(ABC):
    """Schema-agnostic persistence interface for durable document storage.

    Implementations must provide CRUD + list operations over a collection
    of JSON documents.  Each document is identified by a (collection, doc_id)
    pair where *collection* maps to the backend's native container (e.g. an
    Elasticsearch index) and *doc_id* is a caller-supplied unique key.

    The interface deliberately carries no knowledge of prompts, alert configs,
    or any other domain object — that concern lives in the layers above.

    Document id convention: every method that returns a stored document
    (``create`` / ``read`` / ``update`` / each item under ``list``) echoes
    the id as the ``"_id"`` key so callers can round-trip to subsequent
    ``update`` / ``delete`` calls without having to mirror the id into
    the document body.
    """

    @abstractmethod
    def create(
        self,
        collection: str,
        doc_id: str,
        document: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Store a new document.

        Args:
            collection: Logical collection / index name.
            doc_id: Unique identifier within the collection.
            document: Arbitrary JSON-serialisable dict.

        Returns:
            The stored document (may include server-added fields like timestamps).

        Raises:
            DocumentAlreadyExistsError: if *doc_id* is already present.
            PersistenceError: on backend failure.
        """

    @abstractmethod
    def read(
        self,
        collection: str,
        doc_id: str,
    ) -> Dict[str, Any]:
        """Retrieve a single document by ID.

        Returns:
            The document dict.

        Raises:
            DocumentNotFoundError: if *doc_id* does not exist.
            PersistenceError: on backend failure.
        """

    @abstractmethod
    def update(
        self,
        collection: str,
        doc_id: str,
        partial: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Merge *partial* into an existing document.

        Only the keys present in *partial* are overwritten; other fields
        are preserved.

        Returns:
            The full document after the merge.

        Raises:
            DocumentNotFoundError: if *doc_id* does not exist.
            PersistenceError: on backend failure.
        """

    @abstractmethod
    def delete(
        self,
        collection: str,
        doc_id: str,
    ) -> bool:
        """Remove a document.

        Returns:
            True if the document was deleted.

        Raises:
            DocumentNotFoundError: if *doc_id* does not exist.
            PersistenceError: on backend failure.
        """

    @abstractmethod
    def list(
        self,
        collection: str,
        filters: Optional[Dict[str, Any]] = None,
        size: int = 100,
        from_: int = 0,
    ) -> Dict[str, Any]:
        """List documents, optionally filtered.

        Args:
            collection: Collection / index to query.
            filters: Key-value pairs for exact-match filtering.
                     e.g. ``{"alert_type": "collision"}``
            size: Maximum number of documents to return.
            from_: Offset for pagination.

        Returns:
            ``{"items": [dict, ...], "total": int}``
        """

    @abstractmethod
    def exists(
        self,
        collection: str,
        doc_id: str,
    ) -> bool:
        """Return True if the document exists."""

    @abstractmethod
    def health(self) -> bool:
        """Return True if the backend is reachable and operational."""
