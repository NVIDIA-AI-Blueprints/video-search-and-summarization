# Brev Environment Reference

How to deploy VSS on a Brev GPU instance so the UI and API are reachable
from a browser via Brev **secure links** (a Cloudflare-fronted reverse proxy).

This reference derives from `deploy/docker/scripts/deploy_vss_launchable.ipynb`, which is the
interactive reference implementation.

## When this applies

A Brev-managed instance sets `BREV_ENV_ID=<instance-id>` in `/etc/environment`.
If that file doesn't contain `BREV_ENV_ID`, you're not on a Brev-provisioned
instance and this reference doesn't apply — use the normal host IP + port
access pattern from `base.md`.

## Architecture

The deployment has **two origins** and they are not interchangeable:

```
Browser  ──https:443──>  <prefix>-<BREV_ENV_ID>.<brev-domain>  (Cloudflare Access)
                             │           the PUBLIC origin: VSS_PUBLIC_*
                             ▼
                   Brev network tunnel        (TLS terminates HERE, outside VSS)
                             │
                             ▼
              vss-haproxy-ingress :7777 on the instance
                    ▲        │
   http://vss.local:7777     │        the GATEWAY origin: VSS_GATEWAY_ORIGIN,
   in-deployment callers     │        plain HTTP, never https and never :443
                             │
           ┌─────────────────┼─────────────────┐
           ▼                 ▼                 ▼
        UI :3000      Agent API :8000     VST :30888
```

`VSS_PUBLIC_*` describes what the **browser** uses and is what report links,
media URLs and `vss configure --base-url` are built from. `VSS_GATEWAY_ORIGIN`
is HAProxy's own listener and is what containers call each other on. Do not set
the gateway origin from the public one: `https://vss.local:443` is a listener
that does not exist.

## Resolving a secure link

**A secure link cannot be constructed.** Its label, its domain and the host port
it forwards to are three independent facts chosen when the environment is
created, and none of them follows from `BREV_ENV_ID`. A real instance serves
`jupyter-<id>.gobrev.dev` on 443 forwarding to host port 8888: the label is a
name rather than a port, and the domain is one no template would have offered.

Read the link out of the environment context file, which is the only
authoritative record of what Brev published for this instance. These helpers are
used throughout this reference; define them once per shell:

```bash
BREV_CTX="${BREV_ENVIRONMENT_CONTEXT_PATH:-/etc/brev/environment-context.json}"
# FQDN published for a destination port; empty when the port is not exposed.
brev_fqdn() { jq -er --argjson p "$1" '[.ports[]? | select(.destination_port == $p) | .fqdn][0] // empty' "$BREV_CTX" 2>/dev/null; }
# Public port for that link (443 in current Brev; read it, don't assume it).
brev_public_port() { jq -er --argjson p "$1" '[.ports[]? | select(.destination_port == $p) | .public_port][0] // empty' "$BREV_CTX" 2>/dev/null; }
# Browse origin for that port. Use this for any URL you hand the user or curl:
# an origin built from the FQDN alone is wrong wherever public_port is not 443.
brev_origin() {
  local host port
  host="$(brev_fqdn "$1")" && port="$(brev_public_port "$1")" || return 1
  if [ "$port" = 443 ]; then echo "https://${host}"; else echo "https://${host}:${port}"; fi
}

# Every HTTP link on the instance, so you can see which host ports are reachable.
jq -r '.ports[]? | select(.destination_port != 22) | "\(.public_port) -> \(.destination_port)  \(.fqdn)"' "$BREV_CTX"
```

`brev_fqdn 7777` asks for the link to the HAProxy ingress' default host port. An
empty result means **no link is published for it**, and Brev does not open ports
on demand — so either expose one, or publish the gateway on a host port that
already has a link (`HAPROXY_HOST_PORT`). Do not fall back to constructing a
hostname: `VSS_PUBLIC_HOST` feeds the gateway's Host **allowlist**, so a guessed
value is refused with `x-vss-gateway-deny: unknown-host` rather than degrading.

## Per-profile secure link requirements

Everything HAProxy routes is reachable through the **single 7777 link** — that
is the point of the consolidated gateway. Only ports it does *not* route need
their own link.

| Profile | Required links | Optional |
|---|---|---|
| `base` | **7777** (HAProxy ingress) | — |
| `lvs` | **7777**, **31000** (nvstreamer) | — |
| `search` | **7777**, **31000** (nvstreamer) | — |
| `alerts` | **7777**, **31000** (nvstreamer) | — |

