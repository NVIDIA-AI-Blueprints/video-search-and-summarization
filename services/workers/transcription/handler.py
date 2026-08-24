"""Transcription worker: Amazon Transcribe job lifecycle.

Step Functions task payloads:
  mode=START   {media_bucket, media_key, video_id}      -> {transcription_job_name}
  mode=POLL    {transcription_job_name}                  -> {status, transcript_s3_key?}
"""

from __future__ import annotations

import os
from urllib.parse import urlparse

import boto3

from workers.common.artifacts import (
    ArtifactStore,
    TranscriptSegment,
    video_id_from_s3_key,
)


def _transcribe_client():
    return boto3.client("transcribe")


def start_transcription(event: dict) -> dict:
    media_key = event["media_key"]
    media_bucket = event.get("media_bucket") or os.environ["MEDIA_BUCKET"]
    video_id = event.get("video_id") or video_id_from_s3_key(media_key)
    job_name = f"ava-{video_id}"

    _transcribe_client().start_transcription_job(
        TranscriptionJobName=job_name,
        LanguageCode=os.environ.get("TRANSCRIBE_LANGUAGE", "en-US"),
        Media={"MediaFileUri": f"s3://{media_bucket}/{media_key}"},
        OutputBucketName=os.environ["ARTIFACTS_BUCKET"],
        Subtitles={"Formats": ["vtt"]},
        Settings={"ShowSpeakerLabels": True, "MaxSpeakerLabels": 5},
    )
    return {"transcription_job_name": job_name, "video_id": video_id}


def poll_transcription(event: dict) -> dict:
    job_name = event["transcription_job_name"]
    job = _transcribe_client().get_transcription_job(TranscriptionJobName=job_name)[
        "TranscriptionJob"
    ]
    status = job["TranscriptionJobStatus"]

    result: dict = {"status": status}
    if status == "COMPLETED":
        transcript_uri = job["Transcript"]["TranscriptFileUri"]
        raw = _fetch_transcript_json(transcript_uri)
        segments = _to_segments(raw)
        store = ArtifactStore()
        key = f"transcripts/{event['video_id']}/transcript.json"
        store.put_json(key, [s.to_dict() for s in segments])
        result["transcript_s3_key"] = key
    elif status == "FAILED":
        result["failure_reason"] = job.get("FailureReason", "unknown")
    return result


def _fetch_transcript_json(uri: str) -> dict:
    parsed = urlparse(uri)
    bucket = parsed.netloc
    key = parsed.path.lstrip("/")
    return ArtifactStore(bucket).get_json(key)


def _to_segments(payload: dict) -> list[TranscriptSegment]:
    items = payload["results"].get("items", [])
    segments: list[TranscriptSegment] = []
    buffer: dict | None = None

    for item in items:
        if item["type"] == "pronunciation":
            start = int(float(item["start_time"]) * 1000)
            end = int(float(item["end_time"]) * 1000)
            if buffer and start - buffer["end_ms"] < 1200:
                buffer["text"] += f" {item['alternatives'][0]['content']}"
                buffer["end_ms"] = end
            else:
                if buffer:
                    segments.append(TranscriptSegment(**buffer))
                buffer = {
                    "start_ms": start,
                    "end_ms": end,
                    "text": item["alternatives"][0]["content"],
                }
        elif item["type"] == "punctuation" and buffer:
            buffer["text"] += item["alternatives"][0]["content"]

    if buffer:
        segments.append(TranscriptSegment(**buffer))
    return segments


def lambda_handler(event: dict, context=None) -> dict:
    mode = event.get("mode", "START")
    if mode == "START":
        return start_transcription(event)
    if mode == "POLL":
        return poll_transcription(event)
    raise ValueError(f"Unknown transcription mode: {mode}")
