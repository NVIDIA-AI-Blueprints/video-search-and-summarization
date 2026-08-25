# Deployment endpoint resolution

`vss-build-vision-agent` owns publication of the public endpoints that operate
skills consume. Operate skills do not discover Helm releases, Services, or
hostnames; the completed deployment supplies the public origin as
`VSS_PUBLIC_URL`.

For the catalog-level overview, see `skills/README.md`. This file is the
canonical contract for mapping deployment settings to operator-facing public
endpoint variables.

## Deployment output: `VSS_PUBLIC_URL`

A Kubernetes deployment must publish one public Ingress origin, including the
scheme and any non-default port:

```bash
# base / lvs profile example (same main-host pattern)
VSS_PUBLIC_URL=https://vss.example.com
# search profile example
VSS_PUBLIC_URL=https://vss-search.example.com
```

The deployment workflow must surface this value to the operator. Operate skills
trim trailing slashes before building derived URLs. The variable name does not
encode the profile; the deployment publishes one origin and each skill uses the
routes that profile exposes.

### Shared Helm / runtime name mapping

Operate skills take **one** public origin: `VSS_PUBLIC_URL`. Helm and agent pods
may label that same host differently; treat those names as deploy-side aliases,
not additional operate inputs:

| Operate-skill name | Helm / runtime equivalents |
|---|---|
| `VSS_PUBLIC_URL` | `global.externalHost`, main Ingress host, `AGENT_BASE_URL`, `VSS_AGENT_EXTERNAL_URL`, and deploy-minted `VST_EXTERNAL_URL` when it equals that origin |
| `VSS_VIOS_URL` | `${VSS_PUBLIC_URL}/vst` |
| `VST_API_BASE` | `${VSS_VIOS_URL}/api/v1` — all VIOS `curl` targets |
| `AGENT_URL` | `${VSS_PUBLIC_URL}` for Kubernetes operate skills |
| `VLM_ENDPOINT` | Profile-dependent mount, always an OpenAI-compatible root ending in `/v1`: `${VSS_PUBLIC_URL}/v1` on base (Prefix) and LVS (Exact `/v1/models` + `/v1/chat/completions`); `${VSS_PUBLIC_URL}/rtvi-vlm/v1` on search, which mounts RT-VLM by service name and serves no bare `/v1`. Probe the candidates rather than assuming the root |
| `LVS_BACKEND_URL` / `VIDEO_SUMMARIZATION_URL` | `${VSS_PUBLIC_URL}` on Kubernetes (origin only — **no** `/v1` suffix); Docker Compose remains `http://${HOST_IP}:38111` |
| `VSS_STREAMER_URL` | Separate streamer Ingress host (`streamer.<ip>.nip.io`); **not** under `/vst`; search (and other NvStreamer-bearing) profiles only |

Do not make operate skills invent a Brev or nip.io hostname. The deployment
workflow publishes the public origin; operate skills consume it as
`VSS_PUBLIC_URL` only. Do not require a second operate variable named
`VST_EXTERNAL_URL`. `VSS_ENDPOINT` is a legacy alias for `VSS_PUBLIC_URL` in
`vss-ask-video`; prefer `VSS_PUBLIC_URL`.

### Consumer-derived public endpoints

Operate skills may derive these values from the deployment output:

```bash
VSS_PUBLIC_URL="${VSS_PUBLIC_URL%/}"
AGENT_URL="${AGENT_URL:-${VSS_PUBLIC_URL}}"
VSS_VIOS_URL="${VSS_VIOS_URL:-${VSS_PUBLIC_URL}/vst}"
VST_API_BASE="${VST_API_BASE:-${VSS_VIOS_URL}/api/v1}"
```

A public VSS origin alone does not prove that `/v1` routes to an RT-VLM: the
search Ingress, for example, sends that path to its UI catch-all and mounts
RT-VLM at `/rtvi-vlm` instead. So a skill needing direct VLM access probes both
mounts and adopts the first that answers with a model id — never a single
candidate, and never an unprobed default:

