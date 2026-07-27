# Deployment resolution for operate skills

Archive search and VIOS listing/inspection select their interface by deployment
type. Neither path needs a shell inside a container or pod.

For the catalog-level overview, see `skills/README.md`. This file is the
**canonical contract** for public Ingress operate mode and the mapping between
operator-facing names and Helm/runtime variables.

## Operator input: `VSS_PUBLIC_URL`

Operate skills on Kubernetes require one public Ingress origin, including the
scheme and any non-default port:

```bash
VSS_PUBLIC_URL=https://vss-search.example.com
```

Trim trailing slashes before building derived URLs.

### Helm / runtime name mapping (search profile)

Helm and agent pods usually expose the same origin under different names. Treat
them as equivalent when they resolve to the same host:

| Operate-skill name | Helm / runtime equivalents |
|---|---|
| `VSS_PUBLIC_URL` | `global.externalHost`, main Ingress host (`vss-search.<ip>.nip.io`), `AGENT_BASE_URL`, `VSS_AGENT_EXTERNAL_URL` |
| `VST_EXTERNAL_URL` | Same public origin used for shareable VST media links and agent upload URLs |
| `VSS_VIOS_URL` | `${VSS_PUBLIC_URL}/vst` |
| `VST_API_BASE` | `${VSS_VIOS_URL}/api/v1` — all VIOS `curl` targets |
| `AGENT_URL` | `${VSS_PUBLIC_URL}` for Kubernetes operate skills |
| `VSS_STREAMER_URL` | Separate streamer Ingress host (`streamer.<ip>.nip.io`); **not** under `/vst` |

Do not invent a Brev or nip.io hostname in operate skills. The deployment
workflow supplies `VST_EXTERNAL_URL` / secure-link values; operate skills consume
them.

### Derived public endpoints

```bash
VSS_PUBLIC_URL="${VSS_PUBLIC_URL%/}"
AGENT_URL="${AGENT_URL:-${VSS_PUBLIC_URL}}"
VSS_VIOS_URL="${VSS_VIOS_URL:-${VSS_PUBLIC_URL}/vst}"
VST_API_BASE="${VST_API_BASE:-${VSS_VIOS_URL}/api/v1}"
VST_EXTERNAL_URL="${VST_EXTERNAL_URL:-${VSS_PUBLIC_URL}}"
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
time. Compare and validate against `VST_EXTERNAL_URL` / `VSS_PUBLIC_URL`; do
not substitute localhost or reconstructed URLs for returned screenshot or clip
links.

## Docker Compose

Use `--deployment docker --profile <profile>` for host CLI search. The profile
must have both its checked-in `.env` and a runtime `generated.env` created by
`dev-profile.sh`.

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

Read `VST_EXTERNAL_URL` from `generated.env` for shareable media origin checks
(Brev HTTPS, not localhost).

The host CLI reads shared VST/RTVI defaults, overlays `.env` then
`generated.env`, maps Compose service DNS to loopback ports, and resolves index
names from the interpolated agent config. Direct Elasticsearch and RTVI probes
are valid Docker readiness checks; they are not Kubernetes operate
prerequisites.

## Kubernetes operate (no port-forward)

Require `VSS_PUBLIC_URL`. Use the derived variables above.

```bash
: "${VSS_PUBLIC_URL:?Provide the public VSS search Ingress origin}"
AGENT_URL="${VSS_PUBLIC_URL%/}"
VSS_VIOS_URL="${AGENT_URL}/vst"
VST_API_BASE="${VSS_VIOS_URL}/api/v1"
```

**Search:** `POST ${AGENT_URL}/generate` with an explicit natural-language
prompt that preserves resolved source, mode, attributes, time bounds, and top-k.
Treat the Agent response as conversational text (`SEARCH_TEXT`); do not parse it
as Docker CLI `SearchOutput` (no `.data[]` / `screenshot_url` contract).

**Ingest / delete:** Agent `/api/v1/...` on the same origin. Upload URLs returned
by the agent already use `${VST_EXTERNAL_URL}/vst/api/v1/storage/file` when
Ingress is configured.

**Source listing:** `GET ${VST_API_BASE}/sensor/list` (or `/sensor/streams`).

This operate-skill path deliberately does **not** use the host CLI's
`--deployment kubernetes` selector. That selector port-forwards private backends
(Elasticsearch, RTVI, in-cluster VST) and is outside the public Ingress contract.

Do not read Deployments, ConfigMaps, Services, Secrets, or Helm values; do not
use Service DNS, NodePorts, guessed release names, or `kubectl port-forward`.

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
