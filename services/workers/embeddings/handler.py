"""Embedding worker: Titan embeddings per chunk, stored as JSON vectors in S3."""

from __future__ import annotations

import json
import os

import boto3

from workers.common.artifacts import ArtifactStore

EMBED_BATCH_SIZE = 20


def lambda_handler(event: dict, context=None) -> dict:
    video_id = event["video_id"]
    store = ArtifactStore()
    chunks = store.get_json(f"chunks/{video_id}/chunks.json")

    client = boto3.client("bedrock-runtime", region_name=os.environ.get("AWS_REGION", "us-east-1"))
    model_id = os.environ.get("BEDROCK_EMBED_MODEL_ID", "amazon.titan-embed-text-v2:0")

    embeddings = []
    for offset in range(0, len(chunks), EMBED_BATCH_SIZE):
        batch = chunks[offset : offset + EMBED_BATCH_SIZE]
        for chunk in batch:
            vector = embed_text(client, model_id, _chunk_input(chunk))
            embeddings.append({"chunk_id": chunk["chunk_id"], "vector": vector})

    key = f"embeddings/{video_id}/embeddings.json"
    store.put_json(key, embeddings)
    return {"embeddings_s3_key": key, "embedding_count": len(embeddings)}


def _chunk_input(chunk: dict) -> str:
    parts = [chunk.get("text", ""), chunk.get("visual_summary", "")]
    return "\n".join(part for part in parts if part).strip() or "(empty chunk)"


def embed_text(client, model_id: str, text: str) -> list[float]:
    response = client.invoke_model(
        modelId=model_id,
        body=json.dumps({"inputText": text}),
        accept="application/json",
        contentType="application/json",
    )
    return json.loads(response["body"].read())["embedding"]


__all__ = ["embed_text", "lambda_handler"]
