"""Elasticsearch access for the eval-set worker: sliced search over the shared
PIT (opened by the backend), and the final PIT close (owned by the summary
consumer)."""

from typing import Any

from elasticsearch import AsyncElasticsearch

from eval_set_worker.config import settings


class ElasticsearchService:
    def __init__(self, client: AsyncElasticsearch | None = None) -> None:
        self._client = client or AsyncElasticsearch(hosts=[settings.es_url])

    async def search_slice_page(
        self,
        pit_id: str,
        query: dict[str, Any],
        slice_id: int,
        slice_max: int,
        sort: list[Any],
        keep_alive: str,
        size: int,
        search_after: list[Any] | None = None,
        source: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch one page of a slice over the shared PIT (design §6.4, App. A).

        Uses PIT + slice + search_after. `track_total_hits` is disabled on the
        hot path; the caller advances `search_after` to the last hit's sort and
        repeats until an empty page. `slice_max == 1` disables slicing (single
        slice over the whole PIT) since ES rejects slice with max=1.
        """
        # Explicit kwargs (not body=) — elasticsearch-py 9.x deprecated body= and
        # it conflicts with native params. `source` maps to _source; when a PIT
        # is supplied the index must be omitted.
        kwargs: dict[str, Any] = {
            "pit": {"id": pit_id, "keep_alive": keep_alive},
            "query": query,
            "sort": sort,
            "size": size,
            "track_total_hits": False,
        }
        if slice_max > 1:
            kwargs["slice"] = {"id": slice_id, "max": slice_max}
        if search_after is not None:
            kwargs["search_after"] = search_after
        if source is not None:
            kwargs["source"] = source
        resp = await self._client.search(**kwargs)
        return list(resp["hits"]["hits"])

    async def close_pit(self, pit_id: str) -> None:
        await self._client.close_point_in_time(id=pit_id)

    async def close(self) -> None:
        await self._client.close()
