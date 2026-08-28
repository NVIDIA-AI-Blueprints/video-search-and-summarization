# Docker deployment (`deploy/docker`)

This tree is the Docker Compose packaging for **Video Search & Summarization**. The root **`compose.yml`** pulls three layers together:

| Include | Role |
|---------|------|
| **`services/compose.yml`** | Shared microservices (infra, VIOS, UI, RTVI, NIMs, etc.) |
| **`developer-profiles/compose.yml`** | Developer profiles: **base**, **lvs**, **alerts**, **search** |
| **`industry-profiles/compose.yml`** | Industry blueprints (e.g. **warehouse-operations**) |

Run Compose from **`deploy/docker`** so relative paths resolve correctly.

---

## Environment files and precedence

The deployment files use a layered environment model. Later `--env-file`
arguments override earlier ones, so order matters.

| File | Role |
|------|------|
| **`containers.env`** | Shared first-party container registry and tag defaults. Pass this before profile env files when running Compose directly. |
| **`developer-profiles/dev-profile-*/.env`** / **`industry-profiles/*/.env`** | Stable profile defaults. These files should not carry machine-specific paths, host ports, credentials, or generated runtime values. |
| **`developer-profiles/dev-profile-*/overrides.env`** | Checked-in developer-profile template. Copy it to `user-overrides.env` before direct Compose deployment; do not edit the template. |
| **`developer-profiles/dev-profile-*/user-overrides.env`** | Ignored user-managed direct-Compose overlay for hardware, model placement, endpoint URLs, host paths, credentials, public ingress, host-published ports, and `COMPOSE_PROFILES`. |
| **`industry-profiles/*/overrides.env`** | Mutable deployment-specific defaults for industry profiles. |
| **`generated.env`** | Developer-profile runtime overlay created by `dev-profile.sh` from `overrides.env`. It also receives derived values such as `VSS_APPS_DIR`, `VSS_DATA_DIR`, `HOST_IP`, API keys, model slugs, and compose-wide defaults. Do not edit or commit this file. |

`dev-profile.sh` starts developer stacks with env files in this order:

```bash
--env-file containers.env \
--env-file developer-profiles/dev-profile-<profile>/.env \
--env-file developer-profiles/dev-profile-<profile>/generated.env
```

When running Compose directly, first create the ignored user-managed overlay from the
checked-in template (repeat the copy to discard prior layout choices):

```bash
cp <profile>/overrides.env <profile>/user-overrides.env
```

Then pass `containers.env`, the profile `.env`, and the user overlay:

```bash
--env-file containers.env \
--env-file <profile>/.env \
--env-file <profile>/user-overrides.env
```

Before direct Compose bring-up, update the deployment-specific placeholders in
`user-overrides.env`, especially `VSS_APPS_DIR`, `VSS_DATA_DIR`, `HOST_IP`,
`EXTERNAL_IP`, credentials, and the active `COMPOSE_PROFILES`.

---

## Developer profiles (recommended path)

Use the **`dev-profile`** helper instead of hand-editing Compose for day-to-day developer stacks (**base**, **lvs**, **search**, **alerts**).

**Script:** `deploy/docker/scripts/dev-profile.sh`

**Examples:**

```bash
cd /path/to/video-search-and-summarization

# Required for bring-up: NGC CLI API key (pull + NIM)
export NGC_CLI_API_KEY="<your-key>"

# Base profile — minimal developer stack (hardware profile required)
./deploy/docker/scripts/dev-profile.sh up \
  --profile base \
  --hardware-profile H100

# LVS profile — video summarization / LVS-oriented bundle (hardware profile required)
./deploy/docker/scripts/dev-profile.sh up \
  --profile lvs \
  --hardware-profile H100

# Alerts profile — set --mode to verification or real-time
./deploy/docker/scripts/dev-profile.sh up \
  --profile alerts \
  --mode verification \
  --hardware-profile H100

# Search profile
./deploy/docker/scripts/dev-profile.sh up \
  --profile search \
  --hardware-profile H100

# Tear down (no profile flags — cleans the managed Compose project and data dir)
./deploy/docker/scripts/dev-profile.sh down
```

**Full options** (models, remote LLM/VLM, device IDs, edge hardware, etc.):

```bash
./deploy/docker/scripts/dev-profile.sh --help
```

### LVS GPU hardware metrics

The LVS developer profile starts NVIDIA DCGM Exporter alongside the application.
It uses NVIDIA Data Center GPU Manager to expose host GPU telemetry in Prometheus
format at `http://localhost:9400/metrics`, including GPU and decoder utilization,
frame-buffer usage, power draw, and temperature when supported by the GPU and
driver.