Ports that should NOT get their own secure link — HAProxy already serves them
under a path on the 7777 link, and a second link makes the browser treat them as
a separate origin (see the CORS row in [Troubleshooting](#troubleshooting)):

| Port | Service | Reachable at |
|---|---|---|
| 3000 | UI | `/` |
| 8000 | Agent API | `/api`, `/chat`, `/websocket`, `/static` |
| 30888 | VST / VIOS | `/vios` (and the legacy `/vst`) |
| 5601 | Kibana | `/kibana` |
| 6006 | Phoenix tracing | `/phoenix` |
| 9200 | Elasticsearch | `/elasticsearch` (allowlisted read operations plus the one `vss-memory*` write; writes and cluster admin are denied at the edge) |

`31000` (nvstreamer) is the exception: HAProxy has no backend for it, so a
profile that needs the nvstreamer UI still needs its own secure link.

## Setup flow

Which of the two paths below applies depends on how you deploy.

### If you deploy with `dev-profile.sh`

**Nothing to set.** `deploy/docker/scripts/dev-profile.sh` configures the whole
two-origin split itself when `BREV_ENV_ID` is in its environment. It reads the
same context file the helpers above use, takes the published link verbatim, and
writes `VSS_PUBLIC_HOST` (the link's FQDN), `VSS_PUBLIC_PORT` (its public port),
`VSS_PUBLIC_HTTP_PROTOCOL=https`, `VSS_PUBLIC_WS_PROTOCOL=wss` and
`HAPROXY_HOST_PORT` into the profile's `generated.env` — the same file Step 1c/1d
writes. The launchable notebooks call it, so they inherit this. **Do not also
apply the manual recipe below**; it would only overwrite values read from the
source of truth with a hand-built guess.

`HAPROXY_HOST_PORT` is the part that is easy to miss. Brev forwards a link only
to the host port it was created with, so the gateway has to be published on
*that* port rather than on its own default. An instance whose only HTTP link is
`443 -> 8888` gets `HAPROXY_HOST_PORT=8888`; the container keeps listening on
`HAPROXY_PORT` (`7777`), so in-deployment callers on `http://vss.local:7777` are
unaffected.

When the context file is missing, unreadable, or publishes no HTTP link,
`dev-profile.sh` **fails loudly** rather than falling back to a template, and
names the three overrides to set by hand — `VSS_PUBLIC_HOST`, `VSS_PUBLIC_PORT`
and `HAPROXY_HOST_PORT`. `VSS_PUBLIC_HOST` set in the environment always wins
outright. `BREV_LINK_PREFIX` and `BREV_LINK_DOMAIN` still work, but only as a
fallback for instances too old to have a context file.

One thing is on you: **`dev-profile.sh` reads `BREV_ENV_ID` from its own
environment, not from `/etc/environment`.** A login shell on a Brev instance has
it; a service, cron job or non-login shell may not. Export it first if in doubt:

```bash
export BREV_ENV_ID="$(awk -F= '/^BREV_ENV_ID=/ {gsub(/"/, "", $2); print $2; exit}' /etc/environment)"
```

Then check what it recorded:

```bash
grep -E '^(VSS_PUBLIC_|BREV_LINK_)' deploy/docker/developer-profiles/dev-profile-<profile>/generated.env
```

### If you drive Compose directly

Step 1c/1d of [`../SKILL.md`](../SKILL.md) copies `overrides.env` to
`generated.env` and calls Compose itself, which bypasses the block above. In
that case set the values yourself, before `docker compose up`.
**`EXTERNAL_IP` alone is not enough** — the secure link is served over **HTTPS
on 443**, but the profile `.env` ships `VSS_PUBLIC_HTTP_PROTOCOL=http`,
`VSS_PUBLIC_WS_PROTOCOL=ws` and `VSS_PUBLIC_PORT=${HAPROXY_HOST_PORT}` (7777).
Leaving those at the defaults makes the agent emit `http://…:7777` UI/API/WS
URLs from an `https://` page, which the browser blocks as mixed content. Set the
host, protocol and port together, and **read** the link rather than building one:

```bash
# Pick the host port whose link you intend to serve on. 7777 is the gateway's
# default, but only a link Brev actually published will route -- list them with
# the jq one-liner above and choose from that.
host_port=7777
host="$(brev_fqdn "${host_port}")" || { echo "no Brev link forwards to ${host_port}"; exit 1; }
public_port="$(brev_public_port "${host_port}")"

GEN=deploy/docker/developer-profiles/dev-profile-<profile>/generated.env
sed -i \
  -e "s|^EXTERNAL_IP=.*|EXTERNAL_IP=${host}|" \
  -e "s|^VSS_PUBLIC_HOST=.*|VSS_PUBLIC_HOST=${host}|" \
  -e "s|^VSS_PUBLIC_HTTP_PROTOCOL=.*|VSS_PUBLIC_HTTP_PROTOCOL=https|" \
  -e "s|^VSS_PUBLIC_WS_PROTOCOL=.*|VSS_PUBLIC_WS_PROTOCOL=wss|" \
  -e "s|^VSS_PUBLIC_PORT=.*|VSS_PUBLIC_PORT=${public_port}|" \
  -e "s|^HAPROXY_HOST_PORT=.*|HAPROXY_HOST_PORT=${host_port}|" \
  "$GEN"
```

`brev_fqdn` failing is the answer, not an obstacle to work around: a hostname
you assembled yourself will be rejected by the gateway's Host allowlist.

Leave `VSS_GATEWAY_*` alone. It is pinned to HAProxy's own listener on purpose.

## Verifying the deploy is reachable externally

After `docker compose up -d`:

```bash
# 1. The gateway is up and routing. There is NO /health route on the gateway --
#    it is a path router, not a health endpoint, and /health falls through to
#    the UI and answers 404. Probe the UI root or a service's own health path.
curl -sfo /dev/null http://localhost:7777/ && echo "gateway OK"
curl -sf http://localhost:7777/va-mcp/health   # profiles that mount va-mcp

# 2. The secure-link Host is admitted by the gateway's allowlist. This is the
#    check that catches the 404-from-the-browser case, and it works before the
#    link itself is reachable.
host=$(grep -m1 '^VSS_PUBLIC_HOST=' deploy/docker/developer-profiles/dev-profile-<profile>/generated.env | cut -d= -f2)
curl -so /dev/null -w '%{http_code}\n' -H "Host: ${host}" http://localhost:7777/   # want 200, not 404

# 3. Print the browser URL the user should open
echo "https://${host}"
```

If step 1 fails, `vss-haproxy-ingress` hasn't come up — check
`docker logs vss-haproxy-ingress`. The usual cause is another service already
bound to port 7777.

If step 2 returns **404**, the gateway is running but was never told about that
hostname. Confirm it with the response header rather than guessing:

```bash
curl -sI -H "Host: ${host}" http://localhost:7777/ | grep -i x-vss-gateway-deny
# x-vss-gateway-deny: unknown-host
```

`VSS_PUBLIC_HOST` is the variable that admits it. Set it (`EXTERNAL_IP` is also
on the allowlist but is not what `dev-profile.sh` writes for Brev), then
`docker compose up -d --force-recreate vss-haproxy-ingress`.

## Troubleshooting

| Symptom | Cause |
|---|---|
| Browser gets **404** on a link that `curl localhost:7777` serves fine | The gateway's Host allowlist was never told that hostname. Confirm with `curl -sI -H "Host: <secure-link>" http://localhost:7777/ \| grep -i x-vss-gateway-deny` → `unknown-host`. Fix `VSS_PUBLIC_HOST` and recreate `vss-haproxy-ingress`. A bare 404 with no such header is a genuinely unrouted path instead. |
| User says the Brev link won't load at all | Ask how the secure link was exposed. The default is `<prefix>-<id>.<brev-domain>` with prefix `7777`; the domain is `apps.run.brev.nvidia.com` or `brevlab.com` depending on the instance. An older inherited launchable may still serve the legacy trailing-`0` form `77770-<id>...`, or a manually-created link may use a different port. Set `VSS_PUBLIC_HOST` (and `BREV_LINK_PREFIX` / `BREV_LINK_DOMAIN`) to whatever the actual link is, then redeploy. |
| Deployed on a Skybridge instance but the link 404s at Cloudflare | The domain was assumed. `apps.run.brev.nvidia.com` and `brevlab.com` are both live; check which one `generated.env` recorded in `BREV_LINK_DOMAIN` and compare it with the link Brev actually issued. |
| UI loads but AJAX calls to `/api/*` CORS-fail | A second secure link was created for port 8000 → browser treats it as a different origin. Delete the extra link; everything is behind the 7777 link. |
| Kibana or Phoenix "needs a secure link" | It doesn't — they are routed at `/kibana` and `/phoenix` on the 7777 link. A separate 5601 or 6006 link creates a second origin and is what breaks their embedded assets. |
| `curl https://<secure-link>` → 502 | HAProxy container (`vss-haproxy-ingress`) is down — `docker logs vss-haproxy-ingress` |
| `curl https://<secure-link>` → Cloudflare Access login page forever | User hasn't been granted access in the Brev org; not a deploy issue |
| Agent-generated report URLs don't open, or the browser blocks them as mixed content | The public origin was not applied before the services started. `VST_EXTERNAL_URL`, `VST_BASE_URL`, `VSS_AGENT_EXTERNAL_URL` and `VSS_AGENT_REPORTS_BASE_URL` are interpolated from `VSS_PUBLIC_*` **at Compose time** and baked into the agent container, so changing `VSS_PUBLIC_HOST` afterwards and recreating only the gateway leaves them pointing at the old origin. Check with `docker inspect vss-agent --format '{{range .Config.Env}}{{println .}}{{end}}' \| grep -E 'EXTERNAL_URL\|REPORTS_BASE_URL'`; if they are wrong, fix `VSS_PUBLIC_*` and recreate the agent, not just the gateway. |
