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
{{- /*
Whether any selected transport actually opens a Redis connection.

Takes the root context. Used to decide when redis.host has to be present: the
block is inert in the default all-Kafka deployment, so an empty host there is
not a misconfiguration.
*/}}
{{- define "vss-alert-bridge.usesRedis" -}}
{{- $src := include "vss-alert-bridge.transport" (.Values.eventSourceType | default "kafka") -}}
{{- $sink := include "vss-alert-bridge.transport" (.Values.eventSinkType | default "kafka") -}}
{{- $vlm := include "vss-alert-bridge.transport" (.Values.vlmSinkType | default "elastic") -}}
{{- if or (eq $src "redisstream") (eq $sink "redisstream") (eq $vlm "redisstream") -}}
true
{{- end -}}
{{- end -}}
{{- /*
Resolve the Redis host, and refuse to invent one.

Takes the root context. There is deliberately **no** release-based default here.
It used to fall back to `<release>-redis`, which meant a deployment that selected
a redisStream transport and did not set redis.host silently attached to whatever
answered that name in the namespace -- normally the bundled infra Redis, which is
a development convenience and not a stream the deployment owns. Alert MS never
deploys Redis and keeps no state in it, so the endpoint has to come from whoever
does.

The bundled instance is still reachable, by asking for it: redis.useInClusterRedis
puts the release-based name back. That is one line in values and it is visible in
review, which the silent default was not.
*/}}
{{- define "vss-alert-bridge.redisHost" -}}
{{- $g := .Values.global | default dict -}}
{{- $pfx := default false (coalesce .Values.useReleaseNamePrefix (index $g "useReleaseNamePrefix")) -}}
{{- $redis := .Values.redis | default dict -}}
{{- $host := $redis.host | default "" | toString | trim -}}
{{- if and (eq $host "") $redis.useInClusterRedis -}}
{{- $host = ternary (printf "%s-redis" .Release.Name) "redis" $pfx -}}
{{- end -}}
{{- if and (eq $host "") (include "vss-alert-bridge.usesRedis" .) -}}
{{- fail "vss-alert-bridge: a redisStream transport is selected but redis.host is empty. Alert MS does not deploy Redis and keeps no state in it, so point redis.host at the instance you provide. To use the bundled in-cluster Redis instead -- development only, its streams are not yours to keep -- set redis.useInClusterRedis: true. This used to default to <release>-redis, which attached to that instance without asking." -}}
{{- end -}}
{{- $host -}}
{{- end -}}
{{- /* The inline Redis password, refused unless the operator has said they mean
       it. This chart renders the connection into a ConfigMap, so a value here is
       readable by anything with get on ConfigMaps in the namespace and shows up
       in `helm get values`, CI logs and whatever stores the release. That is not
       a warning-level difference from a mounted Secret; it is the difference
       between a secret and not one.

       Still reachable, because a local instance with requirepass on a throwaway
       password is a real case and forcing a Secret for it buys nothing. What
       changes is that reaching it takes a second key, so it cannot be arrived at
       by filling in the field that looked like the obvious one. Use
       redis.passwordSecret for anything else -- it mounts the Secret and points
       password_file at it, and takes precedence over this value. */}}
{{- define "vss-alert-bridge.redisPassword" -}}
{{- $redis := .Values.redis | default dict -}}
{{- $secret := $redis.passwordSecret | default dict -}}
{{- $password := $redis.password | default "" | toString -}}
{{- if and $secret.name $secret.key -}}
{{- /* Rendered empty rather than alongside password_file. The mounted Secret
       already wins at connect time, so an inline value here changes nothing
       about which password is used -- it only writes it into the ConfigMap as
       well, which is the one thing configuring a Secret was meant to avoid. */}}
{{- else if ne $password "" -}}
{{- if not $redis.allowPasswordInConfigMap -}}
{{- fail "vss-alert-bridge: redis.password is set without redis.passwordSecret. This chart renders the Redis connection into a ConfigMap, so the password would be stored unencrypted and readable by anything that can read ConfigMaps in this namespace. Use redis.passwordSecret: {name, key} to mount a Secret instead. For a local or throwaway instance where that does not matter, set redis.allowPasswordInConfigMap: true to say so explicitly." -}}
{{- end -}}
{{- $password -}}
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
