# `vss` — the VSS command-line interface

The host-side entry point to a deployed VSS stack. Runs beside a deployment, not
inside it: no NAT, no torch, no GPU, no agent framework. One process per call,
JSON on stdout, typed exit codes.

Driving this from an agent or a skill? The bootstrap and the cross-cutting
contract are in [AGENTS.md at the repository root](../../../../AGENTS.md);
per-command detail is in [AGENTS.md](AGENTS.md) beside this file. Memory policy
invariants (companion-index identity, credential handling, backfill semantics)
are in [MEMORY.md](MEMORY.md).

## Run it

```bash
cd services/agent
uv run --no-dev --extra cli vss --help
```

`uv run` syncs `services/agent/.venv` on first use. `--extra cli` is required —
the base meta-package does not pull the `nvidia-vss-cli` distribution that
provides the executable — and `--no-dev` keeps it to the CLI's runtime
(256 MB, no `nvidia-nat`) rather than the agent stack (630 MB).

## Develop

```bash
cd services/agent
uv sync --frozen --extra cli
uv run --no-sync pytest packages/vss_cli/tests packages/vss_core/tests -q
```

Development needs the test tooling, so this one keeps the default group.

Keep `--no-sync` after that first sync, and note it works the other way too:
the `--no-dev` run above re-resolves to the runtime spec and drops pytest. The
two specs share one `.venv`, so pick the one matching what you are doing and
stay on it.

CI additionally runs a NAT-free lane (`--no-dev --group cli-dev`) to prove the
CLI imports nothing from the agent stack:

```bash
uv sync --frozen --no-dev --group cli-dev --extra cli
uv run pytest packages/vss_cli/tests packages/vss_core/tests -q
uv run ruff check packages/vss_cli packages/vss_core
uv run mypy packages/vss_cli/src/vss_cli packages/vss_core/src/vss_core/vios
```

## Point it at a deployment

Once per deployment. Everything after this takes no host, port, or endpoint.

```bash
vss configure --base-url https://vss.example.nvidia.com
vss configure show     # what was recorded
vss configure check    # re-probe; exit 3 if a route disappeared
```

`vss configure` is the only command that works without an existing config. It is
not a job group: discovery is a probe, not a guess, so a route the deployment
does not expose is *absent* from the file rather than present-but-broken.

| Flag | Default | Meaning |
|------|---------|---------|
| `--base-url` | required | Deployment origin, e.g. `http://10.0.0.1:7777`. A missing scheme is assumed `http://` with a note on stderr |
| `--timeout` | 5.0 | Per-route probe timeout in seconds (0.1–120) |

| Subcommand | What it does |
|------------|--------------|
| `vss configure --base-url URL` | Probe every known route and rewrite the config |
| `vss configure show` | Print the recorded deployment as JSON |
| `vss configure check` | Re-probe each recorded route, then list which command groups are available |
| `vss configure memory …` | Static memory policy (see below) |

### What gets probed and recorded

Each service is requested at its probe path and recorded only if the origin
answers. `200/201/204/400/401/403/405/422` prove a mapping (an auth challenge
still means the route exists); `404` means the ingress has no such mapping.
Where a service can describe itself, the config stores the backend's own answer
rather than a typed-in value.

| Service key | Mount | Records |
|-------------|-------|---------|
| `agent` | `/api` | URL only |
| `vst` | `/vst` | URL only |
| `elasticsearch` | `/elasticsearch` | URL + index names |
| `rt_embed` | `/rtvi-embed` | URL + model ids |
| `rtvi_cv` | `/rtvi-cv` | URL only (no introspection endpoint) |
| `rt_vlm` | `/rtvi-vlm` | URL + model ids — the default model for `vss vlm` and introspection follow-ups |
| `lvs` | `/lvs` | URL + model ids (long-video summarization) |

If the origin exposes none of them, `configure` fails rather than writing an
empty config. Elasticsearch indices are created by ingestion, not deployment,
so configuring a fresh stack records zero `mdx-*` indices and says so — re-run
`configure` after ingesting video and before searching.

### The config file

Written to `~/.vss/config.json` at mode 0600, holding **no credentials**: only
URLs, discovered model/index names, `written_at`, and static memory policy.
Set `VSS_CONFIG_HOME` to point at a different directory for a second deployment
or for tests. The file carries a `version`; one written by a newer CLI is
refused rather than half-read, with a message telling you to re-run `configure`.

Re-running `vss configure --base-url …` refreshes routes and **preserves valid
static memory policy**, so re-probing after a deployment change does not reset
your judge, embedding, or persistence settings.

