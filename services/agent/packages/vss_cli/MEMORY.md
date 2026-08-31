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

Configure introspection separately. This is explicit static configuration: VSS
does not discover a judge endpoint, model, or credential automatically.

For an OpenClaw Gateway:

```console
openclaw config set gateway.http.endpoints.chatCompletions.enabled true
# Restart the gateway after changing its HTTP endpoint configuration.
export OPENCLAW_GATEWAY_TOKEN='<gateway-token>'

vss configure memory introspection \
  --judge-endpoint http://127.0.0.1:18789/v1 \
  --judge-model openclaw/default \
  --judge-api-key-env OPENCLAW_GATEWAY_TOKEN
```

`--judge-api-key-env` stores only the environment-variable name. The variable
must contain the Bearer token whenever `vss memory introspect` runs. The token
is never written to `~/.vss/config.json` or displayed by `memory show`.

`openclaw/default` delegates model selection to OpenClaw. To request one
specific OpenClaw backend, add `--judge-backend-model <provider/model>`; VSS
sends that value as `x-openclaw-model`. For any other OpenAI-compatible Chat
Completions service, set its base `/v1` URL and API-facing model explicitly:

```console
vss configure memory introspection \
  --judge-endpoint https://llm.example.com/v1 \
  --judge-model llama-3.3-70b-instruct \
  --judge-api-key-env CUSTOM_LLM_API_KEY
```

Set sufficiency criteria inline or from a UTF-8 file:

```console
vss configure memory introspection --judge-criteria "Require direct evidence for every material claim."
vss configure memory introspection --judge-criteria-file ./introspection-criteria.txt
```

Updates preserve unspecified values. Use `--clear-judge-api-key-env` or
`--clear-judge-backend-model` to remove those optional settings.
`vss configure memory show` and `check` never run introspection or call the
judge (`check` still validates the configured memory backend).

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
vss memory introspect --query "What happened?" --sensor <sensor-name>
vss memory embeddings backfill --dry-run
```

Use `get` for an exact parent or child identity, `query` for filtered or text
recall, `events` for temporal child-record recall, and `upsert` for explicit
record writes. Identity, status, sensor, time-window, text, and result-limit
flags remain dynamic.

Memory records use the human-readable VIOS sensor name in
`input.sensors[].id`; internal sensor UUIDs, stream IDs, and video IDs remain
under `input.sensors[].info`. Sensor filters also match older records that
stored the readable name as `input.sensors[].info.name`. Text queries rank by
relevance before recency, while queries without text remain newest-first.

`memory introspect` performs bounded, memory-first question answering and emits
exactly one JSON object to stdout (compact by default, indented with `--pretty`).
The query must be scoped by `--sensor`, `--job-id`, or a complete
`--start-time`/`--end-time` range. A child lookup requires its full public
identity: `--job-id`, `--record-type`, and `--record-id`. Time bounds accept
ISO-8601 UTC instants only.
`--record-type event|search_hit|incident` and `--group summary|search|alert`
refine a useful scope but do not establish one by themselves.

One workflow retrieves at most 10 records, requests at most 3 VLM follow-ups,
limits each clip to 60 seconds, and has a 180-second overall timeout. The
introspection request/result is never stored and never creates a Markdown note.
The configured OpenAI-compatible text LLM performs both memory-sufficiency
judgment and final answer synthesis. RT-VLM is not used as a judge or
synthesizer. It is used only for grounded visual follow-ups when the text judge
identifies a missing sensor/time window. Each follow-up uses the normal
`vss vlm run` execution path, follows the configured static persistence policy,
and remains independently visible through VLM job reads when persistence is
enabled.

Accepted job groups are `summary`, `search`, `alert`, and `vlm`. `media` is
not a job group because VIOS does not mint job IDs or memory completion
records.

There is no `lookup` or `retrieve` command. The schema has no slug or
`memory_id`. `events --window` is deferred until duration and boundary
semantics are defined; use `--start-time` and `--end-time`.

## Derived embeddings and hybrid recall

The canonical `nv.vss.memory/1.0` documents in Elasticsearch stay
authoritative. Vectors live in a separate versioned companion index. An
authoritative record gains only `output.embedding` references — companion
index, document id, model, dimensions, and content hash — never vector values.
Markdown notes are untouched by this: they carry no vectors and no embedding
references.

Enable the capability once:

```console
vss configure memory \
  --enable \
  --backend elasticsearch \
  --index vss-memory \
  --embeddings \
  --embedding-provider openai_compatible \
  --embedding-endpoint http://embedding-service:8000/v1 \
  --embedding-model nvidia/llama-nemotron-embed-300m-v2 \
  --embedding-dimensions 768 \
  --embedding-index vss-memory-embeddings-v1 \
  --embedding-timeout 30 \
  --embedding-batch-size 16 \
  --retrieval-mode hybrid \
  --semantic-candidate-count 50 \
  --rrf-rank-constant 60
