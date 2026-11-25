"""Elasticsearch access for the KPI worker: sliced search over the shared PIT,
and the final PIT close (owned by the finalizer)."""

from typing import Any

from elasticsearch import AsyncElasticsearch

from kpi_worker.config import settings


class ElasticsearchService:
    def __init__(self, client: AsyncElasticsearch | None = None) -> None:
        self._client = client or AsyncElasticsearch(hosts=[settings.es_url])

    async def search(self, **kwargs: Any) -> dict[str, Any]:
        # .body is the deserialized JSON dict; the ObjectApiResponse wrapper is
        # dict-like at runtime but not typed as dict.
        resp = await self._client.search(**kwargs)
        return resp.body

    async def close_pit(self, pit_id: str) -> None:
        await self._client.close_point_in_time(id=pit_id)

    async def close(self) -> None:
        await self._client.close()
