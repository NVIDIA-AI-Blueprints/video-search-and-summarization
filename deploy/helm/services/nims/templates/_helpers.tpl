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

{{- define "nims.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "nims.fullname" -}}
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

{{- define "nims.labels" -}}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
app.kubernetes.io/name: {{ include "nims.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Resolve the effective resource name for each NIM.
Must stay in sync with the subchart _helpers.tpl fullname templates so the
ConfigMaps rendered here match the names the NIMService envFrom references.
*/}}
{{- define "nims.nemotron.fullname" -}}
{{- if .Values.nemotron.fullnameOverride }}
{{- .Values.nemotron.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $short := "nvidia-nemotron-nano-9b-v2" }}
{{- $g := .Values.global | default dict }}
{{- $pfx := default false (coalesce .Values.useReleaseNamePrefix (index $g "useReleaseNamePrefix")) }}
{{- if $pfx }}
{{- printf "%s-%s" .Release.Name $short | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $short }}
{{- end }}
{{- end }}
{{- end }}

{{- define "nims.cosmos.fullname" -}}
{{- if .Values.cosmos.fullnameOverride }}
{{- .Values.cosmos.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $short := "nvidia-cosmos-reason2-8b" }}
{{- $g := .Values.global | default dict }}
{{- $pfx := default false (coalesce .Values.useReleaseNamePrefix (index $g "useReleaseNamePrefix")) }}
{{- if $pfx }}
{{- printf "%s-%s" .Release.Name $short | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $short }}
{{- end }}
{{- end }}
{{- end }}
