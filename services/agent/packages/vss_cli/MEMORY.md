# VSS CLI memory policy

Elasticsearch is the authoritative structured VSS memory store. OpenClaw
Markdown notes are an optional compact cache for agent recall; OpenClaw
`memory-core` owns their indexing, retrieval, consolidation, and promotion.

## Configure static policy once

Use `vss configure memory`, not `vss memory configure`:

```console
vss configure memory \
  --enable \
  --backend elasticsearch \
  --index vss-memory \
  --persist-by-default
```

This stores infrastructure, sink selection, and default policy in
`~/.vss/config.json`. Existing deployment services remain intact.

`memory.enabled` controls whether memory read/write commands can access the
store. `memory.persist_by_default` independently controls whether search and
summarize runs persist automatically. Memory can remain enabled for recall
while automatic persistence is disabled.

Inspect or validate the effective policy without changing records:

```console
vss configure memory show
vss configure memory check
```

Backend and index selection are not normal per-request flags. Search,
summarize, status, get, list, and `vss memory` do not expose
`--memory-index`. Job-producing commands do not expose a positive `--persist`
flag. Use `--no-persist` as the safe per-request opt-out.

## Access structured memory

`vss memory` is the data-access surface:

```console
vss memory upsert
vss memory get --job-id <job-id>
vss memory query --job-id <job-id>
vss memory events --asset-id <sensor-or-video-id>
```

Use `get` for an exact parent or child identity, `query` for filtered or text
recall, `events` for temporal child-record recall, and `upsert` for explicit
record writes. Identity, status, sensor, time-window, text, and result-limit
flags remain dynamic.

Accepted job groups are `summary`, `search`, `alert`, and `vlm`. `media` is
not a job group because VIOS does not mint job IDs or memory completion
records.

There is no `lookup` or `retrieve` command. The schema has no slug or
`memory_id`. `events --window` is deferred until duration and boundary
semantics are defined; use `--start-time` and `--end-time`.

## Optional OpenClaw Markdown cache

Enable the capability and choose its default once:

```console
vss configure memory \
  --markdown \
  --harness openclaw \
  --workspace /absolute/path/to/openclaw/workspace \
  --write-notes-by-default
```

Notes are written only after the authoritative Elasticsearch parent succeeds,
under:

```text
memory/YYYY-MM-DD-vss.md
```

VSS never writes `MEMORY.md`, `DREAMS.md`, or session files. Each bounded
block has a job marker, so rewriting the same job replaces its block.

Use `--write-memory-note` or `--no-write-memory-note` on one search or
summarize run to override the configured note default. These flags never
enable Elasticsearch persistence. Explicit note writing with `--no-persist`,
with static persistence disabled, or without a configured Markdown sink is
rejected.

The completion marker's `persisted` field always describes authoritative
Elasticsearch persistence. Markdown status is reported separately as
`memory_note`.

## Scope

This surface preserves parent/child persistence and recall. It does not
implement introspection, gap or sufficiency analysis, VLM follow-up
orchestration, introspection traces, semantic/vector recall, or graph memory.

Trusted persistence callbacks are not supported. No active code or tests
require them, and arbitrary callback execution is not exposed to agents.
