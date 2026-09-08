# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

{{/* Does this release prefix in-cluster object names with the release name? */}}
{{- define "vss-reid-embed.usePrefix" -}}
{{- $global := .Values.global | default dict -}}
{{- default false (coalesce .Values.useReleaseNamePrefix (index $global "useReleaseNamePrefix")) -}}
{{- end }}

{{- define "vss-reid-embed.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default "vss-reid-embed" .Values.nameOverride }}
{{- if eq (include "vss-reid-embed.usePrefix" .) "true" }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s" $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/* Gallery backends keep the Compose alias shape (reid-etcd / reid-minio /
     reid-milvus) under the vss- prefix the rest of the stack uses. */}}
{{- define "vss-reid-embed.etcdFullname" -}}
{{- if eq (include "vss-reid-embed.usePrefix" .) "true" }}
{{- printf "%s-vss-reid-etcd" .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- print "vss-reid-etcd" }}
{{- end }}
{{- end }}

{{- define "vss-reid-embed.minioFullname" -}}
{{- if eq (include "vss-reid-embed.usePrefix" .) "true" }}
{{- printf "%s-vss-reid-minio" .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- print "vss-reid-minio" }}
{{- end }}
{{- end }}

{{- define "vss-reid-embed.milvusFullname" -}}
{{- if eq (include "vss-reid-embed.usePrefix" .) "true" }}
{{- printf "%s-vss-reid-milvus" .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- print "vss-reid-milvus" }}
{{- end }}
{{- end }}

