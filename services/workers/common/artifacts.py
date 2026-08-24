"""Shared models and AWS helpers for pipeline workers."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict


@dataclass
class TranscriptSegment:
    start_ms: int
    end_ms: int
    text: str
    speaker: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class VisualEvent:
    start_ms: int
    end_ms: int
    label: str
    description: str
    confidence: float = 0.0
    frame_s3_key: str | None = None

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass
class ChunkRecord:
    chunk_id: str
    start_ms: int
    end_ms: int
    text: str
    visual_summary: str = ""
    source_segment_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


class ArtifactStore:
    """Thin S3 wrapper for pipeline artifacts."""

    def __init__(self, bucket: str | None = None) -> None:
        import boto3

        self._bucket = bucket or os.environ["ARTIFACTS_BUCKET"]
        self._s3 = boto3.client("s3")

    def put_json(self, key: str, payload) -> str:
        self._s3.put_object(
            Bucket=self._bucket,
            Key=key,
            Body=json.dumps(payload, default=str).encode("utf-8"),
            ContentType="application/json",
        )
        return key

    def get_json(self, key: str):
        response = self._s3.get_object(Bucket=self._bucket, Key=key)
        return json.loads(response["Body"].read().decode("utf-8"))

    def get_bytes(self, key: str) -> bytes:
        response = self._s3.get_object(Bucket=self._bucket, Key=key)
        return response["Body"].read()


def video_id_from_s3_key(key: str) -> str | None:
    """Extract the video id from a media key of form videos/<owner>/<video_id>/<file>."""
    parts = key.split("/")
    if len(parts) >= 4 and parts[0] == "videos":
        return parts[2]
    return None
