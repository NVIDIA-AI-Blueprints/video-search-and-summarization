# Ingress Capability Owner

## Capabilities and service keys

| Capability | Canonical service profile keys |
|---|---|
| Single-origin HTTP ingress for browse / data-plane surfaces | `vss-haproxy-ingress` |

## Access role

A bridge-network reverse proxy that fronts HTTP surfaces on one origin (default
port `7777`) behind a host-header allowlist. It is infrastructure, not a
capability producer: it only routes to owners the build already deploys and
introduces no data path of its own. It has two independent uses — (a) the
interactive tier's public front door (UI + agent), and (b) a standalone browse
ingress for a headless build that exposes only data-plane surfaces. It is
reached only when the request asks to expose surfaces through one origin;
otherwise it is pruned. NvStreamer is never fronted by this owner (see below).

## Required peers

- Routes only to services already in the build; a route whose backend is not
  deployed simply returns `503` (every backend uses `init-addr none`), so the
  proxy starts regardless of which surfaces are present.
- The interactive front-door use requires the Agent owner (`vss-agent` / UI).
  The headless browse use requires none of that tier — only the data-plane
  owners it routes to (ELK/Kibana, VIOS, Alerts).
- Requires the host-identity env below, or the host-header allowlist returns
  `404` for every request.

## Headless browse / data-plane ingress

The shipped `haproxy.cfg.template` is authored for the full stack: its catch-all
plus the `/api`, `/chat`, `/static`, `/websocket`, `/api/chat`, `/phoenix`, and
`/va-mcp` routes target the interactive tier that a headless build prunes. Two
headless modes, not interchangeable — **default to the curated patch** when the
build exposes a chosen set of surfaces through one origin (a "single ingress"
request, or any named-surface list). It is a build-generation artifact, so a
validate-only pass is no reason to skip it. Use as-is only when the caller
explicitly accepts advertised dead routes and a `503` `/` landing.

- **Curated (patch).** Mount a trimmed config via a `patches/<haproxy>.yml`
  service-definition patch that overrides the config volume, keeping only the
  routes whose backends serve HTTP on the routed port and replacing the catch-all.
- **As-is (explicit shortcut only).** Activate `vss-haproxy-ingress` and set the
  host-identity env. Interactive routes 503 harmlessly; the data-plane routes —
  `/kibana`, `/vst`, `/storage`, `/video-analytics-api`, and (combined only)
  `/alert-bridge` — work, but dead routes are advertised and `/` 503s.

Discipline for the trimmed config:

- **Prune, do not author.** Derive it by deleting the backends and routes for
  pruned services from the shipped template and swapping the catch-all — never
  write one from scratch, so it stays a faithful subset and is re-derivable when
  the template moves. Copy every `replace-path` block verbatim.
- **Route only HTTP surfaces, not merely deployed containers.** A backend that
  binds no HTTP port always `503`s. Behavior-Analytics (the
  `vss-search-analytics-2d-fusion` worker) is Kafka-only, so drop its
  `/behavior-analytics` route — that data is reached via Kibana/ES, not the ingress.
- **Guard the catch-all — do not let it preempt routes.** HAProxy runs **every
  `http-request` rule before any `use_backend`**, regardless of line order, so
  an unconditional `http-request redirect location /kibana/ … if h_main`
  redirects *every* request (even `/vst`, `/kibana`) before routing runs — a
  bug that hides until a non-Kibana surface is exercised. The template's
  catch-all avoids this by being a `use_backend … if h_main` (routed last, a
  true fallback). Keep it phase-correct: guard the redirect to exclude the
  routed prefixes (`if h_main !p_routed`, with `p_routed` in sync with the
  `use_backend` routes kept) so real routes fall through and only an
  unmatched/bare path bounces to `/kibana/`.
- **`/kibana` stays no-strip.** Kibana runs with `server.basePath: "/kibana"` +
  `server.rewriteBasePath: true` (`kibana.yml`), so the proxy must not strip the
  prefix — unlike `alert-bridge` / `video-analytics-api`, which do strip.
- **Lint before use:** `haproxy -c -f <cfg>` must pass; add it to the build's
  validate step so a bad config fails at build time, not at container start.
- **NvStreamer is not routed here.** It is reached directly on its published
  port (default `31000`) in every profile; do not add a subpath route for it
  (its UI has no base-path setting, so a subpath mount breaks its assets).

### Reference trimmed config (headless data-plane)

Pruned from `haproxy.cfg.template`; mount it via the patch. Keep the omitted
verbatim blocks exactly as the template has them.

