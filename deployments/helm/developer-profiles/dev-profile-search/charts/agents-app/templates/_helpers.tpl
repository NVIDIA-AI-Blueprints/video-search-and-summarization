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

{{/*
Expand the name of the chart.
*/}}
{{- define "agents.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "agents.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "agents.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "agents.labels" -}}
helm.sh/chart: {{ include "agents.chart" . }}
{{ include "agents.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "agents.selectorLabels" -}}
app.kubernetes.io/name: {{ include "agents.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Create the name of the service account to use
*/}}
{{- define "agents.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "agents.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
Component specific labels
*/}}
{{- define "agents.componentLabels" -}}
app.kubernetes.io/component: {{ .component }}
{{- end }}

{{/*
Generate labels for a component
*/}}
{{- define "agents.componentSelectorLabels" -}}
app.kubernetes.io/name: {{ include "agents.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end }}

{{/*
Get the host IP - uses global.hostIP (DEPRECATED: use agents.externalHost for browser-facing URLs)
*/}}
{{- define "agents.hostIP" -}}
{{- .Values.global.hostIP }}
{{- end }}

{{/*
Get the external host for browser-accessible URLs
Falls back to global.hostIP for backward compatibility
*/}}
{{- define "agents.externalHost" -}}
{{- if .Values.global.externalHost }}
{{- .Values.global.externalHost }}
{{- else }}
{{- .Values.global.hostIP }}
{{- end }}
{{- end }}

{{/*
Get VST MCP URL (internal service-to-service communication)
Auto-wires to <release>-vst-mcp:8001 when not explicitly set.
*/}}
{{- define "agents.vstMcpUrl" -}}
{{- if .Values.vssAgent.vst.mcpUrl }}
{{- .Values.vssAgent.vst.mcpUrl }}
{{- else }}
{{- printf "http://%s-vst-mcp:8001" .Release.Name }}
{{- end }}
{{- end }}

{{/*
=============================================================================
Ingress detection helpers
=============================================================================
*/}}

{{/*
Check if Ingress is enabled via global.ingress.enabled.
Returns "true" (truthy) or "" (falsy).
*/}}
{{- define "agents.isIngressEnabled" -}}
{{- $gIngress := index (.Values.global | default dict) "ingress" | default dict }}
{{- if (index $gIngress "enabled") }}true{{- end }}
{{- end }}

{{/*
Get the Ingress main host (for VSS UI, Agent, VST routes).
Priority: global.ingress.mainHost > auto-constructed from global.externalHost.
*/}}
{{- define "agents.ingressMainHost" -}}
{{- $gIngress := index (.Values.global | default dict) "ingress" | default dict }}
{{- $mainHost := index $gIngress "mainHost" | default "" }}
{{- if $mainHost }}
{{- $mainHost }}
{{- else }}
{{- $gExHost := index (.Values.global | default dict) "externalHost" | default "127.0.0.1" }}
{{- printf "vss-search.%s.nip.io" $gExHost }}
{{- end }}
{{- end }}

{{/*
Get the Ingress streamer host (for NVStreamer HTTP API).
Priority: global.ingress.streamerHost > auto-constructed from global.externalHost.
*/}}
{{- define "agents.ingressStreamerHost" -}}
{{- $gIngress := index (.Values.global | default dict) "ingress" | default dict }}
{{- $streamerHost := index $gIngress "streamerHost" | default "" }}
{{- if $streamerHost }}
{{- $streamerHost }}
{{- else }}
{{- $gExHost := index (.Values.global | default dict) "externalHost" | default "127.0.0.1" }}
{{- printf "streamer.%s.nip.io" $gExHost }}
{{- end }}
{{- end }}

{{/*
=============================================================================
External URL helpers for browser-accessible services (vss-ui)
When Ingress is enabled, URLs use Ingress hostnames (port 80).
When disabled (NodePort), URLs use externalHost + NodePort.
=============================================================================
*/}}

{{/*
Get external VSS Agent WebSocket URL (for browser chat)
*/}}
{{- define "agents.external.vssAgentWebsocketUrl" -}}
{{- if .Values.vssUi.externalUrls.vssAgentWebsocket }}
{{- .Values.vssUi.externalUrls.vssAgentWebsocket }}
{{- else if (include "agents.isIngressEnabled" .) }}
{{- printf "ws://%s/websocket" (include "agents.ingressMainHost" .) }}
{{- else }}
{{- printf "ws://%s:%d/websocket" (include "agents.externalHost" .) (int .Values.vssUi.externalPorts.vssAgent) }}
{{- end }}
{{- end }}

{{/*
Get external VSS Agent HTTP URL (for browser chat stream)
*/}}
{{- define "agents.external.vssAgentHttpUrl" -}}
{{- if .Values.vssUi.externalUrls.vssAgentHttp }}
{{- .Values.vssUi.externalUrls.vssAgentHttp }}
{{- else if (include "agents.isIngressEnabled" .) }}
{{- printf "http://%s/chat/stream" (include "agents.ingressMainHost" .) }}
{{- else }}
{{- printf "http://%s:%d/chat/stream" (include "agents.externalHost" .) (int .Values.vssUi.externalPorts.vssAgent) }}
{{- end }}
{{- end }}

{{/*
Get external VSS Agent API Base URL (for browser API access)
*/}}
{{- define "agents.external.vssAgentApiUrl" -}}
{{- if .Values.vssUi.externalUrls.vssAgentApi }}
{{- .Values.vssUi.externalUrls.vssAgentApi }}
{{- else if (include "agents.isIngressEnabled" .) }}
{{- printf "http://%s/api/v1" (include "agents.ingressMainHost" .) }}
{{- else }}
{{- printf "http://%s:%d/api/v1" (include "agents.externalHost" .) (int .Values.vssUi.externalPorts.vssAgent) }}
{{- end }}
{{- end }}

{{/*
Get external VST API URL (for browser — video management, stream operations)
*/}}
{{- define "agents.external.vstApiUrl" -}}
{{- if .Values.vssUi.externalUrls.vstApi }}
{{- .Values.vssUi.externalUrls.vstApi }}
{{- else if (include "agents.isIngressEnabled" .) }}
{{- printf "http://%s/vst/api" (include "agents.ingressMainHost" .) }}
{{- else }}
{{- printf "http://%s:%d/vst/api" (include "agents.externalHost" .) (int .Values.vssUi.externalPorts.vst) }}
{{- end }}
{{- end }}

{{/*
Get external MDX Web API URL (for browser alerts)
*/}}
{{- define "agents.external.mdxWebApiUrl" -}}
{{- if .Values.vssUi.externalUrls.mdxWebApi }}
{{- .Values.vssUi.externalUrls.mdxWebApi }}
{{- else if (include "agents.isIngressEnabled" .) }}
{{- $pathPrefix := default "" .Values.vssUi.apiPathPrefixes.mdx }}
{{- printf "http://%s%s" (include "agents.ingressMainHost" .) $pathPrefix }}
{{- else }}
{{- $pathPrefix := default "" .Values.vssUi.apiPathPrefixes.mdx }}
{{- printf "http://%s:%d%s" (include "agents.externalHost" .) (int .Values.vssUi.externalPorts.mdx) $pathPrefix }}
{{- end }}
{{- end }}

{{/*
Get external Kibana Dashboard URL (for browser dashboard).
Priority: explicit externalUrls.kibana > global.kibanaPublicUrl > ingress auto-wire > NodePort fallback
*/}}
{{- define "agents.external.kibanaUrl" -}}
{{- if .Values.vssUi.externalUrls.kibana }}
{{- .Values.vssUi.externalUrls.kibana }}
{{- else }}
{{- $gExHost := index (.Values.global | default dict) "externalHost" | default "" }}
{{- $gKibUrl := index (.Values.global | default dict) "kibanaPublicUrl" | default "" }}
{{- if $gKibUrl }}
{{- printf "%s/app/dashboards" $gKibUrl }}
{{- else if (include "agents.isIngressEnabled" .) }}
{{- printf "http://kibana.%s.nip.io/app/dashboards" $gExHost }}
{{- else }}
{{- printf "http://%s:%d/app/dashboards" (include "agents.externalHost" .) (int .Values.vssUi.externalPorts.kibana) }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Get external Map URL (for browser map tab)
*/}}
{{- define "agents.external.mapUrl" -}}
{{- if .Values.vssUi.externalUrls.map }}
{{- .Values.vssUi.externalUrls.map }}
{{- else if (include "agents.isIngressEnabled" .) }}
{{- printf "http://%s/ui/smartcities" (include "agents.ingressMainHost" .) }}
{{- else }}
{{- printf "http://%s:%d/ui/smartcities" (include "agents.externalHost" .) (int .Values.vssUi.externalPorts.mdx) }}
{{- end }}
{{- end }}

{{/*
=============================================================================
Blueprint Configuration Generic Helpers
All blueprint-specific config is in .Values.blueprintConfigs[blueprint]
Users should edit blueprintConfigs directly for their selected blueprint
These generic helpers work for ANY blueprint config value - no need to add
new helpers when adding new config values.
=============================================================================
*/}}

{{/*
Generic helper to get any value from blueprintConfigs based on selected blueprint.
Accepts a dict with "root" (context) and "keys" (list of nested keys to traverse).

Usage examples:
  {{ include "agents.bpValue" (dict "root" . "keys" (list "ui" "appTitle")) }}
  {{ include "agents.bpValue" (dict "root" . "keys" (list "vaMcp" "vlmVerified")) }}
  {{ include "agents.bpValue" (dict "root" . "keys" (list "ui" "map" "enabled")) }}
*/}}
{{- define "agents.bpValue" -}}
{{- $bp := .root.Values.blueprint -}}
{{- $config := index .root.Values.blueprintConfigs $bp -}}
{{- $result := $config -}}
{{- range .keys -}}
{{- $result = index $result . -}}
{{- end -}}
{{- $result -}}
{{- end }}

{{/*
Helper for boolean values that need string output ("true"/"false")
Use this when the value will be quoted in YAML (e.g., env vars)
*/}}
{{- define "agents.bpBool" -}}
{{- $bp := .root.Values.blueprint -}}
{{- $config := index .root.Values.blueprintConfigs $bp -}}
{{- $result := $config -}}
{{- range .keys -}}
{{- $result = index $result . -}}
{{- end -}}
{{- if $result }}true{{- else }}false{{- end -}}
{{- end }}

{{/*
Helper for list values that need YAML output
*/}}
{{- define "agents.bpList" -}}
{{- $bp := .root.Values.blueprint -}}
{{- $config := index .root.Values.blueprintConfigs $bp -}}
{{- $result := $config -}}
{{- range .keys -}}
{{- $result = index $result . -}}
{{- end -}}
{{- $result | toYaml -}}
{{- end }}

{{/*
Get VST Base URL
Should be set to the ingress or service URL for VST API
*/}}
{{- define "agents.vstBaseUrl" -}}
{{- .Values.vssAgent.vst.baseUrl }}
{{- end }}

{{/*
Get VST Internal URL for server-to-server communication.
RTVI-embed requires IP-based URLs (rejects hostnames), so this uses
the external host IP + vst-ingress NodePort for URL translation.
Falls back to K8s service name if externalHost is not set.
*/}}
{{- define "agents.vstInternalUrl" -}}
{{- if .Values.vssAgent.vst.internalUrl }}
{{- .Values.vssAgent.vst.internalUrl }}
{{- else }}
{{- $gExHost := index (.Values.global | default dict) "externalHost" | default "" }}
{{- if $gExHost }}
{{- printf "http://%s:%d" $gExHost (int .Values.vssUi.externalPorts.vst) }}
{{- else }}
{{- printf "http://%s-vst-ingress:8000" .Release.Name }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Get VST Internal Host (scheme-stripped, for code paths that prepend http://).
Returns just "host:port" without the http:// prefix.
*/}}
{{- define "agents.vstInternalHost" -}}
{{- include "agents.vstInternalUrl" . | trimPrefix "http://" | trimPrefix "https://" }}
{{- end }}

{{/*
Get VST External URL (browser-accessible, via NodePort or Ingress)
*/}}
{{- define "agents.vstExternalUrl" -}}
{{- if .Values.vssAgent.vst.externalUrl }}
{{- .Values.vssAgent.vst.externalUrl }}
{{- else if (include "agents.isIngressEnabled" .) }}
{{- printf "http://%s" (include "agents.ingressMainHost" .) }}
{{- else }}
{{- printf "http://%s:%d" (include "agents.externalHost" .) (int .Values.vssUi.externalPorts.vst) }}
{{- end }}
{{- end }}


{{/*
Get Video Analysis MCP URL (internal)
*/}}
{{- define "agents.vaMcpUrl" -}}
{{- printf "http://%s-vss-va-mcp:%d" (include "agents.fullname" .) (int .Values.vssVaMcp.port) }}
{{- end }}

{{/*
Get Elasticsearch URL
Auto-wires to http://<release>-elasticsearch:9200 when not explicitly set.
*/}}
{{- define "agents.elasticsearchUrl" -}}
{{- if .Values.elasticsearch.url }}
{{- .Values.elasticsearch.url }}
{{- else }}
{{- printf "http://%s-elasticsearch-elasticsearch:9200" .Release.Name }}
{{- end }}
{{- end }}

{{/*
Get Cosmos Embed endpoint (RTVI Embed service URL).
Auto-wires to http://<release>-rtvi-embed:8000 when not explicitly set.
*/}}
{{- define "agents.cosmosEmbedEndpoint" -}}
{{- if .Values.vssAgent.cosmosEmbed.endpoint }}
{{- .Values.vssAgent.cosmosEmbed.endpoint }}
{{- else }}
{{- printf "http://%s-rtvi-embed-rtvi-embed:8000" .Release.Name }}
{{- end }}
{{- end }}

{{/*
Get Phoenix telemetry endpoint.
Auto-wires to http://<release>-phoenix:6006 when not explicitly set.
*/}}
{{- define "agents.phoenixEndpoint" -}}
{{- if .Values.vssAgent.phoenix.endpoint }}
{{- .Values.vssAgent.phoenix.endpoint }}
{{- else }}
{{- printf "http://%s-phoenix:6006" .Release.Name }}
{{- end }}
{{- end }}

{{/*
Get LLM base URL (Nemotron NIM).
Auto-wires to http://<release>-nemotron:8000 when not explicitly set.
The NIMService CRD creates a Service named <release>-nemotron via the nims subchart.
*/}}
{{- define "agents.llmBaseUrl" -}}
{{- if .Values.vssAgent.llm.baseUrl }}
{{- .Values.vssAgent.llm.baseUrl }}
{{- else }}
{{- printf "http://%s-nemotron:8000" .Release.Name }}
{{- end }}
{{- end }}

{{/*
Get VLM base URL (Cosmos Reason2 8B NIM).
Auto-wires to http://<release>-cosmos:8000 when not explicitly set.
The NIMService CRD creates a Service named <release>-cosmos via the nims subchart.
*/}}
{{- define "agents.vlmBaseUrl" -}}
{{- if .Values.vssAgent.vlm.baseUrl }}
{{- .Values.vssAgent.vlm.baseUrl }}
{{- else }}
{{- printf "http://%s-cosmos:8000" .Release.Name }}
{{- end }}
{{- end }}

{{/*
Get RTVI CV base URL (perception service).
Auto-wires to http://<release>-perception-perception:9000 when not explicitly set.
*/}}
{{- define "agents.rtviCvBaseUrl" -}}
{{- if .Values.vssAgent.rtvi.cvBaseUrl }}
{{- .Values.vssAgent.rtvi.cvBaseUrl }}
{{- else }}
{{- printf "http://%s-perception-perception:%s" .Release.Name (.Values.vssAgent.rtvi.cvPort | default "9000") }}
{{- end }}
{{- end }}

{{/*
Get VSS Agent Reports Base URL (points to vss-agent /static/ endpoint)
*/}}
{{- define "agents.reportsBaseUrl" -}}
{{- printf "http://%s-vss-agent:%d/static/" (include "agents.fullname" .) (int .Values.vssAgent.port) }}
{{- end }}