```bash
# Verify the exporter is ready and inspect its GPU metrics.
curl --fail http://localhost:9400/metrics |
  grep -E 'DCGM_FI_DEV_(GPU_UTIL|DEC_UTIL|FB_USED|POWER_USAGE|GPU_TEMP)'
```

Set `DCGM_EXPORTER_HOST_PORT` in
`developer-profiles/dev-profile-lvs/user-overrides.env` if port 9400 is already in
use. The exporter requires the NVIDIA driver, NVIDIA Container Toolkit, and a
GPU supported by DCGM. Its `/metrics` endpoint can be scraped directly by
Prometheus, Dynatrace, or another Prometheus-compatible monitoring system.

Each developer profile ships a stable **`.env`** and a mutable
**`overrides.env`** under **`developer-profiles/dev-profile-<profile>/`**. On
`up`, the helper reads both, copies `overrides.env` to **`generated.env`**, adds
derived runtime values, and starts Compose with `containers.env`, the profile
`.env`, and `generated.env` in that order.

The helper resets its managed state before every `up`: it stops the Compose
project **`mdx`**, removes Compose volumes, deletes old `generated.env` files,
cleans generated SDRC artifacts, and deletes the developer data directory
(default: **`deploy/docker/data-dir`**) before recreating it. Use `--dry-run` to
preview the commands and generated environment without starting containers.

### RTVI CV startup policy

- Docker uses one canonical RTVI CV startup entrypoint: `services/rtvi/rtvi-cv/ds-start.sh`.
- Developer profiles (**alerts**, **search**) and warehouse **2D/3D** use the shared startup path selected by env/config data.
- Per-profile startup wrapper scripts are not used.
- **MV3DT is the documented exception** and keeps its dedicated `ds-start-mv3dt.sh` command override.
- Model acquisition for **developer profiles** (alerts, search) and **warehouse RT-CV profiles** (2D, 3D, MV3DT) runs as phase 0 of the perception startup script (`ds-start.sh` / MV3DT `ds-start-mv3dt.sh`) when a per-profile `models-download.json` is mounted. There is no separate download init service. Warehouse still uses the pre-extracted `VSS_DATA_DIR` bundle for videos, playback, and calibration (see the warehouse section below).

### Direct Compose usage and data directories

`dev-profile.sh` creates and permissions developer-profile data directories automatically. If you run
`docker compose` directly, you are responsible for both the env-file order and the
host directories.

For a developer profile started by `dev-profile.sh`, use its helper-created `generated.env`:

```bash
cd /path/to/video-search-and-summarization/deploy/docker

docker compose -f compose.yml \
  --env-file containers.env \
  --env-file developer-profiles/dev-profile-base/.env \
  --env-file developer-profiles/dev-profile-base/generated.env \
  config
```

For direct Compose, copy `overrides.env` to `user-overrides.env` first, then
replace the user file's placeholder values for `VSS_APPS_DIR`, `VSS_DATA_DIR`, `HOST_IP`,
credentials, ports, and model settings.

### Gateway identity

HAProxy is the HTTP front door. Service-to-service HTTP inside the deployment
uses one logical origin plus the stable path contract below — not Compose DNS
nicknames like `http://vss-va-mcp:9901`. Placement lives in DNS and in HAProxy;
no caller branches on whether a service is colocated.

Profiles expose three variables:

| Variable | Default | What it is |
|---|---|---|
| `VSS_GATEWAY_HOST` | `vss.local` | The gateway's name. HAProxy publishes `vss.local` as a bridge network alias unconditionally. |
| `VSS_GATEWAY_PORT` | `${HAPROXY_PORT:-7777}` | The listener HAProxy binds. |
| `VSS_GATEWAY_ORIGIN` | `http://vss.local:7777` | What callers prefix a mount with. |

**The gateway origin is pinned to HAProxy's listener and is deliberately not
derived from `VSS_PUBLIC_*`.** The two describe different callers. `VSS_PUBLIC_*`
is the browser's origin; where a platform terminates TLS outside the stack — a
Brev secure link is `https` on `443`, forwarding plain HTTP to `7777` — reusing
it here would hand every container `https://vss.local:443`, a listener that does
not exist. `VSS_PUBLIC_*` keeps browsers, report links, and
`vss configure --base-url` on the host; the gateway keeps the containers. Never
point a container at the platform's secure-link hostname.

The end state is one front door, one path contract, two origins: internal
callers use `http://vss.local:7777`, external callers use the platform's HTTPS
URL.

