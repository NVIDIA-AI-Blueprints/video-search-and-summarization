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
    exposure who is allowed to reach the mount (FR-29). One of:
             public-user-accessible   a browser dereferences it
             remote-agent-accessible  an off-host agent or the host-side CLI
                                      calls it, but no browser does
             internal-only            neither; only callers already inside the
                                      deployment network

  On `exposure`, because it is a policy field in a routing table and that
  deserves saying plainly:

  FR-29 requires every route to carry one of those three values and makes
  `internal-only` the default "unless a route is required by browser clients or
  remote agents". So the value is derived from who demonstrably calls the mount,
  not from how sensitive the service feels: `/video-analytics-api` is
  public-user-accessible because `NEXT_PUBLIC_MDX_WEB_API_URL` bakes it into the
  browser bundle, and `/elasticsearch` is remote-agent-accessible because the
  CLI probes it from the host -- restricted by the method/path allowlist at the
  edge (FR-30) rather than by tier.

  What the tier does and does not do. `internal-only` is enforced: the Docker
  edge refuses those mounts to any caller that is not both on an internal Host
  and in a private source range, and the canonical table does not mount them on
  the public Ingress rule. The line between the other two is DESCRIPTIVE only --
  both arrive over the same origin with no authentication, so separating them
  would need the route-level auth FR-32 explicitly defers to a future
  iteration. Recording it anyway is the point of FR-29: it is the review record
  that says which mounts a browser is expected to reach, so the day auth
  arrives there is a policy to implement rather than a survey to redo.

  It lives on the row rather than in a lint's lookup table so that adding a
  route and classifying it are the same edit. `.github/scripts/
  check_gateway_route_exposure.py` holds this table and the Docker edge to
  agreeing, and fails on a row with no `exposure` at all.

  Every rewrite source renders `^`-anchored (`^<path>/(.*)`, `^<path>$`): the
  rules run in order against the previous rule's output, so an unanchored
  `/alerts` would fire again on what the /alert-bridge strip produced. There is
  no per-row opt-out.

  Ordering is the rendered order: the UI's /api/* routes before the agent's
  /api catch-all, and the UI / catch-all last. The HAProxy controller matches
  longest-prefix regardless, but keeping the file readable in match order is
  worth more than the few lines it costs.
*/}}
{{- define "vss.ingress.routeTable" -}}
- key: ui
  path: /api/chat
  pathType: Prefix
  rewrite: none
  exposure: public-user-accessible
# /api/agent is served by the UI's embedded backend-agnostic agent adapter (see
# deploy/docker/services/ui/compose.yml and haproxy.cfg.template p_api_agent
# rule). Must precede /api so the longest-prefix match falls to the UI rather
# than being forwarded to the agent backend.
- key: ui
  path: /api/agent
  pathType: Prefix
  rewrite: none
  exposure: public-user-accessible
- key: ui
  path: /api/vss-chat
  pathType: Prefix
  rewrite: none
  exposure: public-user-accessible
- key: ui
  path: /api/proxy
  pathType: Prefix
  rewrite: none
  exposure: public-user-accessible
- key: agent
  path: /api
  pathType: Prefix
  rewrite: none
  exposure: public-user-accessible
- key: agent
  path: /chat
  pathType: Prefix
  rewrite: none
  exposure: public-user-accessible
- key: agent
  path: /v1
  pathType: Prefix
  rewrite: none
  exposure: public-user-accessible
- key: agent
  path: /websocket
  pathType: Prefix
  rewrite: none
  exposure: public-user-accessible
- key: agent
  path: /static
  pathType: Prefix
  rewrite: none
  exposure: public-user-accessible
- key: agent
  path: /docs
  pathType: Prefix
  rewrite: none
  exposure: public-user-accessible
- key: agent
  path: /redoc
  pathType: Prefix
  rewrite: none
  exposure: public-user-accessible
- key: agent
  path: /generate
  pathType: Prefix
  rewrite: none
  exposure: public-user-accessible
- key: agent
  path: /openapi.json
  pathType: Exact
  rewrite: none
  exposure: public-user-accessible
- key: vst
  path: /vst
  pathType: Prefix
  rewrite: none
  exposure: public-user-accessible
# Alias for /vst. Rewritten onto /vst, not stripped: VST serves its whole
# surface under /vst/, so /vios/api/v1/x must arrive as /vst/api/v1/x.
- key: vst
  path: /vios
  pathType: Prefix
  rewrite: /vst
  exposure: public-user-accessible
# VST media links are minted absolute against the origin root. The Docker edge
# answers them with the same replacement, so a clip URL works on either
# deployment without the caller rewriting it.
- key: vst
  path: /storage
  pathType: Prefix
  rewrite: /vst/storage
  exposure: public-user-accessible