`vss configure check` prints per-service reachability and a `commands:` table
marking each group available or unavailable (a group is available only when
*every* service it needs is routed). It exits 3 if any recorded route no longer
answers.

That same file is where memory policy lives. `vss configure` records service
URLs (including RT-VLM). `vss configure memory` records how the CLI uses
Elasticsearch, embeddings, the text judge, and optional Markdown notes.

## The surface

| Group | What it is | Verbs |
|-------|-----------|-------|
| `vss search` | Fused archive search over ES + the embedding NIM | `run`, `status`, `get`, `list` |
| `vss summarize` | VLM summarization of stored video | `run`, `status`, `get`, `list` |
| `vss vlm` | One VLM answer from a recorded sensor window | `run`, `status`, `get`, `list` |
| `vss memory` | Unified-memory access, embeddings backfill, introspection | `upsert`, `get`, `query`, `events`, `introspect`, `embeddings backfill` |
| `vss vios` | Media plane: sensors, timelines, clip and snapshot URLs | `list`, `timeline`, `clip`, `snapshot`, `add`, `delete` |
| `vss configure` | Resolve a deployment and set static memory policy | `show`, `check`, `memory`, `memory show`, `memory check`, `memory introspection` |

`search`, `summarize`, and `vlm` are **job groups**: every run mints a `job_id`, and the
result stays retrievable by that id. `vios` is **not** — it resolves handles and
mints URLs, so it has no job verbs. See [AGENTS.md](AGENTS.md#the-two-shapes).

There is no per-request `--persist` flag and no `--memory-index` on job or
memory commands. Persistence defaults come from static config; opt out of one
run with `--no-persist`.

## Configure memory

Static policy is `vss configure memory`, not `vss memory configure`. Unspecified
flags keep their current values. Inspect without writing records:

```bash
vss configure memory show
vss configure memory check
```

`show` prints only the memory object. `check` validates policy and probes
Elasticsearch (and the embedding endpoint when embeddings are enabled). Neither
runs introspection or calls the judge.

### Store and persistence

```bash
vss configure memory \
  --enable \
  --backend elasticsearch \
  --index vss-memory \
  --persist-by-default
```

| Flag | Default | Meaning |
|------|---------|---------|
| `--enable` / `--disable` | enabled | Whether `vss memory` and job persistence can use the store |
| `--backend` | `elasticsearch` | Only Elasticsearch is supported |
| `--index` | `vss-memory` | Authoritative document index |
| `--persist-by-default` / `--no-persist-by-default` | persist | Whether `search` / `summarize` / `vlm` writes on success |

Memory can stay enabled for recall while `--no-persist-by-default` turns off
automatic writes. One job still opts out with `--no-persist`.

### Text judge (introspection LLM)

The judge is a Chat Completions LLM used for **sufficiency** and **answer
synthesis**. It is not discovered from the deployment; configure it explicitly.
RT-VLM is never the judge.

OpenClaw Gateway:

```bash
export OPENCLAW_GATEWAY_TOKEN='<gateway-token>'
vss configure memory introspection \
  --judge-endpoint http://127.0.0.1:18789/v1 \
  --judge-model openclaw/default \
  --judge-api-key-env OPENCLAW_GATEWAY_TOKEN
```

Any other OpenAI-compatible `/v1`:

```bash
vss configure memory introspection \
  --judge-endpoint https://llm.example.com/v1 \
  --judge-model llama-3.3-70b-instruct \
  --judge-api-key-env CUSTOM_LLM_API_KEY
```

| Flag | Default (first setup) | Meaning |
|------|----------------------|---------|
| `--judge-endpoint` | required first time | OpenAI-compatible base URL (`…/v1`) |
| `--judge-model` | `openclaw/default` | API-facing model or OpenClaw agent target |
| `--judge-backend-model` | unset | OpenClaw-only override sent as `x-openclaw-model` |
| `--clear-judge-backend-model` | — | Remove that override |
| `--judge-api-key-env` | unset | **Name** of the env var holding the Bearer token |
| `--clear-judge-api-key-env` | — | Stop sending a token |
| `--judge-criteria` | built-in prompt | Inline sufficiency criteria |
| `--judge-criteria-file` | — | UTF-8 file that replaces the criteria prompt |

`--judge-api-key-env` stores the variable name only. The token must be present
in the environment whenever `vss memory introspect` runs. It is never written
to `~/.vss/config.json` or printed by `show`.

### Sufficiency prompt

The stored `criteria_prompt` is the only configurable introspection prompt. It
tells the judge when memory is enough and when to emit grounded VLM gaps. First
setup installs a default that requires direct, in-scope, cited evidence.

```bash
vss configure memory introspection --judge-criteria "Require direct evidence for every material claim."
vss configure memory introspection --judge-criteria-file ./introspection-criteria.txt
```

Do not combine `--judge-criteria` with `--judge-criteria-file`. Later
introspection updates keep the current prompt unless you pass one of those
flags.

Workflow bounds are not CLI flags today: at most 10 memory records, 3 VLM
follow-ups, 60-second clips, 180-second overall timeout. The introspection
request itself is never stored.

### VLM / RT-VLM endpoint

Visual follow-ups use the deployment's **`rt_vlm`** service (`/rtvi-vlm` from
`vss configure --base-url`), not `vss configure memory`. The model defaults to
whatever that probe recorded. Direct questions:

