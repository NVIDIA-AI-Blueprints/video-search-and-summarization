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

{{- define "vst-mcp.fullname" -}}
{{- printf "%s-%s" .Release.Name "vst-mcp" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "vst-mcp.labels" -}}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version | replace "+" "_" }}
app.kubernetes.io/name: vst-mcp
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: metropolis-baseapp
app.kubernetes.io/component: vst-mcp
{{- end }}

{{- define "vst-mcp.selectorLabels" -}}
app.kubernetes.io/name: vst-mcp
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: vst-mcp
{{- end }}

{{- define "vst-mcp.vstIngressSvc" -}}
{{- printf "%s-vst-ingress" .Release.Name | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "vst-mcp.cppApiBaseUrl" -}}
{{- if .Values.gateway.cppApiBaseUrl }}
{{- .Values.gateway.cppApiBaseUrl }}
{{- else }}
{{- printf "http://%s:%s/vst" (include "vst-mcp.vstIngressSvc" .) (toString .Values.gateway.vstIngressPort) }}
{{- end }}
{{- end }}
