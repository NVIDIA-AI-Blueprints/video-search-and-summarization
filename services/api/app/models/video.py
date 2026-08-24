import time
import uuid
from enum import StrEnum

from pydantic import BaseModel, Field


class ProcessingStatus(StrEnum):
    UPLOADED = "UPLOADED"
    TRANSCRIBING = "TRANSCRIBING"
    VISION_PROCESSING = "VISION_PROCESSING"
    CHUNKING = "CHUNKING"
    EMBEDDING = "EMBEDDING"
    READY = "READY"
    FAILED = "FAILED"


class TranscriptSegment(BaseModel):
    start_ms: int
    end_ms: int
    text: str
    speaker: str | None = None


class VisualEvent(BaseModel):
    start_ms: int
    end_ms: int
    label: str
    description: str
    confidence: float = 0.0


class VideoMetadata(BaseModel):
    entity_type: str = "video"
    video_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    owner_id: str
    filename: str
    content_type: str = "video/mp4"
    size_bytes: int = 0
    status: ProcessingStatus = ProcessingStatus.UPLOADED
    duration_ms: int | None = None
    title: str | None = None
    description: str | None = None
    error_message: str | None = None
    created_at: int = Field(default_factory=lambda: int(time.time() * 1000))
    updated_at: int = Field(default_factory=lambda: int(time.time() * 1000))


class VideoCreatedResponse(BaseModel):
    video: VideoMetadata
    upload_url: str


class VideoListResponse(BaseModel):
    videos: list[VideoMetadata]


class ChatRequest(BaseModel):
    question: str
    video_ids: list[str] = Field(default_factory=list)


class Citation(BaseModel):
    video_id: str
    start_ms: int
    end_ms: int
    quote: str | None = None


class ChatResponse(BaseModel):
    answer: str
    citations: list[Citation] = []
