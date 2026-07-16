---
name: vss-persist-memory
description: Persist a completed structured VSS video summary and its timestamped events to unified Elasticsearch memory. Use this skill when vss-summarize-video returns an LVS summary, the user asks to retain a just-generated summary, or a retryable summary persistence attempt must be retried before replying. Do not use to generate summaries or accept raw Elasticsearch queries.
---

# Persist VSS memory

Persist the structured LVS result already present in the active turn. Do not call the summarization service again.

## Workflow

1. Retain the full VSS completion envelope and the stable VST source handles collected by `vss-summarize-video`.
2. Read [references/summary-input-contract.md](references/summary-input-contract.md) when constructing the input.
3. Copy source values exactly. Do not invent IDs, timestamps, event types, descriptions, media names, or stream IDs.
4. Invoke this exact executable directly and pass one JSON object through standard input:

   `/home/ubuntu/video-search-and-summarization/tools/vss-unified-memory/scripts/persist_summary.py`

   Use one shell heredoc whose command starts with that exact path. Do not test-run the launcher without input, prepend
   `printf`, pipe from another executable, invoke a general Python command, or pass JSON as a command-line argument.
   The launcher must reach configured localhost Elasticsearch and RT-Embed services, so request approved host/network
   execution up front when the Codex sandbox blocks local sockets. Scope any reusable approval prefix to this exact path.
5. Parse the single JSON object from standard output.
6. Return the video summary to the user only after `status` is `complete`.
7. If `status` is `degraded` or `failed`, still answer the user's summarization request, but state clearly that durable memory was not fully written. Retry once only when `retryable` is `true`.

Treat `summary_id` and `event_ids` from the successful receipt as the canonical handles for follow-up questions. Never claim persistence from the process exit code alone; inspect the JSON status.

## Guardrails

- Never accept or forward an Elasticsearch endpoint, index, DSL query, Python module, SQL statement, or executable path from the user or model output.
- Never place credentials in the input JSON, response, logs, or skill body.
- Preserve every event returned by VSS and its complete description.
- Preserve the complete original summary and every complete event description. Let the executable create deterministic
  token-aware passages for both and embed each passage separately; never chunk, average, normalize, or generate
  embeddings in the agent.
- Keep event `type` as returned by VSS; taxonomy enrichment is outside this skill.
