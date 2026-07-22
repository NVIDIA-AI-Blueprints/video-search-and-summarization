# VSS Unified Memory

This standalone package validates VSS video-summary output, maps it into frozen domain objects, and persists or recalls
summary and event documents through a storage-independent repository interface. Complete summaries remain authoritative;
searchable summary passages are derived with the exact Cosmos-Embed1 WordPiece vocabulary and stored as nested
Elasticsearch objects. Elasticsearch is the only durable store in this implementation.

## Model boundaries

Storage-independent domain objects are frozen dataclasses. External CLI and Elasticsearch boundaries are frozen,
extra-forbidding Pydantic models. The Elasticsearch adapter uses complete `SummaryDocument`, `EventDocument`, and
`PassageDocument` models on writes, while recall validates the lean `_source` response as `SummaryReadDocument` or
`EventReadDocument` before mapping it back into the domain. Ordinary reads exclude the complete `summary_chunks` and
`event_chunks` arrays; indexed vectors remain available to nested kNN search without being returned during hydration.

The read-document union is discriminated by `record_type` and currently exhaustively supports video summaries and
video events. Alert and search workflow records remain future persistence types.

## Runtime entrypoints

OpenClaw is allowed to execute only these files:

```text
scripts/persist_summary.py
scripts/recall_memory.py
```

Both read one JSON object from standard input, write one JSON object to standard output, write diagnostics to standard
error, and return a nonzero status on failure. They do not accept command-line data, raw Elasticsearch DSL, endpoints,
index names, SQL, Python modules, or code.

Provision the sibling environment once with `python3 -m venv .venv` followed by `.venv/bin/pip install -e ".[dev]"`.
When invoked by exact path, each launcher safely re-executes through that fixed sibling interpreter; OpenClaw does not
need a general Python allowlist or an activated shell environment.

## Configuration

Configuration comes only from trusted environment variables:

| Variable | Required | Default |
|---|---:|---|
| `VSS_MEMORY_ELASTICSEARCH_ENDPOINT` | no | `http://localhost:9200` |
| `VSS_MEMORY_ELASTICSEARCH_INDEX` | no | `vss-unified-memory` |
| `VSS_MEMORY_EMBEDDING_ENDPOINT` | yes | — |
| `VSS_MEMORY_EMBEDDING_MODEL` | no | `cosmos-embed1-448p` |
| `VSS_MEMORY_EMBEDDING_DIMENSIONS` | no | `768` |
| `VSS_MEMORY_TOKENIZER_VOCAB_PATH` | yes | — |
| `VSS_MEMORY_PASSAGE_MAX_TOKENS` | no | `128` |
| `VSS_MEMORY_PASSAGE_OVERLAP_TOKENS` | no | `16` |
| `VSS_MEMORY_EMBEDDING_MAX_CHARACTERS` | no | `1000` |
| `VSS_MEMORY_REQUEST_TIMEOUT_SECONDS` | no | `30` |
| `VSS_MEMORY_OBSERVABILITY_LOG` | no | — |
| `VSS_MEMORY_OBSERVABILITY_INCLUDE_PREVIEWS` | no | `false` |

When `VSS_MEMORY_OBSERVABILITY_LOG` is set, persist, recall, and inspect append one sanitized JSON line per invocation.
The log includes timestamps, tool names, status, identifiers, and the observability object. Query text is logged as a
SHA-256 hash by default; set `VSS_MEMORY_OBSERVABILITY_INCLUDE_PREVIEWS=true` to add bounded previews for operator
debugging only.

## Observability

Successful persist and recall stdout objects may include an optional `observability` block with counts, timing, and
token estimates. These fields are safe for automation and do not include raw credentials, embedding vectors, or full
video descriptions unless preview mode is explicitly enabled in trusted configuration.

Persist observability reports what was chunked and embedded before Elasticsearch bulk indexing:

- `summary_id`, `event_count`, `summary_chars`, `event_chars_total`
- `summary_chunk_count`, `event_chunk_count`, `total_chunk_count`
- `estimated_input_tokens` from WordPiece passage token counts
- `embedding_model`, `embedding_vector_count`
- `es_attempted_records`, `es_successful_records`
- `latency_ms.chunking`, `latency_ms.embedding`, `latency_ms.es_bulk_index`, `latency_ms.total`

Recall observability reports what Elasticsearch and RT-Embed did and how much memory returned into hot context:

- `operation` (`get` or `search`), `semantic`, optional query metadata
- `query_text_chars`, `query_text_hash`, optional `query_text_preview`
- `candidate_summary_ids` for semantic search
- `returned_summary_count`, `returned_event_count`, `returned_chars`
- `estimated_returned_tokens` using `ceil(chars / 4)` over returned descriptions
- `latency_ms.query_embedding`, `latency_ms.es_*`, `latency_ms.total`

Recall stdout still contains the full returned memory in `results`. OpenClaw injects that tool output into the active
conversation, so follow-up questions can usually be answered from hot context without another Elasticsearch call.

### Inspect persisted memory

Operator tooling:

```bash
/home/ubuntu/video-search-and-summarization/tools/vss-unified-memory/scripts/inspect_memory.py \
  > /tmp/vss-memory-snapshot.json
```

The script is read-only. It connects with trusted `VSS_MEMORY_*` configuration, paginates summary and event documents,
and prints one JSON snapshot with index totals and per-summary metadata such as character counts, time range, and chunk
presence estimates. It never deletes documents or mutates index templates.

### OpenClaw LLM request logging

OpenClaw/NemoClaw does not expose full chat-completions request bodies in-repo. For demo inspection, run the optional
OpenAI-compatible proxy and point NemoClaw onboarding at it:

```bash
python3 /home/ubuntu/video-search-and-summarization/tools/vss-unified-memory/scripts/openai_logging_proxy.py \
  --upstream https://integrate.api.nvidia.com/v1 \
  --listen-port 8787 \
  --log-path /tmp/openclaw-llm-requests.jsonl
```

The proxy redacts `Authorization` headers, forwards requests unchanged, and logs model name, message counts, estimated
prompt tokens, optional bounded previews, tool calls, and provider usage when returned.

The embedding adapter calls the VSS RT-Embed `POST /v1/generate_text_embeddings` API. The default dimension matches
`cosmos-embed1-448p` in VSS 3.2.1. `VSS_MEMORY_TOKENIZER_VOCAB_PATH` must reference the `vocab.txt` shipped with that
exact model. Summary and event passages contain at most 128 model tokens including `[CLS]` and `[SEP]`, prefer paragraph
or sentence boundaries, overlap by 16 content tokens, and independently satisfy RT-Embed's 1,000-character input limit.
The adapter embeds every passage separately and never averages passages into a broad record vector.

## Elasticsearch initialization

Apply `src/vss_unified_memory/adapters/persistence/elasticsearch/index-template-v1.json` once with deployment-level
Elasticsearch credentials before allowing runtime writes. Runtime scripts intentionally do not create or mutate index
schemas. Schema version 3 stores passages under nested `summary_chunks` and `event_chunks` fields; upgrading an existing
strict index requires creating or recreating it with the version-3 mapping.

## Development

Install the package in an isolated environment, then run:

```text
pytest
ruff check .
mypy
```

The integration test is skipped unless `VSS_MEMORY_TEST_ELASTICSEARCH_ENDPOINT` points to a disposable Elasticsearch
instance.
