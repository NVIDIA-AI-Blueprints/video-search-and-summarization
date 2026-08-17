# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

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
