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
introspection trace. Semantic/vector recall and graph memory are not included.

Trusted persistence callbacks are not supported. No active code or tests
require them, and arbitrary callback execution is not exposed to agents.
