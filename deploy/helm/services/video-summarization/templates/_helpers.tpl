# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

{{/*
Expand the name of the chart.
*/}}
{{- define "vss-summarization.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
We truncate at 63 chars because some Kubernetes name fields are limited to this (by the DNS naming spec).
If release name contains chart name it will be used as a full name.
*/}}
{{- define "vss-summarization.fullname" -}}
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

{{- define "vss-summarization.filesConfigMapName" -}}
{{ include "vss-summarization.fullname" . }}-files
{{- end }}

{{- define "vss-summarization.configMapName" -}}
{{- $name := .name | trunc 50 | trimSuffix "-" -}}
{{- $prefixMaxLen := int (sub 62 (len $name)) -}}
{{- printf "%s-%s" ((include "vss-summarization.fullname" .root) | trunc $prefixMaxLen | trimSuffix "-") $name | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "vss-summarization.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Sanitize chart name for use as container name (replace dots with hyphens for RFC 1123 compliance).
*/}}
{{- define "vss-summarization.containerName" -}}
{{- .Chart.Name | replace "." "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "vss-summarization.labels" -}}
helm.sh/chart: {{ include "vss-summarization.chart" . }}
{{ include "vss-summarization.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- with (index .Chart.Annotations "blueprint-builder.nvidia.com/tool-version") }}
blueprint-builder.nvidia.com/tool-version: {{ . | quote }}
{{- end }}
{{- with (index .Chart.Annotations "blueprint-builder.nvidia.com/blueprint") }}
blueprint-builder.nvidia.com/blueprint: {{ . | quote }}
{{- end }}
{{- with (index .Chart.Annotations "blueprint-builder.nvidia.com/blueprint-version") }}
blueprint-builder.nvidia.com/blueprint-version: {{ . | quote }}
{{- end }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "vss-summarization.selectorLabels" -}}
app.kubernetes.io/name: {{ include "vss-summarization.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Create the name of the service account to use
*/}}
{{- define "vss-summarization.serviceAccountName" -}}
{{- if and .Values.serviceAccount .Values.serviceAccount.create }}
{{- default (include "vss-summarization.fullname" .) .Values.serviceAccount.name }}
{{- else if and .Values.serviceAccount .Values.serviceAccount.name }}
{{- .Values.serviceAccount.name }}
{{- else }}
{{- "default" }}
{{- end }}
{{- end }}

{{/*
Create the name of the service to use
If service.name is set in values, use that; otherwise use the fullname
*/}}
{{- define "vss-summarization.serviceName" -}}
{{- if and .Values.service .Values.service.name }}
{{- .Values.service.name }}
{{- else }}
{{- include "vss-summarization.fullname" . }}
{{- end }}
{{- end }}

{{/*
Create the name of the headless service (used as the StatefulSet governing service).
Follows the convention: <serviceName>-headless
*/}}
{{- define "vss-summarization.headlessServiceName" -}}
{{- include "vss-summarization.serviceName" . }}-headless
{{- end }}


{{/*
Pod spec used in Deployments, StatefulSets, and Jobs. Include this within the 'template' section where a podTemplateSpec is needed.
*/}}
{{- define "vss-summarization.podTemplateSpec" -}}
metadata:
  {{- with .Values.podAnnotations }}
  annotations:
    {{- toYaml . | nindent 8 }}
  {{- end }}
  labels:
    {{- include "vss-summarization.labels" . | nindent 8 }}
    {{- with .Values.podLabels }}
    {{- toYaml . | nindent 8 }}
    {{- end }}
spec:
  {{- if or .Values.imagePullSecrets (and .Values.global .Values.global.imagePullSecrets) }}
  imagePullSecrets:
    {{- if .Values.imagePullSecrets }}
    {{- toYaml .Values.imagePullSecrets | nindent 8 }}
    {{- else if and .Values.global .Values.global.imagePullSecrets }}
    {{- toYaml .Values.global.imagePullSecrets | nindent 8 }}
    {{- end }}
  {{- end }}
  serviceAccountName: {{ include "vss-summarization.serviceAccountName" . }}
  {{- with .Values.runtimeClassName }}
  runtimeClassName: {{ . | quote }}
  {{- end }}
  {{- if kindIs "bool" .Values.enableServiceLinks }}
  enableServiceLinks: {{ .Values.enableServiceLinks }}
  {{- end }}
  {{- with .Values.podSecurityContext }}
  securityContext:
    {{- toYaml . | nindent 8 }}
  {{- end }}
  {{- if .Values.initContainers }}
  initContainers:
  {{- range $k, $v := .Values.initContainers }}
    - name: {{ $k }}
      {{- if $v.image }}
      image: {{ $v.image }}
      {{- end }}
      {{- if $v.command }}
      command:
        {{- toYaml $v.command | nindent 12 }}
      {{- end }}
      {{- if $v.args }}
      args:
        {{- toYaml $v.args | nindent 12 }}
      {{- end }}
      {{- if $v.env }}
      env:
      {{- range $envName, $envValue := $v.env }}
        - name: {{ $envName }}
        {{- toYaml $envValue | nindent 10 }}
      {{- end }}
      {{- end }}
      {{- if $v.resources }}
      resources:
        {{- toYaml $v.resources | nindent 12 }}
      {{- end }}
      {{- if $v.securityContext }}
      securityContext:
        {{- toYaml $v.securityContext | nindent 12 }}
      {{- end }}
      {{- if $v.workingDir }}
      workingDir: {{ $v.workingDir }}
      {{- end }}
      {{- if $v.volumeMounts }}
      volumeMounts:
        {{- toYaml $v.volumeMounts | nindent 8 }}
      {{- end }}
  {{- end }}
  {{- end }}
  containers:
    - name: {{ include "vss-summarization.containerName" . }}
      {{- with .Values.securityContext }}
      securityContext:
        {{- toYaml . | nindent 12 }}
      {{- end }}
      {{- if .Values.image }}
      image: "{{ .Values.image.repository }}:{{ .Values.image.tag | default .Chart.AppVersion }}"
      imagePullPolicy: {{ .Values.image.pullPolicy }}
      {{- if and .Values.image.command (kindIs "slice" .Values.image.command) }}
      command:
        {{- range .Values.image.command }}
         - {{ . | quote }}
        {{- end }}
      {{- end }}
      {{- if and .Values.image.args (kindIs "slice" .Values.image.args) }}
      args:
        {{- range .Values.image.args }}
         - {{ . | quote }}
        {{- end }}
      {{- end }}
      {{- else }}
      image: "placeholder:latest"
      {{- end }}
      {{- if .Values.env }}
      env:
      {{- $envVars := list }}
      {{- range $name, $value := .Values.env }}
        {{- $hasRef := false }}
        {{- if hasKey $value "value" }}
          {{- if or (contains "$(" (toString $value.value)) (contains "${" (toString $value.value)) }}
            {{- $hasRef = true }}
          {{- end }}
        {{- end }}
        {{- $envVars = append $envVars (dict "name" $name "value" $value "hasRef" $hasRef) }}
      {{- end }}
      {{- $simpleVars := list }}
      {{- $refVars := list }}
      {{- range $envVars }}
        {{- if .hasRef }}
          {{- $refVars = append $refVars . }}
        {{- else }}
          {{- $simpleVars = append $simpleVars . }}
        {{- end }}
      {{- end }}
      {{- range $simpleVars }}
        - name: {{ .name }}
        {{- if hasKey .value "value" }}
          value: {{ tpl (index .value "value" | toString) $ | quote }}
        {{- else if .value.valueFrom }}
          valueFrom:
          {{- toYaml .value.valueFrom | nindent 12 }}
        {{- end }}
      {{- end }}
      {{- range $refVars }}
        - name: {{ .name }}
        {{- if hasKey .value "value" }}
          value: {{ tpl (index .value "value" | toString) $ | quote }}
        {{- else if .value.valueFrom }}
          valueFrom:
          {{- toYaml .value.valueFrom | nindent 12 }}
        {{- end }}
      {{- end }}
      {{- end }}
      {{- with .Values.envFrom }}
      envFrom:
          {{- toYaml . | nindent 12 }}
      {{- end }}
      {{- if and .Values.service .Values.service.port }}
      ports:
        - name: app-port
          containerPort: {{ .Values.service.port }}
          protocol: TCP
      {{- end }}
      {{- with .Values.startupProbe }}
      startupProbe:
        {{- toYaml . | nindent 12 }}
      {{- end }}
      {{- with .Values.livenessProbe }}
      livenessProbe:
        {{- toYaml . | nindent 12 }}
      {{- end }}
      {{- with .Values.readinessProbe }}
      readinessProbe:
        {{- toYaml . | nindent 12 }}
      {{- end }}
      {{- with .Values.resources }}
      resources:
        {{- toYaml . | nindent 12 }}
      {{- end }}
      {{- if or .Values.volumeMounts (and .Values.sharedMemory .Values.sharedMemory.sizeLimit) (.Files.Glob "files/*") }}
      volumeMounts:
        {{- with .Values.volumeMounts }}
        {{- toYaml . | nindent 8 }}
        {{- end }}
        {{- if and .Values.sharedMemory .Values.sharedMemory.sizeLimit }}
        - name: dshm
          mountPath: /dev/shm
        {{- end }}
        {{- if .Files.Glob "files/*" }}
        - name: config-files
          mountPath: /etc/app/config
          readOnly: true
        {{- end }}
      {{- end }}
  {{- if or .Values.volumes (and .Values.sharedMemory .Values.sharedMemory.sizeLimit) (.Files.Glob "files/*") }}
  volumes:
    {{- range .Values.volumes }}
    - name: {{ .name }}
      {{- if .configMap }}
      configMap:
        {{- with .configMap.name }}
        {{- if and $.Values.configMaps (hasKey $.Values.configMaps .) }}
        name: {{ include "vss-summarization.configMapName" (dict "root" $ "name" .) }}
        {{- else }}
        name: {{ . }}
        {{- end }}
        {{- end }}
        {{- with (omit .configMap "name") }}
        {{- toYaml . | nindent 8 }}
        {{- end }}
      {{- else }}
      {{- omit . "name" | toYaml | nindent 6 }}
      {{- end }}
    {{- end }}
    {{- if and .Values.sharedMemory .Values.sharedMemory.sizeLimit }}
    - name: dshm
      emptyDir:
        medium: Memory
        sizeLimit: {{ .Values.sharedMemory.sizeLimit }}
    {{- end }}
    {{- if .Files.Glob "files/*" }}
    - name: config-files
      configMap:
        name: {{ include "vss-summarization.filesConfigMapName" . }}
    {{- end }}
  {{- end }}
  {{- with .Values.nodeSelector }}
  nodeSelector:
    {{- toYaml . | nindent 8 }}
  {{- end }}
  {{- with .Values.affinity }}
  affinity:
    {{- toYaml . | nindent 8 }}
  {{- end }}
  {{- with .Values.tolerations }}
  tolerations:
    {{- toYaml . | nindent 8 }}
  {{- end }}
{{- end }}
