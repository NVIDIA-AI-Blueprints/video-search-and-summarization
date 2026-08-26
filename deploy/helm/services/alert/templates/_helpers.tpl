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

{{- define "vss-alert-bridge.fullname" -}}
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
{{- /*
Canonicalize a transport name the same way the application does.

Alert MS resolves transports through _normalize_transport(), which trims
surrounding whitespace, lowercases the value and strips "_" and "-" before
looking it up, and it treats "redis" as an alias of "redisStream". Every step
has to be mirrored here, including the trim: YAML block scalars and copy-pasted
values pick up stray spaces easily, and " redisStream" that the application
resolves but the chart does not would leave the init container waiting for the
wrong broker -- exactly the crash-loop this wait exists to prevent.
*/}}
{{- define "vss-alert-bridge.transport" -}}
{{- $value := . | default "" | trim | lower | replace "_" "" | replace "-" "" -}}
{{- if eq $value "redis" -}}
redisstream
{{- else -}}
{{- $value -}}
{{- end -}}
{{- end -}}
{{- define "vss-alert-bridge.image" -}}
{{- $global := .Values.global | default dict -}}
{{- $prefix := index $global "container_prefix" | default "" -}}
{{- $repository := .Values.image.repository -}}
{{- if $prefix -}}
{{- $repository = printf "%s/vss-alert-ms" (trimSuffix "/" $prefix) -}}
{{- end -}}
{{- $tag := index $global "container_tag" | default .Values.image.tag -}}
{{- printf "%s:%s" $repository $tag -}}
{{- end -}}
