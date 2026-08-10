---
name: vss-recall-memory
description: Recall previously persisted VSS video summaries and events from unified Elasticsearch memory. Use only when the record is not already present in active context; after a successful recall returns a complete summary with related events, do not recall that record again in the same conversation.
---

# Recall VSS memory

Invoke durable recall at most once per record per conversation after a successful result. Treat the returned complete summary and related events as working context for every follow-up about that record, including sequence and timestamp questions. Recall the same record again only when the user explicitly requests another lookup.

## Workflow

1. Prefer the stable `summary_id` or `event_id` receipt from the current or previous persistence call.
2. Read [references/recall-query-contract.md](references/recall-query-contract.md) to select one typed operation.
3. Invoke this exact executable directly and pass one JSON object through standard input:

   `/home/ubuntu/video-search-and-summarization/tools/vss-unified-memory/scripts/recall_memory.py`

   The executable has a narrow host allowlist entry, but the required stdin heredoc is shell redirection and therefore
   cannot use OpenClaw's deterministic allowlist fast path. Before the first call, request approved host-network
   execution through the tool surface (`sandbox_permissions=require_escalated` or its equivalent) with a justification
   limited to read-only localhost Elasticsearch and RT-Embed access for VSS recall. Do not first probe the launcher in
   the restricted sandbox and wait for it to fail. Scope any reusable approval prefix to the exact executable path.

   Make the command content begin with the literal absolute path. Do not manually add `/bin/bash -lc`, `bash -lc`,
   `sh -c`, or another outer shell; the Bash tool may display a platform-added shell wrapper. Use this shape:

   ```bash
   /home/ubuntu/video-search-and-summarization/tools/vss-unified-memory/scripts/recall_memory.py <<'JSON'
   {"operation":"search","query_text":"forklift near miss","semantic":true,"limit":5}
   JSON
   ```

   Do not test-run the launcher without input, prepend `printf`, pipe from another executable, invoke a general Python
   command, or pass JSON as a command-line argument. The launcher must reach localhost Elasticsearch and, for semantic
   search, RT-Embed. If the tool surface cannot request host-network execution, use its exec-approval flow before
   invoking the launcher instead of intentionally generating a failed sandbox call.
4. Parse the single JSON object from standard output.
5. Use returned memory as prior generated evidence. Do not claim it is a fresh visual inspection.

If a call nevertheless reports `PermissionError: Operation not permitted`, `embedding_failed`, or an unreachable
localhost service, treat it as a possible permission-routing failure first. Retry the same typed operation once with
explicit approved host-network execution. Preserve `semantic: true` across this retry; do not silently downgrade to
full-text search. Only switch from semantic to full-text after an explicitly host-routed semantic retry still reports
an unavailable embedding service, and disclose that fallback in the answer.

Use exact-ID lookup before search when a handle exists. Use full-text search for literal terms and semantic search for
conceptual similarity. Semantic search considers both summary and event passages, groups candidates by
`summary_id`, and returns complete summaries with their related events. Ask for a narrower scope when multiple returned
videos could answer the question ambiguously.

## Guardrails

- Never submit raw Elasticsearch DSL, endpoints, index names, SQL, Python modules, or executable paths.
- Do not expose stored data from another workspace or tenant unless runtime authorization permits it.
- Do not rerun VSS summarization merely because durable memory returned no match; explain that no stored match was found.
- Preserve timestamps and distinguish summary-level narrative from individual event evidence.
