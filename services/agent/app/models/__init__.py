"""Request/response models for the agent service."""

from pydantic import BaseModel, Field


class Citation(BaseModel):
    video_id: str
    start_ms: int
    end_ms: int
    quote: str | None = None
    timestamp: str | None = None


class AgentAnswer(BaseModel):
    answer: str
    citations: list[Citation] = Field(default_factory=list)


class InvocationRequest(BaseModel):
    question: str = Field(min_length=1)
    video_ids: list[str] = Field(default_factory=list)


def coerce_citations(raw: list[dict]) -> list[Citation]:
    citations: list[Citation] = []
    seen: set[tuple] = set()
    for item in raw:
        try:
            citation = Citation.model_validate(item)
        except Exception:
            continue
        key = (citation.video_id, citation.start_ms, citation.end_ms)
        if key not in seen:
            seen.add(key)
            citations.append(citation)
    return citations
