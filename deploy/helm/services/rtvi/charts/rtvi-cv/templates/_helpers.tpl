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

{{- define "vss-rtvi-cv.fullname" -}}
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

{{- define "vss-rtvi-cv.labels" -}}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
app.kubernetes.io/name: vss-rtvi-cv
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{- define "vss-rtvi-cv.selectorLabels" -}}
app.kubernetes.io/name: vss-rtvi-cv
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/* Headless Service for StatefulSet pod DNS; must differ from ClusterIP Service (vss-rtvi-cv.fullname). */}}
{{- define "vss-rtvi-cv.headlessServiceName" -}}
{{- printf "%s-headless" (include "vss-rtvi-cv.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "vss-rtvi-cv.image" -}}{{ printf "%s:%s" .Values.image.repository .Values.image.tag }}{{- end -}}

{{- define "vss-rtvi-cv.scriptsConfigMapName" -}}
{{- if .Values.scripts.existingConfigMap }}
{{- .Values.scripts.existingConfigMap }}
{{- else }}
{{- printf "%s-scripts" (include "vss-rtvi-cv.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}

{{- define "vss-rtvi-cv.kafkaBootstrap" -}}
{{- if .Values.kafka.bootstrapServers }}
{{- .Values.kafka.bootstrapServers }}
{{- else }}
{{- $name := "kafka-kafka" }}
{{- $global := .Values.global | default dict }}
{{- $usePrefix := default false (coalesce .Values.useReleaseNamePrefix (index $global "useReleaseNamePrefix")) }}
{{- if $usePrefix }}
{{- printf "%s-%s:9092" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s:9092" $name }}
{{- end }}
{{- end }}
{{- end }}

{{/* Models PVC size: prefer existing claim (stable upgrades). lookup empty in helm template/dry-run. Set forceModelsStorageFromValues to use values only. */}}
{{- define "vss-rtvi-cv.effectiveAlertsModelsStorage" -}}
{{- $claim := printf "%s-models" (include "vss-rtvi-cv.fullname" .) }}
{{- $default := .Values.modelsPvc.size | default "10Gi" }}
{{- if .Values.forceModelsStorageFromValues }}
{{- print $default }}
{{- else }}
{{- $pvc := lookup "v1" "PersistentVolumeClaim" .Release.Namespace $claim }}
{{- $got := dig "spec" "resources" "requests" "storage" "" $pvc }}
{{- if $got }}
{{- print $got }}
{{- else }}
{{- print $default }}
{{- end }}
{{- end }}
{{- end }}

{{- define "vss-rtvi-cv.effectiveSearchModelsStorage" -}}
{{- $claim := printf "%s-models" (include "vss-rtvi-cv.fullname" .) }}
{{- $default := .Values.persistence.models.size | default "50Gi" }}
{{- if .Values.forceModelsStorageFromValues }}
{{- print $default }}
{{- else }}
{{- $pvc := lookup "v1" "PersistentVolumeClaim" .Release.Namespace $claim }}
{{- $got := dig "spec" "resources" "requests" "storage" "" $pvc }}
{{- if $got }}
{{- print $got }}
{{- else }}
{{- print $default }}
{{- end }}
{{- end }}
{{- end }}