```bash
if [ -z "${VLM_ENDPOINT:-}" ] && [ -n "${VSS_PUBLIC_URL:-}" ]; then
  # Most specific first: the search mount, then the base/LVS root.
  for _candidate in "${VSS_PUBLIC_URL%/}/rtvi-vlm/v1" "${VSS_PUBLIC_URL%/}/v1"; do
    if _models=$(curl -sf --max-time 5 "${_candidate}/models") \
      && _model=$(printf '%s' "${_models}" | jq -r '.data[0].id // empty') \
      && [ -n "${_model}" ]; then
      VLM_ENDPOINT="${_candidate}"
      VLM_MODEL="${VLM_MODEL:-${_model}}"
      break
    fi
  done
fi
```

The model-id guard is what makes this safe against a catch-all: a UI page
answers `curl -sf` but yields no `.data[0].id`, so that candidate is rejected
rather than adopted.

Shareable media URLs from `/url` endpoints may embed an internal host at mint
time. On Kubernetes, compare and validate against `VSS_PUBLIC_URL`; do not
substitute localhost or reconstructed URLs for returned screenshot or clip
links.

### Base profile public routes

Main host pattern: `vss.<ip>.nip.io` (Helm `dev-profile-base`). Base does **not**
require a streamer or Kibana host. Default Ingress exposes Agent, UI, VIOS, and
RT-VLM:

| Capability | Public endpoint |
|---|---|
| Agent readiness (K8s) | `GET ${AGENT_URL}/openapi.json` — prefer this over `/health` on Ingress |
| Agent API / chat | `${AGENT_URL}/api/...`, `/websocket`, `/chat` |
| VIOS list/inspect/clips | `GET ${VST_API_BASE}/sensor/list`, storage `/url`, replay `/picture` |
| Direct VLM (ask / report Mode A) | `${VSS_PUBLIC_URL}/v1` → `GET …/models`, `POST …/chat/completions` |
| Phoenix (optional) | `${VSS_PUBLIC_URL}/phoenix` |

Base operate skills for the quickstart walkthrough:

- `vss-manage-video-io-storage` — VIOS via `${VST_API_BASE}`
- `vss-ask-video` — VIOS clip URL + direct VLM at `${VLM_ENDPOINT}` (**not** Agent `/generate`)
- `vss-generate-video-report` Mode A — same VIOS + VLM path (**not** Agent `/generate`)

Do **not** use `${VSS_PUBLIC_URL}/vlm/v1`. Stock base Helm exposes RT-VLM under
**`/v1`**; neither base Helm nor Docker HAProxy serves `/vlm`. On Docker Compose,
the VLM is not published on the public origin — use the host port (`:30082` for
NIM or `:8018` for RT-VLM).

### Search profile public routes

Main host pattern: `vss-search.<ip>.nip.io` (Helm `dev-profile-search`). Search
adds archive search and a separate NvStreamer host:

| Capability | Public endpoint |
|---|---|
| Agent search (operate) | `POST ${AGENT_URL}/generate` with `{"input_message": "..."}` |
| Agent ingest/delete | `${AGENT_URL}/api/v1/...` |
| Agent readiness (K8s) | `GET ${AGENT_URL}/openapi.json` — `/health` is not on search Ingress |
| VIOS list/inspect | `GET ${VST_API_BASE}/sensor/list` |
| Direct VLM (ask / report Mode A) | `${VSS_PUBLIC_URL}/rtvi-vlm/v1` → `GET …/models`, `POST …/chat/completions` — **not** the bare `/v1` base uses |
| Elasticsearch (host CLI) | `${VSS_PUBLIC_URL}/elasticsearch` — read-only edge guard |
| RT-Embed / RT-CV (host CLI) | `${VSS_PUBLIC_URL}/rtvi-embed/v1`, `${VSS_PUBLIC_URL}/rtvi-cv/api/v1` |
| NvStreamer HTTP | `${VSS_STREAMER_URL}/api/v1/...` — separate host, no `/vst` prefix |

Search mounts every backend by service name rather than at the origin root, so
these are the same paths `vss configure` records (`vss_cli/config.py:INGRESS_SERVICES`).
The RT-VLM, Elasticsearch, RT-Embed, and RT-CV mounts are all off by default.
`ingress.cliRoutes.enabled` publishes RT-VLM and Elasticsearch. RT-Embed and
RT-CV come from that toggle or from `global.rtviInternalIngress`, whose own
Ingress claims both paths and makes the main one skip them, so those two can be
public with CLI routes off. Probe rather than infer: a build exposing none of
them leaves search, ask, and report Mode A without the backends they call
directly.

