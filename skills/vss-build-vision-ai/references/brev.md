# Brev Environment Reference

How to deploy VSS on a Brev GPU instance so the UI and API are reachable
from a browser via Brev **secure links** (a Cloudflare-fronted reverse proxy).

This reference derives from `deploy/docker/scripts/deploy_vss_launchable.ipynb`, which is the
interactive reference implementation.

## When this applies

A Brev-managed instance publishes an **environment context file** —
`$BREV_ENVIRONMENT_CONTEXT_PATH`, conventionally
`/etc/brev/environment-context.json` — listing one entry per exposed port with
its `destination_port`, `public_port`, and `fqdn`. It also sets
`BREV_ENV_ID=<instance-id>` in `/etc/environment`. If neither is present you are
not on a Brev-provisioned instance and this reference doesn't apply — use the
normal host IP + port access pattern from the selected file under `profiles/`.

**Resolve every hostname from the context file; never build one from a
pattern.** The domain is not always `brevlab.com` and the prefix convention
varies. Read the `fqdn` for the port you want. A port with no entry is not
exposed and has no URL. Resolve it with the helpers under
[Resolving a secure link](#resolving-a-secure-link).

## Architecture

```
Browser  ──https──>  <fqdn published for port 7777>  (Cloudflare Access)
                             │
                             ▼
                   Brev network tunnel
                             │
                             ▼
              vss-haproxy-ingress :7777 on the instance
                             │
           ┌─────────────────┼─────────────────┐
           ▼                 ▼                 ▼
        UI :3000      Agent API :8000     VST :30888
```

## Resolving a secure link

Look the port up in the context file. These helpers are used throughout this
reference; define them once per shell:

```bash
BREV_CTX="${BREV_ENVIRONMENT_CONTEXT_PATH:-/etc/brev/environment-context.json}"
# FQDN published for a destination port; empty when the port is not exposed.
brev_fqdn() { jq -er --argjson p "$1" '[.ports[]? | select(.destination_port == $p) | .fqdn][0] // empty' "$BREV_CTX" 2>/dev/null; }
# Public port for that link (443 in current Brev; read it, don't assume it).
brev_public_port() { jq -er --argjson p "$1" '[.ports[]? | select(.destination_port == $p) | .public_port][0] // empty' "$BREV_CTX" 2>/dev/null; }

brev_fqdn 7777        # the haproxy ingress link — the VSS browse origin
brev_public_port 7777
```

`7777` is the destination port on the instance — the HAProxy ingress VSS exposes.
An empty result means no link is published for it: expose one in Brev, or deploy
behind a port that has one. Do not fall back to constructing a hostname.

## Per-profile secure link requirements

| Profile | Required links | Optional |
|---|---|---|
| `base` | **7777** (HAProxy ingress — UI + Agent + VST) | 6006 (Phoenix tracing) |
| `lvs` | **7777**, **5601** (Kibana) | 6006 |
| `search` | **7777**, **5601**, **31000** (nvstreamer) | 6006 |
| `alerts` | **7777**, **5601**, **31000** (nvstreamer) | 6006 |

Ports that should NOT get their own secure link (they're behind HAProxy):
3000 (UI), 8000 (Agent), 30888 (VST).

## Setup flow

Before resolving Compose, set the Brev secure-link values in the build's
`_builds/<name>/override.env`. **`EXTERNAL_IP` alone is not enough** — the Brev secure
link is served over **HTTPS on the link's own `public_port`** (443 in current
Brev — read it, per above), but the profile `.env` ships
`VSS_PUBLIC_HTTP_PROTOCOL=http`, `VSS_PUBLIC_WS_PROTOCOL=ws`, and
`VSS_PUBLIC_PORT=${HAPROXY_HOST_PORT}` (7777). Leaving those at the defaults makes the
agent emit `http://…:7777` UI/API/WS URLs from an `https://` page → the browser
blocks them as mixed content. Set the host, protocol, and port together:

```bash
BUILD_OVERRIDE="_builds/<name>/override.env"
host="$(brev_fqdn 7777)"                      # helpers from "Resolving a secure link"
port="$(brev_public_port 7777)"
[ -n "$host" ] && [ -n "$port" ] || { echo "No Brev link published for port 7777" >&2; exit 1; }
sed -i \
  -e "s|^EXTERNAL_IP=.*|EXTERNAL_IP=${host}|" \
  -e "s|^VSS_PUBLIC_HOST=.*|VSS_PUBLIC_HOST=${host}|" \
  -e "s|^VSS_PUBLIC_HTTP_PROTOCOL=.*|VSS_PUBLIC_HTTP_PROTOCOL=https|" \
  -e "s|^VSS_PUBLIC_WS_PROTOCOL=.*|VSS_PUBLIC_WS_PROTOCOL=wss|" \
  -e "s|^VSS_PUBLIC_PORT=.*|VSS_PUBLIC_PORT=${port}|" \
  "$BUILD_OVERRIDE"
```

Stop on an empty `host`; never substitute a constructed one. These values also
fill HAProxy's `known_host` allowlist, so a wrong hostname `404`s every browser
request while `curl localhost:7777` still succeeds.

On a NemoClaw build other than `alerts`, drop the `EXTERNAL_IP` line above and
leave that variable to the sandbox ([`agent-harness.md`](agent-harness.md)). The
rest is unchanged: `VSS_PUBLIC_HOST` already carries the secure link, and the
`known_host` ACL admits it in its own right.

## Verifying the deploy is reachable externally

After `docker compose up -d`:

```bash
# 1. Proxy is up and routing. Probe /, not /health: the ingress is HAProxy and its
# route table has no /health, so that path 404s on a perfectly healthy proxy.
curl -sf -o /dev/null http://localhost:7777/ && echo "proxy OK"

# 2. UI reachable through the proxy (internally)
curl -sfI http://localhost:7777/ | head -1

# 3. Print the browser URL the user should open — resolved, never constructed
echo "https://$(brev_fqdn 7777)"
```

If step 1 fails, the haproxy container (`vss-haproxy-ingress`) hasn't come up — check
`docker logs vss-haproxy-ingress`. Common reason: another service on the host is
already bound to port 7777, or `EXTERNAL_IP` in the build override doesn't
match the secure-link domain (haproxy's `known_host` ACL rejects the
request as 404 from the browser even though `curl localhost:7777` works).

## Troubleshooting

| Symptom | Cause |
|---|---|
| User says the Brev link won't load at all | Compare their URL with `brev_fqdn 7777`. A hostname absent from the context file is not exposed. If the deployment sits behind a different exposed port, resolve that port and redeploy. |
| UI loads but AJAX calls to `/api/*` CORS-fail | A second secure link was created for port 8000 → browser treats it as a different origin. Delete the extra link; the UI should use the proxy only. |
| `curl https://$(brev_fqdn 7777)` → 502 | HAProxy container (`vss-haproxy-ingress`) is down — `docker logs vss-haproxy-ingress` |
| `curl https://$(brev_fqdn 7777)` → Cloudflare Access login page forever | User hasn't been granted access in the Brev org; not a deploy issue |
| Agent-generated report URLs don't open | `EXTERNAL_IP` in the build override is still the internal `${HOST_IP}` default → reports hard-code internal IPs. Set it from `brev_fqdn 7777` in `_builds/<name>/override.env` (see [Setup flow](#setup-flow)), regenerate `resolved.yml`, and redeploy. |
