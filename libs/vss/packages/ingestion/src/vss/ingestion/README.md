# Ingestion guide

`vss.ingestion` sends a local video to one caption or embedding pipeline and
returns validated response chunks.

## Install

From the repository root:

```bash
uv pip install vss-ingestion
```

Install OpenCV separately for `cpu_vlm`, or PyNvVideoCodec for `gpu_vlm`.

## Configure a local-decoding VLM pipeline

```bash
export VLM_BASE_URL=https://your-vlm-endpoint/v1
export VLM_MODEL=your-vlm-model
export VLM_API_KEY=your-vlm-api-key
```

```python
import asyncio

from vss.ingestion import RequestSource, ingest


async def main() -> None:
    chunks = await ingest(
        RequestSource.for_file("/path/to/video.mp4"),
        pipeline="cpu_vlm",
        chunk_duration=30,
    )
    for chunk in chunks:
        print(chunk.vlm_response)


asyncio.run(main())
```

Use `pipeline="gpu_vlm"` for NVIDIA GPU decoding.

## Configure RTVI

```bash
export RTVI_BASE_URL=http://127.0.0.1:8018
export RTVI_MODEL=your-rtvi-model
export RTVI_API_KEY=your-optional-api-key
```

Select `pipeline="rtvi"` for captions. RTVI-Embed uses
`RTVI_EMBED_BASE_URL`, `RTVI_EMBED_MODEL`, and `RTVI_EMBED_API_KEY`; select
`pipeline="rt_embed"` to create an `IngestionJob`, then await that job for its
embedding chunks:

```python
embedding_job = ingest(source, pipeline="rt_embed")
embeddings = await embedding_job
```

`ingest()` returns an `IngestionJob`. Await the job for its result, call
`cancel()` to request cancellation, and await `wait_closed()` before releasing
caller-owned resources.

Persistence is opt-in. See the [storage guide](../../../../../src/vss/storage/README.md) for file
and Elasticsearch checkpoints, resume requirements, and deferred summarization.
