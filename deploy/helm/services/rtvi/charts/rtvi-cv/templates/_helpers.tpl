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

{{- define "vss-rtvi-cv.image" -}}
{{- $global := .Values.global | default dict -}}
{{- $prefix := index $global "container_prefix" | default "" -}}
{{- $repository := .Values.image.repository -}}
{{- if $prefix -}}
{{- $repository = printf "%s/vss-rt-cv" (trimSuffix "/" $prefix) -}}
{{- end -}}
{{- $tag := index $global "container_tag" | default .Values.image.tag -}}
{{- printf "%s:%s" $repository $tag -}}
{{- end -}}

{{/*
Managed-image resolution for the MV3DT BEV fusion container.
Mirrors the shared managed-image channel used by Docker Compose:
- Default repository/tag come from values (ghcr.io/.../vss-rt-cv-mv3dt-bev-fusion:develop-latest).
- global.container_prefix overrides the repository prefix (uses image basename).
- global.container_tag overrides the tag.
This lets QA switch to a promoted NGC staging drop by setting the two globals
without editing this subchart.
*/}}
{{- define "vss-rtvi-cv.fusionImage" -}}
{{- $global := .Values.global | default dict -}}
{{- $fusion := ((.Values.standaloneWarehouse | default dict).mv3dt | default dict).fusion | default dict -}}
{{- $img := $fusion.image | default dict -}}
{{- $repository := $img.repository -}}
{{- $prefix := index $global "container_prefix" | default "" -}}
{{- if $prefix -}}
{{- $repository = printf "%s/vss-rt-cv-mv3dt-bev-fusion" (trimSuffix "/" $prefix) -}}
{{- end -}}
{{- $tag := index $global "container_tag" | default $img.tag -}}
{{- printf "%s:%s" $repository $tag -}}
{{- end -}}

{{/*
Managed-image resolution for the MV3DT config-init container.
Same contract as vss-rtvi-cv.fusionImage, for the init container that generates
camInfo and the pub/sub topology from calibration:
- Default repository/tag come from values (ghcr.io/.../vss-rt-cv-mv3dt-config-init:develop-latest).
- global.container_prefix overrides the repository prefix (uses image basename).
- global.container_tag overrides the tag.
*/}}
{{- define "vss-rtvi-cv.configInitImage" -}}
{{- $global := .Values.global | default dict -}}
{{- $dynamic := ((.Values.standaloneWarehouse | default dict).mv3dt | default dict).dynamicCameraConfig | default dict -}}
{{- $img := ($dynamic.configInit | default dict).image | default dict -}}
{{- $repository := $img.repository -}}
{{- $prefix := index $global "container_prefix" | default "" -}}
{{- if $prefix -}}
{{- $repository = printf "%s/vss-rt-cv-mv3dt-config-init" (trimSuffix "/" $prefix) -}}
{{- end -}}
{{- $tag := index $global "container_tag" | default $img.tag -}}
{{- printf "%s:%s" $repository $tag -}}
{{- end -}}

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

{{- define "vss-rtvi-cv.mv3dtMqttHost" -}}
{{- $mv3dt := .Values.standaloneWarehouse.mv3dt | default dict -}}
{{- if $mv3dt.mqttHost -}}
{{- $mv3dt.mqttHost -}}
{{- else if $mv3dt.mqttServiceName -}}
{{- $mv3dt.mqttServiceName -}}
{{- else -}}
{{- $global := .Values.global | default dict -}}
{{- $usePrefix := default false (coalesce .Values.useReleaseNamePrefix (index $global "useReleaseNamePrefix")) -}}
{{- if $usePrefix -}}
{{- printf "%s-mosquitto" .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "mosquitto" -}}
{{- end -}}
{{- end -}}
{{- end }}

{{- define "vss-rtvi-cv.mv3dtRedisHost" -}}
{{- $mv3dt := .Values.standaloneWarehouse.mv3dt | default dict -}}
{{- if $mv3dt.redisHost -}}
{{- $mv3dt.redisHost -}}
{{- else -}}
{{- $global := .Values.global | default dict -}}
{{- $usePrefix := default false (coalesce .Values.useReleaseNamePrefix (index $global "useReleaseNamePrefix")) -}}
{{- if $usePrefix -}}
{{- printf "%s-redis" .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "redis" -}}
{{- end -}}
{{- end -}}
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