None of these may render empty. An empty origin produces URLs like
`/elasticsearch`, which fail inside an HTTP client as a malformed request rather
than here as a configuration error, so `services/agent/compose.yml` spells out
an inline default on every gateway-derived variable.

Agent Compose derives the agent's HTTP backends from the origin —
`VIDEO_ANALYSIS_MCP_URL`, `VST_INTERNAL_URL`, `ELASTIC_SEARCH_ENDPOINT`,
`COSMOS_EMBED_ENDPOINT`, `RTVI_CV_ENDPOINT`, `RTVI_VLM_BASE_URL`,
`ALERT_BRIDGE_URL`, `LVS_BACKEND_URL`, `PHOENIX_ENDPOINT`. Browser-facing
`VST_EXTERNAL_URL` stays on `VSS_PUBLIC_*`. Non-HTTP data planes — Kafka, Redis,
RTSP, raw media — are out of scope and keep their own addressing.

Some process-local and bootstrap HTTP settings also remain on Docker DNS by
design. `services/alert/alert.env` addresses the alert-bridge process itself;
`services/vios/vst.env` is VST's own internal identity; Logstash, Kibana,
`elasticsearch-init`, and analytics writers use Elasticsearch operations that
the gateway ACL intentionally rejects. These are backend/service definitions,
not agent defaults, and the endpoint lint excludes them.

### Colocated and remote agents

For a colocated agent, use the ordinary profile Compose files. The agent joins
the deployment bridge and defaults to `http://vss.local:7777`; the bridge alias
is published by HAProxy, not by the caller:

```bash
cd deploy/docker
docker compose -f compose.yml \
  --env-file containers.env \
  --env-file developer-profiles/dev-profile-alerts/.env \
  --env-file developer-profiles/dev-profile-alerts/overrides.env \
  up --detach vss-agent
```

Check the rendered colocated contract, then verify it from the bridge:

```bash
docker compose -f compose.yml \
  --env-file containers.env \
  --env-file developer-profiles/dev-profile-alerts/.env \
  --env-file developer-profiles/dev-profile-alerts/overrides.env \
  config vss-agent vss-va-mcp vss-haproxy-ingress
docker exec vss-agent getent hosts vss.local
docker exec vss-agent curl -fsS http://vss.local:7777/va-mcp/health
docker exec vss-agent curl -fsS http://vss.local:7777/elasticsearch/
```

The last two prove the rewrite, not just the route: `/va-mcp/health` must return
the MCP server's own health body and `/elasticsearch/` the cluster banner. A 404
means HAProxy matched the path but rewrote it into something the backend does
not serve.

From the host, the same front door on the published port:

```bash
curl -fsS "${VSS_PUBLIC_URL}/va-mcp/health"
vss configure --base-url "${VSS_PUBLIC_URL}"   # never http://vss.local:7777
vss configure check
```

To run the agent on another host against an already-running stack, copy
`remote-agent.env.example` to a private operator file and replace every
placeholder. `VSS_GATEWAY_ORIGIN` must be HTTP-reachable from that host, usually
`http://<VSS_HOST_IP>:7777` or the deployment's public origin. Never use
`vss.local`: it exists only on the deployment's Compose bridge.

The remote overlay removes the agent's optional colocated dependencies, so the
command starts exactly one service. It still uses the selected profile's agent
config and image defaults:

```bash
mkdir -p /absolute/path/to/remote-agent-data/agent_eval

docker compose -f compose.yml -f compose.remote-agent.yml \
  --env-file containers.env \
  --env-file developer-profiles/dev-profile-alerts/.env \
  --env-file developer-profiles/dev-profile-alerts/overrides.env \
  --env-file /secure/path/remote-agent.env \
  up --detach vss-agent
```

The profile's `VSS_AGENT_CONFIG_FILE` selects base, alerts, search, or LVS
behavior. Model APIs are not gateway mounts; if that workflow uses an LLM or
VLM, the remote env file must replace profile-local model Docker names with
origins reachable from the remote host.

Before starting, validate DNS and every gateway path the selected agent needs:

```bash
set -a
. /secure/path/remote-agent.env
set +a

getent hosts "${VSS_GATEWAY_HOST}"
curl -fsS "${VSS_GATEWAY_ORIGIN}/va-mcp/health"
curl -fsS "${VSS_GATEWAY_ORIGIN}/vst/api/v1/sensor/streams"
curl -fsS "${VSS_GATEWAY_ORIGIN}/elasticsearch/"
```

Then configure the host-side CLI with the public origin. The CLI does not read
service endpoints from process environment and must not be pointed at
`vss.local`:

```bash
vss configure --base-url "${VSS_PUBLIC_URL}"
vss configure check
```

