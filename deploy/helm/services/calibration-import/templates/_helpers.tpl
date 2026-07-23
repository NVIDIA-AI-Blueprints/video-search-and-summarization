# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

{{- define "calibration-import.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else if .Values.name }}
{{- .Values.name | trunc 63 | trimSuffix "-" }}
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

{{- define "calibration-import.labels" -}}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version | replace "+" "_" }}
app.kubernetes.io/name: {{ include "calibration-import.fullname" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: metropolis-baseapp
app.kubernetes.io/component: calibration-import
{{- end }}

{{- define "calibration-import.selectorLabels" -}}
app.kubernetes.io/name: {{ include "calibration-import.fullname" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: calibration-import
{{- end }}

{{- define "calibration-import.scriptsConfigMapName" -}}
{{- if .Values.scripts.existingConfigMap }}
{{- .Values.scripts.existingConfigMap }}
{{- else }}
{{- printf "%s-scripts" (include "calibration-import.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
