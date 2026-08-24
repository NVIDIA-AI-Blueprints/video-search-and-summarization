"""Vision worker: frame sampling + Bedrock multimodal visual event extraction."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile

import boto3

from workers.common.artifacts import ArtifactStore, VisualEvent, video_id_from_s3_key


FRAME_INTERVAL_SECONDS = 5.0
MAX_FRAMES = 120


def lambda_handler(event: dict, context=None) -> dict:
    """Extract frames from the source video and describe them with Bedrock.

    Requires ffmpeg in the execution environment (container image or layer).
    """
    media_key = event["media_key"]
    media_bucket = event.get("media_bucket") or os.environ["MEDIA_BUCKET"]
    video_id = event.get("video_id") or video_id_from_s3_key(media_key)
    _ = media_bucket, video_id  # wired below once Bedrock prompt flow is finalized

    raise NotImplementedError(
        "Vision worker is scaffolded; implement frame extraction + Bedrock analysis (see TODO)."
    )


def extract_frames(local_video: str, interval: float = FRAME_INTERVAL_SECONDS,
                   max_frames: int = MAX_FRAMES) -> list[tuple[float, str]]:
    """Sample frames at `interval` seconds; returns [(timestamp_seconds, file_path)]."""
    workdir = tempfile.mkdtemp(prefix="frames-")
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", local_video,
         "-vf", f"fps=1/{interval},scale=854:-2", f"{workdir}/frame-%05d.jpg"],
        check=True,
    )
    frames = []
    for index, name in enumerate(sorted(os.listdir(workdir))):
        if index >= max_frames:
            break
        frames.append((index * interval, os.path.join(workdir, name)))
    return frames


def describe_frame(bedrock_runtime, model_id: str, image_bytes: bytes) -> dict:
    """Ask Bedrock to describe one frame; returns {label, description}."""
    import base64

    response = bedrock_runtime.invoke_model(
        modelId=model_id,
        body=json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 300,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "image", "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": base64.b64encode(image_bytes).decode(),
                    }},
                    {"type": "text", "text":
                        "Describe what is visible. Reply with JSON: "
                        '{"label": "<short noun phrase>", "description": "<one sentence>"}'},
                ],
            }],
        }),
    )
    return json.loads(_extract_json(response["body"].read()))


def _extract_json(raw: bytes) -> str:
    text = raw.decode("utf-8")
    start, end = text.find("{"), text.rfind("}") + 1
    if start == -1 or end == 0:
        raise ValueError(f"No JSON in model response: {text[:200]}")
    return text[start:end]


def cleanup(frames_dir: str) -> None:
    shutil.rmtree(frames_dir, ignore_errors=True)


__all__ = [
    "lambda_handler",
    "extract_frames",
    "describe_frame",
    "VisualEvent",
    "ArtifactStore",
]
