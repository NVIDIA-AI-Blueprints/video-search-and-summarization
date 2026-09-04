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
- **MV3DT is the documented exception** and keeps its dedicated `ds-start-mv3dt.sh` command override, invoked with `--tracker-reid` since this mode runs appearance-based ReID.
- Model acquisition for **developer profiles** (alerts, search) and **warehouse RT-CV profiles** (2D, 3D, MV3DT) runs as phase 0 of the perception startup script (`ds-start.sh` / MV3DT `ds-start-mv3dt.sh`) when a per-profile `models-download.json` is mounted. There is no separate download init service for detection/pose models. Warehouse still uses the pre-extracted `VSS_DATA_DIR` bundle for videos, playback, and calibration (see the warehouse section below).
- **ReID/embedding assets are the exception to that rule.** MV3DT runs a one-shot `vss-reid-embed-init-mv3dt` container that populates `$VSS_DATA_DIR/models/reid` (CLIP-ReID tracker ONNX + SigLIP2) before the ReID service and perception start. See the warehouse MV3DT reference for the full pipeline.

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

### The scheme the caller used

Most of the contract needs no absolute URL: the ingresses run with
`absolute_redirect off`, so their `Location` headers are relative and correct on
whatever origin the caller arrived on. The agent is the exception. Starlette
builds the trailing-slash redirect on `/static` out of the request itself, so it
has to know the scheme — and where TLS terminates outside the deployment, the
request that reaches HAProxy is plain HTTP. Emitting `http://` there is mixed
content on an HTTPS page and the browser blocks it.

HAProxy already tells the agent, with a normalised `X-Forwarded-Proto`. Whether
the agent believes it is a separate decision, because uvicorn honours forwarded
headers only from peers listed in `FORWARDED_ALLOW_IPS` — and its default,
`127.0.0.1`, does not include the bridge address HAProxy connects from. Left
alone, the header is silently discarded.

| Variable | Default | What it is |
|---|---|---|
| `VSS_AGENT_FORWARDED_ALLOW_IPS` | `127.0.0.1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16` | Peers whose `X-Forwarded-Proto` the agent honours. |

The default is the RFC1918 space Docker's address pools are drawn from, not one
subnet, because the bridge subnet is assigned by the daemon and differs between
hosts — a single CIDR would be right here and wrong on the next box, and the
symptom of getting it wrong is the redirect quietly going back to `http`. Public
peers are never trusted. **Only addresses and CIDRs work.** uvicorn compares
this against the peer's numeric address, so a container name is accepted, never
matches, and behaves exactly as if the variable were unset.

Trusting a peer also means the agent takes its `X-Forwarded-For` as the client
address in the access log, in place of the gateway's own. That is the intended
gain — before this the log recorded HAProxy for every request — but it is why
the set is worth scoping. Narrow it to the bridge's own CIDR where you know it,
and narrow it if you publish the agent's port somewhere a caller can reach it
without going through the gateway: such a caller can state its own scheme and
client address. Nothing in the agent authorises on either, so the exposure is
the access log and the URLs minted back to that caller, but it is real.

A remote agent (below) is not behind this gateway, so its peers are whatever
reaches it directly. Set the variable to the reverse proxy in front of it, or to
`127.0.0.1` to honour nothing.

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

# The agent image is distroless: no shell, no curl, no getent. Use the same
# interpreter its own healthcheck uses, so the probe runs in the real agent
# container rather than a stand-in.
docker exec vss-agent /usr/local/bin/python3 -c \
  "import urllib.request as u; r=u.urlopen('http://vss.local:7777/va-mcp/health', timeout=10); print(r.status, r.read().decode()[:200])"
docker exec vss-agent /usr/local/bin/python3 -c \
  "import urllib.request as u; r=u.urlopen('http://vss.local:7777/elasticsearch/', timeout=10); print(r.status, r.read().decode()[:200])"
```

Those two prove the rewrite, not just the route: `/va-mcp/health` must return the
MCP server's own health body and `/elasticsearch/` the cluster banner. A 404
means HAProxy matched the path but rewrote it into something the backend does
not serve.

For an interactive shell against the same routes — or to check DNS, which the
agent has no tool for — attach a throwaway client to the deployment network
instead of trying to get one out of the agent:

```bash
docker run --rm --network "$(docker inspect vss-agent \
  --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}}{{end}}')" \
  curlimages/curl:latest -fsS http://vss.local:7777/va-mcp/health
