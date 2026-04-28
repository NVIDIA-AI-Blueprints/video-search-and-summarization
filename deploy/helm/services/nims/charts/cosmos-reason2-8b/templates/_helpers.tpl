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
Resource basename aligned with dev-profile-base nims chart (nvidia-cosmos-reason2-8b)
so NIMService / operator pod names match other Metropolis developer profiles.
Override via nameOverride / fullnameOverride when needed.
*/}}
{{- define "cosmos.name" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- default "nvidia-cosmos-reason2-8b" .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}

{{- define "cosmos.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := include "cosmos.name" . }}
{{- $g := .Values.global | default dict }}
{{- $pfx := default false (coalesce .Values.useReleaseNamePrefix (index $g "useReleaseNamePrefix")) }}
{{- if $pfx }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name }}
{{- end }}
{{- end }}
{{- end }}

{{- define "cosmos.labels" -}}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
app.kubernetes.io/name: {{ include "cosmos.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/component: vlm-nim
{{- end }}

{{- define "cosmos.selectorLabels" -}}
app.kubernetes.io/name: {{ include "cosmos.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Resolve effective storage class: subchart value takes precedence, else global.
*/}}
{{- define "cosmos.storageClass" -}}
{{- if .Values.storage.pvc.storageClass }}
{{- .Values.storage.pvc.storageClass }}
{{- else }}
{{- .Values.global.storageClass }}
{{- end }}
{{- end }}
