import re

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.retrieval import Segment, backend


def _format_ts(ms: int) -> str:
    seconds, ms_part = divmod(int(ms), 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{ms_part:03d}"


def _segment_citation(segment: Segment, quote_limit: int = 140) -> dict:
    return {
        "video_id": segment.video_id,
        "start_ms": segment.start_ms,
        "end_ms": segment.end_ms,
        "quote": segment.text[:quote_limit],
        "timestamp": _format_ts(segment.start_ms),
    }


def parse_citations(text: str) -> list[dict]:
    """Extract the JSON-ish citation list the model appends under 'Citations:'."""
    citations: list[dict] = []
    marker = text.lower().rfind("citations:")
    if marker == -1:
        return citations
    for match in re.finditer(r"\{[^{}]*video_id[^{}]*\}", text[marker:]):
        try:
            import json

            raw = match.group(0).replace("'", '"')
            item = json.loads(raw)
            if "video_id" in item:
                citations.append(item)
        except (ValueError, TypeError):
            continue
    seen = set()
    unique = []
    for c in citations:
        key = (c.get("video_id"), c.get("start_ms"), c.get("end_ms"))
        if key not in seen:
            seen.add(key)
            unique.append(c)
    return unique


class TranscriptSearchInput(BaseModel):
    query: str = Field(description="What was said; keywords or a phrase")
    video_ids: list[str] = Field(description="Video IDs to search")


@tool("search_transcript", args_schema=TranscriptSearchInput)
def search_transcript(query: str, video_ids: list[str]) -> dict:
    """Search spoken content in video transcripts. Returns timestamped transcript segments."""
    scored: list[tuple[float, Segment]] = []
    for video_id in video_ids:
        for segment in backend.transcript(video_id):
            score = backend.keyword_score(query, segment.text)
            if score > 0:
                scored.append((score, segment))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    top = scored[: backend._settings.max_segments_per_search]
    return {
        "results": [
            {"timestamp": _format_ts(s.start_ms), "start_ms": s.start_ms, "end_ms": s.end_ms,
             "video_id": s.video_id, "text": s.text, "score": round(score, 3)}
            for score, s in top
        ],
        "citations": [_segment_citation(s) for _, s in top],
    }


class VisualSearchInput(BaseModel):
    query: str = Field(description="What is visually happening; objects, actions, scenes")
    video_ids: list[str] = Field(description="Video IDs to search")


@tool("search_visual_events", args_schema=VisualSearchInput)
def search_visual_events(query: str, video_ids: list[str]) -> dict:
    """Search visual events detected in videos (objects, actions, on-screen text)."""
    scored: list[tuple[float, Segment]] = []
    for video_id in video_ids:
        for event in backend.visual_events(video_id):
            haystack = f"{event.label or ''} {event.text}"
            score = max(backend.keyword_score(query, haystack), event.confidence * 0.25)
            if score > 0:
                scored.append((score, event))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    top = scored[: backend._settings.max_segments_per_search]
    return {
        "results": [
            {"timestamp": _format_ts(s.start_ms), "start_ms": s.start_ms, "end_ms": s.end_ms,
             "video_id": s.video_id, "label": s.label, "description": s.text, "score": round(score, 3)}
            for score, s in top
        ],
        "citations": [_segment_citation(s) for _, s in top],
    }


class RetrieveContextInput(BaseModel):
    query: str = Field(description="Thematic question or topic to retrieve context for")
    video_ids: list[str] = Field(description="Video IDs to search")
    k: int = Field(default=6, description="Number of chunks to retrieve")


@tool("retrieve_context", args_schema=RetrieveContextInput)
def retrieve_context(query: str, video_ids: list[str], k: int = 6) -> dict:
    """Semantic retrieval over combined transcript + visual chunks using embeddings."""
    pairs = backend.semantic_top_chunks(query, video_ids, max(k, 1))
    results = []
    citations = []
    for chunk, score in pairs:
        results.append({
            "chunk_id": chunk.chunk_id,
            "video_id": chunk.video_id,
            "start_ms": chunk.start_ms,
            "end_ms": chunk.end_ms,
            "text": chunk.text[:600],
            "score": round(score, 3),
        })
        citations.append({
            "video_id": chunk.video_id,
            "start_ms": chunk.start_ms,
            "end_ms": chunk.end_ms,
            "quote": chunk.text[:140],
        })
    return {"results": results, "citations": citations}


class TimestampInput(BaseModel):
    start_ms: int = Field(description="Start of the cited range, milliseconds")
    end_ms: int = Field(default=0, description="End of the range; 0 means same as start")
    video_id: str = Field(description="Video the timestamps belong to")
    quote: str = Field(default="", description="Short supporting text for the citation")


@tool("get_timestamp", args_schema=TimestampInput)
def get_timestamp(start_ms: int, end_ms: int, video_id: str, quote: str = "") -> dict:
    """Normalize and validate a timestamp range into a well-formed citation object."""
    if end_ms < start_ms:
        end_ms = start_ms
    return {
        "citation": {
            "video_id": video_id,
            "start_ms": start_ms,
            "end_ms": end_ms,
            "quote": quote[:140],
            "timestamp": _format_ts(start_ms),
            "readable_range": f"{_format_ts(start_ms)} - {_format_ts(end_ms)}",
        }
    }


ALL_TOOLS = [search_transcript, search_visual_events, retrieve_context, get_timestamp]

__all__ = [
    "ALL_TOOLS",
    "search_transcript",
    "search_visual_events",
    "retrieve_context",
    "get_timestamp",
    "parse_citations",
    "_format_ts",
]