On Brev, this is intentionally two origins: deployment containers keep
`http://vss.local:7777`, while the remote agent and host CLI use the HTTPS
secure-link `VSS_PUBLIC_URL`. TLS termination and production authentication or
mTLS policy remain external ingress/operator concerns; this Compose path does
not add either.

Gateway path contract (HAProxy). Callers use `${VSS_GATEWAY_ORIGIN}<mount>` then
the service's own path. Prefix is stripped only where the backend does not
serve that prefix natively.

| Mount | Prefix | Caller base when gateway-routed | Example public path | Backend path |
|---|---|---|---|---|
| `/va-mcp` | strip | `${VSS_GATEWAY_ORIGIN}/va-mcp` | `/va-mcp/mcp`, `/va-mcp/health` | `/mcp`, `/health` |
| `/alert-bridge` | strip | `${VSS_GATEWAY_ORIGIN}/alert-bridge` | `/alert-bridge/health` | `/health` |
| `/video-analytics-api` | strip | origin + `/video-analytics-api` | `/video-analytics-api/...` | `/...` |
| `/elasticsearch` | strip (method/path restricted) | `${VSS_GATEWAY_ORIGIN}/elasticsearch` | `/elasticsearch/_cat/indices` | `/_cat/indices` |
| `/rtvi-vlm` | strip | `${VSS_GATEWAY_ORIGIN}/rtvi-vlm` | `/rtvi-vlm/v1/models` | `/v1/models` |
| `/rtvi-cv` | strip | `${VSS_GATEWAY_ORIGIN}/rtvi-cv` | `/rtvi-cv/api/v1/stream/add` | `/api/v1/stream/add` |
| `/rtvi-embed` | strip | `${VSS_GATEWAY_ORIGIN}/rtvi-embed` | `/rtvi-embed/v1/models` | `/v1/models` |
| `/lvs` | strip | `${VSS_GATEWAY_ORIGIN}/lvs` | `/lvs/v1/live` | `/v1/live` |
| `/phoenix` | strip | `${VSS_GATEWAY_ORIGIN}/phoenix` | `/phoenix` | `/` (keep `PHOENIX_HOST_ROOT_PATH=/phoenix`) |
| `/vst` | preserve | `${VSS_GATEWAY_ORIGIN}` (not `/vst`) | `/vst/api/...` | `/vst/api/...` |
| `/vios` | **rewrite to `/vst`** | `${VSS_GATEWAY_ORIGIN}/vios` | `/vios/api/...` | `/vst/api/...` |
| `/alerts` | strip | `${VSS_GATEWAY_ORIGIN}/alerts` | `/alerts/api/v1/alerts` | `/api/v1/alerts` |
| `/video-summarization` | strip | `${VSS_GATEWAY_ORIGIN}/video-summarization` | `/video-summarization/v1/summarize` | `/v1/summarize` |
| `/storage` | rewrite to `/vst/storage` | origin | `/storage/...` | `/vst/storage/...` |
| `/kibana` | preserve | origin + `/kibana` | `/kibana/...` | `/kibana/...` |
| `/api`, `/chat`, `/websocket`, `/static` | preserve | origin | `/api/v1/...` | `/api/v1/...` |
| `/behavior-analytics`, `/perception-sdr` | preserve | origin + prefix | prefix paths | same (often 503 if backend has no HTTP) |

#### `/vios`, `/alerts`, `/video-summarization` are aliases, not renames

The three new prefixes route to the same backends as `/vst`, `/alert-bridge`
and `/lvs`. **All six work.** Nothing that addresses an old prefix has to
change, which is the point: a hard rename would have to move the `skills/`
tree, the CLI's vios client (which spells `/vst/` in many places), the Helm
templates, the notebooks and the skill evals in one commit, and any straggler
would 404 in production.

`/vios` is the one that is **rewritten rather than stripped**, and the
asymmetry is not cosmetic. VST serves its whole surface under `/vst/`, so
`/vios/api/v1/x` has to arrive as `/vst/api/v1/x`; a strip would send
`/api/v1/x` and 404 every call. `/alerts` and `/video-summarization` strip,
exactly like the prefixes they alias. `/vios/storage` also inherits the same
HEAD/OPTIONS range short-circuits `/vst/storage` gets, so the alias cannot
answer a range request differently from the prefix it stands in for.

`/vios` has no counterpart to `bk_vst_prefixed_compat`, the backend that
repairs legacy media URLs embedding `host:port` ahead of `/vst`. Nothing emits
such a URL under a prefix introduced here.

