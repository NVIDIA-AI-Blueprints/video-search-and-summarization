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

{{/*
Expand chart name.
*/}}
{{- define "dev-profile-search.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Fully-qualified app name.
*/}}
{{- define "dev-profile-search.fullname" -}}
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

{{/*
Chart label.
*/}}
{{- define "dev-profile-search.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels.
*/}}
{{- define "dev-profile-search.labels" -}}
helm.sh/chart: {{ include "dev-profile-search.chart" . }}
{{ include "dev-profile-search.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: metropolis-dev-profile-search
{{- end }}

{{/*
Selector labels.
*/}}
{{- define "dev-profile-search.selectorLabels" -}}
app.kubernetes.io/name: {{ include "dev-profile-search.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Kubernetes Service.metadata.name for a sibling subchart short name.
Respects global.useReleaseNamePrefix (dict "root" $ "short" "redis").
*/}}
{{- define "dev-profile-search.serviceShort" -}}
{{- $short := index . "short" -}}
{{- $root := index . "root" -}}
{{- $g := $root.Values.global | default dict }}
{{- $pfx := default false (coalesce $root.Values.useReleaseNamePrefix (index $g "useReleaseNamePrefix")) }}
{{- if $pfx }}{{ printf "%s-%s" $root.Release.Name $short | trunc 63 | trimSuffix "-" }}{{- else -}}{{ $short | trunc 63 | trimSuffix "-" }}{{- end }}
{{- end }}

{{/*
Shared PVC for nvstreamer / streamprocessing (templates/shared-streamer-videos-pvc.yaml).
*/}}
{{- define "dev-profile-search.streamerVideosPvcName" -}}
{{- $g := .Values.global | default dict }}
{{- $pfx := default false (coalesce .Values.useReleaseNamePrefix (index $g "useReleaseNamePrefix")) }}
{{- if $pfx }}
{{- printf "%s-streamer-videos" .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- print "streamer-videos" }}
{{- end }}
{{- end }}

{{/*
NGC image pull secret name.
*/}}
{{- define "dev-profile-search.imagePullSecretName" -}}
{{- default "ngc-docker-reg-secret" .Values.global.imagePullSecretName }}
{{- end }}

{{/*
  Resolves the Kubernetes name for a dependency subchart (same rules as each subchart's .fullname helper).
  Pass: dict "Values" .Values "Release" .Release "depKey" "vss-agent" "chartName" "vss-agent"
  Optional: "subchartValues" (dict) — when set, used instead of index .Values .depKey.
*/}}
{{- define "vss.subchartFullname" -}}
{{- $vios := index .Values "vios" | default dict }}
{{- $agent := index .Values "agent" | default dict }}
{{- $agentVss := index $agent "vss-agent" | default (index .Values "vss-agent") | default dict }}
{{- $topDep := index .Values .depKey | default (index $vios .depKey) | default dict }}
{{- $fromDep := ternary $agentVss $topDep (eq .depKey "vss-agent") }}
{{- $vals := .subchartValues | default $fromDep | default dict }}
{{- if $vals.fullnameOverride }}
{{- $vals.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .chartName $vals.nameOverride }}
{{- $global := .Values.global | default dict }}
{{- $usePrefix := default false (coalesce $vals.useReleaseNamePrefix (index $global "useReleaseNamePrefix")) }}
{{- if $usePrefix }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s" $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{- define "dev-profile-search.rtviScriptsConfigMapName" -}}
{{- $rtviRoot := index .Values "rtvi" | default dict -}}
{{- $rtvi := index $rtviRoot "vss-rtvi-cv" | default dict -}}
{{- $existing := dig "scripts" "existingConfigMap" "" $rtvi -}}
{{- if $existing }}
{{- $existing -}}
{{- else -}}
{{- $base := "" -}}
{{- if (index $rtvi "fullnameOverride") -}}
{{- $base = (index $rtvi "fullnameOverride") -}}
{{- else -}}
{{- $name := default "vss-rtvi-cv" (index $rtvi "nameOverride") -}}
{{- $global := .Values.global | default dict -}}
{{- $usePrefix := default false (coalesce (index $rtvi "useReleaseNamePrefix") (index $global "useReleaseNamePrefix")) -}}
{{- if $usePrefix -}}
{{- $base = printf "%s-%s" .Release.Name $name -}}
{{- else -}}
{{- $base = printf "%s" $name -}}
{{- end -}}
{{- end -}}
{{- printf "%s-scripts" $base | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end }}

{{/*
Kubernetes name of one Search VIOS webhook ConfigMap. The caller supplies an
unprefixed logical name; when global.useReleaseNamePrefix is true the release
is prepended so two Search installs in one namespace do not share ConfigMaps.
*/}}
{{- define "dev-profile-search.viosNotificationConfigMapName" -}}
{{- $root := .root -}}
{{- $cm := .name | default "" -}}
{{- $g := $root.Values.global | default dict -}}
{{- $pfx := default false (index $g "useReleaseNamePrefix") -}}
{{- if and $cm $pfx -}}
{{- printf "%s-%s" $root.Release.Name $cm | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $cm -}}
{{- end -}}
{{- end }}

{{/*
One service-specific Search VIOS notification_config.json. The bundled webhook
policy (or global inline replacement) is shared, but every scalar placeholder
is resolved with that service's values so the overlay does not discard
sensor/streamprocessing broker overrides.
*/}}
{{- define "dev-profile-search.viosNotificationConfig" -}}
{{- $root := .root }}
{{- $service := .service | default dict }}
{{- $g := $root.Values.global | default dict }}
{{- $vios := index $g "vios" | default dict }}
{{- $path := index $vios "notificationConfigFile" | default "configs/vios/notification_config.json" }}
{{- $raw := index $vios "notificationConfig" | default "" }}
{{- if kindIs "map" $raw }}
{{- $raw = $raw | toPrettyJson }}
{{- end }}
{{- if not $raw }}
{{- $raw = $root.Files.Get $path }}
{{- end }}
{{- if not $raw }}
{{- fail (printf "Search VIOS notification config is empty (set global.vios.notificationConfig or %q)" $path) }}
{{- end }}
{{- $pfx := default false (index $g "useReleaseNamePrefix") }}
{{- $redisH := ternary (printf "%s-redis" $root.Release.Name) "redis" $pfx }}
{{- $kafkaH := ternary (printf "%s-kafka-kafka" $root.Release.Name) "kafka-kafka" $pfx }}
{{- $esH := ternary (printf "%s-elasticsearch" $root.Release.Name) "elasticsearch" $pfx }}
{{- $rtviCvH := ternary (printf "%s-vss-rtvi-cv" $root.Release.Name) "vss-rtvi-cv" $pfx }}
{{- $rtviEmbedH := ternary (printf "%s-vss-rtvi-embed" $root.Release.Name) "vss-rtvi-embed" $pfx }}
{{- $rtviVlmH := ternary (printf "%s-vss-rtvi-vlm" $root.Release.Name) "vss-rtvi-vlm" $pfx }}
{{- $mqttH := ternary (printf "%s-mosquitto" $root.Release.Name) "mosquitto" $pfx }}
{{- $redisAddr := index $service "redisServerAddress" | default (printf "%s:6379" $redisH) }}
{{- $kafkaAddr := index $service "kafkaServerAddress" | default (printf "%s:9092" $kafkaH) }}
{{- $rtviVlmAddr := index $service "rtviVlmServerAddress" | default (printf "%s:8000" $rtviVlmH) }}
{{- $elasticsearchAddr := index $service "elasticsearchServerAddress" | default (printf "%s:9200" $esH) }}
{{- $mqttAddr := index $service "mqttBrokerAddress" | default (printf "tcp://%s:1883" $mqttH) }}
{{- $messageBrokerConsumer := index $service "messageBrokerConsumer" | default (index $vios "messageBrokerConsumer" | default "redis") }}
{{- $messageBrokerTopicConsumer := index $service "messageBrokerTopicConsumer" | default (index $vios "messageBrokerTopicConsumer" | default "mdx-raw") }}
{{- $messageBrokerMetadataTopic := index $service "messageBrokerMetadataTopic" | default (index $vios "messageBrokerMetadataTopic" | default "mdx-raw") }}
{{- $rii := index $g "rtviInternalIngress" | default dict }}
{{- $riiEnabled := index $rii "enabled" | default false }}
{{- $controllerService := index $rii "controllerService" | default "haproxy-kubernetes-ingress.haproxy-controller" }}
{{- $controllerPort := index $rii "controllerPort" | default 80 }}
{{- $rtviCvPath := index $rii "rtviCvPath" | default "/rtvi-cv" | trimSuffix "/" }}
{{- $rtviEmbedPath := index $rii "rtviEmbedPath" | default "/rtvi-embed" | trimSuffix "/" }}
{{- $rtviCvDefault := ternary (printf "%s:%v%s" $controllerService $controllerPort $rtviCvPath) (printf "%s:9000" $rtviCvH) $riiEnabled }}
{{- $rtviEmbedDefault := ternary (printf "%s:%v%s" $controllerService $controllerPort $rtviEmbedPath) (printf "%s:8000" $rtviEmbedH) $riiEnabled }}
{{- $rtviCvAddr := index $service "rtviCvServerAddress" | default $rtviCvDefault }}
{{- $rtviEmbedAddr := index $service "rtviEmbedServerAddress" | default $rtviEmbedDefault }}
{{- $raw | replace "__REDIS_ADDRESS__" $redisAddr | replace "__KAFKA_ADDRESS__" $kafkaAddr | replace "__MQTT_BROKER_ADDRESS__" $mqttAddr | replace "__RTVI_CV_ADDRESS__" $rtviCvAddr | replace "__RTVI_EMBED_ADDRESS__" $rtviEmbedAddr | replace "__RTVI_VLM_ADDRESS__" $rtviVlmAddr | replace "__ELASTICSEARCH_ADDRESS__" $elasticsearchAddr | replace "__USE_MESSAGE_BROKER_CONSUMER__" $messageBrokerConsumer | replace "__MESSAGE_BROKER_TOPIC_CONSUMER__" $messageBrokerTopicConsumer | replace "__MESSAGE_BROKER_METADATA_TOPIC__" $messageBrokerMetadataTopic -}}
{{- end }}

