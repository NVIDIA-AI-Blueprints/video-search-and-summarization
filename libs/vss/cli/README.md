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
cd libs/vss
uv run vss --help
```

`uv run` syncs `libs/vss/.venv` on first use. No extras and no `--no-dev`:
`libs/vss` is the library's own workspace and the agent stack is not a member,
so the environment is NAT-free by construction rather than by flag. There is no
`cli` extra to ask for here — that one belongs to `services/agent`, which
re-exports this distribution for the agent image.

## Develop

```bash
cd libs/vss
uv sync --frozen
uv run --no-sync pytest core/tests/unit_test cli/tests/unit_test -q
uv run --no-sync ruff check core cli
uv run --no-sync mypy cli/src/vss_cli core/src/vss_core
```

The `dev` group carries the test tooling and is included by default. Keep
`--no-sync` after that first sync so each command reuses the same `.venv`
instead of re-resolving.

Being NAT-free is a property of the workspace, not of a lane you have to
remember to run — nothing in `libs/vss` may depend on the agent stack, so
`import nat` fails here by construction. CI asserts it on every run.

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
| `--base-url` | required | Deployment origin, e.g. `http://10.0.0.1:7777`, or a bare VLM endpoint (see [A VLM endpoint without VSS](#a-vlm-endpoint-without-vss)). A missing scheme is assumed `http://` with a note on stderr |
| `--timeout` | 5.0 | Per-route probe timeout in seconds (0.1–120) |

| Subcommand | What it does |
|------------|--------------|
| `vss configure --base-url URL` | Probe every known route and rewrite the config |
| `vss configure show` | Print the recorded deployment as JSON |
| `vss configure check` | Re-probe each recorded route, then list which command groups are available |
| `vss configure memory …` | Static memory policy (see below) |
| `vss configure vlm …` | Reusable VLM request policy, with optional locking |

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
| `video_analytics` | `/video-analytics-api` | URL only |
| `elasticsearch` | `/elasticsearch` | URL + index names |
| `rt_embed` | `/rtvi-embed` | URL + model ids |
| `rtvi_cv` | `/rtvi-cv` | URL only (no introspection endpoint) |
| `rt_vlm` | `/rtvi-vlm` | URL + model ids — the default model for `vss vlm` and introspection follow-ups |
| `lvs` | `/lvs` | URL + model ids (long-video summarization) |

When no `/rtvi-vlm` route answers, `configure` also tries the origin as an
OpenAI-compatible VLM (below): a bare model server, or a partial stack with
VIOS and Elasticsearch behind the ingress and vLLM at its root. If the origin
exposes nothing at all, `configure` fails rather than writing an empty config. Elasticsearch indices are created by ingestion, not deployment,
so configuring a fresh stack records zero `mdx-*` indices and says so — re-run
`configure` after ingesting video and before searching.

### The config file

Written to `~/.vss/config.json` at mode 0600, holding **no credentials**: only
URLs, discovered model/index names, `written_at`, and static memory/VLM policy.
Set `VSS_CONFIG_HOME` to point at a different directory for a second deployment
or for tests. The file carries a `version`; one written by a newer CLI is
refused rather than half-read, with a message telling you to re-run `configure`.

Re-running `vss configure --base-url …` refreshes routes and **preserves valid
static memory and VLM policy**, so re-probing after a deployment change does
not reset request, judge, embedding, or persistence settings. An existing file
it cannot read (corrupt, or carrying settings this version no longer has)
stops `configure` with the reason and writes nothing, rather than being
replaced by one without those policies.

`vss configure check` prints per-service reachability and a `commands:` table
marking each group available or unavailable (a group is available only when
*every* service it needs is routed). It exits 3 if any recorded route no longer
answers.

### A VLM endpoint without VSS