```bash
vss vlm run --sensor warehouse --prompt "What happened?" --start-time T --end-time T
```

| Flag | Default | Meaning |
|------|---------|---------|
| `--sensor` / `--media-url` / `--file` | one required | Exactly one media source |
| `--start-time` / `--end-time` | clip bounds | ISO-8601 UTC; with `--sensor` only |
| `--prompt` | required | Question sent to the VLM |
| `--model` | deployment `rt_vlm` model | Override the recorded model name |
| `--timeout` | 30s (`vlm run`); 180s (introspection follow-ups) | HTTP / workflow budget |
| `--num-frames` | 8 | Frame-sampling budget |
| `--max-tokens` / `--temperature` | unset | Optional generation knobs |
| `--intent` | `qa` (`vlm run`); `introspection` (follow-ups) | Stored on the memory record |
| `--no-persist` | off | Skip writing this VLM job |

Introspection follow-ups reuse this path and honor `--persist-by-default`.
Persisted jobs remain visible via `vss vlm get` / `list`.

### Embeddings and retrieval mode

Vectors are **derived**. Authoritative `nv.vss.memory/1.0` documents stay in
`--index`. Embeddings go to a separate companion index. Records only gain
`output.embedding` references — never raw vectors.

Enable the OpenClaw Gateway profile (CLI loads no model weights):

```bash
export OPENCLAW_GATEWAY_TOKEN="<gateway token>"
vss configure memory --embeddings
```

That resolves to provider `openclaw_gateway`, endpoint
`http://127.0.0.1:18789/v1`, model `openclaw/default`, and
`api_key_env=OPENCLAW_GATEWAY_TOKEN`. Omit `--embedding-dimensions` to probe
length once at configure time, or set it to configure offline.

Custom OpenAI-compatible `/v1/embeddings`:

```bash
vss configure memory \
  --embeddings \
  --embedding-provider openai_compatible \
  --embedding-endpoint https://embedding.example.com/v1 \
  --embedding-model example-embedding-model \
  --embedding-api-key-env EXAMPLE_EMBEDDING_TOKEN
```

Local unauthenticated endpoint: add `--no-embedding-auth`. Endpoints must be
absolute `http`/`https` URLs with **no** userinfo, query string, or fragment.

| Flag | Default | Meaning |
|------|---------|---------|
| `--embeddings` / `--no-embeddings` | off | Enable the companion vector index |
| `--embedding-provider` | `openclaw_gateway` when enabling | `openclaw_gateway` or `openai_compatible` |
| `--embedding-endpoint` | Gateway `http://127.0.0.1:18789/v1` | Embeddings base URL |
| `--embedding-model` | `openclaw/default` | Agent target or API model |
| `--embedding-dimensions` | probed if omitted | Expected vector length |
| `--embedding-index` | `vss-memory-embeddings-v1` | Companion ES index (must differ from `--index`) |
| `--embedding-timeout-seconds` | 30 | Provider HTTP timeout (≤ 300) |
| `--embedding-batch-size` | 16 | Passages per request (1–128) |
| `--embedding-api-key-env` | `OPENCLAW_GATEWAY_TOKEN` on Gateway profile | Env var **name** for the Bearer token |
| `--no-embedding-auth` | off | Do not send a token |
| `--embedding-query-input-type` | unset | Extra `input_type` on query embeds |
| `--embedding-document-input-type` | unset | Extra `input_type` on passage embeds |
| `--retrieval-mode` | `hybrid` | Static default for **text** queries |
| `--semantic-candidate-count` | 50 | kNN candidate pool |
| `--rrf-rank-constant` | 60 | Reciprocal-rank fusion constant |

**Retrieval modes** (text queries only: `vss memory query --query …` and
introspection scoped by `--sensor` or a time window):

