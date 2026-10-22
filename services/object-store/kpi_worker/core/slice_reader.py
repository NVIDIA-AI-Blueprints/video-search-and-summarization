"""The sliced search_after + PIT pagination engine (mirrors snapshot §6.4).

Given the shared pit_id, the compiled query, and this worker's slice, page
through the slice with search_after until it is exhausted: shared PIT, disjoint
slice, unique-tiebreaker sort, flat-cost cursor, no total-hit counting, and
source filtering.
"""

from typing import Any, AsyncIterator

from kpi_worker.shared.elasticsearch import ElasticsearchService

# Fetch only what the manifest needs — the single biggest perf lever.
MANIFEST_SOURCE = ["metadata"]


async def read_slice(
    es: ElasticsearchService,
    *,
    pit_id: str,
    query: dict[str, Any],
    slice_id: int,
    slice_max: int,
    sort: list,
    keep_alive: str,
    page_size: int,
) -> AsyncIterator[list[dict[str, Any]]]:
    """Yield pages of hits for one slice until it is exhausted."""
    after = None
    while True:
        body: dict[str, Any] = {
            "pit": {"id": pit_id, "keep_alive": keep_alive},  # shared frozen view
            "query": query,
            "sort": sort,                                      # unique tiebreaker
            "size": page_size,
            "track_total_hits": False,                        # no recount per page
            "source": MANIFEST_SOURCE,                        # source filtering
        }
        # ES rejects slice.max < 2; with a single slice, don't slice at all.
        if slice_max and slice_max > 1:
            body["slice"] = {"id": slice_id, "max": slice_max}
        if after is not None:
            body["search_after"] = after

        resp = await es.search(**body)
        hits = resp["hits"]["hits"]
        if not hits:
            return
        yield hits
        after = hits[-1]["sort"]  # advance the cursor to the last hit's sort
