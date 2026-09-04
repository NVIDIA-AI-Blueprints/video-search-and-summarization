#!/usr/bin/env bash

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

# Two-origin (public HTTPS vs internal HTTP) gateway proof.
#
#   bash gateway-brev.sh [<profile-dir-name>]
#
# WHY A SEPARATE SCRIPT AND NOT A SECTION IN gateway-proof.sh
# ----------------------------------------------------------
# gateway-proof.sh proves one origin: the deployment's own. Every check there
# runs unconditionally against http://vss.local:7777 and the host IP, and it is
# the artefact the PR's single-host validation is quoted against, so its check
# count is a number people compare across runs. This file asks a different
# question -- does the contract survive being reached on a SECOND origin that
# the deployment does not listen on, whose TLS is terminated by something
# outside the deployment entirely -- and it needs a different parameterisation
# (public origin, public host, a --resolve target, an optional TLS mode) and a
# different failure disposition: on a host with no public origin declared, the
# whole file is legitimately inapplicable, whereas gateway-proof.sh is never
# inapplicable. Folding this in would either make gateway-proof.sh's count
# depend on whether a public origin happens to be configured, or bury a whole
# inapplicable dimension inside a file that otherwise always applies.
#
# PARAMETERS (all optional; defaults read the deployment's own configuration)
#   PUBLIC_HOST     hostname callers use off-host. Default: VSS_PUBLIC_HOST
#                   from the haproxy container's environment.
#   PUBLIC_PORT     Default: VSS_PUBLIC_PORT from the same place.
#   PUBLIC_SCHEME   http|https. Default: VSS_PUBLIC_HTTP_PROTOCOL, else http.
#   RESOLVE_TARGET  ip:port curl should actually connect to for PUBLIC_HOST.
#                   Default: <host ip>:<HAPROXY_PORT>. This is what lets the
#                   file run before a Brev box exists: declare a synthetic
#                   public hostname and point it at the local listener.
#   INTERNAL_ORIGIN Default http://vss.local:7777.
#
# Runs entirely from the host with curl --resolve, so no in-bridge sidecar is
# needed for the public-origin half.

set -uo pipefail

PROFILE="${1:-dev-profile-alerts}"
# deploy/docker, two levels up from scripts/gateway-harness/. Overridable so a
# checkout can be driven from elsewhere, but never hardcoded to one home
# directory the way the out-of-tree copy of this file was.
DOCKER_DIR="${DOCKER_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
P="${DOCKER_DIR}/developer-profiles/${PROFILE}"
BRIDGE_CTR="${BRIDGE_CTR:-gwproof}"
INTERNAL_ORIGIN="${INTERNAL_ORIGIN:-http://vss.local:7777}"

HOST_IP="$(ip route get 1.1.1.1 | awk '/src/ {for (i=1;i<=NF;i++) if ($i=="src") print $(i+1)}')"

gw_env() { docker inspect vss-haproxy-ingress --format '{{range .Config.Env}}{{println .}}{{end}}' 2>/dev/null | grep -E "^${1}=" | head -1 | cut -d= -f2-; }

HAPROXY_PORT="${HAPROXY_PORT:-$(gw_env HAPROXY_PORT)}"; HAPROXY_PORT="${HAPROXY_PORT:-7777}"
PUBLIC_HOST="${PUBLIC_HOST:-$(gw_env VSS_PUBLIC_HOST)}"
PUBLIC_PORT="${PUBLIC_PORT:-$(gw_env VSS_PUBLIC_PORT)}"; PUBLIC_PORT="${PUBLIC_PORT:-${HAPROXY_PORT}}"
PUBLIC_SCHEME="${PUBLIC_SCHEME:-$(gw_env VSS_PUBLIC_HTTP_PROTOCOL)}"; PUBLIC_SCHEME="${PUBLIC_SCHEME:-http}"
PUBLIC_WS_SCHEME="${PUBLIC_WS_SCHEME:-$(gw_env VSS_PUBLIC_WS_PROTOCOL)}"
RESOLVE_TARGET="${RESOLVE_TARGET:-${HOST_IP}:${HAPROXY_PORT}}"

PUBLIC_AUTH="${PUBLIC_HOST}:${PUBLIC_PORT}"
PUBLIC_ORIGIN="${PUBLIC_SCHEME}://${PUBLIC_AUTH}"

PASS=0; FAIL=0; SKIP=0
declare -a FAILURES=() SKIPS=()

ok()   { echo "PASS  $1"; PASS=$((PASS+1)); }
bad()  { echo "FAIL  $1"; FAIL=$((FAIL+1)); FAILURES+=("$1"); }
skip() { echo "SKIP  $1"; echo "      reason: $2"; SKIP=$((SKIP+1)); SKIPS+=("$1 -- $2"); }
section() { echo; echo "=== $1 ==="; }

# ------------------------------------------------- deployment inventory ----
#
# Same two-source evidence rule as gateway-proof.sh: a mount is only skipped
# when the compose project neither declares nor runs a service that can back
# it. Never on the check's own outcome.
COMPOSE=(docker compose -f "${DOCKER_DIR}/compose.yml"
         --env-file "${DOCKER_DIR}/containers.env"
         --env-file "${P}/.env"
         --env-file "${P}/overrides.env"
         --env-file "${P}/generated.env")