```

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

# Resolve the gateway host only when it is a name -- that is the check that
# catches a missing /etc/hosts entry or private zone. On an IP literal getent
# does a reverse lookup, which says nothing about reachability and normally
# finds nothing, so running it there reports a failure that is not one.
case "${VSS_GATEWAY_HOST}" in
  *[a-zA-Z]*) getent hosts "${VSS_GATEWAY_HOST}" ;;
esac

curl -fsS "${VSS_GATEWAY_ORIGIN}/va-mcp/health"
curl -fsS "${VSS_GATEWAY_ORIGIN}/vst/api/v1/sensor/streams"
curl -fsS "${VSS_GATEWAY_ORIGIN}/elasticsearch/"
```

### The origin callers use has to be declared

The Host ACLs are an allowlist, so the gateway answers only for origins the
deployment was told about: `VSS_PUBLIC_HOST`, `VSS_GATEWAY_HOST`, `HOST_IP`,
`EXTERNAL_IP`, `localhost` and `127.0.0.1`, each with and without the port.
**Reaching a deployment by any other name returns 404 on every path**, however
correct the route is — the common cases being a public DNS record, a client-side
`/etc/hosts` alias, and a Brev secure link.

The match is exact, and the port is part of the identity rather than an
afterthought. Each host value is paired with one specific port: `VSS_PUBLIC_HOST`
with `VSS_PUBLIC_PORT`, `VSS_GATEWAY_HOST` with `VSS_GATEWAY_PORT`, and
`HOST_IP` / `EXTERNAL_IP` / `localhost` / `127.0.0.1` with `HAPROXY_PORT`. So a
name declared for a TLS terminator on 443 is *not* admitted on 7777, and neither
a suffix nor a prefix of a declared name is admitted at all —
`example.com.evil`, `evil-example.com` and `example.com:9999` are each refused
alongside any other undeclared origin.

Set `VSS_PUBLIC_HOST` to the hostname callers use and recreate
`vss-haproxy-ingress`. The IP entries stay valid alongside it, so declaring a
name does not cost you the address:

```bash
curl -fsS -o /dev/null -w '%{http_code}\n' "https://${PUBLIC_NAME}/va-mcp/health"
```

An unrecognised Host is answered with `x-vss-gateway-deny: unknown-host` and a
plain-text body naming this setting, which is what separates it from a 404 for a
path no service mounts. Check that header before looking for a routing bug.

A **503 with no `x-vss-gateway-deny` header** on one origin while the others
still work is a different fault and is worth naming, because it looks like a
dead backend and is not. The origins are declared twice in
`services/infra/haproxy/haproxy.cfg.template` — once as `known_host`, which
gates the deny, and once as `h_main`, which gates every `use_backend`. An origin
added to the first but not the second is admitted and then routed nowhere.
`.github/scripts/check_gateway_host_acls.py` fails CI on that divergence.

#### Do you have to configure `HOST_IP` or `EXTERNAL_IP`?

For the Host allowlist: **no, and not by hand in any supported topology.** Both
`dev-profile.sh` and `blueprint-deploy.sh` derive `HOST_IP` from
`ip route get 1.1.1.1` and write it into `generated.env` on every run, and the
profiles default `EXTERNAL_IP="${HOST_IP}"`. What a non-default topology may
still need is `VSS_PUBLIC_HOST`.

| Topology | `HOST_IP` | `EXTERNAL_IP` | What the operator sets |
|---|---|---|---|
| Single host, reached by its own address | auto-derived, leave it | auto (`= HOST_IP`) | nothing |
| Reached by a DNS name or a client-side `/etc/hosts` alias | auto-derived, leave it | not needed | `VSS_PUBLIC_HOST` = the name |
| TLS terminated outside the stack (Brev secure link) | auto-derived, leave it | not needed | `VSS_PUBLIC_HOST`/`_PORT`/`_HTTP_PROTOCOL`, which `dev-profile.sh` fills in from `BREV_ENV_ID` |
| Remote agent off-host (`compose.remote-agent.yml`) | auto-derived on the deployment | not needed | `VSS_GATEWAY_ORIGIN` on the agent host, naming an origin the deployment already declares |

