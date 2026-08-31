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
Managed-image resolution for the mc-tracking BEV fusion container.
Mirrors the shared managed-image channel used by Docker Compose:
- Default repository/tag come from values (ghcr.io/.../vss-rt-cv-mv3dt-bev-fusion:develop-latest).
  Image name stays vss-rt-cv-mv3dt-bev-fusion until a renamed build is published.
- global.container_prefix overrides the repository prefix (uses image basename).
- global.container_tag overrides the tag.
This lets QA switch to a promoted NGC staging drop by setting the two globals
without editing this subchart.
*/}}
{{- define "vss-rtvi-cv.fusionImage" -}}
{{- $global := .Values.global | default dict -}}
{{- $fusion := ((.Values.standaloneWarehouse | default dict).mcTracking | default dict).fusion | default dict -}}
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
Managed-image resolution for the mc-tracking config-init container.
Same contract as vss-rtvi-cv.fusionImage, for the init container that generates
camInfo and the pub/sub topology from calibration:
- Default repository/tag come from values (ghcr.io/.../vss-rt-cv-mv3dt-config-init:develop-latest).
  Image name stays vss-rt-cv-mv3dt-config-init until a renamed build is published.
- global.container_prefix overrides the repository prefix (uses image basename).
- global.container_tag overrides the tag.
*/}}
{{- define "vss-rtvi-cv.configInitImage" -}}
{{- $global := .Values.global | default dict -}}
{{- $dynamic := ((.Values.standaloneWarehouse | default dict).mcTracking | default dict).dynamicCameraConfig | default dict -}}
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

{{- define "vss-rtvi-cv.mcTrackingMqttHost" -}}
{{- $mcTracking := .Values.standaloneWarehouse.mcTracking | default dict -}}
{{- if $mcTracking.mqttHost -}}
{{- $mcTracking.mqttHost -}}
{{- else if $mcTracking.mqttServiceName -}}
{{- $mcTracking.mqttServiceName -}}
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

{{- define "vss-rtvi-cv.mcTrackingRedisHost" -}}
{{- $mcTracking := .Values.standaloneWarehouse.mcTracking | default dict -}}
{{- if $mcTracking.redisHost -}}
{{- $mcTracking.redisHost -}}
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
