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

{{- define "vss-vios-streamprocessing.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- $global := .Values.global | default dict }}
{{- if and (hasKey .Values "useReleaseNamePrefix") (kindIs "bool" .Values.useReleaseNamePrefix) .Values.useReleaseNamePrefix }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- else if and (hasKey .Values "useReleaseNamePrefix") (kindIs "bool" .Values.useReleaseNamePrefix) (not .Values.useReleaseNamePrefix) }}
{{- printf "%s" $name | trunc 63 | trimSuffix "-" }}
{{- else if and (hasKey $global "useReleaseNamePrefix") (kindIs "bool" (index $global "useReleaseNamePrefix")) (index $global "useReleaseNamePrefix") }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s" $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}
{{/* Shared VST PVC names when the vios umbrella creates claims (vios.vstStorage.createSharedPvcs): top-level Helm release + fixed suffixes. */}}
{{- define "vss-vios-streamprocessing.sharedVstClaimVstData" -}}
{{- printf "%s-vst-data" (.Release.Name | trunc 63 | trimSuffix "-") }}
{{- end }}
{{- define "vss-vios-streamprocessing.sharedVstClaimVstVideo" -}}
{{- printf "%s-vst-video" (.Release.Name | trunc 63 | trimSuffix "-") }}
{{- end }}
{{- define "vss-vios-streamprocessing.sharedVstClaimStreamerVideos" -}}
{{- printf "%s-vst-streamer-videos" (.Release.Name | trunc 63 | trimSuffix "-") }}
{{- end }}
{{- define "vss-vios-streamprocessing.image" -}}{{ printf "%s:%s" .Values.image.repository .Values.image.tag }}{{- end -}}
{{/* Matches charts/vios/charts/vios-postgres vss-vios-postgres.fullname (sibling subchart). */}}
{{- define "vss-vios-streamprocessing.postgresFullname" -}}
{{- $g := .Values.global | default dict }}
{{- if index $g "postgresServiceHost" }}{{ index $g "postgresServiceHost" }}
{{- else }}
{{- $name := "vss-vios-postgres" }}
{{- $usePrefix := default false (coalesce .Values.useReleaseNamePrefix (index $g "useReleaseNamePrefix")) }}
{{- if $usePrefix }}{{ printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- else }}{{ printf "%s" $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}
{{- define "vss-vios-streamprocessing.postgresCmName" -}}
{{- printf "%s-postgres-cm" (include "vss-vios-streamprocessing.postgresFullname" .) }}
{{- end }}
{{- define "vss-vios-streamprocessing.headlessServiceName" -}}
{{- if .Values.useSdrEnvoyStyleHeadless }}
{{- printf "%s-streamprocessing-ms-headless" .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else if .Values.headlessServiceName }}
{{- .Values.headlessServiceName | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-headless" (include "vss-vios-streamprocessing.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{/* In-cluster DNS short name for sibling charts when global.useReleaseNamePrefix is set. */}}
{{- define "vss-vios-streamprocessing.peerHost" -}}
{{- $root := index . "root" -}}
{{- $short := index . "short" -}}
{{- $g := $root.Values.global | default dict }}
{{- $pfx := default false (coalesce $root.Values.useReleaseNamePrefix (index $g "useReleaseNamePrefix")) }}
{{- if $pfx }}{{ printf "%s-%s" $root.Release.Name $short }}{{- else -}}{{ $short }}{{- end }}
{{- end }}
{{/*
  VST_INGRESS_ENDPOINT: host[:port]/vst (no scheme; app prepends http://). Incident/video URLs must be reachable from remote VLM when global.vlmBaseUrl is set — use global.externalHost like vss-alert-bridge vst_config.
*/}}
{{- define "vss-vios-streamprocessing.vstIngressEndpoint" -}}
{{- $g := .Values.global | default dict }}
{{- $pfx := default false (coalesce .Values.useReleaseNamePrefix (index $g "useReleaseNamePrefix")) }}
{{- $eh := index $g "externalHost" | default "" | trim }}
{{- $ep := index $g "externalPort" | default "" | trim }}
{{- $globVlm := trim (default "" (index $g "vlmBaseUrl")) }}
{{- $explicit := trim (default "" .Values.vstIngressEndpoint) }}
{{- if ne $explicit "" }}
{{- $explicit }}
{{- else }}
{{- $internal := ternary (printf "%s-vss-vios-ingress:30888/vst" .Release.Name) "vss-vios-ingress:30888/vst" $pfx }}
{{- if and (ne $globVlm "") (ne $eh "") }}
{{- if ne $ep "" }}
{{- printf "%s:%s/vst" $eh $ep }}
{{- else }}
{{- printf "%s/vst" $eh }}
{{- end }}
{{- else }}
{{- $internal }}
{{- end }}
{{- end }}
{{- end }}
