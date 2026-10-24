"""Elasticsearch access for Workflow 2 inference upserts and catalog snapshots.

Uses a single AsyncElasticsearch client (same pattern as main) for PIT lifecycle,
inference search/update, and snapshot planning.
"""

import hashlib
import logging
from typing import Any

from elasticsearch import ApiError, AsyncElasticsearch

from backend.api.metadata import AOIMetadata
from backend.config import settings

logger = logging.getLogger(__name__)

OBJ_STORE_PATH_FIELD = "metadata.content_metadata.objStorePath"

# ES re-reads and re-applies the partial doc on a version conflict instead of
# returning 409. Guards against concurrent/redelivered inference results for the
# same board (Kafka at-least-once + archiver retries) colliding on one document.
RETRY_ON_CONFLICT = 5


def aoi_metadata_to_es_content_metadata(metadata: AOIMetadata) -> dict[str, Any]:
    """Flatten nested AOIMetadata to ES ``content_metadata`` (mirrors Logstash ingest)."""
    machine = metadata.machineInfo
    defects = metadata.aoiDefects
    image = metadata.imageInfo
    path = metadata.objStorePath

    cm: dict[str, Any] = {
        "doc_type": "aoi_inspection",
        "uuid": hashlib.sha256(path.encode()).hexdigest()[:32],
        "doc_i": 0,
        "chunkIdx": 0,
        "machineId": machine.machineId,
        "location": machine.location,
        "jobType": machine.jobType,
        "defectCount": defects.count,
        "objStorePath": path,
        "imageType": image.imageType,
        "imageSize": image.imageSize,
        "version": metadata.version,
        "description": metadata.description,
        "timestamp": metadata.timestamp,
    }

    if image.cropInfo:
        cm["cropInfo"] = [
            crop.model_dump(by_alias=True, exclude_none=True) for crop in image.cropInfo
        ]

    if metadata.inferenceSummary:
        cm["inferenceSummary"] = metadata.inferenceSummary.model_dump(exclude_none=True)

    return {k: v for k, v in cm.items() if v != ""}