### LVS profile public routes

Main host pattern: `vss.<ip>.nip.io` (Helm `dev-profile-lvs`) — same family as
base, not `vss-search.*`. LVS does **not** mount a Prefix `/v1` the way base
does. Stock Ingress uses **Exact** paths that split LVS and RT-VLM:

| Capability | Public endpoint |
|---|---|
| LVS readiness | `GET ${VSS_PUBLIC_URL}/v1/ready` → video-summarization |
| LVS summarize | `POST ${VSS_PUBLIC_URL}/v1/summarize` → video-summarization |
| RT-VLM models / chat | `GET ${VSS_PUBLIC_URL}/v1/models`, `POST ${VSS_PUBLIC_URL}/v1/chat/completions` |
| VIOS list/inspect/clips | `GET ${VST_API_BASE}/sensor/list`, storage `/url`, … |
| Agent OpenAPI | `GET ${AGENT_URL}/openapi.json` — **Agent**, not LVS |

Derive the LVS client base as the **origin only**:

```bash
# Kubernetes — append /v1/ready and /v1/summarize yourself.
# Force the public origin; a leftover Docker LVS_BACKEND_URL must not win.
LVS_BACKEND_URL="${VSS_PUBLIC_URL%/}"
VIDEO_SUMMARIZATION_URL="${LVS_BACKEND_URL}"
# Docker Compose (unchanged)
# LVS_BACKEND_URL=http://${HOST_IP}:38111
```

Do **not** set `LVS_BACKEND_URL=${VSS_PUBLIC_URL}/v1` — that yields `/v1/v1/ready`.
Do **not** probe bare `${VSS_PUBLIC_URL}/v1` as an LVS or VLM health URL on LVS;
without a Prefix rule it falls through to the UI catch-all.

**Not on stock LVS Ingress** (Docker `:38111` only): LVS `/models`, LVS
`/openapi.json`, `/recommended_config`, `/metrics`, and the `/summarize`
compatibility alias. Public `/openapi.json` is the Agent document — never treat
it as the LVS schema. On Kubernetes, discover the summarize model via
`${VSS_PUBLIC_URL}/v1/models` (RT-VLM Exact) or an explicit `VLM_NAME`, and use
the skill's checked-in `/v1/summarize` contract when live LVS OpenAPI is not
reachable.

LVS operate skills for the docs walkthrough:

- `vss-manage-video-io-storage` — VIOS via `${VST_API_BASE}`
- `vss-summarize-video` — Exact `/v1/ready` + `/v1/summarize` on `${VSS_PUBLIC_URL}`
- `vss-generate-video-report` Mode A — when LVS `/v1/ready` is 200, delegates to
  `vss-summarize-video`; otherwise VLM-direct

Clip URLs passed into `POST /v1/summarize` must stay as VIOS minted them (LVS
fetches that URL). Browser/report links may still rewrite `/storage/...` under
`/vst` using the base-profile rule. Deploy must mint media URLs the LVS pod can
reach (typically `VST_EXTERNAL_URL` equal to the public origin).

### Alerts profile public routes

Main host pattern: `vss.<ip>.nip.io` (Helm `dev-profile-alerts`) — same family as
base/lvs, not `vss-search.*`. Stock Ingress publishes Agent, VIOS, Alert Bridge,
and VA-MCP (path-rewrite strips the public prefix):

| Capability | Public endpoint |
|---|---|
| Alert Bridge health | `GET ${VSS_PUBLIC_URL}/alert-bridge/health` (not `/api/v1/health`) |
| Realtime rules | `GET`/`POST`/`DELETE ${VSS_PUBLIC_URL}/alert-bridge/api/v1/realtime` |
| Realtime incidents | `GET ${VSS_PUBLIC_URL}/alert-bridge/api/v1/realtime/incidents` |
| On-demand / verifier config | `${VSS_PUBLIC_URL}/alert-bridge/api/v1/verification/...` |
| VA-MCP health | `GET ${VSS_PUBLIC_URL}/va-mcp/health` (rewritten to `/health`; prefer over `/mcp` or `/`) |
| VA-MCP | `${VSS_PUBLIC_URL}/va-mcp/mcp` (rewritten to `/mcp`) |
| VIOS list/inspect | `GET ${VST_API_BASE}/sensor/list`, … |
| Agent OpenAPI / generate | `${AGENT_URL}/openapi.json`, `/generate` — **not** for rule CRUD |
| NvStreamer HTTP | `${VSS_STREAMER_URL}/api/v1/...` — separate `streamer.*` host |