**Deprecation window.** `/vst`, `/alert-bridge` and `/lvs` remain supported
for the whole of 3.3.x and are removed no earlier than 3.4.0 — one full minor
release of overlap, so callers can move on their own schedule. Removal is a
separate ticket that retires the old prefix in Docker, Helm and the CLI
together, and it should not start until the aliases have soaked.

**The soak is measured, not guessed.** A response on a legacy prefix carries an
RFC 9745 / RFC 8594 deprecation signal, so the question "is anything still
calling `/vst`?" is answered by proxy logs and client warnings instead of an
assumption:

| Header | Value | Notes |
|--------|-------|-------|
| `Deprecation` | `true` | On `/vst`, `/alert-bridge`, `/lvs` only |
| `Link` | `</vios>; rel="successor-version"` | Per prefix: `/vios`, `/alerts` or `/video-summarization`, so a client can migrate itself |
| `Sunset` | operator-supplied HTTP-date | **Absent by default** |

`Sunset` takes an HTTP-date and 3.4.0 has no release date, so it is emitted
only when `VSS_GATEWAY_LEGACY_SUNSET` is set — a guessed date is worse than
none, because clients automate against this header. Set it in your profile's
env once the date is known:

```bash
VSS_GATEWAY_LEGACY_SUNSET="Wed, 01 Jul 2026 00:00:00 GMT"
```

Leaving it empty (`VSS_GATEWAY_LEGACY_SUNSET=`) is the same as not setting it:
the header is simply absent. Unset it rather than blanking it if you prefer,
but neither form can take the gateway down.

Nothing but the headers changes: same status, body, routing and timeouts, so a
client that works today keeps working. The aliases emit nothing, and neither
does `/storage` nor the `host:port` `/vst` compat route — both rewrite into
VST's namespace, but they are shims for URLs the product already emitted, with
no removal planned.

**Where the signal is silent.** Three gaps, none of them a defect, all of them
worth knowing before you read a soak number as a fact:

- **`HEAD`/`OPTIONS` on `/vst/storage`.** These are answered inside HAProxy by
  an `http-request return` short-circuit, which never reaches the
  `http-response` rules. Range probes on media carry no header.
- **HAProxy-synthesized `503`s.** When no backend is available HAProxy
  generates the response itself, so again no `http-response` rule runs.
  Measured on the alerts profile, where LVS is not deployed: `/lvs/v1/live`
  returns a bare `503` with no `Deprecation` and no `Link`. The consequence
  matters more than the mechanism — **a legacy-prefix call to a down or
  undeployed service is invisible to header-based measurement**, so absence of
  the header is not evidence of absence of legacy traffic. Closing this would
  mean `http-after-response`, which reaches synthesized responses but also
  stamps error pages; that trade has not been made.
- **Kubernetes.** No equivalent at all (below).

Kubernetes does not carry this signal. The HAProxy ingress controller can set
response headers per-ingress, but the legacy and alias paths for a backend live
in the **same** Ingress object in every chart here, so an annotation would
stamp the successor prefix too — the one thing the signal must not do. Splitting
each chart's Ingress in two to express it would change the rendered object
graph for a telemetry nicety. Docker gets the header; Helm gets the same
routing without it, and the deprecation window is documented rather than
advertised at the edge.

The CLI still probes and records the **old** prefixes. `vss configure` is
deliberately left on `/vst` and `/lvs`: pointing it at an unsoaked alias would
couple every CLI invocation to a route with no field exposure, for no gain,
since both resolve to the same backend. Moving it is a follow-up, and it wants
its own change so a regression there is attributable.

That decision has a consequence for the soak numbers. Every media URL `vss`
mints is on `/vst/storage`, so **the CLI is itself a significant legacy-prefix
caller** and its responses carry `Deprecation: true`. Nothing is wrong here —
it follows directly from leaving the CLI on the stable prefixes — but a large
share of legacy traffic during the window will be self-generated, and that has
to be subtracted before the totals are read as evidence about third-party
callers.

### Elasticsearch through the gateway

`/elasticsearch` is a **narrow** mount, not a general-purpose ES proxy. The
frontend allows `GET`/`HEAD`/`POST`/`OPTIONS`, answers `403` on
`_cluster`/`_nodes`/`_snapshot`/`_security`/`_settings`/`_shutdown`/`_license`
and on `<index>/_bulk`, `_update`, `_delete_by_query`, `_forcemerge`, `_close`
and `_open`, and permits exactly one `PUT` — `vss-memory[-suffix]/_doc/<id>`,
for unified memory. That is the query surface the agent and the `vss` CLI need,
and widening it would turn an ingress-exposed route into unauthenticated cluster
administration.