- key: vst
  path: /vios/storage
  pathType: Prefix
  rewrite: /vst/storage
  exposure: public-user-accessible
- key: va-mcp
  path: /va-mcp
  pathType: Prefix
  rewrite: strip
  exposure: remote-agent-accessible
- key: alert-bridge
  path: /alert-bridge
  pathType: Prefix
  rewrite: strip
  exposure: remote-agent-accessible
# Alias for /alert-bridge; strips identically. The canonical prefix stays.
- key: alert-bridge
  path: /alerts
  pathType: Prefix
  rewrite: strip
  exposure: remote-agent-accessible
- key: video-analytics-api
  path: /video-analytics-api
  pathType: Prefix
  rewrite: strip
  exposure: public-user-accessible
# public-user-accessible on evidence, not on preference: the UI compose file
# sets NEXT_PUBLIC_MDX_WEB_API_URL to the public origin plus this mount, and
# Next.js inlines a NEXT_PUBLIC_ value into the bundle it ships, so a browser
# dereferences this path by construction.
# No strip: the Docker edge forwards this one whole, and the service is
# reached by Kibana/ES in most builds, so nothing depends on a stripped form.
#
# The three warehouse charts strip it instead, and that disagreement is left
# standing on purpose: this mount serves no HTTP on either chart family, so
# there is nothing to observe. `vss-behavior-analytics:develop-latest` -- the
# image both families render -- ships no web framework and no app source that
# builds an HTTP server, the running container has no listening TCP socket, and
# the live Docker edge answers /behavior-analytics with 503 (`nbsrv(...) eq 0`).
# Reconciling strip-vs-forward needs an image that answers HTTP first, and
# whether the mount should exist at all is a product question about the service
# rather than a routing one.
# internal-only by FR-29's default rather than by a judgement about the service:
# no browser bundle names this mount, the CLI does not probe it, and it is not in
# the remote agent's endpoint contract -- so it is "required by" neither of the
# two things that lift a route out of the default.
#
# Carries the publicMountException below, so this row still renders on the
# public rule. It is mounted there today by dev-profile-search and
# dev-profile-alerts, and hand-written onto the two warehouse charts' own
# Ingress objects, and dropping it would be answering the open product question
# the note above records -- whether this mount should exist at all -- as a side
# effect of classifying it. That is a decision for the service's owner. The
# exception is what keeps the classification honest without taking it: the tier
# says who needs the route, and the exception says what is still published while
# somebody decides.
#
# Nothing observable rides on the difference in the meantime: the image both
# chart families render serves no HTTP, so the mount answers 503 either way.
- key: behavior-analytics
  path: /behavior-analytics
  pathType: Prefix
  rewrite: none
  exposure: internal-only
  publicMountException: open product question whether the mount should exist at all; serves no HTTP either way
# remote-agent-accessible, NOT internal-only, and the distinction is the whole
# point of FR-30 sitting next to FR-29: `vss search` runs on the host and
# `vss configure` probes /elasticsearch/_cat/indices from there, so an
# internal-only tier would break the CLI. What keeps the exposure defensible is
# the method/path allowlist at the edge, not the tier -- FR-30's "method and
# path restrictions sufficient for the intended query use case".
- key: elasticsearch
  path: /elasticsearch
  pathType: Prefix
  rewrite: strip
  exposure: remote-agent-accessible
- key: rtvi-vlm
  path: /rtvi-vlm
  pathType: Prefix
  rewrite: strip
  exposure: remote-agent-accessible
- key: rtvi-cv
  path: /rtvi-cv
  pathType: Prefix
  rewrite: strip
  exposure: remote-agent-accessible
- key: rtvi-embed
  path: /rtvi-embed
  pathType: Prefix
  rewrite: strip
  exposure: remote-agent-accessible
- key: lvs
  path: /lvs
  pathType: Prefix
  rewrite: strip
  exposure: remote-agent-accessible
# Alias for /lvs; strips identically. The canonical prefix stays.
- key: lvs
  path: /video-summarization
  pathType: Prefix
  rewrite: strip
  exposure: remote-agent-accessible
- key: phoenix
  path: /phoenix
  pathType: Prefix
  rewrite: strip
  exposure: public-user-accessible
# One address for the LLM whatever GPU it landed on, so a consumer needs no
# knowledge of the placement: <origin>/llm/v1/chat/completions reaches the NIM's
# own /v1/chat/completions. Backed by the in-deployment LLM NIM Service, which
# is why a profile that runs no NIM does not mount this at all rather than
# proxying to a Service that was never created.
- key: llm
  path: /llm
  pathType: Prefix
  rewrite: strip
  exposure: remote-agent-accessible
