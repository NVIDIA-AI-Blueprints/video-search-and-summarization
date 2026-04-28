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

{{- define "kafka.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "kafka.fullname" -}}
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

{{- define "kafka.labels" -}}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
app.kubernetes.io/name: {{ include "kafka.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{- define "kafka.selectorLabels" -}}
app.kubernetes.io/name: {{ include "kafka.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Advertised PLAINTEXT listener host — matches the primary broker ClusterIP Service (kafka-kafka / <release>-kafka-kafka) so client metadata aligns with bootstrap URLs used across profiles.
Override with advertisedHost when a custom listener hostname is required.
*/}}
{{- define "kafka.advertisedHost" -}}
{{- if .Values.advertisedHost }}
{{- .Values.advertisedHost | trim }}
{{- else }}
{{- include "kafka.brokerServiceHost" . | trim }}
{{- end }}
{{- end }}

{{/*
Topic list for the init Job: YAML slice is rendered as JSON; a string value is passed through (legacy JSON-in-values).
*/}}
{{- define "kafka.topicsJSON" -}}
{{- $t := .Values.topics }}
{{- if kindIs "string" $t }}
{{- $t }}
{{- else if kindIs "slice" $t }}
{{- if $t }}
{{- toJson $t }}
{{- else }}
[]
{{- end }}
{{- else }}
[]
{{- end }}
{{- end }}

{{/*
Broker ClusterIP service hostname (matches templates/service.yaml).
*/}}
{{- define "kafka.brokerServiceHost" -}}
{{- printf "%s-kafka" (include "kafka.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Prometheus kafka-exporter workload name (legacy: <release>-kafka-exporter when prefixed).
*/}}
{{- define "kafka.exporterIdent" -}}
{{- $g := .Values.global | default dict }}
{{- $pfx := default false (coalesce .Values.useReleaseNamePrefix (index $g "useReleaseNamePrefix")) }}
{{- if $pfx }}
{{- printf "%s-kafka-exporter" .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-kafka-exporter" (include "kafka.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
