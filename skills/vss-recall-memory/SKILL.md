---
name: vss-recall-memory
description: Recall previously persisted VSS video summaries and events from unified Elasticsearch memory. Use this skill when a follow-up answer is not already available in active context, including lookup by summary ID, event ID, video ID, time range, event type, full text, or semantic similarity.
---

# Recall VSS memory

Check active conversation context first. Use durable recall only when the needed summary or event is absent, incomplete, or explicitly requested from prior memory.

## Workflow

1. Prefer the stable `summary_id` or `event_id` receipt from the current or previous persistence call.
2. Read [references/recall-query-contract.md](references/recall-query-contract.md) to select one typed operation.
3. Invoke this exact executable directly and pass one JSON object through standard input:

   `/home/ubuntu/video-search-and-summarization/tools/vss-unified-memory/scripts/recall_memory.py`

   Use one shell heredoc whose command starts with that exact path. Do not test-run the launcher without input, prepend
   `printf`, pipe from another executable, invoke a general Python command, or pass JSON as a command-line argument.
   The launcher must reach configured localhost Elasticsearch and, for semantic search, RT-Embed, so request approved
   host/network execution up front when the Codex sandbox blocks local sockets. Scope any reusable approval prefix to
   this exact path.
4. Parse the single JSON object from standard output.
5. Use returned memory as prior generated evidence. Do not claim it is a fresh visual inspection.

Use exact-ID lookup before search when a handle exists. Use full-text search for literal terms and semantic search for
conceptual similarity. Semantic search considers both summary and event passages, groups candidates by
`summary_id`, and returns complete summaries with their related events. Ask for a narrower scope when multiple returned
videos could answer the question ambiguously.

## Guardrails

- Never submit raw Elasticsearch DSL, endpoints, index names, SQL, Python modules, or executable paths.
- Do not expose stored data from another workspace or tenant unless runtime authorization permits it.
- Do not rerun VSS summarization merely because durable memory returned no match; explain that no stored match was found.
- Preserve timestamps and distinguish summary-level narrative from individual event evidence.
