<!--
SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
-->

# `common` — the canonical VSS ingress route table

A Helm **library** chart. It installs nothing; it holds the one route table every
developer profile renders its `Ingress` from, so `/rtvi-vlm` means the same thing on
`dev-profile-base` as on `dev-profile-search`, and the same thing it means on the Docker
edge (`deploy/docker/services/infra/haproxy/haproxy.cfg.template`).

Before this chart, each profile owned its own path list. RT-VLM answered at `/v1` on base,
at `/v1/models` on LVS, and at `/rtvi-vlm/v1` on search; Elasticsearch was published on two
profiles and missing on a third that deployed it. Callers had to probe. They no longer do.

## The table

Every service is mounted at **its own name** and carries whatever it itself serves —
`/rtvi-vlm/v1/models`, `/elasticsearch/_cat/indices`, `/vst/api/v1/sensor/list`. These are
the paths `vss configure` records
(`services/agent/packages/vss_cli/src/vss_cli/config.py:INGRESS_SERVICES`).

| Backend key | Mount | Prefix | Notes |
|---|---|---|---|
| `ui` | `/api/chat`, `/` | kept | `/` is the catch-all and renders last |
| `agent` | `/api`, `/chat`, `/websocket`, `/static`, `/docs`, `/redoc`, `/generate`, `/openapi.json` | kept | `/openapi.json` is `Exact`, the rest `Prefix` |
| `vst` | `/vst` | kept | |
| `vst` | `/storage` | → `/vst/storage` | VST mints absolute media links against the origin root |
| `va-mcp` | `/va-mcp` | stripped | public MCP endpoint is `/va-mcp/mcp` |
| `alert-bridge` | `/alert-bridge` | stripped | |
| `video-analytics-api` | `/video-analytics-api` | stripped | |
| `behavior-analytics` | `/behavior-analytics` | kept | |
| `elasticsearch` | `/elasticsearch` | stripped | edge guard on the ES Service, see below |
| `rtvi-vlm` | `/rtvi-vlm` | stripped | |
| `rtvi-cv` | `/rtvi-cv` | stripped | also on the host-less east-west rule |
| `rtvi-embed` | `/rtvi-embed` | stripped | also on the host-less east-west rule |
| `lvs` | `/lvs` | stripped | |
| `phoenix` | `/phoenix` | stripped | keep `PHOENIX_HOST_ROOT_PATH` in step |

Kibana and NVStreamer are **not** in the table. Both are served on their own host
(`kibana.<host>`, `streamer.<host>`): Kibana needs `server.basePath` to live under a
subpath, and NVStreamer's UI has no base-path setting at all.

A profile mounts a route only when it deploys that backend, so a route is either present
or absent — never present and broken. A profile that runs no `lvs-server` has no `/lvs`.
Enablement is read through `vss.ingress.enabled` rather than `default true .enabled`: sprig
treats `false` as empty and hands back the default, which would turn an explicitly disabled
component back on and mount a route at a Service that was never created.

## Using it

The profile supplies a `backends` dict of `key -> {service, port}` for what it deploys; the
table decides the rest:

```gotemplate
{{- $b := dict }}
{{- $_ := set $b "agent" (dict "service" "vss-agent" "port" 8000) }}
{{- $_ := set $b "rtvi-vlm" (dict "service" "vss-rtvi-vlm" "port" 8000) }}
{{- include "vss.ingress.assertBackends" (dict "backends" $b) }}
...
  annotations:
    {{- if include "vss.ingress.hasPathRewrites" (dict "backends" $b) }}
    haproxy.org/path-rewrite: |
      {{- include "vss.ingress.pathRewrites" (dict "backends" $b) | nindent 6 }}
    {{- end }}
spec:
  rules:
    - host: {{ $host | quote }}
      http:
        paths:
          {{- include "vss.ingress.paths" (dict "backends" $b) | nindent 10 }}
```

| Template | Renders |
|---|---|
| `vss.ingress.paths` | the `paths:` list; `only` (list of keys) narrows it to a subset |
| `vss.ingress.pathRewrites` | the `haproxy.org/path-rewrite` value, derived from the same rows |
| `vss.ingress.hasPathRewrites` | non-empty when any mounted route rewrites |
| `vss.ingress.assertBackends` | fails the render on an unknown key or a missing service/port |
| `vss.ingress.enabled` | `"true"` when a component is deployed, honouring an explicit `false` |

`assertBackends` is what keeps the profiles from drifting apart again: a new route has to be
added to the table, where every profile picks it up, rather than to one chart's template.

## Two things that live outside this table

**Per-backend behaviour belongs on the Service, not the Ingress.** Each `haproxy.org/*`
annotation is valid at a specific set of scopes, and a Service-scoped one applies to that
backend alone ([annotation
matrix](https://github.com/haproxytech/kubernetes-ingress/blob/master/documentation/annotations.md)).
`timeout-server`, `backend-config-snippet` and `load-balance` are valid on a Service, so
stream affinity sits on `vss-rtvi-cv` / `vss-rtvi-embed`, the long server timeouts on
`vss-summarization`, `vss-rtvi-vlm` and `vss-agent`, and the Elasticsearch edge guard on the
`elasticsearch` Service. `path-rewrite` is Ingress-scope only, so the route shaping stays on
the `Ingress`.

`timeout-client` and `timeout-tunnel` are **ConfigMap-only** in this controller. The LVS and
search charts still render them for continuity with earlier releases, but they have never had
any effect as Ingress annotations — raising those means a cluster-wide controller setting,
which is outside these charts. That is a pre-existing gap this refactor documents rather than
fixes.

**East-west traffic needs a host-less rule.** An in-cluster caller reaching the ingress
controller's ClusterIP sends that Service name as its `Host`, which matches no named rule. So
when `global.rtviInternalIngress.enabled` routes the agent's RT-CV / RT-Embed calls through
the controller, the profile renders a second rule with no `host:` carrying those two mounts —
an Ingress rule without a host matches any Host.

It is gated on that flag and off by default, because "host-less" means every Host that
reaches the controller, its **external** listener included. Enabling internal affinity
therefore also exposes `/rtvi-cv` and `/rtvi-embed` outside the named host. That was already
true of the separate Ingress this replaced; it is stated here because folding the rule into
the main object makes it easy to miss.

## Checking it

```bash
python3 deploy/helm/scripts/verify-ingress-routes.py --verbose
```

Renders all four profiles and asserts: no path or `pathType` outside this table, no backend
mounted at the origin root, rewrite annotations whose *destinations* match the table, every
mount `vss configure` probes present, a disabled component losing its route rather than
keeping a dangling backend, the host-less rule absent by default, and the hand-applied
`vss-ingress-example*.yaml` files still describing what the chart renders.

It deliberately does not check the Docker edge: `haproxy.cfg.template` is aligned to this
table separately and still carries Docker-only routes (`/kibana`, `/perception-sdr`).
