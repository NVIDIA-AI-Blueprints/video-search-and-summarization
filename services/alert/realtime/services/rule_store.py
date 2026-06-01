#!/usr/bin/env python3
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

"""
Domain-specific rule storage for real-time VLM alert rules.

:class:`RuleStore` provides a CRUD interface fixed to the
``alert-realtime-rules`` collection.  :class:`ESRuleStore` delegates to
the shared :class:`~persistence.base.PersistenceStore` so the existing
Elasticsearch client, index-prefix, OCC retry, and auto-create logic
are reused without duplication.
"""

import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

from persistence import DocumentNotFoundError, PersistenceError, PersistenceStore

logger = logging.getLogger(__name__)

DEFAULT_RULES_COLLECTION = "alert-realtime-rules"


class RuleStore(ABC):
    """CRUD interface for durable realtime alert rule storage.

    All write methods raise :class:`~persistence.exceptions.PersistenceError`
    (or a subclass) on backend failure so callers can surface 502 without
    catching bare ``Exception``.
    """

    @abstractmethod
    def create(self, rule_id: str, document: Dict[str, Any]) -> Dict[str, Any]:
        """Persist a new rule document.

        Raises:
            DocumentAlreadyExistsError: if *rule_id* already exists.
            PersistenceError: on backend failure.
        """

    @abstractmethod
    def get(self, rule_id: str) -> Optional[Dict[str, Any]]:
        """Return a rule by ID, or ``None`` if not found.

        Raises:
            PersistenceError: on backend failure.
        """

    @abstractmethod
    def list(
        self,
        filters: Optional[Dict[str, Any]] = None,
        size: int = 100,
        from_: int = 0,
    ) -> Dict[str, Any]:
        """List rules.  Returns ``{"items": [...], "total": int}``.

        Raises:
            PersistenceError: on backend failure.
        """

    @abstractmethod
    def update(self, rule_id: str, partial: Dict[str, Any]) -> Dict[str, Any]:
        """Merge *partial* into an existing rule document.

        Raises:
            DocumentNotFoundError: if *rule_id* does not exist.
            PersistenceError: on backend failure.
        """

    @abstractmethod
    def delete(self, rule_id: str) -> bool:
        """Delete a rule.  Returns ``True`` if deleted, ``False`` if
        the rule was already absent.

        Raises:
            PersistenceError: on backend failure.
        """


class ESRuleStore(RuleStore):
    """Elasticsearch-backed :class:`RuleStore`.

    Delegates every operation to :class:`~persistence.base.PersistenceStore`.
    The collection name defaults to ``alert-realtime-rules`` but can be
    overridden via ``rtvi_vlm.rules_collection`` in ``config.yaml``.
    The underlying store handles index creation, OCC, and refresh
    semantics.
    """

    def __init__(
        self,
        store: PersistenceStore,
        collection: str = DEFAULT_RULES_COLLECTION,
    ) -> None:
        self._store = store
        self._collection = collection

    def create(self, rule_id: str, document: Dict[str, Any]) -> Dict[str, Any]:
        return self._store.create(self._collection, rule_id, document)

    def get(self, rule_id: str) -> Optional[Dict[str, Any]]:
        try:
            return self._store.read(self._collection, rule_id)
        except DocumentNotFoundError:
            return None

    def list(
        self,
        filters: Optional[Dict[str, Any]] = None,
        size: int = 100,
        from_: int = 0,
    ) -> Dict[str, Any]:
        return self._store.list(
            self._collection, filters=filters, size=size, from_=from_,
        )

    def update(self, rule_id: str, partial: Dict[str, Any]) -> Dict[str, Any]:
        return self._store.update(self._collection, rule_id, partial)

    def delete(self, rule_id: str) -> bool:
        # PersistenceStore.delete raises DocumentNotFoundError on 404;
        # RuleStore.delete promises idempotent semantics (False = absent).
        # The translation is intentional: infrastructure layer raises,
        # domain layer returns a boolean.
        try:
            return self._store.delete(self._collection, rule_id)
        except DocumentNotFoundError:
            return False
