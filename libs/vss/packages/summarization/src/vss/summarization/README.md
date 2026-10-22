# Summarization guide

`vss.summarization` aggregates caption chunks into ordered events and sends
those events to an OpenAI-compatible LLM for a final video summary.

## Install and configure

From the repository root:

```bash
uv pip install "vss-summarization[openai]"

# Include ingestion to run the end-to-end example below
uv pip install vss-ingestion "vss-summarization[openai]"
```

```bash
export VSS_SUMMARIZATION_BASE_URL=https://your-llm-endpoint/v1
export VSS_SUMMARIZATION_MODEL=your-summary-model
export VSS_SUMMARIZATION_API_KEY=your-summary-api-key
# Optional: responses (default) or chat_completions
export VSS_SUMMARIZATION_API=responses
```

## Summarize ingestion results

```python
import asyncio
import json

from vss.ingestion import RequestSource, ingest
from vss.summarization import summarize_video


async def main() -> None:
    chunks = await ingest(
        RequestSource.for_file("/path/to/video.mp4"),
        pipeline="cpu_vlm",
    )
    completion = await summarize_video(chunks)
    result = json.loads(completion.choices[0].message.content)
    print(json.dumps(result, indent=2))


asyncio.run(main())
```

The response content contains ordered `events`, `total_events`,
`video_summary`, and source `uuids`.

## Summarize stored captions

Use `summarize_from_store(job_id, storage=...)` to summarize a completed
persistent ingestion without decoding the video or calling the VLM again.
The summary LLM configuration is still required.

See the [storage guide](../../../../../src/vss/storage/README.md) for durable ingestion and adapter
configuration.
