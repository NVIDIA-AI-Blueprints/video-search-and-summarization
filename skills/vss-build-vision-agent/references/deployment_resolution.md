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
# base profile example
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
| `VLM_ENDPOINT` | `${VSS_PUBLIC_URL}/v1` when the Ingress exposes RT-VLM (base Helm `vssIngress.vlm.enabled`) — OpenAI-compatible root ending in `/v1` |
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
search Ingress, for example, sends that path to its UI catch-all. When a skill
needs direct VLM access, probe before adopting the candidate endpoint:

```bash
if [ -z "${VLM_ENDPOINT:-}" ] && [ -n "${VSS_PUBLIC_URL:-}" ]; then
  _candidate="${VSS_PUBLIC_URL%/}/v1"
  if _models=$(curl -sf --max-time 5 "${_candidate}/models") \
    && _model=$(printf '%s' "${_models}" | jq -r '.data[0].id // empty') \
    && [ -n "${_model}" ]; then
    VLM_ENDPOINT="${_candidate}"
    VLM_MODEL="${VLM_MODEL:-${_model}}"
  fi
fi
```

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
| NvStreamer HTTP | `${VSS_STREAMER_URL}/api/v1/...` — separate host, no `/vst` prefix |

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

## Kubernetes consumer contract (no port-forward)

Operate skills require `VSS_PUBLIC_URL` and use the consumer-derived variables
above:

```bash
: "${VSS_PUBLIC_URL:?Provide the public VSS Ingress origin}"
AGENT_URL="${VSS_PUBLIC_URL%/}"
VSS_VIOS_URL="${AGENT_URL}/vst"
VST_API_BASE="${VSS_VIOS_URL}/api/v1"
# Resolve VLM_ENDPOINT only with the probe-before-adopt flow above.
```

The public Agent, VIOS (`/vst`), and — when the chart enables it — RT-VLM (`/v1`)
routes are the supported operate interfaces. Operate skills do not read
Deployments, ConfigMaps, Services, Secrets, or Helm values, and do not use
Service DNS, NodePorts, guessed release names, or `kubectl port-forward`.

Private backends (Elasticsearch, RTVI-Embed, RTVI-CV, and RT-VLM when it is
**not** published under `/v1`) remain agent-side dependencies. Do not expose or
forward them merely to satisfy host-side operate checks. On the base profile,
RT-VLM at `${VSS_PUBLIC_URL}/v1` is a supported public operate path for
`vss-ask-video` and `vss-generate-video-report` Mode A.

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