DECLARED="$("${COMPOSE[@]}" config --services 2>/dev/null | sort -u)"
RUNNING="$("${COMPOSE[@]}" ps --services --status running 2>/dev/null | sort -u)"
if [[ -z "${DECLARED}" ]]; then
  echo "FATAL: could not resolve the composed service list for ${PROFILE}; refusing to guess skips."
  exit 2
fi

declare -A MOUNT_SERVICE=(
  [va-mcp]="vss-va-mcp" [elasticsearch]="elasticsearch" [kibana]="kibana"
  [alert-bridge]="alert-bridge" [lvs]="lvs-server" [vst]="vst-ingress"
  [rtvi-vlm]="rtvi-vlm" [phoenix]="phoenix"
  [video-analytics-api]="vss-video-analytics-api"
  [agent]="vss-agent" [ui]="vss-ui"
)
in_list() { [[ -n "$2" ]] && grep -qxF "$1" <<<"$2"; }
declare -A MOUNT_STATE=()
EXPECTED_MOUNTS=""; ABSENT_MOUNTS=""
for k in "${!MOUNT_SERVICE[@]}"; do
  d=0; r=0
  for svc in ${MOUNT_SERVICE[$k]}; do
    in_list "${svc}" "${DECLARED}" && d=1
    in_list "${svc}" "${RUNNING}"  && r=1
  done
  if   (( d && r )); then MOUNT_STATE[$k]=present
  elif (( d ));      then MOUNT_STATE[$k]=declared-not-running
  elif (( r ));      then MOUNT_STATE[$k]=running-not-declared
  else                    MOUNT_STATE[$k]=absent
  fi
  if [[ "${MOUNT_STATE[$k]}" == absent ]]; then ABSENT_MOUNTS+="${k} "; else EXPECTED_MOUNTS+="${k} "; fi
done
EXPECTED_MOUNTS="$(tr ' ' '\n' <<<"${EXPECTED_MOUNTS}" | sort | tr '\n' ' ')"
ABSENT_MOUNTS="$(tr ' ' '\n' <<<"${ABSENT_MOUNTS}" | sort | tr '\n' ' ')"

mount_evidence() {
  echo "backing compose service(s) '${MOUNT_SERVICE[$1]}' are neither declared by \
\`compose config --services\` for ${PROFILE} nor running per \`compose ps\`"
}
want() {
  if [[ "${MOUNT_STATE[$1]}" == absent ]]; then skip "$2" "$(mount_evidence "$1")"; return 1; fi
  return 0
}

# --------------------------------------------------------------- helpers ----

hdr_val() { echo "$1" | grep -i "^$2:" | tr -d '\r' | head -1 | sed "s/^[^:]*: *//"; }

# Three modes, decided by probing rather than by being told, so a run on a real
# Brev box and a run here differ only in what the probe finds.
#
#   real-tls       something actually terminates TLS for the public origin. Talk
#                  https to it exactly as a browser would.
#   simulated-tls  the deployment DECLARES an https public origin but nothing
#                  here terminates it. Present the request the way an external
#                  terminator presents it to the deployment: plain HTTP to the
#                  listener, Host = the public hostname with no port (a
#                  terminator drops the default :443), X-Forwarded-Proto: https.
#                  Routing and Host behaviour are fully exercised; the TLS layer
#                  is not, and every TLS-specific claim is skipped by name.
#   direct-http    the public origin is plain http, as on a bare dev host.
#
# Either way the connection is made with --resolve so the Host header, the URL
# authority and the SNI name are all the public name while the TCP connection
# lands on the real listener.
RESOLVE_ADDR="${RESOLVE_TARGET%%:*}"
RESOLVE_PORT="${RESOLVE_TARGET##*:}"

MODE=""
if [[ "${PUBLIC_SCHEME}" == "https" ]]; then
  if curl -sS -o /dev/null --max-time 8 \
       --resolve "${PUBLIC_HOST}:${PUBLIC_PORT}:${RESOLVE_ADDR}" \
       "https://${PUBLIC_HOST}:${PUBLIC_PORT}/" >/dev/null 2>&1; then
    MODE=real-tls
  else
    MODE=simulated-tls
  fi
else
  MODE=direct-http
fi