Derive Alert Bridge and VA-MCP from the **public origin** (force; ignore leftover
Docker host-port env):

```bash
# Kubernetes — path-rewrite strips /alert-bridge and /va-mcp on the Service.
ALERT_BRIDGE_URL="${VSS_PUBLIC_URL%/}/alert-bridge"
VA_MCP_URL="${VSS_PUBLIC_URL%/}/va-mcp"
# Docker Compose (unchanged host ports)
# ALERT_BRIDGE_URL=http://${HOST_IP}:9080
# VA_MCP_URL=http://${HOST_IP}:9901
```

**Not on stock alerts Ingress** (Docker host ports / private backends only):
Elasticsearch `:9200`, Kafka, Redis, RT-CV (Docker `:9000`), RT-VLM
`:8018`, and `alert-notify` `:9090`. Do not `kubectl port-forward` them for
operate checks. Workflow B's
interim ES verdict probe stays Docker-only unless a public route is added later.

Alerts operate skills for the docs walkthrough (real-time mode):

- `vss-manage-video-io-storage` — VIOS via `${VST_API_BASE}`
- `vss-manage-alerts` — Alert Bridge via `${ALERT_BRIDGE_URL}` (Workflows C/D; never Agent `/generate` for rules)
- `vss-query-analytics` / report Mode B — probe `${VA_MCP_URL}/health`, then MCP via `${VA_MCP_URL}/mcp`

## Docker Compose

Resolve the deployment once with `vss configure`, then read the endpoints back
from the recorded config. The profile must have both its checked-in `.env` and a
runtime `generated.env` from the profile deploy workflow.

```bash
VSS_ORIGIN="${VSS_ORIGIN:-http://${HOST_IP:-127.0.0.1}:${HAPROXY_HOST_PORT:-7777}}"
VSS=(uv run --project "${VSS_REPO_ROOT}/services/agent" --no-dev vss)

"${VSS[@]}" configure --base-url "${VSS_ORIGIN}"
DEPLOYMENT=$("${VSS[@]}" configure show)

AGENT_URL=$(printf '%s' "${DEPLOYMENT}" | jq -er '.base_url')
VSS_VIOS_URL=$(printf '%s' "${DEPLOYMENT}" | jq -er '.services.vst.url')
VST_API_BASE="${VSS_VIOS_URL}/api/v1"
ES_URL=$(printf '%s' "${DEPLOYMENT}" | jq -er '.services.elasticsearch.url')
# `vss configure` records RT-VLM at `.services.rt_vlm.url` where the profile
# routes it, which is what discovery needs. Inference keeps using the host port:
# the ingress applies `timeout server 120s`, and a non-streaming completion or a
# caption over a whole video runs past that.
RTVI_VLM_URL="http://${HOST_IP:-127.0.0.1}:${RTVI_VLM_PORT:-8018}"
```

For VIOS-only operate work without the search CLI, the Compose fallback is:

```bash
VSS_VIOS_URL="http://${HOST_IP:-127.0.0.1}:${VST_INGRESS_HOST_PORT:-30888}/vst"
VST_API_BASE="${VSS_VIOS_URL}/api/v1"
NVSTREAMER_ENDPOINT="http://${HOST_IP:-127.0.0.1}:${NVSTREAMER_HTTP_PORT:-31000}"
```

For Docker shareable-media origin checks, read the deploy-minted
`VST_EXTERNAL_URL` from `generated.env` (Brev HTTPS, not localhost). That
variable is a Compose/deploy runtime detail, not a Kubernetes operate input;
when `VSS_PUBLIC_URL` is set, ignore `VST_EXTERNAL_URL` and use
`VSS_PUBLIC_URL`.

`vss configure` probes each known route on the origin and records only the ones
that answer, so a route the deployment does not expose is absent from the config
rather than present-but-broken. Index names come from that same probe, and they
are created by ingestion — re-run `vss configure` after ingesting, or the
recorded index list stays empty. Direct Elasticsearch and RTVI probes are valid
Docker readiness checks; they are not Kubernetes operate prerequisites.

### Read vs write path resolution (headless)