`--base-url` also accepts a bare OpenAI-compatible VLM server: a standalone
vLLM, a Cosmos Reason NIM, RT-VLM's own port, or Inference Hub. When no
`/rtvi-vlm` route answers, `configure` requests `<origin>/v1/models` and, if it
answers, records the origin as the `rt_vlm` service: the only service for a
bare endpoint, or next to the VSS routes it found for a partial stack. A bare host, an OpenAI `base_url` ending in `/v1`
and a full `/v1/chat/completions` URL all record the same origin.

```bash
export VSS_VLM_API_KEY=sk-...          # Inference Hub; never written to the config
vss configure --base-url https://inference-api.nvidia.com/v1
vss configure vlm --model <model-id>   # needed when the endpoint lists several
vss vlm run --media-url https://host/clip.mp4 --prompt "What happens?"
```

- **Credential.** `VSS_VLM_API_KEY`, when set, is sent as `Authorization: Bearer`
  on the probe and on every `vss vlm run`. A 401 or 403 from `/v1/models` stops
  `configure` with a request to export it.
- **Backend.** Inference Hub identifies itself (LiteLLM `x-litellm-*` headers),
  and `configure` records the `openai` backend unless `VSS_VLM_BACKEND` or a
  saved non-default backend says otherwise. A model list reporting vLLM is only
  a hint, because RT-VLM can answer the same way and rejects vLLM's request
  shape: `configure` prints it, and `vss configure vlm --backend vllm` applies it.
- **Model.** `--model`, else `VSS_VLM_MODEL` or `vss configure vlm --model`, else
  the endpoint's only listed model. With several listed and none chosen,
  `vss vlm run` stops and lists them.
- **Media.** There is no VIOS, so `--sensor` fails; use `--media-url` with a URL
  the endpoint can fetch, or a local file with `--use-base64`. Results persist
  only if memory is configured separately.

`vss configure check` re-probes `/v1/models` for such a config.

That same file is where memory policy lives. `vss configure` records service
URLs (including RT-VLM). `vss configure memory` records how the CLI uses
Elasticsearch, embeddings, the text judge, and optional Markdown notes.

## Configure VLM requests

`vss configure vlm` stores reusable defaults for `vss vlm run`. Add `--lock`
to reject conflicting per-call flags:

```bash
vss configure vlm \
  --backend rt-vlm \
  --timeout 600 \
  --temperature 0 \
  --max-tokens 8192 \
  --seed 1 \
  --disable-reasoning \
  --chunk-duration 0 \
  --fps 4 \
  --max-frames 256 \
  --total-pixels 16777216 \
  --lock
```

Each field also has an independent runtime environment override:

| Field | Environment variable |
|-------|----------------------|
| `backend` | `VSS_VLM_BACKEND` (`rt_vlm`, `vllm`, alpha `cosmos_reason_nim`, or `openai` for a plain chat completion) |
| `model` | `VSS_VLM_MODEL` |
| `timeout` | `VSS_VLM_TIMEOUT` |
| `temperature` | `VSS_VLM_TEMPERATURE` |
| `max_tokens` | `VSS_VLM_MAX_TOKENS` |
| `seed` | `VSS_VLM_SEED` |
| `enable_reasoning` | `VSS_VLM_ENABLE_REASONING` (`true` or `false`) |
| `chunk_duration` | `VSS_VLM_CHUNK_DURATION` |
| `fps` | `VSS_VLM_FPS` |
| `max_frames` | `VSS_VLM_MAX_FRAMES` |
| `total_pixels` | `VSS_VLM_TOTAL_PIXELS` |
| `locked` | `VSS_VLM_LOCKED` (`true` or `false`; see below) |

