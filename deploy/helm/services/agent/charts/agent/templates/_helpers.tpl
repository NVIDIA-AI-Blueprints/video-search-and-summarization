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

{{- define "vss-agent.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- $global := .Values.global | default dict }}
{{- $usePrefix := default false (coalesce .Values.useReleaseNamePrefix (index $global "useReleaseNamePrefix")) }}
{{- if $usePrefix }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s" $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}
{{- define "vss-agent.image" -}}{{ printf "%s:%s" .Values.image.repository .Values.image.tag }}{{- end -}}
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
{{- $global := .Values.global | default dict }}
{{- $usePrefix := default false (coalesce .Values.useReleaseNamePrefix (index $global "useReleaseNamePrefix")) }}
{{- if $usePrefix }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s" $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
In-cluster Kubernetes Service hostname (short name or <release>-<short>).
*/}}
{{- define "agents.k8sSvcHost" -}}
{{- $root := index . "root" }}
{{- $short := index . "short" }}
{{- $g := $root.Values.global | default dict }}
{{- $pfx := default false (coalesce $root.Values.useReleaseNamePrefix (index $g "useReleaseNamePrefix")) }}
{{- if $pfx }}
{{- printf "%s-%s" $root.Release.Name $short }}
{{- else }}
{{- $short }}
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
Get VST MCP URL (internal service-to-service communication).
Auto-wires to <release>-vss-vios-mcp:8001 when not explicitly set (flat .Values.vstMcpUrl).
*/}}
{{- define "agents.vstMcpUrl" -}}
{{- if .Values.vstMcpUrl }}
{{- .Values.vstMcpUrl }}
{{- else }}
{{- printf "http://%s:8001" (include "agents.k8sSvcHost" (dict "root" . "short" "vss-vios-mcp")) }}
{{- end }}
{{- end }}
