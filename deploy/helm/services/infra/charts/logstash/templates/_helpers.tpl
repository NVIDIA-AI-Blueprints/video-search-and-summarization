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

{{- define "logstash.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "logstash.fullname" -}}
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

{{- define "logstash.labels" -}}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
app.kubernetes.io/name: {{ include "logstash.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{- define "logstash.selectorLabels" -}}
app.kubernetes.io/name: {{ include "logstash.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Prometheus logstash-exporter workload name (legacy: <release>-logstash-exporter when prefixed).
*/}}
{{- define "logstash.exporterIdent" -}}
{{- $g := .Values.global | default dict }}
{{- $pfx := default false (coalesce .Values.useReleaseNamePrefix (index $g "useReleaseNamePrefix")) }}
{{- if $pfx }}
{{- printf "%s-logstash-exporter" .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-exporter" (include "logstash.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}

{{/*
Logstash HTTP API (port 9600) — matches deployment metadata.name {{ fullname }}-logstash.
*/}}
{{- define "logstash.httpApiUrl" -}}
{{- printf "http://%s-logstash:9600" (include "logstash.fullname" .) }}
{{- end }}

{{- define "logstash.kafkaBootstrap" -}}
{{- if .Values.kafka.bootstrapServers }}
{{- .Values.kafka.bootstrapServers }}
{{- else }}
{{- $name := "kafka-kafka" }}
{{- $g := .Values.global | default dict }}
{{- $pfx := default false (coalesce .Values.useReleaseNamePrefix (index $g "useReleaseNamePrefix")) }}
{{- if $pfx }}
{{- printf "%s-%s:9092" .Release.Name $name }}
{{- else }}
{{- printf "%s:9092" $name }}
{{- end }}
{{- end }}
{{- end }}

{{- define "logstash.elasticsearchUrl" -}}
{{- if .Values.elasticsearch.host }}
{{- printf "http://%s" .Values.elasticsearch.host }}
{{- else }}
{{- $g := .Values.global | default dict }}
{{- $pfx := default false (coalesce .Values.useReleaseNamePrefix (index $g "useReleaseNamePrefix")) }}
{{- if $pfx }}
{{- printf "http://%s-elasticsearch:9200" .Release.Name }}
{{- else }}
{{- printf "http://elasticsearch:9200" }}
{{- end }}
{{- end }}
{{- end }}
