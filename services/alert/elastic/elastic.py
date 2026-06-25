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
ElasticSearch client utilities for reading and writing JSON documents.

Requirements:
  pip install elasticsearch>=8

Example:
  from alert_agent.elastic import ElasticClient
  es = ElasticClient(url="http://localhost:9200")
  es.ensure_json_index('anomalies')
  es.write_json('anomalies', {'sensorId': 'cam-1', 'data': 'hello world'})
  doc = es.get_document('anomalies', 'doc-id-123')
"""

from __future__ import annotations
import copy
import json
import logging
from dataclasses import dataclass
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from mdx.anomaly.utils.elastic_ready import (
    generate_alert_fingerprint,
    normalize_alert_event,
    generate_incident_fingerprint,
    normalize_incident_event,
)

logger = logging.getLogger(__name__)

try:
    # elasticsearch-py v8
    from elasticsearch import Elasticsearch
    from elasticsearch import ApiError  # type: ignore
    from elasticsearch import ConflictError  # type: ignore
except Exception:  # pragma: no cover
    # Fallback types if package not installed yet
    Elasticsearch = object  # type: ignore
    class ApiError(Exception):  # type: ignore
        pass
    class ConflictError(ApiError):  # type: ignore
        pass


logger = logging.getLogger(__name__)


@dataclass
class ElasticConfig:
    hosts: Tuple[str, ...] = tuple()
    username: Optional[str] = None
    password: Optional[str] = None
    api_key: Optional[str] = None
    cloud_id: Optional[str] = None
    verify_certs: bool = False
    ca_certs: Optional[str] = None
    request_timeout: int = 10


class ElasticClient:
    """Thin wrapper around elasticsearch-py for indexing text documents."""

    def __init__(self, 
                 url: Optional[str] = None,
                 config: Optional[ElasticConfig] = None) -> None:
        """Initialize ElasticClient.
        
        Args:
            url: Elasticsearch URL (e.g., "http://localhost:9200"). 
                 If provided, overrides config.hosts.
            config: ElasticConfig object for advanced configuration.
        """
        cfg = config or ElasticConfig()
        
        # Override hosts if URL is provided
        if url:
            cfg.hosts = (url,)

        if not cfg.cloud_id and not cfg.hosts:
            raise ValueError("Elastic configuration requires a 'hosts' entry or cloud_id.")

        client_kwargs: Dict[str, Any] = {
            "verify_certs": cfg.verify_certs,
            "request_timeout": cfg.request_timeout,
        }

        if cfg.ca_certs:
            client_kwargs["ca_certs"] = cfg.ca_certs

        if cfg.cloud_id:
            client_kwargs["cloud_id"] = cfg.cloud_id
        else:
            client_kwargs["hosts"] = list(cfg.hosts)

        if cfg.api_key:
            client_kwargs["api_key"] = cfg.api_key
        elif cfg.username and cfg.password:
            # elasticsearch-py v8 prefers basic_auth
            try:
                client_kwargs["basic_auth"] = (cfg.username, cfg.password)  # type: ignore[arg-type]
            except TypeError:
                # older clients use http_auth
                client_kwargs["http_auth"] = (cfg.username, cfg.password)  # type: ignore[assignment]

        self.client: Elasticsearch = Elasticsearch(**client_kwargs)  # type: ignore[call-arg]
        
        # Cache for index existence checks to avoid repeated API calls
        self._index_cache: set = set()
        
        # Test connection during initialization
        if not self.ping():
            raise ConnectionError(f"Failed to connect to Elasticsearch at {cfg.hosts}. "
                                f"Please check if the service is running and accessible.")

    def ping(self) -> bool:
        """Return True if the cluster is reachable."""
        try:
            return bool(self.client.ping())  # type: ignore[attr-defined]
        except Exception:
            return False


    def generate_daily_index_name(self, base_name: str, timestamp_iso: str) -> str:
        """Generate a daily index name based on timestamp.
        
        Args:
            base_name: Base index name (e.g., 'mdx-vlm-incidents')
            timestamp_iso: ISO timestamp string (e.g., '2025-09-19T08:23:06.870Z')
            
        Returns:
            Index name with date suffix (e.g., 'mdx-vlm-incidents-2025-09-19')
        """
        try:
            # Extract date from ISO timestamp
            # Handle both with and without timezone info
            if timestamp_iso.endswith('Z'):
                dt = datetime.fromisoformat(timestamp_iso.replace('Z', '+00:00'))
            else:
                dt = datetime.fromisoformat(timestamp_iso)
            
            # Format as YYYY-MM-DD
            date_str = dt.strftime('%Y-%m-%d')
            return f"{base_name}-{date_str}"
        except Exception:
            # Fallback to current date if timestamp parsing fails
            date_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')
            return f"{base_name}-{date_str}"

    def write_json(
        self,
        index: str,
        json_doc: Dict[str, Any],
        doc_id: Optional[str] = None,
        refresh: str = "false",
        op_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Index a JSON document directly.

        Args:
            index: Destination index name.
            json_doc: JSON document to store (as Python dict).
            doc_id: Optional deterministic id.
            refresh: Refresh behavior ("true" | "false" | "wait_for"). Defaults to "false" for performance.
            op_type: Optional ES op_type. Use "create" for atomic create-only semantics
                     (raises ConflictError if doc_id already exists).
        """
        document = dict(json_doc)  # Copy to avoid modifying original

        try:
            index_kwargs: Dict[str, Any] = {
                "index": index,
                "id": doc_id,
                "document": document,
                "refresh": refresh,
            }
            if op_type:
                index_kwargs["op_type"] = op_type
            result = self.client.index(**index_kwargs)  # type: ignore[attr-defined]
            result_text = result.get("result") if isinstance(result, dict) else str(result)
            logger.info(
                "Successfully wrote document to Elasticsearch (index=%s doc_id=%s)",
                index,
                doc_id,
                extra={
                    "index": index,
                    "doc_id": doc_id,
                    "result": result_text,
                },
            )
            return result
        except Exception as e:
            logger.error(
                "Failed to index document to Elasticsearch",
                extra={
                    "index": index,
                    "doc_id": doc_id,
                    "error": str(e),
                    "error_type": type(e).__name__,
                },
                exc_info=True,
            )
            raise

    def update_document(
        self,
        index: str,
        doc_id: str,
        partial_doc: Dict[str, Any],
        refresh: str = "false",
        if_seq_no: Optional[int] = None,
        if_primary_term: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Perform a partial update on an existing document.

        Args:
            index: Destination index name.
            doc_id: Document ID to update.
            partial_doc: Partial document with fields to update/add.
            refresh: Refresh behavior ("true" | "false" | "wait_for").
            if_seq_no: Optional sequence number for optimistic concurrency control.
            if_primary_term: Optional primary term for optimistic concurrency control.
                             Both must be supplied together; ES raises ConflictError if
                             the document has been modified since these values were read.

        Returns:
            Elasticsearch update response.
        """
        # Optimistic concurrency is all-or-nothing on the ES side: sending
        # only one of ``if_seq_no`` / ``if_primary_term`` would silently
        # degrade the call to an unconditional update. Reject the
        # inconsistent combination up front so the caller can't lose the
        # concurrency guarantee by accident.
        if (if_seq_no is None) != (if_primary_term is None):
            raise ValueError(
                "if_seq_no and if_primary_term must both be provided together "
                "or both be omitted"
            )

        try:
            update_kwargs: Dict[str, Any] = {
                "index": index,
                "id": doc_id,
                "doc": partial_doc,
                "refresh": refresh,
            }
            if if_seq_no is not None and if_primary_term is not None:
                update_kwargs["if_seq_no"] = if_seq_no
                update_kwargs["if_primary_term"] = if_primary_term
            result = self.client.update(**update_kwargs)
            result_text = result.get("result") if isinstance(result, dict) else str(result)
            logger.info(
                "Successfully updated document in Elasticsearch (index=%s doc_id=%s)",
                index,
                doc_id,
                extra={
                    "index": index,
                    "doc_id": doc_id,
                    "result": result_text,
                },
            )
            return result
        except Exception as e:
            logger.error(
                "Failed to update document in Elasticsearch",
                extra={
                    "index": index,
                    "doc_id": doc_id,
                    "error": str(e),
                    "error_type": type(e).__name__,
                },
                exc_info=True,
            )
            raise

    def get_document(
        self,
        index: str,
        doc_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Retrieve a document by ID.

        Args:
            index: Index name.
            doc_id: Document ID.

        Returns:
            Document source dict, or None if not found.
        """
        try:
            result = self.client.get(index=index, id=doc_id)  # type: ignore[attr-defined]
            logger.info(
                "Retrieved document from Elasticsearch (index=%s doc_id=%s)",
                index,
                doc_id,
            )
            return result["_source"]
        except ApiError as e:  # type: ignore[misc]
            if getattr(e, "meta", None) and getattr(e.meta, "status", None) == 404:
                logger.debug(
                    "Document not found (index=%s doc_id=%s)", index, doc_id
                )
                return None
            logger.error(
                "Failed to get document from Elasticsearch",
                extra={
                    "index": index,
                    "doc_id": doc_id,
                    "error": str(e),
                    "error_type": type(e).__name__,
                },
                exc_info=True,
            )
            raise

    def get_document_with_meta(
        self,
        index: str,
        doc_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Retrieve a document along with its concurrency metadata.

        Args:
            index: Index name.
            doc_id: Document ID.

        Returns:
            ``{"source": dict, "seq_no": int, "primary_term": int}``, or
            None if not found.
        """
        try:
            result = self.client.get(index=index, id=doc_id)  # type: ignore[attr-defined]
            return {
                "source": result["_source"],
                "seq_no": result.get("_seq_no"),
                "primary_term": result.get("_primary_term"),
            }
        except ApiError as e:  # type: ignore[misc]
            if getattr(e, "meta", None) and getattr(e.meta, "status", None) == 404:
                return None
            logger.error(
                "Failed to get document with meta from Elasticsearch",
                extra={
                    "index": index,
                    "doc_id": doc_id,
                    "error": str(e),
                    "error_type": type(e).__name__,
                },
                exc_info=True,
            )
            raise

    def delete_document(
        self,
        index: str,
        doc_id: str,
        refresh: str = "false",
    ) -> bool:
        """Delete a document by ID.

        Args:
            index: Index name.
            doc_id: Document ID.
            refresh: Refresh behavior ("true" | "false" | "wait_for").

        Returns:
            True if deleted, False if not found.
        """
        try:
            result = self.client.delete(index=index, id=doc_id, refresh=refresh)  # type: ignore[attr-defined]
            result_text = result.get("result") if isinstance(result, dict) else str(result)
            logger.info(
                "Deleted document from Elasticsearch (index=%s doc_id=%s result=%s)",
                index,
                doc_id,
                result_text,
            )
            return True
        except ApiError as e:  # type: ignore[misc]
            if getattr(e, "meta", None) and getattr(e.meta, "status", None) == 404:
                logger.debug(
                    "Document not found for deletion (index=%s doc_id=%s)",
                    index,
                    doc_id,
                )
                return False
            logger.error(
                "Failed to delete document from Elasticsearch",
                extra={
                    "index": index,
                    "doc_id": doc_id,
                    "error": str(e),
                    "error_type": type(e).__name__,
                },
                exc_info=True,
            )
            raise

    def search_documents(
        self,
        index: str,
        query: Optional[Dict[str, Any]] = None,
        size: int = 100,
        from_: int = 0,
        sort: Optional[list] = None,
    ) -> Dict[str, Any]:
        """Search documents in an index.

        Args:
            index: Index name (supports wildcards, e.g. "ab-configs*").
            query: ES query DSL dict. Defaults to match_all.
            size: Max documents to return.
            from_: Offset for pagination.
            sort: Optional sort specification (e.g. [{"created_at": "desc"}]).

        Returns:
            {"hits": [{"id": ..., "source": ...}, ...], "total": int}
        """
        if query is None:
            query = {"match_all": {}}

        search_body: Dict[str, Any] = {"query": query, "size": size, "from": from_}
        if sort:
            search_body["sort"] = sort

        try:
            result = self.client.search(index=index, body=search_body)  # type: ignore[attr-defined]

            hits = result.get("hits", {})
            total = hits.get("total", {})
            total_value = total.get("value", 0) if isinstance(total, dict) else total

            documents = [
                {"id": hit["_id"], "source": hit["_source"]}
                for hit in hits.get("hits", [])
            ]

            logger.info(
                "Search completed (index=%s total=%s returned=%s)",
                index,
                total_value,
                len(documents),
            )
            return {"hits": documents, "total": total_value}
        except ApiError as e:  # type: ignore[misc]
            if getattr(e, "meta", None) and getattr(e.meta, "status", None) == 404:
                logger.debug("Index not found for search: %s", index)
                return {"hits": [], "total": 0}
            logger.error(
                "Failed to search documents in Elasticsearch",
                extra={
                    "index": index,
                    "error": str(e),
                    "error_type": type(e).__name__,
                },
                exc_info=True,
            )
            raise

    def ensure_json_index(self, index: str, shards: int = 1, replicas: int = 0) -> None:
        """Create index optimized for JSON documents (no predefined schema)."""
        # Check cache first to avoid repeated API calls
        if index in self._index_cache:
            logger.debug(f"Index {index} already in cache, skipping creation check")
            return
            
        try:
            exists = self.client.indices.exists(index=index)  # type: ignore[attr-defined]
            if not exists:
                logger.info(f"Creating new Elasticsearch index: {index}")
                body = {
                    "settings": {
                        "number_of_shards": shards,
                        "number_of_replicas": replicas,
                    },
                    "mappings": {
                        "dynamic": True,
                    },
                }
                self.client.indices.create(index=index, mappings=body.get("mappings"), settings=body.get("settings"))  # type: ignore[attr-defined]
                logger.info(f"Successfully created Elasticsearch index: {index}")
            else:
                logger.debug(f"Elasticsearch index already exists: {index}")
            # Add to cache after successful check/creation
            self._index_cache.add(index)
        except ApiError as e:  # type: ignore[misc]
            # Ignore resource_already_exists_exception races
            if getattr(e, "meta", None) and getattr(e.meta, "status", None) == 400:
                logger.debug(f"Index {index} already exists (caught race condition)")
                # Still add to cache since index exists
                self._index_cache.add(index)
            else:
                logger.error(
                    f"Failed to ensure index exists",
                    extra={
                        "index": index,
                        "error": str(e),
                        "error_type": type(e).__name__,
                    },
                    exc_info=True,
                )
                raise

    def write_event_response(
        self,
        message: Dict[str, Any],
        vlm_response: Dict[str, Any] | str,
        prompt: str,
        index: str,
        category_mapping: Optional[Dict[str, str]] = None,
        verdict_description_mapping: Optional[Dict[str, Dict[str, str]]] = None,
    ) -> Dict[str, Any]:
        """Write VLM-enhanced event response to Elasticsearch.

        Args:
            message: Original event message dict
            vlm_response: VLM response as JSON string or dict with 'dropped', 'confidence', 'explanation' fields
            prompt: The prompt used for VLM analysis (currently informational)
            index: Destination index name
            category_mapping: Optional mapping of category -> output_category for custom display names
            verdict_description_mapping: Optional mapping of category -> {verdict -> description}
        """

        # Respect a pre-populated 'info' block from the caller and never build/override it here.
        # This ensures the AlertBridgeResponse structure set by the orchestrator is preserved.
        document = copy.deepcopy(message)
     

        event_type = str(document.get("notification_type", "")).lower()
        # Treat only explicit 'alert' documents as alerts
        alert_kinds = {"alert"}
        doc_id: Optional[str] = None

        if event_type in alert_kinds:
            normalize_alert_event(document)
            doc_id = generate_alert_fingerprint(document)
        else:
            # Incidents: match Logstash normalization and fingerprinting
            normalize_incident_event(document)
            doc_id = generate_incident_fingerprint(document)
        # Populate a deterministic Id field in the document body for both alerts and incidents.
        # Populate a deterministic Id field in the document body when available.
        try:
            computed_fingerprint = doc_id
            if computed_fingerprint:
                document["Id"] = computed_fingerprint
        except Exception:
            # Non-fatal: leave Id unset if computation fails
            pass

        # ─── Apply verdict-based description override AFTER fingerprinting ───
        # Use ORIGINAL category for lookup (before category mapping is applied)
        if verdict_description_mapping and 'category' in document:
            original_category = document['category']
            if original_category in verdict_description_mapping:
                verdict = (document.get("info", {}).get("verdict") or "").lower()
                desc_mapping = verdict_description_mapping[original_category]
                if verdict in desc_mapping:
                    if "analyticsModule" not in document:
                        document["analyticsModule"] = {}
                    document["analyticsModule"]["description"] = desc_mapping[verdict]
                    logger.debug(
                        "Description overridden for category=%s verdict=%s",
                        original_category,
                        verdict,
                    )
        # ─────────────────────────────────────────────────────────────────────

        # ─── Apply custom output category AFTER fingerprinting ───
        # This ensures fingerprint uses original category, but output uses custom name
        if category_mapping and 'category' in document:
            original_category = document['category']
            if original_category in category_mapping:
                document['category'] = category_mapping[original_category]
                logger.debug(
                    "Category mapped for output: %s -> %s",
                    original_category,
                    document['category'],
                )
        # ─────────────────────────────────────────────────────────

        timestamp_value = document.get("timestamp", "")
        if not isinstance(timestamp_value, str):
            timestamp_value = str(timestamp_value)

        daily_index = self.generate_daily_index_name(index, timestamp_value)
        self.ensure_json_index(daily_index)
        # Trim heavy DEBUG logging unless explicitly enabled
        verbose_es = os.getenv('LOG_VERBOSE_ES', 'false').lower() in ('1', 'true', 'yes')
        if logger.isEnabledFor(logging.DEBUG):
            if verbose_es:
                try:
                    from utils.logging_helpers import redact_payload_for_log
                    compact_redacted = redact_payload_for_log(document)
                except Exception:
                    compact_redacted = "<unavailable>"
                logger.debug(
                    "Elasticsearch document prepared for %s: %s",
                    daily_index,
                    compact_redacted,
                )
            else:
                try:
                    # Lightweight summary without payload
                    size_bytes = len(json.dumps(document, separators=(",", ":")))
                except Exception:
                    size_bytes = -1
                logger.debug(
                    "Elasticsearch document ready for index=%s size_bytes=%s",
                    daily_index,
                    size_bytes,
                )
        
        try:
            return self.write_json(
                daily_index,
                document,
                doc_id=doc_id,
            )
        except Exception as e:
            logger.error(
                "Failed to write document to Elasticsearch",
                extra={
                    "index": daily_index,
                    "doc_id": doc_id,
                    "error": str(e),
                    "error_type": type(e).__name__,
                    "sensor_id": document.get("sensorId"),
                    "timestamp": document.get("timestamp"),
                    "category": document.get("category"),
                },
                exc_info=True,
            )
            raise