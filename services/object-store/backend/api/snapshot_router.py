from fastapi import APIRouter, Depends, HTTPException, Request, status

from backend.api.router import get_storage
from backend.api.snapshot_schemas import (
    CatalogStatsResponse,
    SnapshotRequest,
    SnapshotResponse,
    SnapshotStatusResponse,
)
from backend.api.snapshot_service import (
    SnapshotEmptySelectionError,
    SnapshotFanoutError,
    SnapshotSelectionError,
    create_snapshot,
    get_catalog_stats,
    get_snapshot,
)
from backend.shared.catalog import Catalog
from backend.shared.elasticsearch import ElasticsearchService
from backend.shared.kafka import KafkaService
from backend.shared.storage import StorageService

router = APIRouter(prefix="/v1", tags=["snapshot"])


def get_kafka(request: Request) -> KafkaService:
    return request.app.state.kafka


def get_es(request: Request) -> ElasticsearchService:
    return request.app.state.elasticsearch


@router.post(
    "/{catalog}/snapshot",
    response_model=SnapshotResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_snapshot_endpoint(
    catalog: Catalog,
    body: SnapshotRequest,
    es: ElasticsearchService = Depends(get_es),
    kafka: KafkaService = Depends(get_kafka),
    storage: StorageService = Depends(get_storage),
) -> SnapshotResponse:
    # Returns promptly once the slices are fanned out; the export completes
    # asynchronously in the consumer group.
    try:
        snapshot_id = await create_snapshot(es, kafka, storage, body, catalog)
    except (SnapshotSelectionError, SnapshotEmptySelectionError) as exc:
        # The selection itself is unusable; retrying it unchanged cannot help.
        # Structured so a caller can act on the offending keys without parsing
        # prose -- and so the catalog's whole field list is not shipped back on
        # every rejected request.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=exc.as_detail()
        )
    except SnapshotFanoutError as exc:
        # Fan-out failed and the PIT was cleaned up; ask the caller to retry.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        )
    return SnapshotResponse(snapshot_id=snapshot_id)


@router.get("/{catalog}/snapshot/stats", response_model=CatalogStatsResponse)
async def get_catalog_stats_endpoint(
    catalog: Catalog,
    es: ElasticsearchService = Depends(get_es),
    storage: StorageService = Depends(get_storage),
) -> CatalogStatsResponse:
    # Declared before /{catalog}/snapshot/{snapshot_id} so "stats" is not
    # captured as an id. Always 200: before the first snapshot the stats report
    # the live catalog with null last-snapshot pointers, not a 404.
    stats = await get_catalog_stats(es, storage, catalog)
    return CatalogStatsResponse.model_validate(stats)


@router.get("/{catalog}/snapshot/{snapshot_id}", response_model=SnapshotStatusResponse)
async def get_snapshot_endpoint(
    catalog: Catalog,
    snapshot_id: str,
    storage: StorageService = Depends(get_storage),
) -> SnapshotStatusResponse:
    snapshot = await get_snapshot(storage, snapshot_id)
    if snapshot is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"snapshot {snapshot_id} not found",
        )
    return SnapshotStatusResponse.model_validate(snapshot)


# Unprefixed aliases, kept so callers written against the single-catalog API
# keep working. They mean the AOI catalog, which is what they meant before the
# segment existed. FastAPI has no optional path segment, so each is a real route
# that delegates rather than a default on the parameter -- a default would make
# `catalog` a query parameter and let /v1/snapshot?catalog=its in by the back
# door. As above, "stats" is declared before "{snapshot_id}" so it is not
# captured as an id.


@router.post(
    "/snapshot", response_model=SnapshotResponse, status_code=status.HTTP_202_ACCEPTED
)
async def create_snapshot_default_catalog(
    body: SnapshotRequest,
    es: ElasticsearchService = Depends(get_es),
    kafka: KafkaService = Depends(get_kafka),
    storage: StorageService = Depends(get_storage),
) -> SnapshotResponse:
    return await create_snapshot_endpoint("aoi", body, es, kafka, storage)


@router.get("/snapshot/stats", response_model=CatalogStatsResponse)
async def get_catalog_stats_default_catalog(
    es: ElasticsearchService = Depends(get_es),
    storage: StorageService = Depends(get_storage),
) -> CatalogStatsResponse:
    return await get_catalog_stats_endpoint("aoi", es, storage)


@router.get("/snapshot/{snapshot_id}", response_model=SnapshotStatusResponse)
async def get_snapshot_default_catalog(
    snapshot_id: str,
    storage: StorageService = Depends(get_storage),
) -> SnapshotStatusResponse:
    return await get_snapshot_endpoint("aoi", snapshot_id, storage)
