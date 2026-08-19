# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

{{- define "nemotron35lightning.name" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- default "nemotron-3-5-lightning-30b-a3b" .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}

{{- define "nemotron35lightning.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := include "nemotron35lightning.name" . }}
{{- $g := .Values.global | default dict }}
{{- $pfx := default false (coalesce .Values.useReleaseNamePrefix (index $g "useReleaseNamePrefix")) }}
{{- if $pfx }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name }}
{{- end }}
{{- end }}
{{- end }}

{{- define "nemotron35lightning.labels" -}}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
app.kubernetes.io/name: {{ include "nemotron35lightning.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/component: llm-nim
{{- end }}

{{- define "nemotron35lightning.selectorLabels" -}}
app.kubernetes.io/name: {{ include "nemotron35lightning.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "nemotron35lightning.storageClass" -}}
{{- if .Values.storage.pvc.storageClass }}
{{- .Values.storage.pvc.storageClass }}
{{- else }}
{{- .Values.global.storageClass }}
{{- end }}
{{- end }}