```

Only the endpoint has to be given: the model, dimensions, companion index, and
retrieval mode shown above are the defaults. The endpoint is required whenever
embeddings are enabled and must carry no credentials — name an environment
variable holding a Bearer token with `--embedding-api-key-env`. The companion
index must differ from the authoritative one. `--embedding-timeout` (30
seconds), `--embedding-batch-size` (16), `--semantic-candidate-count` (50), and
`--rrf-rank-constant` (60) complete the static policy.

The CLI never loads an embedding model. It posts to an OpenAI-compatible
`/embeddings` endpoint, so CPU or GPU execution is a property of that
endpoint's deployment and not a CLI flag or a host requirement.

Validate the wiring without writing records:

```console
vss configure memory check
```

With embeddings enabled this also embeds one probe string, reports the model
and the dimensions actually returned, and inspects the companion mapping. A
missing companion index is reported as missing — it is created lazily on the
first embedding write or backfill. An incompatible one is a configuration
error.

Retrieval mode applies only to text queries:

```console
vss memory query --query "forklift near the loading dock" --mode hybrid
```

`hybrid` — the default whenever embeddings are enabled — runs the keyword BM25
match over `input.query` and `output.answer` and a filtered kNN search over the
companion index, then fuses the two rankings client-side with reciprocal rank
fusion. `semantic` uses the vector ranking alone. `keyword` embeds nothing.

Identity lookups stay deterministic. `get`, `events`, and any `query` without
`--query` read Elasticsearch directly and embed nothing, as does a
`memory introspect` scoped by `--job-id` or `--record-id`: identity selects the
evidence, so the question is never embedded and never filters it. An
introspection scoped by `--sensor` or a time window sends its question through
the configured retrieval mode.

Recall degrades rather than fails. `--mode semantic` or `--mode hybrid` with
embeddings disabled warns on stderr and answers from keyword retrieval. A
provider or companion-index failure during a query also falls back to keyword
retrieval, logging the failure kind without backend detail.

Records written before embeddings were enabled are indexed by a backfill:

```console
vss memory embeddings backfill --dry-run
vss memory embeddings backfill --batch-size 16 --limit 500
```

It scans authoritative memory in bounded batches and emits one JSON object
counting `scanned`, `eligible`, `embedded`, `reused`, `skipped`, and `failed`,
with per-record `failures`. `--dry-run` reports eligibility without provider
calls or writes. Any failure exits 6 with those counts still on stdout. Only
`completed` and `partial` records with searchable content are eligible, and
re-running is cheap: an unchanged record keeps its vector when the model and
dimensions still match.

Changing the model or the dimensions is not an in-place edit. Point
`--embedding-index` at a new versioned index and backfill it; a write to an
index whose mapping disagrees with the configured model or dimensions is
rejected.

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

This surface preserves parent/child persistence and recall. Introspection adds
bounded sufficiency analysis and VLM follow-ups without storing an
introspection trace. Semantic and hybrid recall are derived from the companion
vector index and never change what the authoritative store holds. Graph memory
is not included.

Trusted persistence callbacks are not supported. No active code or tests
require them, and arbitrary callback execution is not exposed to agents.