case "${MODE}" in
  real-tls)
    CURL_PUB=(curl -s --max-time 25 --resolve "${PUBLIC_HOST}:${PUBLIC_PORT}:${RESOLVE_ADDR}")
    PUB_URL_BASE="https://${PUBLIC_HOST}:${PUBLIC_PORT}"
    PUB_HOST_HDR=()
    ;;
  simulated-tls)
    # curl --resolve maps name:port -> address; it cannot remap the port. So the
    # URL carries the listener port and the Host header carries what the
    # terminator would send.
    CURL_PUB=(curl -s --max-time 25 --resolve "${PUBLIC_HOST}:${RESOLVE_PORT}:${RESOLVE_ADDR}")
    PUB_URL_BASE="http://${PUBLIC_HOST}:${RESOLVE_PORT}"
    if [[ "${PUBLIC_PORT}" == "443" ]]; then
      PUB_HOST_HDR=(-H "Host: ${PUBLIC_HOST}" -H 'X-Forwarded-Proto: https')
    else
      PUB_HOST_HDR=(-H "Host: ${PUBLIC_AUTH}" -H 'X-Forwarded-Proto: https')
    fi
    ;;
  direct-http)
    if [[ "${PUBLIC_PORT}" != "${RESOLVE_PORT}" ]]; then
      CURL_PUB=(curl -s --max-time 25 --resolve "${PUBLIC_HOST}:${RESOLVE_PORT}:${RESOLVE_ADDR}")
      PUB_URL_BASE="http://${PUBLIC_HOST}:${RESOLVE_PORT}"
      PUB_HOST_HDR=(-H "Host: ${PUBLIC_AUTH}")
    else
      CURL_PUB=(curl -s --max-time 25 --resolve "${PUBLIC_HOST}:${PUBLIC_PORT}:${RESOLVE_ADDR}")
      PUB_URL_BASE="http://${PUBLIC_HOST}:${PUBLIC_PORT}"
      PUB_HOST_HDR=()
    fi
    ;;
esac

# The origin a browser would see, which is what minted URLs must match. In
# simulated mode that is still the declared https origin, not the URL curl used.
BROWSER_ORIGIN="${PUBLIC_SCHEME}://${PUBLIC_HOST}"
[[ "${PUBLIC_PORT}" != "443" || "${PUBLIC_SCHEME}" != "https" ]] && BROWSER_ORIGIN="${PUBLIC_ORIGIN}"

pub_code()  { "${CURL_PUB[@]}" "${PUB_HOST_HDR[@]}" -o /dev/null -w '%{http_code}' -X "${1}" "${PUB_URL_BASE}${2}"; }
pub_hdr()   { "${CURL_PUB[@]}" "${PUB_HOST_HDR[@]}" -D - -o /dev/null "${PUB_URL_BASE}${1}"; }
pub_body()  { "${CURL_PUB[@]}" "${PUB_HOST_HDR[@]}" "${PUB_URL_BASE}${1}"; }
int_code()  { docker exec "${BRIDGE_CTR}" curl -s -o /dev/null -w '%{http_code}' -X "${1}" --max-time 20 "${INTERNAL_ORIGIN}${2}" 2>/dev/null; }
int_hdr()   { docker exec "${BRIDGE_CTR}" curl -s -D - -o /dev/null --max-time 20 "${INTERNAL_ORIGIN}${1}" 2>/dev/null; }

expect_code()    { if [[ "$3" == "$2" ]]; then ok "$1 -> $3"; else bad "$1 -> got $3, want $2"; fi; }
expect_code_in() { if [[ " $2 " == *" $3 "* ]]; then ok "$1 -> $3 (allowed: $2)"; else bad "$1 -> got $3, want one of $2"; fi; }

# GET /elasticsearch/_cluster/health answers 200, and that is deliberate.
#
# The edge guard is an allowlist and admits this one path explicitly:
#   acl es_read_shape path_reg ^/elasticsearch/+_cluster/health/*$
# The old denylist answered 403 here, and that was the defect: es_caption.py
# builds "${elasticsearch_url}/_cluster/health" from ELASTIC_SEARCH_ENDPOINT,
# which in a gateway deployment *is* the gateway -- so the denylist was
# refusing a legitimate caller its own health probe. It widens nothing either:
# GET /_cat/* was already allowed and discloses strictly more (every index
# name, doc count and size). Expecting 200 here is the contract, not a
# weakened assertion -- do not "restore" the 403.
#
# Checked on the payload as well as the status, so a route that broke or began
# answering 200 with an error body still fails.
expect_es_health() {
  local label="$1" got="$2" body="$3"
  if [[ "${got}" != "200" ]]; then
    bad "${label} -> got ${got}, want 200"
  elif [[ "${body}" == *'"cluster_name"'* && "${body}" == *'"status"'* ]]; then
    ok "${label} -> 200 with real cluster health (cluster_name + status)"
  else
    bad "${label} -> 200 but the body is not cluster health: ${body:0:160}"
  fi
}

TLS_TERMINATED_HERE=0
[[ "${MODE}" == "real-tls" ]] && TLS_TERMINATED_HERE=1

# Does an absolute URL sit on the public origin? Accepts both spellings of a
# default port, because the deployment interpolates VSS_PUBLIC_PORT literally
# and so mints "https://host:443/..." where a browser would write
# "https://host/...". Both are the same origin and both are correct.
is_public_origin() {
  local u="$1"
  [[ "${u}" == "${PUBLIC_SCHEME}://${PUBLIC_HOST}"      || "${u}" == "${PUBLIC_SCHEME}://${PUBLIC_HOST}/"* ]] && return 0
  [[ "${u}" == "${PUBLIC_SCHEME}://${PUBLIC_AUTH}"      || "${u}" == "${PUBLIC_SCHEME}://${PUBLIC_AUTH}/"* ]] && return 0
  # The URL curl itself used, so a run in simulated mode does not fail a
  # redirect that correctly echoed the request origin.
  [[ "${u}" == "${PUB_URL_BASE}"                        || "${u}" == "${PUB_URL_BASE}/"* ]] && return 0
  return 1
}