{{- define "vss-reid-embed.initFullname" -}}
{{- printf "%s-init" (include "vss-reid-embed.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Job spec.template is immutable. A stable name would make Helm patch the
existing Job on upgrade and fail. The revision is a short hash of everything
that lands in the pod template; a change yields a new name, Helm creates that
Job and deletes the previous one.
*/}}
{{- define "vss-reid-embed.initJobRevision" -}}
{{- $global := .Values.global | default dict -}}
{{- dict
  "image" .Values.image
  "initResources" .Values.init.resources
  "backoffLimit" .Values.init.backoffLimit
  "ttl" .Values.init.ttlSecondsAfterFinished
  "models" .Values.models
  "nodeSelector" .Values.nodeSelector
  "tolerations" .Values.tolerations
  "affinity" .Values.affinity
  "runtimeClassName" .Values.runtimeClassName
  "imagePullSecrets" (.Values.imagePullSecrets | default (index $global "imagePullSecrets"))
  "claim" (include "vss-reid-embed.modelsClaim" .)
  "ngcSecret" (include "vss-reid-embed.ngcSecretName" .)
  "ngcSecretKey" (include "vss-reid-embed.ngcSecretKey" .)
  "download" (.Files.Get "files/download-embedding-models.sh")
  "convert" (.Files.Get "files/convert_clipreid_to_onnx.py")
  | toYaml | sha256sum | trunc 8 -}}
{{- end }}

{{- define "vss-reid-embed.initJobName" -}}
{{- $rev := include "vss-reid-embed.initJobRevision" . -}}
{{- $base := include "vss-reid-embed.initFullname" . | trunc 54 | trimSuffix "-" -}}
{{- printf "%s-%s" $base $rev -}}
{{- end }}

{{- define "vss-reid-embed.scriptsConfigMapName" -}}
{{- printf "%s-scripts" (include "vss-reid-embed.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/* Expected marker contents. Waiters treat a leftover file from an earlier
     Job as not-ready until this matches. */}}
{{- define "vss-reid-embed.generationConfigMapName" -}}
{{- printf "%s-generation" (include "vss-reid-embed.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "vss-reid-embed.commonLabels" -}}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version | replace "+" "_" }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: metropolis-baseapp
{{- end }}

{{- define "vss-reid-embed.labels" -}}
{{ include "vss-reid-embed.commonLabels" . }}
app.kubernetes.io/name: vss-reid-embed
app.kubernetes.io/component: reid-embed
{{- end }}

{{- define "vss-reid-embed.selectorLabels" -}}
app.kubernetes.io/name: vss-reid-embed
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: reid-embed
{{- end }}

{{- define "vss-reid-embed.etcdLabels" -}}
{{ include "vss-reid-embed.commonLabels" . }}
app.kubernetes.io/name: vss-reid-etcd
app.kubernetes.io/component: reid-etcd
{{- end }}

{{- define "vss-reid-embed.etcdSelectorLabels" -}}
app.kubernetes.io/name: vss-reid-etcd
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: reid-etcd
{{- end }}

{{- define "vss-reid-embed.minioLabels" -}}
{{ include "vss-reid-embed.commonLabels" . }}
app.kubernetes.io/name: vss-reid-minio
app.kubernetes.io/component: reid-minio
{{- end }}

{{- define "vss-reid-embed.minioSelectorLabels" -}}
app.kubernetes.io/name: vss-reid-minio
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: reid-minio
{{- end }}

{{- define "vss-reid-embed.milvusLabels" -}}
{{ include "vss-reid-embed.commonLabels" . }}
app.kubernetes.io/name: vss-reid-milvus
app.kubernetes.io/component: reid-milvus
{{- end }}

{{- define "vss-reid-embed.milvusSelectorLabels" -}}
app.kubernetes.io/name: vss-reid-milvus
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: reid-milvus
{{- end }}

{{/* Broker endpoints mirror the rtvi-cv helpers so both land on the same
     in-cluster Services when a profile leaves them unset. */}}
{{- define "vss-reid-embed.kafkaBootstrap" -}}
{{- if .Values.kafka.bootstrapServers }}
{{- .Values.kafka.bootstrapServers }}
{{- else if eq (include "vss-reid-embed.usePrefix" .) "true" }}
{{- printf "%s-kafka-kafka:9092" .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- print "kafka-kafka:9092" }}
{{- end }}
{{- end }}

{{- define "vss-reid-embed.redisHost" -}}
{{- if .Values.redis.host }}
{{- .Values.redis.host }}
{{- else if eq (include "vss-reid-embed.usePrefix" .) "true" }}
{{- printf "%s-redis" .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- print "redis" }}
{{- end }}
{{- end }}

{{- define "vss-reid-embed.viosServer" -}}
{{- if .Values.viosServer }}
{{- .Values.viosServer }}
{{- else if eq (include "vss-reid-embed.usePrefix" .) "true" }}
{{- printf "%s-vss-vios-ingress:%s" .Release.Name (.Values.viosPort | toString) }}
{{- else }}
{{- printf "vss-vios-ingress:%s" (.Values.viosPort | toString) }}
{{- end }}
{{- end }}

{{/* The staged ReID assets have to sit on the RT-CV models claim: the
     DeepStream tracker reads reid_model.onnx through its own /opt/storage
     mount, so a claim of our own would be invisible to it. */}}
{{- define "vss-reid-embed.modelsClaim" -}}
{{- if .Values.models.existingClaim }}
{{- .Values.models.existingClaim }}
{{- else if eq (include "vss-reid-embed.usePrefix" .) "true" }}
{{- printf "%s-%s" .Release.Name .Values.models.rtviCvClaimName | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- .Values.models.rtviCvClaimName }}
{{- end }}
{{- end }}

{{- define "vss-reid-embed.reidDir" -}}
{{- printf "%s/%s" (trimSuffix "/" .Values.models.mountPath) .Values.models.subDir }}
{{- end }}

{{- define "vss-reid-embed.siglipOnnxPath" -}}
{{- if .Values.secondaryEmbedding.onnxModelPath }}
{{- .Values.secondaryEmbedding.onnxModelPath }}
{{- else }}
{{- printf "%s/siglip_v2_vdeployable_v1.0/siglip2_v1.0.onnx" (include "vss-reid-embed.reidDir" .) }}
{{- end }}
{{- end }}

{{/* Marker the staging Job writes and the consumers wait on. */}}
{{- define "vss-reid-embed.readyMarker" -}}
{{- printf "%s/.reid-models-ready" (include "vss-reid-embed.reidDir" .) }}
{{- end }}

{{- define "vss-reid-embed.ngcSecretName" -}}
{{- $global := .Values.global | default dict -}}
{{- $gns := index $global "ngcApiSecret" | default dict -}}
{{- .Values.ngcApiSecret.name | default (index $gns "name") | default "ngc-api" }}
{{- end }}

{{- define "vss-reid-embed.ngcSecretKey" -}}
{{- $global := .Values.global | default dict -}}
{{- $gns := index $global "ngcApiSecret" | default dict -}}
{{- .Values.ngcApiSecret.key | default (index $gns "key") | default "NGC_CLI_API_KEY" }}
{{- end }}
