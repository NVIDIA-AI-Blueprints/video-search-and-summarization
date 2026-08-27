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
#

{{- define "dt-based-calibration.fullname" -}}
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

{{- define "dt-based-calibration.labels" -}}
app.kubernetes.io/name: {{ include "dt-based-calibration.fullname" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{- define "dt-based-calibration.selectorLabels" -}}
app: {{ include "dt-based-calibration.fullname" . }}
{{- end }}

{{/*
Resolve managed image using shared global.container_prefix / container_tag
channel (parity with compose VSS_DT_BASED_CALIBRATION_IMAGE / _TAG). QA
promotions flip the two globals without editing this chart.
*/}}
{{- define "dt-based-calibration.image" -}}
{{- $global := .Values.global | default dict -}}
{{- $prefix := index $global "container_prefix" | default "" -}}
{{- $repository := .Values.image.repository -}}
{{- if $prefix -}}
{{- $repository = printf "%s/dt-based-calibration" (trimSuffix "/" $prefix) -}}
{{- end -}}
{{- $tag := index $global "container_tag" | default .Values.image.tag -}}
{{- printf "%s:%s" $repository $tag -}}
{{- end -}}