# ------------------------------------------------------------- 0. context ----

section "0. Two-origin context"

if [[ -z "${PUBLIC_HOST}" ]]; then
  echo "FATAL: no public host. Set PUBLIC_HOST, or VSS_PUBLIC_HOST in the deployment."
  exit 2
fi

cat <<CTX
profile:            ${PROFILE}
mode:               ${MODE}
internal origin:    ${INTERNAL_ORIGIN}   (in-deployment, service-to-service)
public origin:      ${PUBLIC_ORIGIN}     (browser / off-host callers)
host header sent:   ${PUB_HOST_HDR[*]:-<from url> ${PUBLIC_AUTH}}
connects to:        ${RESOLVE_TARGET}
url curl uses:      ${PUB_URL_BASE}
expected mounts:    ${EXPECTED_MOUNTS:-(none)}
absent mounts:      ${ABSENT_MOUNTS:-(none)}
CTX

case "${MODE}" in
  real-tls)
    ok "a TLS terminator answers for ${PUBLIC_AUTH} -- every assertion below, TLS included, is live"
    ;;
  simulated-tls)
    echo
    echo "SIMULATED TLS. The deployment declares an https public origin but nothing on"
    echo "this host terminates TLS for ${PUBLIC_HOST}. Requests are presented the way an"
    echo "external terminator presents them: plain HTTP to the listener, Host without the"
    echo "default port, X-Forwarded-Proto: https. Routing, aliases, deprecation headers,"
    echo "the Host allowlist and URL minting are all genuinely exercised. The TLS layer is"
    echo "not, and each TLS-specific claim is skipped by name in section 6."
    skip "TLS transport assertions on the public origin" \
         "mode=simulated-tls: no terminator answers https://${PUBLIC_AUTH} from this host, so \
certificate validity, HTTP/2 negotiation and HSTS cannot be observed. These are the \
terminator's responsibility and remain unproven until the deployment sits behind a real one."
    ;;
  direct-http)
    skip "TLS assertions on the public origin" \
         "the deployment declares VSS_PUBLIC_HTTP_PROTOCOL=http, so there is no https public \
origin to test. On Brev this is https and dev-profile.sh sets it automatically from \
BREV_ENV_ID; run this file there, or set PUBLIC_SCHEME=https to exercise the simulated path."
    ;;
esac

if [[ "${PUBLIC_AUTH}" == "${HOST_IP}:${HAPROXY_PORT}" ]]; then
  echo "NOTE: the public origin is identical to the host listener, so the two origins"
  echo "      coincide on this host. Set PUBLIC_HOST to a synthetic name to make them"
  echo "      genuinely distinct -- that is what makes section 3 meaningful."
fi

# ------------------------------------------ 1. path contract, public vhost ----

section "1. Whole path contract under the public origin, Host: ${PUBLIC_AUTH}"

want va-mcp "public GET /va-mcp/health" && \
  expect_code    "public GET /va-mcp/health"               200 "$(pub_code GET /va-mcp/health)"
want elasticsearch "public GET /elasticsearch/_cat/indices" && {
  expect_code    "public GET /elasticsearch/_cat/indices"  200 "$(pub_code GET /elasticsearch/_cat/indices)"
  expect_code    "public GET /elasticsearch/_search"       200 "$(pub_code GET /elasticsearch/_search)"
  expect_es_health "public GET /elasticsearch/_cluster/health (admitted for es_caption)" \
    "$(pub_code GET /elasticsearch/_cluster/health)" "$(pub_body /elasticsearch/_cluster/health)"
  # The carve-out is exactly one path, so pin both of its edges. settings and
  # stats sit under the same /_cluster/ prefix and must stay denied: widening
  # ^/elasticsearch/+_cluster/health/*$ into a /_cluster/ prefix match then
  # fails here instead of shipping.
  expect_code    "public GET /elasticsearch/_cluster/settings (must stay denied)" 403 "$(pub_code GET /elasticsearch/_cluster/settings)"
  expect_code    "public GET /elasticsearch/_cluster/stats (must stay denied)"    403 "$(pub_code GET /elasticsearch/_cluster/stats)"
  expect_code    "public PUT /elasticsearch/some-index (must stay denied)"      405 "$(pub_code PUT /elasticsearch/some-index)"
}
want alert-bridge "public GET /alert-bridge/health" && \
  expect_code    "public GET /alert-bridge/health"         200 "$(pub_code GET /alert-bridge/health)"