class ElasticsearchService:
    def __init__(self, client: AsyncElasticsearch | None = None) -> None:
        self._client = client or AsyncElasticsearch(hosts=[settings.es_url])
        self._owns_client = client is None

    async def close(self) -> None:
        if self._owns_client:
            await self._client.close()

    async def open_pit(self, index: str | None = None, keep_alive: str | None = None) -> str:
        """Open one Point-in-Time over the target index and return its pit_id."""
        resp = await self._client.open_point_in_time(
            index=index or settings.es_index,
            keep_alive=keep_alive or settings.pit_keep_alive,
        )
        return resp["id"]

    async def close_pit(self, pit_id: str) -> None:
        await self._client.close_point_in_time(id=pit_id)

    async def catalog_totals(
        self,
        query: dict,
        index: str | None = None,
        *,
        allow_missing: bool = False,
    ) -> tuple[int, int]:
        """Live total record count and summed image bytes for a query."""
        # allow_no_indices: both catalogs are wildcard patterns, and a pattern
        # that currently matches nothing is a zero result, not an error.
        # ignore_unavailable: a catalog that has never been written has no index,
        # and Elasticsearch answers index_not_found rather than zero. Only callers
        # that expect that (a Smart City deployment before its first frame) ask
        # for it to be swallowed.
        resp = await self._client.search(
            index=index or settings.es_index,
            query=query,
            size=0,
            track_total_hits=True,
            allow_no_indices=True,
            ignore_unavailable=allow_missing,
            aggregations={
                "total_bytes": {"sum": {"field": "metadata.content_metadata.imageSize"}}
            },
        )
        # A fresh deployment has no catalog index until the first sample reaches
        # Logstash. Elasticsearch returns a successful zero-shard response in
        # that case, without the requested aggregations.
        if resp.get("_shards", {}).get("total") == 0:
            return 0, 0
        total_records = resp["hits"]["total"]["value"]
        total_bytes = int(resp["aggregations"]["total_bytes"]["value"] or 0)
        return total_records, total_bytes

    async def find_document_by_obj_store_path(
        self, obj_store_path: str
    ) -> dict[str, Any] | None:
        resp = await self._client.search(
            index=settings.elasticsearch_index_pattern,
            query={"term": {OBJ_STORE_PATH_FIELD: obj_store_path}},
            size=1,
        )
        hits = resp["hits"]["hits"]
        return hits[0] if hits else None

    async def upsert_inference_result(self, metadata: AOIMetadata) -> bool:
        """Write inference-enriched metadata to ES as flattened content_metadata."""
        obj_store_path = metadata.objStorePath
        doc = await self.find_document_by_obj_store_path(obj_store_path)
        if doc is None:
            logger.warning("No ES document for objStorePath=%s", obj_store_path)
            return False

        index = doc["_index"]
        doc_id = doc["_id"]
        content_metadata = aoi_metadata_to_es_content_metadata(metadata)

        await self._client.update(
            index=index,
            id=doc_id,
            doc={"metadata": {"content_metadata": content_metadata}},
            retry_on_conflict=RETRY_ON_CONFLICT,
        )
        logger.info("Updated inference summary for %s in %s", obj_store_path, index)
        return True

    async def search_pit_page(
        self,
        pit_id: str,
        query: dict[str, Any],
        *,
        size: int,
        search_after: list[Any] | None = None,
        source_includes: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """One page of a PIT scan in _shard_doc order.

        Hits carry _seq_no/_primary_term so a caller can delete a document only
        if it has not changed since it was read.
        """
        kwargs: dict[str, Any] = {}
        if search_after is not None:
            kwargs["search_after"] = search_after
        resp = await self._client.search(
            pit={"id": pit_id, "keep_alive": settings.pit_keep_alive},
            query=query,
            sort=[{"_shard_doc": "asc"}],
            size=size,
            seq_no_primary_term=True,
            source_includes=source_includes,
            track_total_hits=False,
            **kwargs,
        )
        return resp["hits"]["hits"]

    @staticmethod
    def _conditional(hit: dict[str, Any]) -> dict[str, Any]:
        op: dict[str, Any] = {"_index": hit["_index"], "_id": hit["_id"]}
        if "_seq_no" in hit and "_primary_term" in hit:
            op["if_seq_no"] = hit["_seq_no"]
            op["if_primary_term"] = hit["_primary_term"]
        return op

    @staticmethod
    def _not_applied(resp: dict[str, Any], action: str) -> tuple[set[str], set[str]]:
        """Split a bulk response into (changed since read, other failures) ids.

        A conditional write to a document that no longer exists is answered
        with 409 "no document was found", not 404; it lands in neither set.
        """
        conflicts: set[str] = set()
        failed: set[str] = set()
        for item in resp.get("items", []):
            result = item.get(action, {})
            status = result.get("status")
            if status in (200, 201, 404):
                continue
            error = result.get("error") or {}
            if status == 409 and "no document was found" in str(error.get("reason", "")):
                continue
            (conflicts if status == 409 else failed).add(result.get("_id"))
            logger.warning(
                "%s %s/%s not applied: %s",
                action,
                result.get("_index"),
                result.get("_id"),
                error.get("type", status),
            )
        return conflicts, failed

    async def delete_hits(self, hits: list[dict[str, Any]]) -> set[str]:
        """Delete the given hits; return the _ids still present afterwards.

        Each delete is conditional on the _seq_no/_primary_term the hit was read
        with, so a document rewritten in the meantime (a frame archived again)
        is left alone. A document that is already gone counts as deleted. The
        refresh makes the deletions visible to the next search.
        """
        if not hits:
            return set()
        operations = [{"delete": self._conditional(hit)} for hit in hits]
        resp = await self._client.bulk(operations=operations, refresh="wait_for")
        conflicts, failed = self._not_applied(resp, "delete")
        return conflicts | failed

    async def update_hits(self, hits: list[dict[str, Any]], doc: dict[str, Any]) -> set[str]:
        """Apply one partial document to each hit; return the _ids not updated.

        Conditional on the version each hit was read with, like delete_hits.
        A document that no longer exists is not recreated.
        """
        if not hits:
            return set()
        operations: list[dict[str, Any]] = []
        for hit in hits:
            operations.append({"update": self._conditional(hit)})
            operations.append({"doc": doc})
        resp = await self._client.bulk(operations=operations, refresh="wait_for")
        conflicts, failed = self._not_applied(resp, "update")
        return conflicts | failed

    async def ensure_date_field(self, index: str, field: str) -> None:
        """Add a date field under metadata.content_metadata to existing indices.

        The index template covers indices created from now on; the indices are
        dynamic: strict, so the field must be mapped before it is written.
        Adding a field is idempotent.
        """
        await self._client.indices.put_mapping(
            index=index,
            properties={
                "metadata": {
                    "properties": {
                        "content_metadata": {"properties": {field: {"type": "date"}}}
                    }
                }
            },
            allow_no_indices=True,
        )

    async def referenced_paths(
        self, index: str, paths: list[str], exclude_ids: list[str] | None = None
    ) -> set[str]:
        """The subset of objStorePath values that some document still points to,
        leaving out the documents in exclude_ids.

        This answer decides what may be deleted, so it must be complete: closed
        indices are expanded (and make the search fail), partial results are
        refused, and a pattern that matches no shard raises LookupError instead
        of reading as "nothing is referenced".
        """
        if not paths:
            return set()
        query: dict[str, Any] = {"terms": {OBJ_STORE_PATH_FIELD: paths}}
        if exclude_ids:
            query = {"bool": {"filter": [query], "must_not": [{"ids": {"values": exclude_ids}}]}}
        try:
            resp = await self._client.search(
                index=index,
                query=query,
                size=0,
                expand_wildcards="all",
                ignore_unavailable=False,
                allow_no_indices=True,
                allow_partial_search_results=False,
                aggregations={
                    "paths": {"terms": {"field": OBJ_STORE_PATH_FIELD, "size": len(paths)}}
                },
            )
        except ApiError as exc:
            # A closed index answers index_closed_exception, a failed shard a
            # search_phase_execution_exception: either way, no complete answer.
            raise LookupError(f"search over {index} failed: {exc}") from exc
        shards = resp.get("_shards", {})
        if shards.get("total", 0) == 0:
            raise LookupError(f"index pattern {index} matches no shard")
        if shards.get("failed", 0) or "aggregations" not in resp:
            raise LookupError(f"search over {index} was incomplete: {shards}")
        return {b["key"] for b in resp["aggregations"]["paths"]["buckets"]}
