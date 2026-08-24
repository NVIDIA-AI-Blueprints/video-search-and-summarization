from pydantic import BaseModel, Field

from app.retrieval import backend
from app.tools.base import observable_tool
from app.tools.common import format_timecode, make_citation


class TranscriptSearchInput(BaseModel):
    query: str = Field(description="What was said; keywords or a phrase")
    video_ids: list[str] = Field(description="Video IDs to search")


def _search_transcript_impl(query: str, video_ids: list[str]) -> dict:
    scored: list[tuple[float, object]] = []
    for video_id in video_ids:
        for segment in backend.transcript(video_id):
            score = backend.keyword_score(query, segment.text)
            if score > 0:
                scored.append((score, segment))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    top = scored[: backend.settings.max_segments_per_search]

    return {
        "results": [
            {
                "timestamp": format_timecode(s.start_ms),
                "start_ms": s.start_ms,
                "end_ms": s.end_ms,
                "video_id": s.video_id,
                "text": s.text,
                "score": round(score, 3),
            }
            for score, s in top
        ],
        "citations": [
            make_citation(s.video_id, s.start_ms, s.end_ms, quote=s.text) for _, s in top
        ],
    }


search_transcript = observable_tool(
    name="search_transcript",
    description=(
        "Search spoken content in video transcripts. "
        "Returns timestamped transcript segments matching the query."
    ),
    args_schema=TranscriptSearchInput,
    func=_search_transcript_impl,
)