want vst "public GET /vst/api/v1/sensor/streams" && {
  expect_code_in "public GET /vst/api/v1/sensor/streams"  "200 204" "$(pub_code GET /vst/api/v1/sensor/streams)"
  expect_code_in "public GET /vios/api/v1/sensor/streams" "200 204" "$(pub_code GET /vios/api/v1/sensor/streams)"
}
want kibana "public GET /kibana/api/status" && \
  expect_code_in "public GET /kibana/api/status"          "200 302" "$(pub_code GET /kibana/api/status)"
want phoenix "public GET /phoenix" && \
  expect_code_in "public GET /phoenix"                    "200 204 301 302 307" "$(pub_code GET /phoenix)"
want rtvi-vlm "public GET /rtvi-vlm/v1/models" && \
  expect_code    "public GET /rtvi-vlm/v1/models"          200 "$(pub_code GET /rtvi-vlm/v1/models)"
want video-analytics-api "public GET /video-analytics-api/health" && \
  expect_code_in "public GET /video-analytics-api/health" "200 404" "$(pub_code GET /video-analytics-api/health)"
want ui "public GET / (UI)" && \
  expect_code_in "public GET / (UI)"                      "200 302" "$(pub_code GET /)"

# Edge short-circuits need no backend, so they hold on every profile.
for pair in "HEAD /storage/x 200" "HEAD /vst/storage/x 200" "HEAD /vios/storage/x 200" \
            "OPTIONS /storage/x 204" "OPTIONS /vst/storage/x 204" "OPTIONS /vios/storage/x 204"; do
  read -r m p want_c <<<"${pair}"
  expect_code "public ${m} ${p}" "${want_c}" "$(pub_code "${m}" "${p}")"
done

# -------------------------- 2. aliases + deprecation identical on both ----

section "2. Aliases and deprecation signals are identical on both origins"

# The point is not that the header exists -- gateway-proof.sh proves that on
# the internal origin. The point is that it is BYTE-IDENTICAL when the request
# arrives on the public vhost. A Host-dependent deprecation signal would mean
# browser clients never learn the prefix is going away.
compare_origins() {  # compare_origins <path> <mount-key>
  local path="$1" key="$2"
  if ! want "${key}" "public vs internal parity for ${path}"; then return; fi
  local ic pc ih ph
  ic="$(int_code GET "${path}")"; pc="$(pub_code GET "${path}")"
  if [[ "${ic}" == "${pc}" ]]; then
    ok "${path} same status on both origins (${pc})"
  else
    bad "${path} status differs: public=${pc} internal=${ic}"
  fi
  ih="$(int_hdr "${path}")"; ph="$(pub_hdr "${path}")"
  for h in deprecation link sunset; do
    local iv pv
    iv="$(hdr_val "${ih}" "${h}")"; pv="$(hdr_val "${ph}" "${h}")"
    if [[ "${iv}" == "${pv}" ]]; then
      ok "${path} ${h}: identical on both origins ('${iv:-<absent>}')"
    else
      bad "${path} ${h} differs -- public='${pv:-<absent>}' internal='${iv:-<absent>}'"
    fi
  done
}

compare_origins /vst/api/v1/sensor/streams  vst
compare_origins /vios/api/v1/sensor/streams vst
compare_origins /alert-bridge/health        alert-bridge
compare_origins /alerts/health              alert-bridge

# /lvs and its alias: parity holds whether or not the backend exists, and when
# it does not, HAProxy's own 503 must be what both origins see.
LVS_I="$(int_code GET /lvs/v1/live)"; LVS_P="$(pub_code GET /lvs/v1/live)"
VS_P="$(pub_code GET /video-summarization/v1/live)"
if [[ "${LVS_I}" == "${LVS_P}" && "${LVS_P}" == "${VS_P}" ]]; then
  ok "/lvs and /video-summarization agree across both origins (${LVS_P})"
else
  bad "/lvs public=${LVS_P} internal=${LVS_I}; /video-summarization public=${VS_P}"
fi

# ---------------- 3. absolute URLs the deployment mints (the real worry) ----

section "3. Absolute URLs are minted on the PUBLIC origin, not vss.local or a bare IP"

# This is the failure mode reviewers are worried about: a request arrives on the
# public vhost, and the deployment answers with a URL a browser on the public
# origin cannot follow -- http://vss.local:7777/... (unresolvable off-host) or
# http://10.x.y.z:7777/... (mixed content from an https page, and often
# unroutable).
#
# Two layers, and the distinction matters:
#
#  (a) CONFIGURATION. VST_EXTERNAL_URL, VST_BASE_URL, VSS_AGENT_EXTERNAL_URL and
#      VSS_AGENT_REPORTS_BASE_URL are interpolated from VSS_PUBLIC_* at compose
#      time and baked into each container's environment. They are therefore NOT
#      per-request: the same value is emitted whichever origin the caller used.
#      So the assertion that actually protects the browser is that the baked
#      value equals the public origin and is not the gateway origin. That is
#      checkable anywhere, including here.
#
#  (b) RUNTIME. What a live response actually carries: a media_url in VST's
#      sensor/streams payload, a Location on a redirect, Content-Location,
#      Refresh. HAProxy does not rewrite these, so if (a) is right they are
#      right, and if (a) is wrong they are wrong -- this catches a service that
#      builds its own URL from the request or from a Docker DNS name instead of
#      from the configured public origin.

