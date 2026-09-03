{{/*
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
*/}}

{{/*
  THE canonical VSS ingress route table.

  One origin, one mount per service, the same on every developer profile and on
  the Docker edge (deploy/docker/services/infra/haproxy/haproxy.cfg.template).
  A service is mounted at its own name and carries whatever it itself serves:
  /rtvi-vlm/v1/models, /elasticsearch/_cat/indices, /vst/api/v1/sensor/list.
  These are the paths `vss configure` records
  (services/agent/packages/vss_cli/src/vss_cli/config.py:INGRESS_SERVICES) and
  the paths the operate skills document, so a caller never has to ask which
  profile it is talking to.

  Callers do not choose paths -- they supply a `backends` dict of
  `<key> -> {service, port}` for the services their profile actually deploys,
  and this table decides where each one is mounted. A key with no entry is not
  mounted; per FR-19 a profile that does not run a backend simply has no route
  rather than a broken one.

  Row fields:
    key      logical backend, the key callers use in `backends`
    path     public mount
    pathType Ingress pathType (Prefix, except /openapi.json which is Exact)
    rewrite  none  -> forwarded with the prefix intact
             strip -> prefix removed before the backend sees it
             /x    -> prefix replaced with /x
    anchored prepend ^ to the HAProxy rewrite source when true

  Ordering is the rendered order: /api/chat before /api, and the UI catch-all
  last. The HAProxy controller matches longest-prefix regardless, but keeping
  the file readable in match order is worth more than the two lines it costs.
*/}}
{{- define "vss.ingress.routeTable" -}}
- key: ui
  path: /api/chat
  pathType: Prefix
  rewrite: none
- key: agent
  path: /api
  pathType: Prefix
  rewrite: none
- key: agent
  path: /chat
  pathType: Prefix
  rewrite: none
- key: agent
  path: /websocket
  pathType: Prefix
  rewrite: none
- key: agent
  path: /static
  pathType: Prefix
  rewrite: none
- key: agent
  path: /docs
  pathType: Prefix
  rewrite: none
- key: agent
  path: /redoc
  pathType: Prefix
  rewrite: none
- key: agent
  path: /generate
  pathType: Prefix
  rewrite: none
- key: agent
  path: /openapi.json
  pathType: Exact
  rewrite: none
- key: vst
  path: /vst
  pathType: Prefix
  rewrite: none
# VST media links are minted absolute against the origin root. The Docker edge
# answers them with the same replacement, so a clip URL works on either
# deployment without the caller rewriting it.
- key: vst
  path: /storage
  pathType: Prefix
  rewrite: /vst/storage
  anchored: true
- key: va-mcp
  path: /va-mcp
  pathType: Prefix
  rewrite: strip
- key: alert-bridge
  path: /alert-bridge
  pathType: Prefix
  rewrite: strip
- key: video-analytics-api
  path: /video-analytics-api
  pathType: Prefix
  rewrite: strip
# No strip: the Docker edge forwards this one whole, and the service is
# reached by Kibana/ES in most builds, so nothing depends on a stripped form.
- key: behavior-analytics
  path: /behavior-analytics
  pathType: Prefix
  rewrite: none
- key: elasticsearch
  path: /elasticsearch
  pathType: Prefix
  rewrite: strip
- key: rtvi-vlm
  path: /rtvi-vlm
  pathType: Prefix
  rewrite: strip
- key: rtvi-cv
  path: /rtvi-cv
  pathType: Prefix
  rewrite: strip
- key: rtvi-embed
  path: /rtvi-embed
  pathType: Prefix
  rewrite: strip
- key: lvs
  path: /lvs
  pathType: Prefix
  rewrite: strip
- key: phoenix
  path: /phoenix
  pathType: Prefix
  rewrite: strip
- key: ui
  path: /
  pathType: Prefix
  rewrite: none
{{- end -}}

