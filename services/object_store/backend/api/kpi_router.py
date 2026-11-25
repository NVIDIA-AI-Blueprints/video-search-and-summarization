from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status

from backend.api.kpi_schemas import KpiCountResponse, KpiRequest, KpiResponse, KpiStatusResponse
from backend.api.kpi_service import KpiEmptySelectionError, KpiFanoutError, compile_kpi_selection, create_kpi, get_kpi
from backend.api.router import get_storage
from backend.shared.elasticsearch import ElasticsearchService
from backend.shared.kafka import KafkaService
from backend.shared.storage import StorageService

router = APIRouter(prefix="/v1", tags=["kpi"])


def get_kafka(request: Request) -> KafkaService:
    return request.app.state.kafka


def get_es(request: Request) -> ElasticsearchService:
    return request.app.state.elasticsearch


@router.get("/kpi/count", response_model=KpiCountResponse)
async def get_kpi_count(
    tag: str,
    es: ElasticsearchService = Depends(get_es),
) -> KpiCountResponse:
    query = compile_kpi_selection({"tag": tag})
    total_records, _ = await es.catalog_totals(query)
    return KpiCountResponse(count=total_records)


@router.post(
    "/{catalog}/kpi",
    response_model=KpiResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_kpi_endpoint(
    catalog: Literal["aoi", "its"],
    body: KpiRequest,
    es: ElasticsearchService = Depends(get_es),
    kafka: KafkaService = Depends(get_kafka),
    storage: StorageService = Depends(get_storage),
) -> KpiResponse:
    # Returns promptly once the slices are fanned out; the export completes
    # asynchronously in the KPI worker.
    try:
        kpi_id = await create_kpi(es, kafka, storage, body)
    except KpiEmptySelectionError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        )
    except KpiFanoutError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        )
    return KpiResponse(kpi_id=kpi_id)


@router.post("/kpi", response_model=KpiResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_legacy_kpi_endpoint(
    body: KpiRequest,
    es: ElasticsearchService = Depends(get_es),
    kafka: KafkaService = Depends(get_kafka),
    storage: StorageService = Depends(get_storage),
) -> KpiResponse:
    """Backward-compatible alias for POST /v1/aoi/kpi."""
    return await create_kpi_endpoint("aoi", body, es, kafka, storage)


@router.get("/kpi/{kpi_id}", response_model=KpiStatusResponse)
async def get_kpi_endpoint(
    kpi_id: str,
    storage: StorageService = Depends(get_storage),
) -> KpiStatusResponse:
    result = await get_kpi(storage, kpi_id)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"kpi {kpi_id} not found"
        )
    return KpiStatusResponse.model_validate(result)
