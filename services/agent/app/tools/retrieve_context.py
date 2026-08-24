from pydantic import BaseModel, Field

from app.retrieval import backend
from app.tools.base import observable_tool
from app.tools.common import make_citation


class RetrieveContextInput(BaseModel):
    query: str = Field(description="Thematic question or topic to retrieve context for")
    video_ids: list[str] = Field(description="Video IDs to search")
    k: int = Field(default=6, description="Number of chunks to retrieve")


def _retrieve_context_impl(query: str, video_ids: list[str], k: int = 6) -> dict:
    pairs = backend.semantic_top_chunks(query, video_ids, max(k, 1))
    results = []
    citations = []
    for chunk, score in pairs:
        results.append(
            {
                "chunk_id": chunk.chunk_id,
                "video_id": chunk.video_id,
                "start_ms": chunk.start_ms,
                "end_ms": chunk.end_ms,
                "text": chunk.text[:600],
                "score": round(score, 3),
            }
        )
        citations.append(
            make_citation(chunk.video_id, chunk.start_ms, chunk.end_ms, quote=chunk.text)
        )
    return {"results": results, "citations": citations}


retrieve_context = observable_tool(
    name="retrieve_context",
    description=(
        "Semantic retrieval over combined transcript + visual chunks using "
        "embeddings. Best for broad or thematic questions."
    ),
    args_schema=RetrieveContextInput,
    func=_retrieve_context_impl,
)
