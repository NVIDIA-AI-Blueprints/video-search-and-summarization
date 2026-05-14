{{/*
SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
*/}}

{{- define "sdrc.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "sdrc.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := include "sdrc.name" . }}
{{- $global := .Values.global | default dict }}
{{- if and (hasKey .Values "useReleaseNamePrefix") (kindIs "bool" .Values.useReleaseNamePrefix) .Values.useReleaseNamePrefix }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- else if and (hasKey .Values "useReleaseNamePrefix") (kindIs "bool" .Values.useReleaseNamePrefix) (not .Values.useReleaseNamePrefix) }}
{{- printf "%s" $name | trunc 63 | trimSuffix "-" }}
{{- else if and (hasKey $global "useReleaseNamePrefix") (kindIs "bool" (index $global "useReleaseNamePrefix")) (index $global "useReleaseNamePrefix") }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s" $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end -}}

{{- define "sdrc.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | quote }}
app.kubernetes.io/name: {{ include "sdrc.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "sdrc.selectorLabels" -}}
app.kubernetes.io/name: {{ include "sdrc.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "sdrc.image" -}}
{{- printf "%s:%s" .Values.image.repository .Values.image.tag -}}
{{- end -}}

{{- define "sdrc.serviceAccountName" -}}
{{- if .Values.serviceAccount.name }}
{{- .Values.serviceAccount.name }}
{{- else }}
{{- include "sdrc.fullname" . }}
{{- end }}
{{- end -}}

{{/* ClusterRole / ClusterRoleBinding names must be unique per Helm release. */}}
{{- define "sdrc.clusterRbacName" -}}
{{- if .Values.fullnameOverride }}
{{- printf "%s-%s" .Release.Name .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := include "sdrc.name" . }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end -}}

{{- define "sdrc.configMapName" -}}
{{- if .Values.config.name }}
{{- .Values.config.name }}
{{- else }}
{{- printf "%s-config" (include "sdrc.fullname" .) }}
{{- end }}
{{- end -}}

{{- define "sdrc.configKey" -}}
{{- default "config.yml" .Values.config.key -}}
{{- end -}}

{{- define "sdrc.configMountPath" -}}
{{- default "/config.yml" .Values.config.mountPath -}}
{{- end -}}

{{- define "sdrc.controllerServiceName" -}}
{{- printf "%s-controller" (include "sdrc.fullname" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "sdrc.sdrcDirectListenerServiceName" -}}
{{- printf "%s-direct-listener" (include "sdrc.fullname" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "sdrc.envoyAdminServiceName" -}}
{{- printf "%s-envoy-admin" (include "sdrc.fullname" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "sdrc.serviceType" -}}
{{- $type := required (printf "%s.type is required" .path) .type -}}
{{- if not (has $type (list "ClusterIP" "NodePort" "LoadBalancer")) -}}
{{- fail (printf "%s.type must be one of ClusterIP, NodePort, or LoadBalancer" .path) -}}
{{- end -}}
{{- $type -}}
{{- end -}}

{{- define "sdrc.serviceNodePort" -}}
{{- if .nodePort -}}
{{- if eq .type "ClusterIP" -}}
{{- fail (printf "%s.nodePort cannot be set when %s.type is ClusterIP" .path .path) -}}
{{- end -}}
nodePort: {{ .nodePort | int }}
{{- end -}}
{{- end -}}

{{- define "sdrc.runtimeEnv" -}}
{{- $env := .Values.runtimeEnv | default dict -}}
- name: WDM_WL_REDIS_SERVER
  value: {{ required "runtimeEnv.WDM_WL_REDIS_SERVER is required" (get $env "WDM_WL_REDIS_SERVER") | quote }}
- name: WDM_WL_REDIS_PORT
  value: {{ required "runtimeEnv.WDM_WL_REDIS_PORT is required" (get $env "WDM_WL_REDIS_PORT") | quote }}
- name: OTEL_SDK_DISABLED
  value: {{ required "runtimeEnv.OTEL_SDK_DISABLED is required" (get $env "OTEL_SDK_DISABLED") | quote }}
- name: KUBERNETES_HOST
  value: {{ required "runtimeEnv.KUBERNETES_HOST is required" (get $env "KUBERNETES_HOST") | quote }}
- name: KUBERNETES_PORT
  value: {{ required "runtimeEnv.KUBERNETES_PORT is required" (get $env "KUBERNETES_PORT") | quote }}
- name: WDM_CONTROLLER_PORT
  value: {{ required "service.controller.port is required" .Values.service.controller.port | quote }}
- name: WDM_CONTROLLER_HOST
  value: {{ required "runtimeEnv.WDM_CONTROLLER_HOST is required" (get $env "WDM_CONTROLLER_HOST") | quote }}
- name: WDM_SDRC_DIRECT_LISTENER_PORT
  value: {{ required "service.sdrcDirectListener.port is required" .Values.service.sdrcDirectListener.port | quote }}
- name: ENVOY_ADMIN_PORT
  value: {{ required "service.envoyAdmin.port is required" .Values.service.envoyAdmin.port | quote }}
- name: ROUTER_PORT
  value: {{ required "service.controller.port is required" .Values.service.controller.port | quote }}
{{- end -}}