Environment variables provide per-field defaults. Values persisted by
`vss configure vlm` override those defaults. Explicit `vss vlm run` arguments
override the resulting policy when it is unlocked; conflicting arguments are
rejected when it is locked. If neither source defines a field, its built-in
request default applies. An empty variable counts as unset, so an image that
declares one empty keeps the default; a malformed one is an error, and so is a
non-empty `VSS_VLM_*` name the CLI does not support (a typo, or a removed
setting such as `VSS_VLM_LOCKED`), which would otherwise do nothing.
A policy is locked by `vss configure vlm --lock` or by `VSS_VLM_LOCKED=true`.
The variable is for images that must fix their settings: under it the values
the environment sets win over saved ones, the policy is locked whatever the
config file says, and `vss configure vlm` refuses changes. `VSS_VLM_LOCKED=false`
(the default the hermes and openclaw images declare) adds no lock and does not
unlock a policy saved with `--lock`.

### Frame sampling

Three fields control what the model sees. They follow VLMEvalKit's video
dataset configs, and the CLI sends them to the backend rather than resolving
them itself:

| Field | Meaning | Sent as |
|-------|---------|---------|
| `fps` | Frames sampled per second | `media_io_kwargs.video.fps` |
| `max_frames` | Upper bound on frames; combines with `fps` | `media_io_kwargs.video.num_frames` (vLLM also gets `max_frames`, which its `qwen3_vl` loader reads) |
| `total_pixels` | Pixel budget for the whole clip | `mm_processor_kwargs.size.longest_edge`, with `shortest_edge` set to the Qwen3-VL floor of 131072 or `total_pixels` if smaller |

There is no separate per-frame pixel field, because `mm_processor_kwargs.max_pixels`
is not one. Where it is read at all -- RT-VLM's patched vLLM `qwen3_vl.py`,
transformers' Qwen2-VL processors -- it is an alias that overwrites
`size.longest_edge`, the field `total_pixels` sets; stock vLLM and the
Qwen3-VL video processor do not accept it. So `total_pixels` is the one pixel
control: a whole-clip budget on Qwen3-VL, where it sets per-frame resolution
together with `max_frames`, and a per-frame limit on Qwen2/2.5-VL.

`total_pixels` assumes a Qwen3-VL-family processor (Qwen3-VL, Cosmos-Reason2),
where the budget covers every frame: 16,777,216 is roughly 8,192 vision tokens
(each token is a 32x32 tile across two merged frames). Qwen2/2.5-VL processors
read the same field as a per-frame limit. A server may also ignore per-request
processor kwargs: vLLM 0.28 serving Qwen3.8-27B did, so there the pixel budget
has to be set server-side.

A field left unset is not sent, and the VLM server's own sampling applies. On
RT-VLM that is the deployment's `VLM_DEFAULT_NUM_FRAMES_PER_SECOND_OR_FIXED_FRAMES_CHUNK`,
which some profiles set to one frame per chunk. `vss configure` prints the
effective values and their source (environment variable or config file) and
names any that are unset.

The `openai` backend sends none of these fields, nor `enable_reasoning`: it is a
plain chat completion, so the endpoint's own sampling applies and `configure`
says so.

RT-VLM and the Cosmos NIM accept `fps` or `num_frames`, not both (HTTP 400).
With both set, the CLI sends them `fps` alone, logs a warning, and their
deployment-wide frame cap applies; `max_frames` applies there only when `fps`
is unset, or when `--max-frames` is given on the command line and `fps` only
came from the environment or saved policy: an explicit frame count replaces an
inherited rate there (a fixed-count benchmark gets its count), unless the policy
is locked. vLLM receives both. With `fps` alone, vLLM also gets
`num_frames: -1`: its `VideoMediaIO` otherwise hands the loader `num_frames=32`
(vLLM 0.28), which would stop the default loader at 32 frames whatever the rate.

## The surface

