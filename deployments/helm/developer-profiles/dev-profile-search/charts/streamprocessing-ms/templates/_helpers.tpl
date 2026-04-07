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

{{- define "streamprocessing-ms.fullname" -}}
{{- printf "%s-%s" .Release.Name "streamprocessing-ms" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "streamprocessing-ms.headlessName" -}}
{{- printf "%s-%s" .Release.Name "streamprocessing-ms-headless" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "streamprocessing-ms.labels" -}}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version | replace "+" "_" }}
app.kubernetes.io/name: streamprocessing-ms
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: metropolis-baseapp
app.kubernetes.io/component: streamprocessing-ms
{{- end }}

{{- define "streamprocessing-ms.selectorLabels" -}}
app.kubernetes.io/name: streamprocessing-ms
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: streamprocessing-ms
{{- end }}

{{- define "streamprocessing-ms.centralizedbPostgresCmName" -}}
{{- printf "%s-postgres-cm" (printf "%s-%s" .Release.Name "centralizedb" | trunc 63 | trimSuffix "-") }}
{{- end }}
