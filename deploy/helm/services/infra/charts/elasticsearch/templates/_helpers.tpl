# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

{{- define "elasticsearch.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "elasticsearch.fullname" -}}
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

{{- define "elasticsearch.labels" -}}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
app.kubernetes.io/name: {{ include "elasticsearch.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{- define "elasticsearch.selectorLabels" -}}
app.kubernetes.io/name: {{ include "elasticsearch.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "elasticsearch.image" -}}
{{- printf "%s:%s" .Values.image.repository .Values.image.tag }}
{{- end }}

{{- define "elasticsearch.httpServiceUrl" -}}
{{- $g := .Values.global | default dict }}
{{- $pfx := default false (coalesce .Values.useReleaseNamePrefix (index $g "useReleaseNamePrefix")) }}
{{- $host := include "elasticsearch.fullname" . }}
{{- printf "http://%s:%d" $host (int .Values.service.port) }}
{{- end }}

{{- define "elasticsearch.releaseScopedName" -}}
{{- $root := index . "root" }}
{{- $g := $root.Values.global | default dict }}
{{- $usePrefix := default false (coalesce $root.Values.useReleaseNamePrefix (index $g "useReleaseNamePrefix")) }}
{{- if $usePrefix }}
{{- printf "%s-%s" $root.Release.Name (index . "name") | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- index . "name" }}
{{- end }}
{{- end }}

{{- define "elasticsearch.initJobName" -}}
{{- include "elasticsearch.releaseScopedName" (dict "root" . "name" "vss-elasticsearch-init") }}
{{- end }}

{{- define "elasticsearch.initScriptsConfigMapName" -}}
{{- include "elasticsearch.releaseScopedName" (dict "root" . "name" "vss-elasticsearch-init-scripts") }}
{{- end }}