So which ES clients ride the gateway is decided by that ACL, not by preference:

| ES client | Routing | Why |
|---|---|---|
| `vss-agent` (search, embed, critic, memory, `es_caption`) | gateway `/elasticsearch` | Query paths plus the one permitted memory `PUT`. |
| `vss-va-mcp` (`video_analytics.es_url`) | gateway `/elasticsearch` | `_search` only. |
| `vss` CLI | gateway `/elasticsearch` | Same query surface, from outside. |
| Kibana | direct `elasticsearch:9200` | It does honor a path in `elasticsearch.hosts`, but its startup node/version probe is `GET _nodes/_all/_none`, which the mount answers `403`: measured against this route, Kibana logs "Unable to retrieve version information from Elasticsearch nodes" and retries forever without becoming ready. It also needs `_cluster`, `_security` and `PUT`/`DELETE` on its own `.kibana*` indices. `kibana.yml` is mounted verbatim with no variable substitution besides. |
| `vss-video-analytics-api` | direct `elasticsearch:9200` | A read-write client: `indices.putIndexTemplate`, `index()` with an id (`PUT`), and `bulk()` on `<index>/_bulk` are 405/403 through the mount. Its JSON config is mounted verbatim, so it has no substitution hook either. |
| `alert-bridge` / `vlm-as-verifier` | direct `elasticsearch:9200` | Writes `mdx-vlm-incidents` / `mdx-vlm-alerts` and creates its own `ab-*` indices. `source_elasticsearch.py` also takes host and port separately and cannot express a path prefix. |
| Logstash pipelines | direct `elasticsearch:9200` | Ingest data plane: ILM and index-template `PUT`s, plus bulk indexing. Not something to put behind an HTTP edge proxy. |
| `elasticsearch-init-container`, `kibana-import-dashboard.sh` | direct `elasticsearch:9200` | Bootstrap: they create the ILM policies, templates and pipelines with `PUT`, and they run before and independently of HAProxy. |
| LVS (`lvs-server`) | direct `ES_HOST` / `ES_PORT` | Takes host and port as separate settings, so it cannot express `origin + /elasticsearch` without a code change. |
| VST / nvstreamer `video_metadata_server` | direct `elasticsearch:9200/mdx-*` | Not a base URL — the setting embeds an index pattern in the host string. |

Every entry left direct is either denied by the ACL above, unable to express a
path prefix, or part of the bootstrap that has to run before the proxy. Moving
any of them means changing the client, not the route.

Create writable host directories for the bind-mounted infrastructure volumes
before starting a direct Compose stack:

```bash
export VSS_DATA_DIR=/path/to/vss-apps-data

mkdir -p \
  "$VSS_DATA_DIR/data_log/elastic/data" \
  "$VSS_DATA_DIR/data_log/elastic/logs" \
  "$VSS_DATA_DIR/data_log/kafka" \
  "$VSS_DATA_DIR/data_log/redis/data" \
  "$VSS_DATA_DIR/data_log/redis/log"

chmod -R 777 "$VSS_DATA_DIR/data_log"
```

The root compose maps Elasticsearch data/log volumes to
`$VSS_DATA_DIR/data_log/elastic/{data,logs}`, Kafka data to
`$VSS_DATA_DIR/data_log/kafka`, and Redis data/logs to
`$VSS_DATA_DIR/data_log/redis`. Missing or non-writable host directories can cause
startup failures such as Kafka being unable to write `/tmp/kafka-data/cluster_id` or
Elasticsearch being unable to open `gc.log`.

### TURN / WebRTC relay

The warehouse VST UI uses WebRTC for live playback. When VST containers run on the Compose bridge network, browsers cannot reach Docker-only media candidates directly, so `services/infra/compose.yml` includes a coturn-based `turnserver` service for warehouse profiles. It exposes the TURN listener and relay range on the host. Developer profiles do not start this TURN service.

Default ports:

| Variable | Default | Purpose |
|----------|---------|---------|
| `TURN_HOST_PORT` / `TURN_PORT` | `3478` | TURN UDP/TCP listener |
| `TURN_MIN_RELAY_HOST_PORT` / `TURN_MAX_RELAY_HOST_PORT` | `49160` / `49200` | Host relay port range |
| `TURN_MIN_RELAY_PORT` / `TURN_MAX_RELAY_PORT` | `49160` / `49200` | Container relay port range |

