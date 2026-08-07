# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Harness-native Markdown memory for VSS.

## Ownership

- **Elasticsearch** is the authoritative structured VSS memory store
  (`nv.vss.memory/1.0`).
- **VSS** deterministically writes the initial human-readable Markdown
  addendum into the configured harness workspace under `memory/*.md`.
- **OpenClaw `memory-core`** (or a later configured plugin) owns indexing,
  organization, consolidation / dreaming, retention, and promotion into
  `MEMORY.md` after that initial write.

VSS never writes directly to `MEMORY.md`, `DREAMS.md`, `USER.md`, or session
transcripts. This first implementation does **not** implement VSS-controlled
Markdown TTLs, GC, demotion, pruning, compression, pin/unpin, or rehydration.

> The first implementation relies on the configured harness memory plugin for
> Markdown lifecycle management. VSS-specific retention or custom dreaming
> policies may be added later only if evaluation shows the native plugin is
> insufficient.

## Configure

```bash
vss configure memory \
  --harness openclaw \
  --plugin memory-core \
  --workspace ~/.openclaw/workspace \
  --enable-memory-notes \
  --write-memory-notes-default

vss configure memory show
```

Default note path: `<workspace>/memory/YYYY-MM-DD-vss.md` (`memory/{date}-vss.md`).
`{date}` resolves in the configured timezone (default **UTC**).

## Job option

Every job-producing CLI group inherits:

```bash
--write-memory-note / --no-write-memory-note
```

- `--write-memory-note` forces a Markdown addendum for this invocation
- `--no-write-memory-note` forces it off
- omitting both uses `memory.harness_sink.write_memory_notes_default`

Normal stdout remains present. The Markdown write is an additional side
effect and does **not** replace `--persist`. The combination
`--no-persist --write-memory-note` is rejected before the backend runs.

## Markdown content

Each block is marked by `job_id` and includes the human-facing answer, request
context, and `vss memory get <job_id>` when ES persistence succeeded. It
excludes embeddings, large `output.ext` collections, temporary/signed media
URLs, credentials, and the complete unified-memory JSON.

Another harness or memory plugin can later replace `memory-core` through
`vss configure memory` without changing individual VSS job commands.