{{/*
  "true" when a component is deployed, "" when it is not.

  `default true .Values.x.enabled` cannot express this: sprig's `default` treats
  `false` as empty and hands back the default, so an explicitly disabled
  component reads as enabled and the profile mounts a route at a Service that
  was never created. Key presence is the only reliable signal.

  Pass: dict "vals" $subchartValues "default" true
  Chain an umbrella with its child: `and $umbrellaOn $childOn`.
*/}}
{{- define "vss.ingress.enabled" -}}
{{- $vals := .vals | default dict -}}
{{- if hasKey $vals "enabled" -}}
{{- if $vals.enabled }}true{{ end -}}
{{- else if .default -}}
true
{{- end -}}
{{- end -}}

{{/*
  Renders the `paths:` list for one Ingress rule.

  Pass: dict "backends" $b
  Optional: "only" (list of keys) to render a subset -- used for the host-less
  east-west rule, which carries the RTVI mounts and nothing else.

  Emitted at zero indent with no leading or trailing blank line; the caller
  applies `nindent`.
*/}}
{{- define "vss.ingress.paths" -}}
{{- include "vss.ingress.pathRows" . | trim -}}
{{- end -}}

{{- define "vss.ingress.pathRows" -}}
{{- $backends := .backends | default dict -}}
{{- $only := .only | default (list) -}}
{{- range $row := include "vss.ingress.routeTable" . | fromYamlArray }}
{{- $b := index $backends $row.key | default dict }}
{{- if and $b.service (or (eq (len $only) 0) (has $row.key $only)) }}
- path: {{ $row.path }}
  pathType: {{ $row.pathType }}
  backend:
    service:
      name: {{ $b.service }}
      port:
        number: {{ $b.port }}
{{- end }}
{{- end }}
{{- end -}}

{{/*
  Renders the value of the `haproxy.org/path-rewrite` annotation: one pair per
  mounted route that rewrites, derived from the same table as the paths, so the
  two can never disagree about which prefix is stripped (FR-16).

  Emitted at zero indent with no leading or trailing blank line; the caller
  applies `nindent`.
*/}}
{{- define "vss.ingress.pathRewrites" -}}
{{- include "vss.ingress.pathRewriteRows" . | trim -}}
{{- end -}}

{{- define "vss.ingress.pathRewriteRows" -}}
{{- $backends := .backends | default dict -}}
{{- range $row := include "vss.ingress.routeTable" . | fromYamlArray }}
{{- $b := index $backends $row.key | default dict }}
{{- $rw := $row.rewrite | default "none" }}
{{- if and $b.service (ne $rw "none") }}
{{- $to := ternary "" $rw (eq $rw "strip") }}
{{- $anchor := ternary "^" "" ($row.anchored | default false) }}
{{ $anchor }}{{ $row.path }}/(.*) {{ $to }}/\1
{{ $anchor }}{{ $row.path }} {{ $to | default "/" }}
{{- end }}
{{- end }}
{{- end -}}

{{/*
  Whether any rewriting route is mounted -- guards the annotation block, since
  an empty `haproxy.org/path-rewrite` is not the same as an absent one.
*/}}
{{- define "vss.ingress.hasPathRewrites" -}}
{{- if include "vss.ingress.pathRewrites" . }}true{{ end -}}
{{- end -}}

{{/*
  Fails the render on a backends dict this table cannot serve: a key that is not
  in the table (a typo, or a route someone added on one profile only) or an
  entry with no port. This is what keeps the four profiles from drifting apart
  again -- a new route has to be added here, where every profile picks it up.
*/}}
{{- define "vss.ingress.assertBackends" -}}
{{- $known := list -}}
{{- range $row := include "vss.ingress.routeTable" . | fromYamlArray -}}
{{- $known = append $known $row.key -}}
{{- end -}}
{{- $known = uniq $known -}}
{{- range $k, $v := (.backends | default dict) -}}
{{- if not (has $k $known) -}}
{{- fail (printf "vss.ingress: %q is not a canonical route key. The table in services/common/templates/_ingress-routes.tpl defines: %s. Add the route there, not in one profile." $k ($known | sortAlpha | join ", ")) -}}
{{- end -}}
{{- if not $v.service -}}
{{- fail (printf "vss.ingress: backend %q has no service name" $k) -}}
{{- end -}}
{{- if not $v.port -}}
{{- fail (printf "vss.ingress: backend %q has no port" $k) -}}
{{- end -}}
{{- end -}}
{{- end -}}
