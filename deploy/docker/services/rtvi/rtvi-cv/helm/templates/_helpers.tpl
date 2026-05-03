# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

{{- define "rtvi-cv.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "rtvi-cv.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{- define "rtvi-cv.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "rtvi-cv.labels" -}}
helm.sh/chart: {{ include "rtvi-cv.chart" . }}
{{ include "rtvi-cv.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{- define "rtvi-cv.selectorLabels" -}}
app.kubernetes.io/name: {{ include "rtvi-cv.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "rtvi-cv.headlessServiceName" -}}
{{- printf "%s-headless" (include "rtvi-cv.fullname" .) -}}
{{- end }}

{{- define "rtvi-cv.warehouseMode" -}}
{{- $m := lower .Values.rtviCv.warehouse.mode -}}
{{- if not (or (eq $m "2d") (eq $m "3d")) -}}
{{- fail "rtviCv.warehouse.mode must be \"2d\" or \"3d\"" -}}
{{- end -}}
{{- $m -}}
{{- end }}

{{- define "rtvi-cv.cm.scripts" -}}
{{- printf "%s-scripts" (include "rtvi-cv.fullname" .) -}}
{{- end }}

{{- define "rtvi-cv.cm.warehouse2d" -}}
{{- printf "%s-warehouse-2d" (include "rtvi-cv.fullname" .) -}}
{{- end }}

{{- define "rtvi-cv.cm.warehouse3d" -}}
{{- printf "%s-warehouse-3d" (include "rtvi-cv.fullname" .) -}}
{{- end }}

{{- define "rtvi-cv.cm.warehouse3dLabels" -}}
{{- printf "%s-warehouse-3d-labels" (include "rtvi-cv.fullname" .) -}}
{{- end }}

{{- define "rtvi-cv.perceptionImage" -}}
{{- printf "%s:%s" .Values.rtviCv.images.perception.repository .Values.rtviCv.images.perception.tag -}}
{{- end }}

{{- define "rtvi-cv.sparse4dOnnxHostPath" -}}
{{- printf "%s/%s" (.Values.rtviCv.models.path | trimSuffix "/") .Values.rtviCv.models.sparse4dOnnxFile -}}
{{- end }}

{{- define "rtvi-cv.sparse4dNpyHostPath" -}}
{{- printf "%s/%s" (.Values.rtviCv.models.path | trimSuffix "/") .Values.rtviCv.models.sparse4dNpyFile -}}
{{- end }}
