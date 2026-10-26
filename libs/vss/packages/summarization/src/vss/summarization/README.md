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
    results = await ingest(
        RequestSource.for_file("/path/to/video.mp4"),
        backends=["cpu_vlm"],
    )
    completion = await summarize_video(results["cpu_vlm"])
    result = json.loads(completion.choices[0].message.content)
    print(json.dumps(result, indent=2))


asyncio.run(main())
```

The response content contains ordered `events`, `total_events`,
`video_summary`, and source `uuids`.

## Summarize in batches

```python
completion = await summarize_video(chunks, batch_size=20)
# Also supported by summarize_video_sync and summarize_from_store.
```

`batch_size` counts normalized events per LLM call, after filtering, sorting
and merging events across all input chunks. It must be an integer from 1 to
1,000,000; `None` (the default) preserves the original single-call behavior.
For example, five events with `batch_size=2` produce three sequential calls to
one generator. Each receives the same prompt and model selection. Their texts
are joined in order with blank lines, without an additional reduction call.
The response still includes every normalized event, its evidence and source IDs.
Empty or fully filtered input makes no LLM call.

This produces a sequence of batch summaries. It does not create a globally
rewritten narrative or deduplicate text across batches. Splitting events can
change summary quality and token cost. It limits neither input memory nor token
context size, and is not a concurrency setting. Provider output caps apply to
each call. A custom prompt should make sense for each independent event subset.

`usage.summary_requests` counts successful calls in a returned response and
`usage.aggregation_tokens` sums reported input and output tokens across batches.
The legacy `summary_tokens` field remains zero. Metrics retain per-call token
availability, so missing usage is distinguishable from measured zero, and count
attempted batches/requests even on failure. Empty input retains one aggregation
batch, zero requests and explicitly inapplicable LLM/token measurements.

If any batch reports `content_filter` or `length`, that reason reaches the final
response (`content_filter` takes precedence). This applies to reasons exposed by
the generator: the existing Responses adapter only maps output-token-limit
incompletion to `length`; other native reasons keep its existing normalization.
Model changes between batches, unsupported generator finish reasons, invalid
results and aggregate response-size/token limits fail the request without a
partial successful response. Earlier provider calls may already have consumed
tokens. Cancellation or a failed batch prevents subsequent calls. An internally
created generator is closed once; injected generators remain caller-owned.
Per-call provider timeouts remain in effect; there is no new request deadline.

This opt-in path has offline functional coverage. Its performance and semantic
quality require separate live qualification; historical single-call benchmark
results do not measure batching.

## Summarize stored captions

Use `summarize_from_store(job_id, storage=...)` to summarize a completed
persistent ingestion without decoding the video or calling the VLM again.
The summary LLM configuration is still required.

See the [storage guide](../../../../../src/vss/storage/README.md) for durable ingestion and adapter
configuration.