| Mode | Behavior |
|------|----------|
| `hybrid` | Keyword BM25 over `input.query` / `output.answer` **and** kNN, fused client-side with RRF. Default when embeddings are on |
| `semantic` | Companion-index ranking only |
| `keyword` | BM25 only; no provider call |

With embeddings **disabled**, the effective mode is always keyword even if
`--retrieval-mode` is `hybrid`. Override one query with
`vss memory query --query "…" --mode keyword|semantic|hybrid`. Identity reads
(`get`, `events`, `query` without `--query`, introspection by `--job-id` /
`--record-id`) never embed.

If the semantic leg fails at query time, recall falls back to keyword and warns
on stderr. Changing provider, model, dimensions, or canonical text version
requires a **new** `--embedding-index` plus backfill; VSS will not rewrite an
incompatible mapping in place.

```bash
vss memory embeddings backfill --dry-run
vss memory embeddings backfill --batch-size 16 --limit 500
```

| Flag | Default | Meaning |
|------|---------|---------|
| `--dry-run` | off | Eligibility scan only; no provider calls or writes |
| `--batch-size` | configured embedding batch size | Records per batch |
| `--limit` | all | Max records to scan |
| `--pretty` | off | Indent JSON |

### Optional Markdown notes

```bash
vss configure memory \
  --markdown \
  --harness openclaw \
  --workspace /absolute/path/to/openclaw/workspace \
  --write-notes-by-default
```

| Flag | Default | Meaning |
|------|---------|---------|
| `--markdown` / `--no-markdown` | off | Compact daily notes under the workspace |
| `--harness` | `openclaw` | Only OpenClaw is supported |
| `--workspace` | unset | Absolute OpenClaw workspace path |
| `--write-notes-by-default` / `--no-write-notes-by-default` | off | Write a note after a successful ES persist |

Per-run overrides: `--write-memory-note` / `--no-write-memory-note` on search or
summarize. Notes never replace Elasticsearch and cannot be enabled with
`--no-persist`.

## Use memory

```bash
vss memory upsert --json '{"schema":"nv.vss.memory/1.0", ...}'
vss memory get --job-id <job-id>
vss memory query --query "forklift near the dock" --mode hybrid
vss memory events --asset-id <sensor-name>
vss memory introspect --query "What happened?" --sensor <sensor-name>
```

Add `--pretty` to indent JSON.

### `query`

| Flag | Default | Meaning |
|------|---------|---------|
| `--query` | unset | Free-text match; omit for filter-only (no embed) |
| `--mode` | configured retrieval | `keyword`, `semantic`, or `hybrid` |
| `--job-id` / `--group` / `--status` | unset | Identity and job filters (`summary`, `search`, `alert`, `vlm`) |
| `--sensor-id` | unset | VIOS sensor name |
| `--record-type` / `--record-id` | unset | Child identity (`event`, `search_hit`, `incident`) |
| `--parents-only` | off | Drop children |
| `--since` / `--until` | unset | ISO-8601 UTC bounds |
| `--time-field` | `created_at` | Or `window` |
| `--limit` | 20 | Max records |

### `introspect`

Requires a configured judge. Scope with `--sensor`, `--job-id`, or both
`--start-time` and `--end-time`. A child lookup needs `--job-id`,
`--record-type`, and `--record-id` together.

| Flag | Meaning |
|------|---------|
| `--query` | Required question |
| `--sensor` | VIOS sensor name |
| `--start-time` / `--end-time` | Inclusive ISO-8601 UTC window |
| `--job-id` / `--record-id` / `--record-type` | Optional identity filters |
| `--group` | `summary`, `search`, or `alert` (refines scope; does not establish it) |

Stdout is one JSON object: status, `sufficient_from_memory`, citations,
`vlm_evidence`, synthesized `answer`, and `unresolved_gaps`.

### `get` / `upsert` / `events`

| Command | Required | Other flags |
|---------|----------|-------------|
| `get` | `--job-id` | `--record-type` + `--record-id` for a child |
| `upsert` | JSON object (`--json` or stdin) | One parent or child `nv.vss.memory/1.0` record |
| `events` | `--asset-id` | `--start-time`, `--end-time`, `--anchor-event-id`, `--direction` (`before`/`after`/`around`, default `around`), `--match`, `--limit` (50) |

## Extending it

Groups are discovered from the `vss.commands` entry point, so a third party adds
one without touching this package:

```toml
[project.entry-points."vss.commands"]
acme = "acme_vss.entrypoint:GROUP"

[project.entry-points."vss.command_summaries"]
acme = "Acme video operations"
```

The object needs `api_version`, `name`, `summary`, and `cli() -> click.Command`.
Summaries are read as raw strings, so `vss --help` lists every installed group
without importing any of them.
