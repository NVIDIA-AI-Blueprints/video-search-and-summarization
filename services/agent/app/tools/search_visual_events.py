from pydantic import BaseModel, Field

from app.retrieval import backend
from app.tools.base import observable_tool
from app.tools.common import format_timecode, make_citation


class VisualSearchInput(BaseModel):
    query: str = Field(description="What is visually happening; objects, actions, scenes")
    video_ids: list[str] = Field(description="Video IDs to search")


def _search_visual_events_impl(query: str, video_ids: list[str]) -> dict:
    scored: list[tuple[float, object]] = []
    for video_id in video_ids:
        for event in backend.visual_events(video_id):
            haystack = f"{event.label or ''} {event.text}"
            score = max(backend.keyword_score(query, haystack), event.confidence * 0.25)
            if score > 0:
                scored.append((score, event))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    top = scored[: backend.settings.max_segments_per_search]

    return {
        "results": [
            {
                "timestamp": format_timecode(s.start_ms),
                "start_ms": s.start_ms,
                "end_ms": s.end_ms,
                "video_id": s.video_id,
                "label": s.label,
                "description": s.text,
                "score": round(score, 3),
            }
            for score, s in top
        ],
        "citations": [
            make_citation(s.video_id, s.start_ms, s.end_ms, quote=f"{s.label}: {s.text}")
            for _, s in top
        ],
    }


search_visual_events = observable_tool(
    name="search_visual_events",
    description=(
        "Search visual events detected in videos: objects, actions, scenes, "
        "on-screen text. Use this for what is seen rather than spoken."
    ),
    args_schema=VisualSearchInput,
    func=_search_visual_events_impl,
)