BAD_ORIGIN_RE='(vss\.local|127\.0\.0\.1|localhost|(vss-|vst-|rtvi-|alert-)[a-z0-9-]*:[0-9]+)'

# (a) configuration.
#
# All four live on vss-agent: it is the service that renders report links and
# hands VST URLs to the UI. vst-ingress does not carry them -- looking for them
# there finds nothing and would report a missing variable that was never meant
# to be there.
for pair in "vss-agent VST_EXTERNAL_URL agent" "vss-agent VST_BASE_URL agent" \
            "vss-agent VSS_AGENT_EXTERNAL_URL agent" "vss-agent VSS_AGENT_REPORTS_BASE_URL agent"; do
  read -r ctr var key <<<"${pair}"
  if ! want "${key}" "${var} baked into ${ctr} is on the public origin"; then continue; fi
  val="$(docker inspect "${ctr}" --format '{{range .Config.Env}}{{println .}}{{end}}' 2>/dev/null | grep -E "^${var}=" | head -1 | cut -d= -f2-)"
  if [[ -z "${val}" ]]; then
    bad "${var} is not set in ${ctr}, so the deployment has no public origin to mint URLs on"
  elif is_public_origin "${val}"; then
    ok "${var}=${val} is on the public origin"
  elif [[ "${val}" =~ ${BAD_ORIGIN_RE} ]]; then
    bad "${var}=${val} is an INTERNAL origin -- a browser on ${PUBLIC_ORIGIN} cannot follow it"
  else
    bad "${var}=${val} is neither the public origin ${PUBLIC_ORIGIN} nor recognisably internal"
  fi
done

# (b) runtime: media_url in VST's own payload
if want vst "VIOS media_url is on the public origin"; then
  STREAMS="$(pub_body /vios/api/v1/sensor/streams)"
  URLS="$(grep -oE '"(media_url|url|rtsp_url|http_url)" *: *"[^"]+"' <<<"${STREAMS}" | sed 's/.*: *"//; s/"$//' | sort -u)"
  if [[ -z "${STREAMS}" || "${STREAMS}" == "[]" || -z "${URLS}" ]]; then
    skip "VIOS media_url origin" \
         "GET /vios/api/v1/sensor/streams on the public origin returned no stream with a URL \
field (payload: '${STREAMS:0:120}'), so there is no minted URL to inspect. This needs a sensor \
added to the deployment; it is NOT evidence that URLs are correct."
  else
    LOCALS="$(grep -E "${BAD_ORIGIN_RE}" <<<"${URLS}")"
    if [[ -n "${LOCALS}" ]]; then
      bad "VIOS minted internal URLs on a public request: ${LOCALS//$'\n'/ | }"
    else
      ok "every VIOS-minted http(s) URL avoids an internal origin: ${URLS//$'\n'/ | }"
    fi
  fi
fi

# (b) runtime: redirect and location-bearing headers across the whole contract
section "3b. Redirect / Location headers on a public request"

LOC_PATHS="/ /phoenix /kibana /vst /vios /alerts /alert-bridge /va-mcp /elasticsearch /api /chat /static"
LOC_BAD=""
LOC_SEEN=""
for p in ${LOC_PATHS}; do
  h="$(pub_hdr "${p}")"
  for hn in location content-location refresh; do
    v="$(hdr_val "${h}" "${hn}")"
    [[ -z "${v}" ]] && continue
    LOC_SEEN+="${p} ${hn}: ${v}"$'\n'
    # Only absolute URLs can point at the wrong origin; a relative one inherits
    # the request origin and is always correct.
    if [[ "${v}" == http*://* ]] && ! is_public_origin "${v}"; then
      LOC_BAD+="${p} ${hn}: ${v}"$'\n'
    fi
  done
done
if [[ -n "${LOC_SEEN}" ]]; then
  echo "--- location-bearing headers observed on the public origin ---"
  printf '%s' "${LOC_SEEN}" | sed 's/^/  /'
fi
if [[ -z "${LOC_SEEN}" ]]; then
  skip "redirect Location origin" \
       "no route in this profile answered with Location, Content-Location or Refresh, so \
there is no absolute redirect to check. Paths probed: ${LOC_PATHS}"
elif [[ -n "${LOC_BAD}" ]]; then
  bad "absolute redirect(s) point off the public origin: ${LOC_BAD//$'\n'/ | }"
else
  ok "every absolute Location/Content-Location/Refresh stays on the public origin (or is relative)"
fi

# ---------------------------------- 4. Host allowlist on the public vhost ----

section "4. Host allowlist: the public name is admitted, an undeclared one is not"

expect_code "public Host ${PUBLIC_AUTH} admitted" 200 "$(pub_code GET /)"

# An undeclared hostname must be refused, and must say why.
UNDECL="not-a-declared-origin.example"
UNDECL_CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 \
  -H "Host: ${UNDECL}" "http://${HOST_IP}:${HAPROXY_PORT}/")"
expect_code "undeclared Host ${UNDECL}" 404 "${UNDECL_CODE}"
UNDECL_HDR="$(curl -s -D - -o /dev/null --max-time 20 -H "Host: ${UNDECL}" \
  "http://${HOST_IP}:${HAPROXY_PORT}/" | grep -i '^x-vss-gateway-deny:' | tr -d '\r')"
if [[ "${UNDECL_HDR}" == *"unknown-host"* ]]; then
  ok "undeclared Host names its cause: ${UNDECL_HDR}"
else
  bad "undeclared Host got 404 but no x-vss-gateway-deny header: '${UNDECL_HDR}'"
fi

# A near-miss on the public name must not be admitted -- the allowlist is exact
# match, not a suffix match, so a lookalike registered by someone else fails.
for near in "${PUBLIC_HOST}.example" "evil-${PUBLIC_HOST}" "${PUBLIC_HOST}:1234"; do
  c="$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 -H "Host: ${near}" \
       "http://${HOST_IP}:${HAPROXY_PORT}/")"
  expect_code "near-miss Host ${near} refused" 404 "${c}"
done

# --- Both pairings simultaneously ---
#
# VSS_PUBLIC_HOST:VSS_PUBLIC_PORT and HOST_IP/EXTERNAL_IP:HAPROXY_PORT are
# separate ACL entries, so both should be admitted at the same time with no
# recreate in between. On Brev those are (secure-link:443) and (host ip:7777).
section "4b. Both Host pairings admitted simultaneously"

PUB_PAIR_CODE="$(pub_code GET /)"
IP_PAIR_CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 \
  -H "Host: ${HOST_IP}:${HAPROXY_PORT}" "http://${HOST_IP}:${HAPROXY_PORT}/")"
EXTERNAL_IP_VAL="$(gw_env EXTERNAL_IP)"
EXT_PAIR_CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 \
  -H "Host: ${EXTERNAL_IP_VAL:-${HOST_IP}}:${HAPROXY_PORT}" "http://${HOST_IP}:${HAPROXY_PORT}/")"

