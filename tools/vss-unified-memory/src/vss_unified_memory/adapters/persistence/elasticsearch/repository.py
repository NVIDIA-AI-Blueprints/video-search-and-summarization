# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Elasticsearch implementation of the generic memory repository."""

from collections.abc import Mapping
from typing import Any, cast

from elasticsearch import Elasticsearch, NotFoundError
from elasticsearch.helpers import bulk

from vss_unified_memory.adapters.persistence.elasticsearch.mapper import (
    source_to_entity,
    summary_to_documents,
)
from vss_unified_memory.adapters.persistence.elasticsearch.models import ElasticsearchDocument
from vss_unified_memory.application.errors import RepositoryError
from vss_unified_memory.application.models import (
    MemoryEmbeddings,
    MemoryQuery,
    MemorySearchResult,
    RepositoryWriteResult,
    WriteStatus,
)
from vss_unified_memory.domain.models import Event, MemoryEntity, RecordType, Summary


class ElasticsearchMemoryRepository:
    _SOURCE_EXCLUDES = ["summary_chunks.embedding", "event_chunks.embedding"]

    def __init__(
        self,
        endpoint: str,
        index: str,
        *,
        embedding_dimensions: int = 768,
        request_timeout_seconds: float = 30.0,
        client: Elasticsearch | None = None,
    ) -> None:
        self._client = client or Elasticsearch(endpoint, request_timeout=request_timeout_seconds)
        self._index = index
        self._embedding_dimensions = embedding_dimensions

    def save(
        self,
        memory: MemoryEntity,
        embeddings: MemoryEmbeddings | None = None,
    ) -> RepositoryWriteResult:
        self._validate_embeddings(embeddings)
        if isinstance(memory, Summary):
            summary_document, event_documents = summary_to_documents(memory, embeddings)
        elif isinstance(memory, Event):
            raise RepositoryError("standalone events must be persisted through their parent summary", retryable=False)
        else:  # pragma: no cover - closed union guard
            raise RepositoryError("unsupported memory entity", retryable=False)

        documents: tuple[ElasticsearchDocument, ...] = (summary_document, *event_documents)
        attempted_records = len(documents)
        try:
            successful_records, errors = self._bulk_documents(documents)
        except Exception as error:
            raise RepositoryError("Elasticsearch memory write failed") from error

        return RepositoryWriteResult(
            status=WriteStatus.DEGRADED if errors else WriteStatus.COMPLETE,
            attempted_records=attempted_records,
            successful_records=successful_records,
        )

    def get(
        self,
        record_id: str,
        record_type: RecordType | None = None,
        include_related: bool = False,
    ) -> MemoryEntity | None:
        try:
            response = self._client.get(index=self._index, id=record_id, source_excludes=self._SOURCE_EXCLUDES)
        except NotFoundError:
            return None
        except Exception as error:
            raise RepositoryError("Elasticsearch read failed") from error

        source: Mapping[str, Any] = response["_source"]
        actual_type = RecordType(str(source["record_type"]))
        if record_type is not None and actual_type != record_type:
            return None
        related_sources: tuple[Mapping[str, Any], ...] = ()
        if include_related and actual_type == RecordType.VIDEO_SUMMARY:
            related_sources = self._get_related_events_for_summaries((str(source["summary_id"]),)).get(
                str(source["summary_id"]), ()
            )
        return source_to_entity(source, related_sources)

    def search(self, query: MemoryQuery) -> tuple[MemorySearchResult, ...]:
        filters = self._build_filters(query)
        try:
            if query.semantic:
                if query.query_vector is None:
                    raise RepositoryError("semantic query is missing its vector", retryable=False)
                return self._semantic_search(query, filters)
            else:
                must: list[dict[str, Any]] = []
                if query.query_text:
                    must.append(
                        {
                            "multi_match": {
                                "query": query.query_text,
                                "fields": ["description", "event_type.text"],
                            }
                        }
                    )
                response = self._client.search(
                    index=self._index,
                    query={"bool": {"must": must or [{"match_all": {}}], "filter": filters}},
                    size=query.limit,
                    source_excludes=self._SOURCE_EXCLUDES,
                )
        except RepositoryError:
            raise
        except Exception as error:
            raise RepositoryError("Elasticsearch search failed") from error

        hits = tuple(response["hits"]["hits"])
        summary_ids = tuple(
            str(hit["_source"]["summary_id"])
            for hit in hits
            if hit["_source"].get("record_type") == RecordType.VIDEO_SUMMARY.value
        )
        related_by_summary = self._get_related_events_for_summaries(summary_ids)
        return tuple(
            MemorySearchResult(
                memory=source_to_entity(
                    hit["_source"],
                    related_by_summary.get(str(hit["_source"].get("summary_id")), ()),
                ),
                score=hit.get("_score"),
            )
            for hit in hits
        )

    def _semantic_search(
        self,
        query: MemoryQuery,
        filters: list[dict[str, Any]],
    ) -> tuple[MemorySearchResult, ...]:
        assert query.query_vector is not None
        candidate_limit = min(100, max(query.limit * 5, query.limit))
        summary_hits: tuple[Mapping[str, Any], ...] = ()
        event_hits: tuple[Mapping[str, Any], ...] = ()

        if query.record_type in (None, RecordType.VIDEO_SUMMARY):
            summary_hits = self._knn_hits(
                field="summary_chunks.embedding",
                query_vector=query.query_vector,
                filters=[*filters, {"term": {"record_type": RecordType.VIDEO_SUMMARY.value}}],
                limit=candidate_limit,
                inner_hits={
                    "name": "matching_summary_chunks",
                    "size": 3,
                    "_source": [
                        "summary_chunks.chunk_id",
                        "summary_chunks.ordinal",
                        "summary_chunks.start_char",
                        "summary_chunks.end_char",
                        "summary_chunks.text",
                    ],
                },
            )
        if query.record_type in (None, RecordType.VIDEO_EVENT):
            event_hits = self._knn_hits(
                field="event_chunks.embedding",
                query_vector=query.query_vector,
                filters=[*filters, {"term": {"record_type": RecordType.VIDEO_EVENT.value}}],
                limit=candidate_limit,
                inner_hits={
                    "name": "matching_event_chunks",
                    "size": 3,
                    "_source": [
                        "event_chunks.chunk_id",
                        "event_chunks.ordinal",
                        "event_chunks.start_char",
                        "event_chunks.end_char",
                        "event_chunks.text",
                    ],
                },
            )

        scores: dict[str, float] = {}
        parent_sources: dict[str, Mapping[str, Any]] = {}
        for hit in summary_hits:
            source: Mapping[str, Any] = hit["_source"]
            summary_id = str(source["summary_id"])
            parent_sources[summary_id] = source
            scores[summary_id] = max(scores.get(summary_id, float("-inf")), float(hit.get("_score") or 0.0))
        for hit in event_hits:
            source = hit["_source"]
            summary_id = str(source["summary_id"])
            scores[summary_id] = max(scores.get(summary_id, float("-inf")), float(hit.get("_score") or 0.0))

        ranked_ids = tuple(
            summary_id for summary_id, _ in sorted(scores.items(), key=lambda item: (-item[1], item[0]))[: query.limit]
        )
        missing_ids = tuple(summary_id for summary_id in ranked_ids if summary_id not in parent_sources)
        parent_sources.update(self._get_summaries_by_ids(missing_ids))
        related_by_summary = self._get_related_events_for_summaries(ranked_ids)
        return tuple(
            MemorySearchResult(
                memory=source_to_entity(parent_sources[summary_id], related_by_summary.get(summary_id, ())),
                score=scores[summary_id],
            )
            for summary_id in ranked_ids
            if summary_id in parent_sources
        )

    def _knn_hits(
        self,
        *,
        field: str,
        query_vector: tuple[float, ...],
        filters: list[dict[str, Any]],
        limit: int,
        inner_hits: dict[str, Any] | None = None,
    ) -> tuple[Mapping[str, Any], ...]:
        knn: dict[str, Any] = {
            "field": field,
            "query_vector": list(query_vector),
            "k": limit,
            "num_candidates": max(100, limit * 10),
            "filter": {"bool": {"filter": filters}},
        }
        if inner_hits is not None:
            knn["inner_hits"] = inner_hits
        response = self._client.search(
            index=self._index,
            knn=knn,
            size=limit,
            source_excludes=self._SOURCE_EXCLUDES,
        )
        return tuple(response["hits"]["hits"])

    def _get_summaries_by_ids(self, summary_ids: tuple[str, ...]) -> dict[str, Mapping[str, Any]]:
        if not summary_ids:
            return {}
        try:
            response = self._client.mget(
                index=self._index,
                ids=list(summary_ids),
                source_excludes=self._SOURCE_EXCLUDES,
            )
        except Exception as error:
            raise RepositoryError("Elasticsearch summary hydration failed") from error
        return {
            str(document["_source"]["summary_id"]): cast(Mapping[str, Any], document["_source"])
            for document in response["docs"]
            if document.get("found")
        }

    def _get_related_events_for_summaries(
        self,
        summary_ids: tuple[str, ...],
    ) -> dict[str, tuple[Mapping[str, Any], ...]]:
        if not summary_ids:
            return {}
        try:
            response = self._client.search(
                index=self._index,
                query={
                    "bool": {
                        "filter": [
                            {"terms": {"summary_id": list(summary_ids)}},
                            {"term": {"record_type": RecordType.VIDEO_EVENT.value}},
                        ]
                    }
                },
                sort=[{"summary_id": "asc"}, {"ordinal": "asc"}],
                size=10000,
                source_excludes=self._SOURCE_EXCLUDES,
            )
        except Exception as error:
            raise RepositoryError("Elasticsearch related-event lookup failed") from error
        hits = response["hits"]["hits"]
        total = response["hits"]["total"]["value"]
        if total > len(hits):
            raise RepositoryError("related-event result exceeded the 10000-event safety limit", retryable=False)
        grouped: dict[str, list[Mapping[str, Any]]] = {summary_id: [] for summary_id in summary_ids}
        for hit in hits:
            source: Mapping[str, Any] = hit["_source"]
            grouped[str(source["summary_id"])].append(source)
        return {summary_id: tuple(events) for summary_id, events in grouped.items()}

    @staticmethod
    def _build_filters(query: MemoryQuery) -> list[dict[str, Any]]:
        filters: list[dict[str, Any]] = []
        if query.record_type is not None:
            filters.append({"term": {"record_type": query.record_type.value}})
        if query.video_id is not None:
            filters.append({"term": {"video_id": query.video_id}})
        if query.time_range is not None:
            filters.extend(
                [
                    {"range": {"start_seconds": {"lte": query.time_range.end_seconds}}},
                    {"range": {"end_seconds": {"gte": query.time_range.start_seconds}}},
                ]
            )
        return filters

    def _validate_embeddings(self, embeddings: MemoryEmbeddings | None) -> None:
        if embeddings is None:
            return
        invalid = next(
            (
                (item.passage.id, item.vector)
                for item in embeddings.summary.passages
                if len(item.vector) != self._embedding_dimensions
            ),
            None,
        )
        if invalid is not None:
            raise RepositoryError(
                f"embedding for {invalid[0]} has {len(invalid[1])} dimensions; expected {self._embedding_dimensions}",
                retryable=False,
            )
        invalid_event = next(
            (
                (item.passage.id, item.vector)
                for record in embeddings.events
                for item in record.passages
                if len(item.vector) != self._embedding_dimensions
            ),
            None,
        )
        if invalid_event is not None:
            raise RepositoryError(
                f"embedding for {invalid_event[0]} has {len(invalid_event[1])} dimensions; "
                f"expected {self._embedding_dimensions}",
                retryable=False,
            )

    def _bulk_documents(
        self,
        documents: tuple[ElasticsearchDocument, ...],
    ) -> tuple[int, list[dict[str, Any]]]:
        actions = (
            {"_op_type": "index", "_index": self._index, "_id": document.id, "_source": document.to_source()}
            for document in documents
        )
        successful, errors = bulk(
            self._client,
            actions,
            refresh="wait_for",
            raise_on_error=False,
            raise_on_exception=True,
        )
        return successful, cast(list[dict[str, Any]], errors)
