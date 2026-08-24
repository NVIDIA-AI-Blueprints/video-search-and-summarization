"""Chunking worker: merge transcript segments + visual events into retrieval chunks."""

from __future__ import annotations

import os

from workers.common.artifacts import ArtifactStore, ChunkRecord, VisualEvent, TranscriptSegment

MAX_CHUNK_MS = 30_000
MIN_CHUNK_MS = 8_000


def lambda_handler(event: dict, context=None) -> dict:
    video_id = event["video_id"]
    store = ArtifactStore()

    transcript = [TranscriptSegment(**s) for s in store.get_json(f"transcripts/{video_id}/transcript.json")]
    visual = [VisualEvent(**v) for v in store.get_json(f"artifacts/{video_id}/visual_events.json")]

    chunks = build_chunks(video_id, transcript, visual)
    key = f"chunks/{video_id}/chunks.json"
    store.put_json(key, [c.to_dict() for c in chunks])
    return {"chunks_s3_key": key, "chunk_count": len(chunks)}


def build_chunks(
    video_id: str,
    transcript: list[TranscriptSegment],
    visual: list[VisualEvent],
) -> list[ChunkRecord]:
    boundaries = _boundaries(transcript, visual)
    chunks: list[ChunkRecord] = []

    for index in range(len(boundaries) - 1):
        start_ms, end_ms = boundaries[index], boundaries[index + 1]
        text_parts = [
            segment.text
            for segment in transcript
            if segment.start_ms >= start_ms and segment.end_ms <= end_ms
        ]
        visuals = [
            f"[{event.label}] {event.description}"
            for event in visual
            if event.start_ms < end_ms and event.end_ms > start_ms
        ]
        if not text_parts and not visuals:
            continue
        chunks.append(
            ChunkRecord(
                chunk_id=f"{video_id}-chunk-{index:04d}",
                start_ms=start_ms,
                end_ms=end_ms,
                text=" ".join(text_parts),
                visual_summary=" ".join(visuals),
            )
        )
    return chunks


def _boundaries(transcript: list[TranscriptSegment], visual: list[VisualEvent]) -> list[int]:
    """Cut points: gap-based where possible, capped at MAX_CHUNK_MS."""
    edges = {0}
    last_end = 0
    for segment in sorted(transcript, key=lambda s: s.start_ms):
        gap = segment.start_ms - last_end
        if gap > MIN_CHUNK_MS:
            edges.add(last_end + gap // 2)
            edges.add(segment.start_ms)
        last_end = max(last_end, segment.end_ms)

    for event in visual:
        edges.add(event.start_ms - MIN_CHUNK_MS if event.start_ms > MIN_CHUNK_MS else 0)

    video_end = max([last_end] + [e.end_ms for e in visual]) if (transcript or visual) else 0
    cursor = 0
    while video_end - cursor > MAX_CHUNK_MS:
        cursor += MAX_CHUNK_MS
        edges.add(cursor)
    edges.add(video_end)
    return sorted(e for e in edges if e <= video_end)


__all__ = ["lambda_handler", "build_chunks"]
