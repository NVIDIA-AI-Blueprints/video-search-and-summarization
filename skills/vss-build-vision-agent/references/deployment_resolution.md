# Deployment endpoint resolution

`vss-build-vision-agent` owns publication of the public endpoints that operate
skills consume. Archive search and VIOS operations do not discover Helm
releases, Services, or hostnames; the completed deployment supplies the public
origin as `VSS_PUBLIC_URL`.

For the catalog-level overview, see `skills/README.md`. This file is the
canonical contract for mapping deployment settings to operator-facing public
endpoint variables.

## Deployment output: `VSS_PUBLIC_URL`

A Kubernetes deployment must publish one public Ingress origin, including the
scheme and any non-default port:

```bash
VSS_PUBLIC_URL=https://vss-search.example.com
```

The deployment workflow must surface this value to the operator. Operate skills
trim trailing slashes before building derived URLs.

### Helm / runtime name mapping (search profile)

Operate skills take **one** public origin: `VSS_PUBLIC_URL`. Helm and agent pods
may label that same host differently; treat those names as deploy-side aliases,
not additional operate inputs:

| Operate-skill name | Helm / runtime equivalents |
|---|---|
| `VSS_PUBLIC_URL` | `global.externalHost`, main Ingress host (`vss-search.<ip>.nip.io`), `AGENT_BASE_URL`, `VSS_AGENT_EXTERNAL_URL`, and deploy-minted `VST_EXTERNAL_URL` when it equals that origin |
| `VSS_VIOS_URL` | `${VSS_PUBLIC_URL}/vst` |
| `VST_API_BASE` | `${VSS_VIOS_URL}/api/v1` — all VIOS `curl` targets |
| `AGENT_URL` | `${VSS_PUBLIC_URL}` for Kubernetes operate skills |
| `VSS_STREAMER_URL` | Separate streamer Ingress host (`streamer.<ip>.nip.io`); **not** under `/vst` |

Do not make operate skills invent a Brev or nip.io hostname. The deployment
workflow publishes the public origin; operate skills consume it as
`VSS_PUBLIC_URL` only. Do not require a second operate variable named
`VST_EXTERNAL_URL`.

### Consumer-derived public endpoints

Operate skills may derive these values from the deployment output:

```bash
VSS_PUBLIC_URL="${VSS_PUBLIC_URL%/}"
AGENT_URL="${AGENT_URL:-${VSS_PUBLIC_URL}}"
VSS_VIOS_URL="${VSS_VIOS_URL:-${VSS_PUBLIC_URL}/vst}"
VST_API_BASE="${VST_API_BASE:-${VSS_VIOS_URL}/api/v1}"
```

Public route contract (search profile):

| Capability | Public endpoint |
|---|---|
| Agent search (operate) | `POST ${AGENT_URL}/generate` with `{"input_message": "..."}` |
| Agent ingest/delete | `${AGENT_URL}/api/v1/...` |
| Agent readiness (K8s) | `GET ${AGENT_URL}/openapi.json` — `/health` is not on search Ingress |
| VIOS list/inspect | `GET ${VST_API_BASE}/sensor/list` |
| NvStreamer HTTP | `${VSS_STREAMER_URL}/api/v1/...` — separate host, no `/vst` prefix |

Shareable media URLs from `/url` endpoints may embed an internal host at mint
time. On Kubernetes, compare and validate against `VSS_PUBLIC_URL`; do not
substitute localhost or reconstructed URLs for returned screenshot or clip
links.

## Docker Compose

Use `--deployment docker --profile <profile>` for host CLI search. The profile
must have both its checked-in `.env` and a runtime `generated.env` from the
profile deploy workflow.

Resolve operate endpoints with `discover_docker_host_endpoints(profile)`:

```bash
PROFILE="${PROFILE:-search}"
DOCKER_ENDPOINTS=$(uv run --project "${VSS_REPO_ROOT}/services/agent" --no-dev \
  python -c 'import json,sys; from vss_cli.deployment import discover_docker_host_endpoints; print(json.dumps(discover_docker_host_endpoints(sys.argv[1])))' \
  "${PROFILE}")
AGENT_URL=$(printf '%s' "${DOCKER_ENDPOINTS}" | jq -er '.agent_url')
VST_URL=$(printf '%s' "${DOCKER_ENDPOINTS}" | jq -er '.vst_url')
VSS_VIOS_URL="${VST_URL%/}/vst"
VST_API_BASE="${VSS_VIOS_URL}/api/v1"
ES_URL=$(printf '%s' "${DOCKER_ENDPOINTS}" | jq -er '.es_url')
RTVI_VLM_URL=$(printf '%s' "${DOCKER_ENDPOINTS}" | jq -er '.rtvi_vlm_url')
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

The host CLI reads shared VST/RTVI defaults, overlays `.env` then
`generated.env`, maps Compose service DNS to loopback ports, and resolves index
names from the interpolated agent config. Direct Elasticsearch and RTVI probes
are valid Docker readiness checks; they are not Kubernetes operate
prerequisites.

## Kubernetes consumer contract (no port-forward)

Operate skills require `VSS_PUBLIC_URL` and use the consumer-derived variables
above:

```bash
: "${VSS_PUBLIC_URL:?Provide the public VSS search Ingress origin}"
AGENT_URL="${VSS_PUBLIC_URL%/}"
VSS_VIOS_URL="${AGENT_URL}/vst"
VST_API_BASE="${VSS_VIOS_URL}/api/v1"
```

The public Agent and VIOS routes are the supported operate interfaces. Operate
skills do not read Deployments, ConfigMaps, Services, Secrets, or Helm values,
and do not use Service DNS, NodePorts, guessed release names, or
`kubectl port-forward`.

Private backends (Elasticsearch, RTVI-Embed, RTVI-CV, RT-VLM) remain agent-side
dependencies. Do not expose or forward them merely to satisfy host-side operate
checks.

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