```haproxy
global
    log stdout format raw local0 info
    maxconn 40000

defaults
    mode http
    log global
    option httplog
    option forwardfor
    timeout connect 10s
    timeout client 120s
    timeout server 120s
    timeout tunnel 3600s

resolvers docker
    nameserver dns 127.0.0.11:53
    accepted_payload_size 8192
    hold valid 10s

# --- Data-plane / browse backends only (rewrites copied verbatim) ---

backend bk_vst_ingress
    server s1 "${VST_INGRESS_SERVICE_HOST}:${VST_PORT}" check resolvers docker init-addr none

backend bk_vst_storage_compat
    http-request replace-path ^/storage/(.*) /vst/storage/\1
    http-request replace-path ^/storage$ /vst/storage
    server s1 "${VST_INGRESS_SERVICE_HOST}:${VST_PORT}" check resolvers docker init-addr none

backend bk_vst_prefixed_compat
    http-request replace-path ^/[^/]+:[0-9]+/vst/(.*) /vst/\1
    http-request replace-path ^/[^/]+:[0-9]+/vst$ /vst
    server s1 "${VST_INGRESS_SERVICE_HOST}:${VST_PORT}" check resolvers docker init-addr none

backend bk_kibana
    server s1 "${KIBANA_SERVICE_HOST}:${KIBANA_PORT}" check resolvers docker init-addr none

backend bk_video_analytics_api_strip
    http-request replace-path ^/video-analytics-api/(.*) /\1
    http-request replace-path ^/video-analytics-api$ /
    server s1 "${VIDEO_ANALYTICS_API_SERVICE_HOST}:${VIDEO_ANALYTICS_API_PORT}" check resolvers docker init-addr none

# Combined (alerts) builds only — drop this backend if no alerts capability ships:
backend bk_alert_bridge_strip
    http-request replace-path ^/alert-bridge/(.*) /\1
    http-request replace-path ^/alert-bridge$ /
    server s1 "${ALERT_BRIDGE_SERVICE_HOST}:${ALERT_BRIDGE_PORT}" check resolvers docker init-addr none

frontend fe_http
    bind "${HAPROXY_BIND_ADDR}:${HAPROXY_PORT}"

    # known_host allowlist + `http-request deny deny_status 404 if !known_host`
    # and the full h_main ACL block: COPY BOTH VERBATIM from haproxy.cfg.template.

    # storage preflight + route (copy the ACLs + HEAD/OPTIONS returns verbatim):
    acl p_storage path /storage
    acl p_storage path_beg /storage/
    # ... (HEAD/OPTIONS return blocks copied verbatim from the template) ...
    use_backend bk_vst_storage_compat if h_main p_storage

    acl p_video_analytics path /video-analytics-api
    acl p_video_analytics path_beg /video-analytics-api/
    use_backend bk_video_analytics_api_strip if h_main p_video_analytics

    # Combined (alerts) builds only:
    acl p_alert_bridge path /alert-bridge
    acl p_alert_bridge path_beg /alert-bridge/
    use_backend bk_alert_bridge_strip if h_main p_alert_bridge

    acl p_kibana path /kibana
    acl p_kibana path_beg /kibana/
    use_backend bk_kibana if h_main p_kibana

    acl p_vst path /vst
    acl p_vst path_beg /vst/
    acl p_vst_prefixed path_reg ^/[^/]+:[0-9]+/vst(/|$)
    use_backend bk_vst_prefixed_compat if h_main p_vst_prefixed
    use_backend bk_vst_ingress if h_main p_vst

    # Catch-all: land any UNMATCHED path on Kibana (no UI in headless). Guarded
    # because HAProxy runs all http-request rules before any use_backend: an
    # unconditional `... if h_main` would 302 every request (even /vst, /kibana)
    # to /kibana/. Keep p_routed in sync with the routes kept above (drop
    # /alert-bridge unless the combined-alerts route ships).
    acl p_routed path_beg /kibana /vst /storage /video-analytics-api /alert-bridge
    acl p_routed path_reg ^/[^/]+:[0-9]+/vst(/|$)
    http-request redirect location /kibana/ code 302 if h_main !p_routed
```

## Configuration knobs

| Environment variable | Use |
|---|---|
| `HAPROXY_HOST_PORT`, `HAPROXY_PORT`, `HAPROXY_BIND_ADDR` | Publish and bind the proxy origin. |
| `VSS_PUBLIC_HOST`, `VSS_PUBLIC_PORT`, `EXTERNAL_IP`, `HOST_IP` | Host-header allowlist — required, or every request 404s. |
| `KIBANA_SERVICE_HOST`, `KIBANA_PORT`, `VST_INGRESS_SERVICE_HOST`, `VST_PORT`, `BEHAVIOR_ANALYTICS_SERVICE_HOST`, `VIDEO_ANALYTICS_API_SERVICE_HOST`, `ALERT_BRIDGE_SERVICE_HOST` (+ ports) | Per-backend targets; Docker-DNS defaults suit the shipped service keys. |

## Sources

- `deploy/docker/services/infra/haproxy/compose.yml`
- `deploy/docker/services/infra/haproxy/haproxy.cfg.template`
- `deploy/docker/services/infra/elk/kibana/configs/kibana.yml`