Two things that follow from the table and are easy to get wrong:

- `EXTERNAL_IP` earns its keep in exactly one case: the box has an external
  address that differs from the one on its interfaces, callers use that address
  rather than a name, **and** `VSS_PUBLIC_PORT` is not the listener port — as it
  is not behind a terminator on 443. Then `EXTERNAL_IP` is what admits the raw
  address on `HAPROXY_PORT`, because `VSS_PUBLIC_HOST` is paired with 443.
  Whenever the public origin is already the address callers use, it is a
  duplicate of the `VSS_PUBLIC_HOST` entry and changes nothing.
- Pointing `VSS_PUBLIC_HOST` at a name does not retire the address, but only
  because `HOST_IP` is still set. In a hand-written env file that leaves
  `HOST_IP` at its `<HOST_IP>` placeholder, declaring a name **does** cost you
  the address: the box's own IP starts answering 404 `unknown-host` while the
  name works.

Leave both variables non-empty. An empty value is worse than the placeholder,
and worse than it looks: HAProxy refuses to parse an empty quoted argument, so
blanking **either** variable — or leaving it unset — stops the gateway starting
at all rather than merely dropping one origin from the allowlist:

```text
[ALERT] config : parsing [haproxy.cfg:184]: argument number 4 at position 44
        is empty and marks the end of the argument list
```

Which is why `HOST_IP='<HOST_IP>'` in the profile is load-bearing rather than
untidy: four of the seven allowlist variables have no Compose default, and that
placeholder is what the rest of the chain resolves to before `dev-profile.sh`
fills in a real address. `check_gateway_host_acls.py` fails CI if any of them
loses its guaranteed value. In profiles that include the SDRC services Compose
catches it first, with `HOST_IP must be set in .env or shell before running
compose`.

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
| `Deprecation` | `@1787702400` | On `/vst`, `/alert-bridge`, `/lvs` only |
| `Link` | `</vios>; rel="successor-version"` | Per prefix: `/vios`, `/alerts` or `/video-summarization`, so a client can migrate itself |
| `Sunset` | operator-supplied HTTP-date | **Absent by default** |

`Deprecation` carries a date, not a boolean. RFC 9745 defines the field as an
Item Structured Field whose value MUST be an RFC 9651 `Date` — `@` followed by
seconds since the epoch — and a conformant parser discards a value it cannot
parse, so the `true` of the withdrawn draft would have hidden the signal from
precisely the clients that implement the spec. `@1787702400` is
2026-08-26T00:00:00Z, the date these prefixes became deprecated. Unlike
`Sunset` that is a known fact rather than a forecast.

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
caller** and its `GET` responses carry `Deprecation: @1787702400`. Nothing is
wrong here — it follows directly from leaving the CLI on the stable prefixes —
but a large share of legacy traffic during the window will be self-generated,
and that has to be subtracted before the totals are read as evidence about
third-party callers.

### A 503 from the gateway is not the same as a 503 from a service

No profile deploys everything. Before the gateway, a caller learned that for
free: it addressed `http://vss-rtvi-cv:9000`, and a service the profile did not
deploy had nothing listening, so the connection was **refused**. Only a service
that was actually running could answer `503` at all. Absent and unwell were
different events at the transport layer, and code that treats a service as
optional relied on it.

Through one origin the connection always succeeds — to HAProxy — so both
arrive as `503 http://vss.local:7777/…`. The gateway therefore says which it
is, because it is the only party that still knows:

| What happened | Status | `x-vss-gateway-unavailable` | What a caller should do |
|---|---|---|---|
| No server is up for the route's backend — the service is not in this profile, or has not started | `503` | **present**, naming the route | Treat the service as absent. Skip it if it is optional to you. |
| The service answered `503` itself — overloaded, warming up, a dependency down | `503` | **absent** | Real failure. Do not skip it. |
| The `Host` is not one of the deployment's origins | `404` | — (`x-vss-gateway-deny: unknown-host`) | Add the hostname to `VSS_PUBLIC_HOST`. |

The marked reply is synthesised by the gateway: the request is never forwarded,
so the service cannot have produced it. Backend-origin responses have the
header stripped, so a live service cannot claim to be absent. **Presence of the
header is the contract** — the value names the route for logs, and a caller that
switched on it would need editing every time a mount is added.

