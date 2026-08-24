"""Bedrock + S3 backed retrieval used by the agent tools.

Artifact layout (S3, one prefix per video):
  transcripts/{video_id}/transcript.json   -> [TranscriptSegment]
  artifacts/{video_id}/visual_events.json  -> [VisualEvent]
  chunks/{video_id}/chunks.json            -> [{chunk_id, start_ms, end_ms, text, visual_summary}]
  embeddings/{video_id}/embeddings.json    -> [{chunk_id, vector}]
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any

import boto3

from app.config import get_settings


@dataclass
class Segment:
    video_id: str
    start_ms: int
    end_ms: int
    text: str
    label: str | None = None
    confidence: float = 0.0


@dataclass
class Chunk:
    chunk_id: str
    video_id: str
    start_ms: int
    end_ms: int
    text: str
    vector: list[float] = field(default_factory=list)


class RetrievalBackend:
    def __init__(self) -> None:
        settings = get_settings()
        self._bucket = settings.artifacts_bucket
        self._s3 = boto3.client("s3", region_name=settings.aws_region)
        self._bedrock_runtime = boto3.client("bedrock-runtime", region_name=settings.aws_region)
        self._settings = settings

    # ---- artifact loading -------------------------------------------------

    def _load_json(self, key: str) -> Any:
        try:
            response = self._s3.get_object(Bucket=self._bucket, Key=key)
            return json.loads(response["Body"].read().decode("utf-8"))
        except self._s3.exceptions.NoSuchKey:
            return []

    def transcript(self, video_id: str) -> list[Segment]:
        rows = self._load_json(f"transcripts/{video_id}/transcript.json")
        return [
            Segment(video_id=video_id, start_ms=int(r["start_ms"]), end_ms=int(r["end_ms"]), text=r["text"])
            for r in rows
        ]

    def visual_events(self, video_id: str) -> list[Segment]:
        rows = self._load_json(f"artifacts/{video_id}/visual_events.json")
        return [
            Segment(
                video_id=video_id,
                start_ms=int(r["start_ms"]),
                end_ms=int(r["end_ms"]),
                text=r.get("description", ""),
                label=r.get("label"),
                confidence=float(r.get("confidence", 0.0)),
            )
            for r in rows
        ]

    def chunks(self, video_id: str) -> list[Chunk]:
        rows = self._load_json(f"chunks/{video_id}/chunks.json")
        vectors = {v["chunk_id"]: v["vector"] for v in self._load_json(f"embeddings/{video_id}/embeddings.json")}
        return [
            Chunk(
                chunk_id=str(r["chunk_id"]),
                video_id=video_id,
                start_ms=int(r["start_ms"]),
                end_ms=int(r["end_ms"]),
                text=r.get("text", ""),
                vector=vectors.get(str(r["chunk_id"]), []),
            )
            for r in rows
        ]

    # ---- embedding + scoring ----------------------------------------------

    def embed_text(self, text: str) -> list[float]:
        response = self._bedrock_runtime.invoke_model(
            modelId=self._settings.bedrock_embedding_model_id,
            body=json.dumps({"inputText": text}),
            accept="application/json",
            contentType="application/json",
        )
        payload = json.loads(response["body"].read())
        return payload["embedding"]

    @staticmethod
    def cosine(a: list[float], b: list[float]) -> float:
        if not a or not b or len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(y * y for y in b))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)

    @staticmethod
    def keyword_score(query: str, text: str) -> float:
        q_terms = set(query.lower().split()) - {"the", "a", "an", "is", "are", "of", "in", "on", "and"}
        if not q_terms or not text:
            return 0.0
        t_terms = set(text.lower().split())
        overlap = q_terms & t_terms
        return len(overlap) / len(q_terms)

    def semantic_top_chunks(self, query: str, video_ids: list[str], k: int) -> list[tuple[Chunk, float]]:
        query_vector = self.embed_text(query)
        scored: list[tuple[Chunk, float]] = []
        for video_id in video_ids:
            for chunk in self.chunks(video_id):
                scored.append((chunk, self.cosine(query_vector, chunk.vector)))
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored[:k]


backend = RetrievalBackend()