Set `TURN_PUBLIC_HOST` to the DNS name or IP address that browser clients use to reach the deployment, and set `TURN_EXTERNAL_IP` to the host IP coturn should advertise. The warehouse profile uses a non-secret default `TURN_USERNAME` and starts a `turnserver-init` job that generates a random password once in the `vss-turn-password` Docker volume. Coturn and VST mount that same generated file; the VST startup helper derives the static TURN URL in the format `user:password@host:port` from `TURN_USERNAME`, the generated password file, `TURN_PUBLIC_HOST`, and `TURN_HOST_PORT`.

For the bundled turnserver, leave `VST_STATIC_TURNURL_LIST` empty:

```env
TURN_HOST_PORT=3478
TURN_PORT=3478
TURN_USERNAME=vss
TURN_PASSWORD_BYTES=32
VST_STATIC_TURNURL_LIST=
```

Remove the Compose-created `vss-turn-password` Docker volume and restart the warehouse profile to rotate the generated password. Only set `VST_STATIC_TURNURL_LIST` for external or multiple TURN endpoints; treat it as sensitive because it embeds TURN credentials.

The warehouse VST streamprocessing startup helper also forces `network.use_coturn_auth_secret=false` and `network.coturn_turnurl_list_with_secret=[]`, matching the static username/password mode. Developer VST streamprocessing and NvStreamer services do not apply this WebRTC/TURN patch.

### LVS Compose notes

Docker Compose does not use Kubernetes secrets or the NIM Operator. For the LVS profile, local model bring-up uses the **`NGC_CLI_API_KEY`** environment variable directly for image pulls and NIM/RT-VLM model access.

Default LVS model wiring:

| Component | Local Compose behavior | Default model name |
|-----------|------------------------|--------------------|
| LLM | Starts the **`nvidia-nemotron-nano-9b-v2`** NIM container on **`LLM_PORT=30081`** when `LLM_MODE` is `local` or `local_shared`. | `nvidia/nvidia-nemotron-nano-9b-v2` |
| VLM / RT-VLM | Starts **`rtvi-vlm`** on **`RTVI_VLM_PORT=8018`**. The LVS profile sets **`VLM_NAME_SLUG=none`**, so Compose does not start a separate Cosmos VLM NIM by default; RT-VLM loads the integrated checkpoint. | `nim_nvidia_cosmos3-nano-reasoner_bf16-final` |

For external endpoints, use the helper flags instead of editing Compose files directly:

```bash
export LLM_ENDPOINT_URL='<REMOTE LLM SERVICE ROOT, no trailing /v1>'
export VLM_ENDPOINT_URL='<REMOTE VLM SERVICE ROOT, no trailing /v1>'

./deploy/docker/scripts/dev-profile.sh up \
  --profile lvs \
  --hardware-profile H100 \
  --use-remote-llm \
  --use-remote-vlm \
  --llm nvidia/nvidia-nemotron-nano-9b-v2 \
  --vlm nim_nvidia_cosmos3-nano-reasoner_bf16-final
```

The helper probes **`${LLM_ENDPOINT_URL}/v1/models`** and **`${VLM_ENDPOINT_URL}/v1/models`**, and the agent config appends **`/v1`** to **`LLM_BASE_URL`** / **`VLM_BASE_URL`**. Do not include **`/v1`** in the endpoint environment variables.

Post-deploy checks for the default local LVS ports:

```bash
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
curl -f http://127.0.0.1:38111/v1/ready
curl -f http://127.0.0.1:8018/v1/health/ready
curl -f http://127.0.0.1:30081/v1/health/ready
curl -f http://127.0.0.1:38111/models
curl -f http://127.0.0.1:30081/v1/models
```

If a local NIM container keeps restarting and logs include **`No available memory for the cache blocks`**, reduce the NIM max model length and/or sequence count for the active hardware profile. One non-destructive way is to pass an override env file through **`--llm-env-file`**:

```env
# /tmp/lvs-nim-low-memory.env
NIM_MAX_MODEL_LEN=65536
NIM_MAX_NUM_SEQS=2
```

```bash
./deploy/docker/scripts/dev-profile.sh up \
  --profile lvs \
  --hardware-profile RTXPRO6000BW \
  --llm-env-file /tmp/lvs-nim-low-memory.env
```

Those numeric values are only an example shape for reducing cache pressure; validate the final values on your GPU and workload.

---

## Warehouse industry profile

The **warehouse** blueprint is driven by
**`industry-profiles/warehouse-operations/`** and is started with direct Docker
Compose from **`deploy/docker`**.

1. **Model and app-data inputs**

Warehouse uses two acquisition paths:

- The `vss-warehouse-app-data` NGC resource remains the source for videos, playback, and calibration data.
- Each RT-CV profile mounts its `models-download.json` on perception and downloads versioned NGC model packages into the flattened `$VSS_DATA_DIR/models/` tree during `ds-start` phase 0.