Marked routes: `/video-analytics-api`, `/alert-bridge`, `/alerts`, `/kibana`,
`/elasticsearch`, `/rtvi-embed`, `/rtvi-cv`, `/rtvi-vlm`, `/lvs`,
`/video-summarization`, `/phoenix`, `/behavior-analytics`, `/perception-sdr`,
`/va-mcp`. The UI, agent and VST/VIOS routes are not marked: nothing treats the
deployment's own front door or its media plane as optional, and their path
prefixes overlap (`/api` inside `/api/chat`, `/vst/api/v1/storage` inside
`/vst`), so a marker keyed on one would misfire on its neighbour.

A service that is deployed but wholly down — crash-looping, or still starting —
has no usable server either, so it is reported **absent**. That is not new: it
refused the connection and read as absent under direct addressing too. This
restores the old signal, it does not widen it.

Reading it, in the agent: `vss_agents/utils/gateway.py`
(`gateway_reports_service_absent`). `.github/scripts/check_gateway_optional_backends.py`
fails CI if a route loses its marker, if a marker names the wrong backend, or if
the header name drifts between the template and the agent.

### Elasticsearch through the gateway

`/elasticsearch` is a **narrow** mount, not a general-purpose ES proxy. The
frontend carries an **allowlist** of `(method, path)` pairs and denies
everything else — `403` when the verb is one the route serves but the operation
is not listed, `405` for a verb the route never serves. What is allowed:

| Method | Paths |
|---|---|
| `GET`, `HEAD` | `/`, `_cat/*`, `_cluster/health`, `<index>`, `<index>/_mapping`, `_settings`, `_alias`, `_doc/<id>`, `_source/<id>` |
| `GET`, `HEAD`, `POST` | `_search`, `_msearch`, `_count`, `_mget`, `_field_caps` — cluster-wide and per-index — plus per-index `_validate/query`, `_terms_enum`, `_explain/<id>`, `_termvectors/<id>` |
| `PUT` | `vss-memory[-suffix]/_doc/<id>` and nothing else, for unified memory |
| `OPTIONS` | any path on the mount (Elasticsearch answers preflight with no data) |

`POST` is served **only** because the query endpoints put their query in the
request body: `POST /<index>/_search` and `POST /_msearch` are how every real
caller searches, and a blanket `POST` denial would break search outright. It is
not a general write grant — `_bulk`, `_reindex`, `_scripts/<id>`,
`<index>/_doc`, `_aliases`, `_clone`/`_split`/`_shrink`/`_rollover` and
`_tasks/<id>/_cancel` are all denied.

An allowlist rather than a denylist of dangerous endpoints, because a denylist
cannot be finished. The revision this replaced named `_bulk`, `_update`,
`_close` and the cluster-admin prefixes, and left `POST` open everywhere else;
measured against a live 9.4.4 deployment that still reached Elasticsearch on
`_reindex`, `_scripts/<id>`, `_aliases` (whose `remove_index` action deletes an
index) and `<index>/_doc`. Elasticsearch here runs with
`xpack.security.enabled: false`, so anything this route forwards executes
unauthenticated, and every ES release adds endpoints the denylist would not have
heard of. Adding a query endpoint is one line; missing a destructive one was
silent.

> **The gateway is not the only path to Elasticsearch.** `services/infra/compose.yml`
> also publishes the cluster itself, on
> `${ELASTICSEARCH_HOST_BIND:-127.0.0.1}:${ELASTICSEARCH_HOST_PORT:-9200}`. The
> bind defaults to loopback because a published 9200 answers unauthenticated to
> anything that can route to the host, regardless of this ACL. Reach the cluster
> from another machine through `/elasticsearch`; set
> `ELASTICSEARCH_HOST_BIND=0.0.0.0` only deliberately, and never from a profile —
> that widens the exposure for every deployment of it. Nothing inside the
> deployment should depend on that publish: an in-network client uses the service
> name, which the bind does not affect. `check_loopback_host_publishes.py` holds
> both halves of that.

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
| LLM | Starts the **`nemotron-3.5-lightning-30b-a3b`** NIM container on **`LLM_PORT=30081`** when `LLM_MODE` is `local` or `local_shared`. | `nvidia/nemotron-3.5-lightning-30b-a3b` |
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
  --llm nvidia/nemotron-3.5-lightning-30b-a3b \
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
