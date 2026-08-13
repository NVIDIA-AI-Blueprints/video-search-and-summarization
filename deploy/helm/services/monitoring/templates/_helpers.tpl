{{/*
SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
*/}}

{{/*
Component name. Matches the redis/phoenix convention: plain component name unless
useReleaseNamePrefix is set on the component, the chart, or globals.
Usage: include "monitoring.componentName" (dict "ctx" . "component" "prometheus")
*/}}
{{- define "monitoring.componentName" -}}
{{- $ctx := .ctx -}}
{{- $component := .component -}}
{{- $values := index $ctx.Values $component | default dict -}}
{{- if index $values "fullnameOverride" -}}
{{- index $values "fullnameOverride" | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $global := $ctx.Values.global | default dict -}}
{{- $usePrefix := default false (coalesce (index $values "useReleaseNamePrefix") $ctx.Values.useReleaseNamePrefix (index $global "useReleaseNamePrefix")) -}}
{{- if $usePrefix -}}
{{- printf "%s-%s" $ctx.Release.Name $component | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $component -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "monitoring.labels" -}}
helm.sh/chart: {{ .ctx.Chart.Name }}-{{ .ctx.Chart.Version | replace "+" "_" }}
app.kubernetes.io/name: {{ .component }}
app.kubernetes.io/instance: {{ .ctx.Release.Name }}
app.kubernetes.io/managed-by: {{ .ctx.Release.Service }}
app.kubernetes.io/part-of: metropolis-baseapp
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{- define "monitoring.selectorLabels" -}}
app.kubernetes.io/name: {{ .component }}
app.kubernetes.io/instance: {{ .ctx.Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{/*
Scrape annotations applied to every exporter pod so the annotation-based
kubernetes_sd_configs job in prometheus.yml picks them up.
*/}}
{{- define "monitoring.scrapeAnnotations" -}}
prometheus.io/scrape: "true"
prometheus.io/port: {{ .port | quote }}
prometheus.io/path: "/metrics"
{{- end -}}

{{/*
Cluster-scoped RBAC names must not collide across namespaces.
*/}}
{{- define "monitoring.prometheus.clusterRoleName" -}}
{{- printf "%s-%s" (include "monitoring.componentName" (dict "ctx" . "component" "prometheus")) .Release.Namespace | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "monitoring.prometheus.serviceAccountName" -}}
{{- $sa := .Values.prometheus.serviceAccount | default dict -}}
{{- if $sa.name -}}
{{- $sa.name -}}
{{- else -}}
{{- include "monitoring.componentName" (dict "ctx" . "component" "prometheus") -}}
{{- end -}}
{{- end -}}
