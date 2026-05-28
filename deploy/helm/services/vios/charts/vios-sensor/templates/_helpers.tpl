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

{{- define "vss-vios-sensor.fullname" -}}
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
{{/* Base name for shared VST PVCs: must match vss-vios-streamprocessing.sharedVstClaim* (umbrella Helm .Release.Name). */}}
{{- define "vss-vios-sensor.streamprocessingSharedPvcBase" -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- define "vss-vios-sensor.image" -}}{{ printf "%s:%s" .Values.image.repository .Values.image.tag }}{{- end -}}
{{/* Matches charts/vios/charts/vios-postgres vss-vios-postgres.fullname (sibling subchart). */}}
{{- define "vss-vios-sensor.postgresFullname" -}}
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
{{- define "vss-vios-sensor.postgresCmName" -}}
{{- printf "%s-postgres-cm" (include "vss-vios-sensor.postgresFullname" .) }}
{{- end }}
{{- define "vss-vios-sensor.peerHost" -}}
{{- $root := index . "root" -}}
{{- $short := index . "short" -}}
{{- $g := $root.Values.global | default dict }}
{{- $pfx := default false (coalesce $root.Values.useReleaseNamePrefix (index $g "useReleaseNamePrefix")) }}
{{- if $pfx }}{{ printf "%s-%s" $root.Release.Name $short }}{{- else -}}{{ $short }}{{- end }}
{{- end }}
{{/*
  Full VST ingress base URL (with scheme). Align with vss-vios-streamprocessing.vstIngressEndpoint when global.vlmBaseUrl + global.externalHost are set (remote VLM / public incident URLs).
*/}}
{{- define "vss-vios-sensor.vstIngressEndpointUrl" -}}
{{- $g := .Values.global | default dict }}
{{- $pfx := default false (coalesce .Values.useReleaseNamePrefix (index $g "useReleaseNamePrefix")) }}
{{- $eh := index $g "externalHost" | default "" | trim }}
{{- $ep := index $g "externalPort" | default "" | trim }}
{{- $es := index $g "externalScheme" | default "http" }}
{{- $globVlm := trim (default "" (index $g "vlmBaseUrl")) }}
{{- $explicit := trim (default "" .Values.vstIngressEndpoint) }}
{{- if ne $explicit "" }}
{{- $explicit }}
{{- else }}
{{- $internal := printf "http://%s" (ternary (printf "%s-vss-vios-ingress:30888/vst" .Release.Name) "vss-vios-ingress:30888/vst" $pfx) }}
{{- if and (ne $globVlm "") (ne $eh "") }}
{{- if ne $ep "" }}
{{- printf "%s://%s:%s/vst" $es $eh $ep }}
{{- else }}
{{- printf "%s://%s/vst" $es $eh }}
{{- end }}
{{- else }}
{{- $internal }}
{{- end }}
{{- end }}
{{- end }}