- key: ui
  path: /
  pathType: Prefix
  rewrite: none
  exposure: public-user-accessible
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
  The `llm` backend entry, or "" when this profile runs no LLM NIM of its own.

  The four profiles resolve every other backend with their own serviceShort /
  subchartFullname helper, but the LLM NIM is not a profile-level dependency:
  it is `nims.nemotron35`, a subchart of a subchart, whose Service is created by
  the NIM Operator from the NIMService CR and therefore named by that chart's
  own fullname rule rather than by the profile's. Resolving it once here is what
  keeps the /llm mount identical on all four, and is why the enablement test
  lives here too -- a remote-LLM or NIM-less profile mounts nothing and the
  request falls through to the UI catch-all as a 404, rather than reaching an
  Ingress backed by a Service that does not exist (FR-19).

  Deliberately not gated on llmBaseUrl: that value says where *consumers* were
  pointed, not whether a NIM is deployed. The Docker edge draws the same line --
  bk_llm_strip is DOWN and answers 503 when the LLM NIM container is not up,
  regardless of where the agent was told to send its own traffic.

  Pass: dict "root" .
  Returns a YAML mapping for `fromYaml`, empty when there is no in-cluster LLM.
*/}}
{{- define "vss.ingress.llmBackend" -}}
{{- $root := index . "root" -}}
{{- $vals := $root.Values | default dict -}}
{{- $nims := index $vals "nims" | default dict -}}
{{- $llm := index $nims "nemotron35" | default dict -}}
{{- $on := and
      (include "vss.ingress.enabled" (dict "vals" $nims "default" false))
      (include "vss.ingress.enabled" (dict "vals" $llm "default" false)) -}}
{{- if $on -}}
{{- $name := "" -}}
{{- if $llm.fullnameOverride -}}
{{- $name = $llm.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $short := default "nemotron-35-lightning-30b-a3b" $llm.nameOverride -}}
{{- /* Same precedence the subchart's own fullname helper sees: its leaf value,
       then the global Helm merges into it (nims.global, then the chart root). */}}
{{- $nimsGlobal := index $nims "global" | default dict -}}
{{- $g := index $vals "global" | default dict -}}
{{- $pfx := default false (coalesce
      (index $llm "useReleaseNamePrefix")
      (index $nimsGlobal "useReleaseNamePrefix")
      (index $g "useReleaseNamePrefix")) -}}
{{- $name = ternary (printf "%s-%s" $root.Release.Name $short) $short $pfx -}}
{{- $name = $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- /* The NIM's own port, not a vssIngress key: a second place to write it is a
       second place for it to disagree with the Service it is mounting. 8000 is
       the nims chart default, which the minimal profiles do not restate. */}}
{{- $svc := index $llm "service" | default dict -}}
service: {{ $name }}
port: {{ index $svc "port" | default 8000 }}
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

{{/*
  Whether a row may be published on the public Ingress rule (FR-29).

  This is the Kubernetes half of internal-only enforcement, and it is the
  strongest form available here: an Ingress rule under the deployment's public
  host IS the public exposure, so the way to make a mount unreachable from
  outside is not to create it. There is no Host or source predicate to add --
  the controller would have to route the request before anything could refuse
  it.

  An internal-only row is therefore skipped, unless it names a
  `publicMountException` -- a written reason why it is still published while
  somebody decides. `check_gateway_route_exposure.py` requires the reason to be
  non-empty, so an exception is a sentence in this file rather than a silent
  boolean.
*/}}
{{- define "vss.ingress.publiclyMountable" -}}
{{- $row := .row -}}
{{- if ne ($row.exposure | default "") "internal-only" -}}
true
{{- else if $row.publicMountException -}}
true
{{- end -}}
{{- end -}}

{{- define "vss.ingress.pathRows" -}}
{{- $backends := .backends | default dict -}}
{{- $only := .only | default (list) -}}
{{- range $row := include "vss.ingress.routeTable" . | fromYamlArray }}
{{- $b := index $backends $row.key | default dict }}
{{- if and $b.service (include "vss.ingress.publiclyMountable" (dict "row" $row)) (or (eq (len $only) 0) (has $row.key $only)) }}
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
{{- /* A route that is not mounted must not leave a rewrite pair behind: the
       annotation would name a prefix no rule routes. Same predicate as the
       paths, so the two cannot disagree about what is published. */}}
{{- if and $b.service (include "vss.ingress.publiclyMountable" (dict "row" $row)) (ne $rw "none") }}
{{- $to := ternary "" $rw (eq $rw "strip") }}
^{{ $row.path }}/(.*) {{ $to }}/\1
^{{ $row.path }}$ {{ $to | default "/" }}
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
