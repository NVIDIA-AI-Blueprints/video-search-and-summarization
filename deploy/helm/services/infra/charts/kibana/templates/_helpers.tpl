# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

{{- define "kibana.fullname" -}}
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

{{- define "kibana.image" -}}
{{- printf "%s:%s" .Values.image.repository .Values.image.tag }}
{{- end }}

{{- define "kibana.elasticsearchServiceHostname" -}}
{{- $g := .Values.global | default dict }}
{{- if index $g "elasticsearchServiceHost" }}{{ index $g "elasticsearchServiceHost" }}
{{- else }}
{{- $short := "elasticsearch" }}
{{- if and (hasKey .Values "useReleaseNamePrefix") (kindIs "bool" .Values.useReleaseNamePrefix) .Values.useReleaseNamePrefix }}
{{- printf "%s-%s" .Release.Name $short | trunc 63 | trimSuffix "-" }}
{{- else if and (hasKey .Values "useReleaseNamePrefix") (kindIs "bool" .Values.useReleaseNamePrefix) (not .Values.useReleaseNamePrefix) }}
{{- printf "%s" $short | trunc 63 | trimSuffix "-" }}
{{- else if and (hasKey $g "useReleaseNamePrefix") (kindIs "bool" (index $g "useReleaseNamePrefix")) (index $g "useReleaseNamePrefix") }}
{{- printf "%s-%s" .Release.Name $short | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s" $short | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{- define "kibana.httpServiceUrl" -}}
{{- printf "http://%s:%d" (include "kibana.fullname" .) (int .Values.service.port) }}
{{- end }}

{{/* Stable names (legacy vss-kibana-init chart); one import Job per namespace. */}}
{{- define "kibana.initJobName" -}}
vss-kibana-init
{{- end }}

{{- define "kibana.initImportConfigMapName" -}}
vss-kibana-init-import
{{- end }}