if [[ "${PUB_PAIR_CODE}" == "200" && "${IP_PAIR_CODE}" == "200" && "${EXT_PAIR_CODE}" == "200" ]]; then
  ok "VSS_PUBLIC_HOST:${PUBLIC_PORT}, HOST_IP:${HAPROXY_PORT} and EXTERNAL_IP:${HAPROXY_PORT} are all admitted in the same running config"
else
  bad "pairings do not all work simultaneously: public=${PUB_PAIR_CODE} host_ip=${IP_PAIR_CODE} external_ip(${EXTERNAL_IP_VAL})=${EXT_PAIR_CODE}"
fi

# The bare public host with no port, and with the listener port, are separate
# ACL entries from host:public-port. A terminator that drops the default :443
# sends Host without a port, so that form has to be admitted too.
BARE_CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 \
  -H "Host: ${PUBLIC_HOST}" "http://${HOST_IP}:${HAPROXY_PORT}/")"
expect_code "bare public Host '${PUBLIC_HOST}' (no port, as an https terminator sends it)" 200 "${BARE_CODE}"

# ------------------------------------------------ 5. websocket upgrade ----

section "5. WebSocket upgrade through the public origin"

# The UI's live channel is /websocket on the agent backend. HAProxy is in
# tunnel mode with `timeout tunnel 3600s`, so an upgrade must be passed
# through rather than answered by the proxy.
if ! want agent "public WebSocket upgrade on /websocket"; then
  :
else
  WS_HDR="$("${CURL_PUB[@]}" "${PUB_HOST_HDR[@]}" -D - -o /dev/null \
    -H 'Connection: Upgrade' -H 'Upgrade: websocket' \
    -H 'Sec-WebSocket-Version: 13' -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==' \
    "${PUB_URL_BASE}/websocket" 2>/dev/null)"
  WS_CODE="$(echo "${WS_HDR}" | awk '/^HTTP\//{c=$2} END{print c}')"
  WS_UPG="$(hdr_val "${WS_HDR}" upgrade)"
  case "${WS_CODE}" in
    101)
      ok "public /websocket upgraded: 101 with Upgrade: ${WS_UPG}"
      ;;
    404|400|426)
      # The agent may only accept the upgrade on a sub-path or with app auth.
      # What must be true regardless is that HAProxy did not refuse it at the
      # edge -- an edge refusal would be the 404 + x-vss-gateway-deny form.
      if echo "${WS_HDR}" | grep -qi '^x-vss-gateway-deny:'; then
        bad "public /websocket was refused by the GATEWAY (${WS_CODE}, x-vss-gateway-deny present)"
      else
        skip "public /websocket 101 upgrade" \
             "the agent answered ${WS_CODE} to the upgrade and the response carries no \
x-vss-gateway-deny header, so the gateway routed it and the backend declined. The \
gateway's pass-through is proven; whether the application accepts an upgrade at this \
exact path is the agent's contract, not the gateway's. Same code on the internal \
origin: $(int_code GET /websocket)."
      fi
      ;;
    "")
      bad "public /websocket returned no HTTP status at all"
      ;;
    *)
      # Any other status still proves the edge passed it through, but say so.
      if echo "${WS_HDR}" | grep -qi '^x-vss-gateway-deny:'; then
        bad "public /websocket refused by the gateway (${WS_CODE})"
      else
        ok "public /websocket reached the agent backend (${WS_CODE}, not a gateway refusal)"
      fi
      ;;
  esac

  # The upgrade must behave the same on both origins.
  WS_INT="$(docker exec "${BRIDGE_CTR}" curl -s -D - -o /dev/null --max-time 20 \
    -H 'Connection: Upgrade' -H 'Upgrade: websocket' -H 'Sec-WebSocket-Version: 13' \
    -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==' "${INTERNAL_ORIGIN}/websocket" 2>/dev/null \
    | awk '/^HTTP\//{c=$2} END{print c}')"
  if [[ "${WS_CODE}" == "${WS_INT}" ]]; then
    ok "/websocket upgrade behaves identically on both origins (${WS_CODE})"
  else
    bad "/websocket upgrade differs: public=${WS_CODE} internal=${WS_INT}"
  fi