| Group | What it is | Verbs |
|-------|-----------|-------|
| `vss search` | Fused archive search over ES + the embedding NIM | `run`, `status`, `get`, `list` |
| `vss summarize` | VLM summarization of stored video | `run`, `status`, `get`, `list` |
| `vss vlm` | One VLM answer from a recorded sensor window | `run`, `status`, `get`, `list` |
| `vss memory` | Unified-memory access, embeddings backfill, introspection | `upsert`, `get`, `query`, `events`, `introspect`, `embeddings backfill` |
| `vss analytics` | Read-only incidents, analytics sensors/places, and metrics | `incidents`, `incident`, `sensors`, `places`, `fov-histogram`, `average-speed`, `analyze` |
| `vss vios` | Media plane: sensors, timelines, clip and snapshot URLs | `list`, `timeline`, `clip`, `snapshot`, `add`, `delete` |
| `vss configure` | Resolve a deployment and set static memory/VLM policy | `show`, `check`, `memory`, `memory show`, `memory check`, `memory introspection`, `vlm` |

`search`, `summarize`, and `vlm` are **job groups**: every run mints a `job_id`, and the
result stays retrievable by that id. `analytics` and `vios` are **not**:
analytics performs direct read-only queries, while VIOS resolves handles and
mints URLs. Neither has job verbs or writes memory. See
[AGENTS.md](AGENTS.md#the-two-shapes).

## Read video analytics

Configure the deployment once, then query the Video Analytics API through its
recorded ingress route:

```bash
vss configure --base-url <origin>
vss configure check
vss analytics incidents --limit 10
vss analytics incident --incident-id <id>
vss analytics sensors
vss analytics places
vss analytics fov-histogram \
  --source <sensor-id> --source-type sensor \
  --start-time <ISO-8601> --end-time <ISO-8601> --bucket-count 10
vss analytics average-speed \
  --source <sensor-id-or-place> --source-type sensor \
  --start-time <ISO-8601> --end-time <ISO-8601>
vss analytics analyze \
  --source <sensor-id-or-place> --source-type sensor \
  --start-time <ISO-8601> --end-time <ISO-8601> \
  --analysis-type max-min-incidents
```

`vss analytics sensors` lists sensor IDs represented in analytics calibration
data. `vss vios list` lists sensors registered in VIOS; they are intentionally
separate inventories. Empty arrays and zero counts are successful results.

### VA-MCP parity

| VA-MCP tool | CLI command | Video Analytics API route | Parity gap |
|---|---|---|---|
| `get_incidents` | `analytics incidents` | `GET /incidents` | Requested includes are normalized client-side because the route returns complete records |
| `get_incident` | `analytics incident` | `GET /incidents` with an exact ID query | Composed from the list route; a miss exits 5 |
| `get_sensor_ids` | `analytics sensors` | `GET /config/calibration` | Returns analytics calibration sensors only; VIOS registration remains `vios list` |
| `get_places` | `analytics places` | `GET /config/calibration` | Place hierarchy is normalized from calibration sensors |
| `get_fov_histogram` | `analytics fov-histogram` | `GET /metrics/occupancy/fov/histogram` | Place input composes per-sensor histograms from calibration |
| `get_average_speeds` | `analytics average-speed` | `GET /metrics/average-speed` | None |
| `analyze` | `analytics analyze` | Composed from the routes above | Deterministic structured JSON replaces bare prose |
| `vst_sensor_list` | `vios list` | Existing VIOS client | None |

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
follow-ups, and a 180-second overall timeout. Follow-up clips use the requested
window without an introspection-specific duration cap. The introspection
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
| `--model` | configured model, else the endpoint's only one | Override the model name |
| `--timeout` | 30s (`vlm run`); 180s (introspection follow-ups) | HTTP / workflow budget |
| `--fps` / `--max-frames` / `--total-pixels` | policy, else server sampling | Frame sampling; see [Frame sampling](#frame-sampling) |
| `--max-tokens` / `--temperature` | unset | Optional generation knobs |
| `--intent` | `qa` (`vlm run`); `introspection` (follow-ups) | Stored on the memory record |
| `--no-persist` | off | Skip writing this VLM job |

Introspection follow-ups use the bounded analyzer rather than direct
`vlm run`'s FPS translation. They accept `--fps`, but a known-duration request
that implies more than 60 frames becomes a fixed 60-frame sample. They also
honor `--persist-by-default`.
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