The two runtime paths resolve endpoints by **different** mechanisms, and the
asymmetry is deliberate — each matches the vantage it runs from:

- **Read / query → `vss configure`** against the build origin (the block above).
  The search CLI takes no endpoints, so ingress-routed URLs for VST, Elasticsearch,
  RT-Embed, and RT-CV all come from the recorded config. There is **no
  ingress-less read path**: a build must front the operate route-set (see
  `services/ingress.md`) to be queryable from the host CLI.
- **Write / provision → loopback host ports**, *not* `vss configure`. The caller
  reads the consumer ports from the build's `resolved.yml` `ports:` mappings
  (`http://localhost:<port>`; stock deploys fall back to profile defaults) and
  hands them to `vss-manage-video-io-storage` `provision-vios-source.md`. Loopback
  covers RT-VLM natively and keeps RT-Embed's live SSE stream off the proxy hop.
  `vss configure` records **ingress URLs, not loopback ports**, so nothing on the
  write path consumes the recorded config — the two mechanisms do not overlap.
- **RT-VLM is loopback-only** on both paths: it has no ingress route
  (`vss configure` records no entry), so it is always the host port
  (`http://${HOST_IP:-127.0.0.1}:${RTVI_VLM_PORT:-8018}`).

## Kubernetes consumer contract (no port-forward)

Operate skills require `VSS_PUBLIC_URL` and use the consumer-derived variables
above:

```bash
: "${VSS_PUBLIC_URL:?Provide the public VSS Ingress origin}"
AGENT_URL="${VSS_PUBLIC_URL%/}"
VSS_VIOS_URL="${AGENT_URL}/vst"
VST_API_BASE="${VSS_VIOS_URL}/api/v1"
# Resolve VLM_ENDPOINT only with the probe-before-adopt flow above.
# LVS client base is the origin (no /v1 suffix); ignore Docker-derived values:
LVS_BACKEND_URL="${AGENT_URL}"
# Alerts — force public prefixes; ignore leftover Docker :9080 / :9901:
ALERT_BRIDGE_URL="${AGENT_URL}/alert-bridge"
VA_MCP_URL="${AGENT_URL}/va-mcp"
```

The public Agent, VIOS (`/vst`), and — when the chart enables them — RT-VLM,
LVS Exact `/v1/...`, Alert Bridge (`/alert-bridge`), and VA-MCP (`/va-mcp`)
routes are the supported operate interfaces. Operate skills do not read
Deployments, ConfigMaps, Services, Secrets, or Helm values, and do not use
Service DNS, NodePorts, guessed release names, `kubectl port-forward`, or
`kubectl`/`docker exec` into pods.

Private backends (Elasticsearch, RTVI-Embed, RTVI-CV, LVS `/models` /
`/openapi.json` when not published, RT-VLM when it is **not** on Exact
`/v1/models`, and alerts `alert-notify` `:9090`) remain agent-side or
Docker-host dependencies. Do not expose or forward them merely to satisfy
host-side operate checks. On the base profile, RT-VLM at
`${VSS_PUBLIC_URL}/v1` (Prefix) is a supported public operate path for
`vss-ask-video` and `vss-generate-video-report` Mode A; on the search profile
the equivalent path is `${VSS_PUBLIC_URL}/rtvi-vlm/v1`. On the LVS profile,
Exact `/v1/ready` and `/v1/summarize` are the supported public operate paths for
`vss-summarize-video`. On the alerts profile, `${VSS_PUBLIC_URL}/alert-bridge`
and `${VSS_PUBLIC_URL}/va-mcp` are the supported public operate paths for
`vss-manage-alerts` and `vss-query-analytics`.

## Authentication boundary

If the public Ingress requires authentication, use only the operator-approved
header or client mechanism. Never inspect Kubernetes Secrets or copy tokens
into prompts, logs, generated files, or final answers.

## Out of scope for single-origin operate mode

| Capability | Why separate |
|---|---|
| NvStreamer HTTP / RTSP | Separate Ingress host; API path is `/api/v1/`, not `/vst/api/v1/` |
| VIOS RTSP proxy output | `rtsp://...` ports are not HTTP-Ingress routed |
| WebRTC live/replay media | Browser-only; not curl-operable through Ingress |
| Host CLI K8s search with ES/RTVI preflight | Needs private backend access or future Ingress routes |
