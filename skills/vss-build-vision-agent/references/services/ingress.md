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
  owners it routes to (ELK/Kibana, VIOS, Behavior-Analytics, Alerts).
- Requires the host-identity env below, or the host-header allowlist returns
  `404` for every request.

## Headless browse / data-plane ingress

The shipped `haproxy.cfg.template` is authored for the full stack: its catch-all
plus the `/api`, `/chat`, `/static`, `/websocket`, `/api/chat`, `/phoenix`, and
`/va-mcp` routes target the interactive tier that a headless build prunes. Two
ways to run it headless:

- **As-is.** Activate `vss-haproxy-ingress` and set the host-identity env. The
  interactive routes 503 harmlessly; the data-plane routes — `/kibana`, `/vst`,
  `/storage`, `/video-analytics-api`, `/behavior-analytics`, and (combined only)
  `/alert-bridge` — work. Zero authoring, but dead routes are advertised and the
  `/` landing 503s.
- **Curated (patch).** Mount a trimmed config via a `patches/<haproxy>.yml`
  service-definition patch that overrides the config volume, keeping only the
  routes whose backends are deployed and replacing the catch-all. This is the
  segregated headless-ingress form.

Discipline for the trimmed config:

- **Prune, do not author.** Derive it by deleting the backends and routes for
  pruned services from the shipped template and swapping the catch-all — never
  write one from scratch, so it stays a faithful subset and is re-derivable when
  the template moves. Copy every `replace-path` block verbatim.
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

backend bk_behavior_analytics
    server s1 "${BEHAVIOR_ANALYTICS_SERVICE_HOST}:${BEHAVIOR_ANALYTICS_PORT}" check resolvers docker init-addr none

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

    acl p_behavior path /behavior-analytics
    acl p_behavior path_beg /behavior-analytics/
    use_backend bk_behavior_analytics if h_main p_behavior

    # Catch-all: no UI in headless — land on Kibana instead of the pruned vss-ui.
    http-request redirect location /kibana/ code 302 if h_main
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
