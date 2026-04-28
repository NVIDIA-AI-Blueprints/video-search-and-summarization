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

{{- define "vss.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- define "vss.profile" -}}
{{- default "base" .Values.profile | quote -}}
{{- end -}}
{{- define "vss.mode" -}}
{{- default "" .Values.mode | quote -}}
{{- end -}}
{{- define "vss.ngcDockerConfigJson" -}}
{{- $auth := printf "$oauthtoken:%s" .Values.ngc.apiKey | b64enc -}}
{{- $authObj := dict "username" "$oauthtoken" "password" .Values.ngc.apiKey "auth" $auth -}}
{{- $auths := dict "nvcr.io" $authObj -}}
{{- dict "auths" $auths | toJson -}}
{{- end -}}
{{/* In-cluster Service.metadata.name for a sibling subchart short name; respects global.useReleaseNamePrefix. */}}
{{- define "vss.lvs.serviceShort" -}}
{{- $short := index . "short" -}}
{{- $root := index . "root" -}}
{{- $g := $root.Values.global | default dict }}
{{- $pfx := default false (index $g "useReleaseNamePrefix") }}
{{- if $pfx }}{{ printf "%s-%s" $root.Release.Name $short | trunc 63 | trimSuffix "-" }}{{- else -}}{{ $short | trunc 63 | trimSuffix "-" }}{{- end }}
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