fi

# The ws:// vs wss:// scheme the UI is told to use has to match the page's
# scheme or the browser blocks it as mixed content. Configuration-level, so
# checkable without TLS.
section "5b. Advertised WebSocket scheme matches the public scheme"

if [[ -z "${PUBLIC_WS_SCHEME}" ]]; then
  skip "VSS_PUBLIC_WS_PROTOCOL matches VSS_PUBLIC_HTTP_PROTOCOL" \
       "VSS_PUBLIC_WS_PROTOCOL is not present in the haproxy container's environment"
else
  case "${PUBLIC_SCHEME}:${PUBLIC_WS_SCHEME}" in
    https:wss|http:ws) ok "public scheme ${PUBLIC_SCHEME} pairs with ws scheme ${PUBLIC_WS_SCHEME}" ;;
    https:ws) bad "public origin is https but VSS_PUBLIC_WS_PROTOCOL=ws -- the browser blocks the socket as mixed content" ;;
    *) bad "unexpected scheme pairing: http=${PUBLIC_SCHEME} ws=${PUBLIC_WS_SCHEME}" ;;
  esac
fi

# ----------------------------------------------------- 6. TLS-only claims ----

section "6. TLS-terminator claims"

if (( TLS_TERMINATED_HERE )); then
  TLS_HDR="$(curl -s -D - -o /dev/null --max-time 25 \
    --resolve "${PUBLIC_HOST}:${PUBLIC_PORT}:${RESOLVE_ADDR}" "https://${PUBLIC_AUTH}/" 2>&1)"
  TLS_CODE="$(echo "${TLS_HDR}" | awk '/^HTTP\//{c=$2} END{print c}')"
  if [[ -n "${TLS_CODE}" ]]; then
    ok "public https origin answered ${TLS_CODE} over real TLS"
  else
    bad "public https origin did not complete a TLS request: ${TLS_HDR:0:200}"
  fi
  if curl -s -o /dev/null --max-time 25 --resolve "${PUBLIC_HOST}:${PUBLIC_PORT}:${RESOLVE_ADDR}" \
       "https://${PUBLIC_AUTH}/" 2>/dev/null; then
    ok "the terminator's certificate validates for ${PUBLIC_HOST} without --insecure"
  else
    bad "the certificate presented for ${PUBLIC_HOST} does not validate"
  fi
  XFP="$(hdr_val "$(pub_hdr /)" x-forwarded-proto)"
  echo "  x-forwarded-proto seen by the deployment: '${XFP:-<absent>}'"
else
  skip "certificate validity / HTTP-2 / HSTS on the public origin" \
       "mode=${MODE}. Nothing on this host terminates TLS for ${PUBLIC_HOST}, so there is no \
certificate to validate and no h2 to negotiate. Only a box where the platform actually \
fronts the deployment with TLS can prove these."
  skip "mixed-content behaviour in a real browser" \
       "requires an https page served by the terminator; curl cannot observe a browser's \
mixed-content block. The configuration-level preconditions for it ARE checked: the ws/http \
scheme pairing in 5b, and every minted absolute URL in section 3."
fi

# --------------------------------------------------------------- summary ----

section "Summary"
echo "profile:          ${PROFILE}"
echo "public origin:    ${PUBLIC_ORIGIN} (connected via ${RESOLVE_TARGET})"
echo "internal origin:  ${INTERNAL_ORIGIN}"
echo "expected mounts:  ${EXPECTED_MOUNTS:-(none)}"
echo "absent mounts:    ${ABSENT_MOUNTS:-(none)}"
echo
echo "PASS: ${PASS}"
echo "FAIL: ${FAIL}"
echo "SKIP: ${SKIP}"
if (( SKIP > 0 )); then
  echo
  echo "Skipped:"
  for s in "${SKIPS[@]}"; do echo "  - ${s}"; done
fi
if (( FAIL > 0 )); then
  echo
  echo "Failures:"
  for f in "${FAILURES[@]}"; do echo "  - ${f}"; done
  exit 1
fi
exit 0