Download and extract the warehouse app data:

```bash
ngc \
   registry \
   resource \
   download-version \
   nvidia/vss-warehouse/vss-warehouse-app-data:3.2.0

# OR manually download the tar file from NGC:
# https://catalog.ngc.nvidia.com/orgs/nvidia/teams/vss-warehouse/resources/vss-warehouse-app-data?version=3.2.0

cd vss-warehouse-app-data_v3.2.0
tar -xvf vss-warehouse-app-data.tar.gz

# Prepare the writable model destination used by ds-start phase-0 download

sudo mkdir -p /path/to/vss-warehouse-app-data/models
sudo chmod 0777 /path/to/vss-warehouse-app-data/models

# This is the path to the data directory. It is set in the industry-profiles/warehouse-operations/.env file for VSS_DATA_DIR.
#VSS_DATA_DIR="/path/to/vss-warehouse-app-data"
```

2. **Edit deployment overrides**

Keep stable profile defaults in
**`industry-profiles/warehouse-operations/.env`**. Update
**`industry-profiles/warehouse-operations/overrides.env`** for the target
machine and selected warehouse scenario:

- **`VSS_APPS_DIR`**: absolute path to this repository's `deploy/docker` directory
- **`VSS_DATA_DIR`**: extracted warehouse app data directory
- **`HOST_IP`** / **`EXTERNAL_IP`**: host address and externally reachable address
- **`NGC_CLI_API_KEY`**: an NGC key with access to the RT-DETR warehouse, Sparse4D, and BodyPose3DNet model packages required by the selected mode; also **`NVIDIA_API_KEY`**, **`OPENAI_API_KEY`** as needed
- **`MODE`**: `2d`, `3d`, or `mv3dt`
- **`BP_PROFILE`**: `bp_wh`, `bp_wh_kafka`, `bp_wh_redis`, or `bp_wh_auto_calib`
- **`HARDWARE_PROFILE`**, model settings, public ingress settings, and host-published ports
- **`COMPOSE_PROFILES`**: one of the warehouse or playback profile lists defined in `overrides.env`

`bp_wh` is valid only with `MODE=2d`. For `MODE=3d` or `MODE=mv3dt`, use
`bp_wh_kafka`, `bp_wh_redis`, or `bp_wh_auto_calib`. Keep `MODE`,
`BP_PROFILE`, `STREAM_TYPE`, sample dataset settings, and `COMPOSE_PROFILES`
aligned with the comments in `overrides.env`.

   Model destinations are shared across profiles: RT-DETR is stored at `models/rtdetr_warehouse_v1.0.2.fp16.onnx`, Sparse4D at `models/sparse4d/sparse4d_warehouse_v2.2.onnx`, and BodyPose3DNet at `models/BodyPose3DNet/bodypose3dnet_accuracy.onnx`.

3. **Start the stack**

```bash
cd /path/to/video-search-and-summarization/deploy/docker

docker compose -f compose.yml \
  --env-file containers.env \
  --env-file industry-profiles/warehouse-operations/.env \
  --env-file industry-profiles/warehouse-operations/overrides.env \
  up --detach --pull always --force-recreate --build
```

4. **Stop the stack**

```bash
docker compose -f compose.yml \
  --env-file containers.env \
  --env-file industry-profiles/warehouse-operations/.env \
  --env-file industry-profiles/warehouse-operations/overrides.env \
  down -v --remove-orphans
```

5. **Data / backup cleanup**

To reset **`data_log`** volumes, calibration/VST data, and
blueprint-configurator backups in a way that matches how you deployed, use
**`deploy/docker/scripts/cleanup_all_datalog.sh`**. Pass the same final env
overlay used for direct Compose:

```bash
bash scripts/cleanup_all_datalog.sh -e industry-profiles/warehouse-operations/overrides.env
```

Compose profiles for warehouse slices are defined in
**`industry-profiles/warehouse-operations/overrides.env`** and selected by
`COMPOSE_PROFILES`.

---

## Requirements

- **Docker** and **Docker Compose** (Compose v2: `docker compose`)
- **bash** (for **`dev-profile.sh`** and cleanup scripts)
- **NVIDIA GPU driver** on the host, at a version supported by your hardware and by the GPU containers you run (see NVIDIA release notes for CUDA / NIM images). Check with **`nvidia-smi`** before starting stacks that use GPUs.
- **NVIDIA Container Toolkit** (nvidia-docker) so containers can access the GPU; required alongside the driver for GPU-backed Compose services.
- Valid **NGC** credentials where images or NIMs require **`NGC_CLI_API_KEY`**


---
