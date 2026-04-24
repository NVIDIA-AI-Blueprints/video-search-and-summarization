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
Expand chart name.
*/}}
{{- define "dev-profile-search.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Fully-qualified app name.
*/}}
{{- define "dev-profile-search.fullname" -}}
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
Chart label.
*/}}
{{- define "dev-profile-search.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels.
*/}}
{{- define "dev-profile-search.labels" -}}
helm.sh/chart: {{ include "dev-profile-search.chart" . }}
{{ include "dev-profile-search.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: metropolis-dev-profile-search
{{- end }}

{{/*
Selector labels.
*/}}
{{- define "dev-profile-search.selectorLabels" -}}
app.kubernetes.io/name: {{ include "dev-profile-search.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Kubernetes Service.metadata.name for a sibling subchart short name.
Respects global.useReleaseNamePrefix (dict "root" $ "short" "redis").
*/}}
{{- define "dev-profile-search.serviceShort" -}}
{{- $short := index . "short" -}}
{{- $root := index . "root" -}}
{{- $g := $root.Values.global | default dict }}
{{- $pfx := default false (coalesce $root.Values.useReleaseNamePrefix (index $g "useReleaseNamePrefix")) }}
{{- if $pfx }}{{ printf "%s-%s" $root.Release.Name $short | trunc 63 | trimSuffix "-" }}{{- else -}}{{ $short | trunc 63 | trimSuffix "-" }}{{- end }}
{{- end }}

{{/*
Shared PVC for nvstreamer / streamprocessing (templates/shared-streamer-videos-pvc.yaml).
*/}}
{{- define "dev-profile-search.streamerVideosPvcName" -}}
{{- $g := .Values.global | default dict }}
{{- $pfx := default false (coalesce .Values.useReleaseNamePrefix (index $g "useReleaseNamePrefix")) }}
{{- if $pfx }}
{{- printf "%s-streamer-videos" .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- print "streamer-videos" }}
{{- end }}
{{- end }}

{{/*
NGC image pull secret name.
*/}}
{{- define "dev-profile-search.imagePullSecretName" -}}
{{- default "ngc-docker-reg-secret" .Values.global.imagePullSecretName }}
{{- end }}

{{/*
  Resolves the Kubernetes name for a dependency subchart (same rules as each subchart's .fullname helper).
  Pass: dict "Values" .Values "Release" .Release "depKey" "vss-agent" "chartName" "vss-agent"
  Optional: "subchartValues" (dict) — when set, used instead of index .Values .depKey.
*/}}
{{- define "vss.subchartFullname" -}}
{{- $vios := index .Values "vios" | default dict }}
{{- $agent := index .Values "agent" | default dict }}
{{- $agentVss := index $agent "vss-agent" | default (index .Values "vss-agent") | default dict }}
{{- $topDep := index .Values .depKey | default (index $vios .depKey) | default dict }}
{{- $fromDep := ternary $agentVss $topDep (eq .depKey "vss-agent") }}
{{- $vals := .subchartValues | default $fromDep | default dict }}
{{- if $vals.fullnameOverride }}
{{- $vals.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .chartName $vals.nameOverride }}
{{- $global := .Values.global | default dict }}
{{- $usePrefix := default false (coalesce $vals.useReleaseNamePrefix (index $global "useReleaseNamePrefix")) }}
{{- if $usePrefix }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s" $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{- define "dev-profile-search.rtviScriptsConfigMapName" -}}
{{- $rtviRoot := index .Values "rtvi" | default dict -}}
{{- $rtvi := index $rtviRoot "vss-rtvi-cv" | default dict -}}
{{- $existing := dig "scripts" "existingConfigMap" "" $rtvi -}}
{{- if $existing }}
{{- $existing -}}
{{- else -}}
{{- $base := "" -}}
{{- if (index $rtvi "fullnameOverride") -}}
{{- $base = (index $rtvi "fullnameOverride") -}}
{{- else -}}
{{- $name := default "vss-rtvi-cv" (index $rtvi "nameOverride") -}}
{{- $global := .Values.global | default dict -}}
{{- $usePrefix := default false (coalesce (index $rtvi "useReleaseNamePrefix") (index $global "useReleaseNamePrefix")) -}}
{{- if $usePrefix -}}
{{- $base = printf "%s-%s" .Release.Name $name -}}
{{- else -}}
{{- $base = printf "%s" $name -}}
{{- end -}}
{{- end -}}
{{- printf "%s-scripts" $base | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end }}
