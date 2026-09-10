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

# FR-35 -- Remote DNS Validation. Run this ON THE REMOTE AGENT HOST.
#
#   bash gateway-offhost.sh
#
# WHAT FR-35 ASKS FOR, VERBATIM
# -----------------------------
#   "Validation shall confirm that a remote agent host can resolve the same
#    canonical gateway hostname to the external HAProxy endpoint."
#
# and the two acceptance-criteria lines it serves:
#
#   "External DNS resolves the same canonical gateway hostname to the HAProxy
#    host or load balancer from the remote agent host."
#   "HAProxy accepts the canonical gateway hostname in Host headers."
#
# That is the whole bar, and this file is scoped to it plus the routes an agent
# actually needs once resolution works (FR-36), the config-hygiene claim that
# nothing agent-facing needs a Docker-only hostname (FR-37), and FR-29's
# exposure tiers -- which belong here rather than anywhere else, because an
# internal-only route is gated on the caller NOT being inside the deployment,
# and on the deployment host both of its predicates are trivially true. A
# colocated test cannot tell "enforced" from "not enforced"; section 6 can.
#
# WHY THIS IS NOT A SECTION IN gateway-brev.sh
# --------------------------------------------
# gateway-brev.sh runs ON the deployment host. It reads the gateway's own
# container environment with `docker inspect`, builds its route inventory from
# `docker compose config --services`, and reaches the public origin with
# `curl --resolve` against a listener on localhost. Every one of those is a
# capability a genuinely remote agent host does not have, and each one is a way
# to accidentally prove nothing: a check that consults the deployment's Docker
# socket has already left the remote host's position.
#
# So this file is deliberately built from the opposite end. It may use:
#   * HTTP to one origin, and
#   * the `vss` CLI, which is what the repository tells an operator to use.
# It may NOT use `docker`, `docker compose`, `kubectl`, or any path into the
# deployment's host. Section 0 asserts that separation rather than assuming it,
# because an off-host proof that silently ran on-host is the failure mode with
# no external symptom.
#
# Route inventory comes from the gateway itself, over HTTP, via the
# `x-vss-gateway-unavailable` marker: a 503 carrying that header is the gateway
# saying "no server is up for this backend, it is not part of this deployment",
# which is exactly the distinction a remote caller needs and cannot get from
# `compose ps`. A 503 WITHOUT the marker is a real backend failure and is
# counted as FAIL. Collapsing those two into one bucket is the single most
# misleading thing this harness could do, so it does not.
#
# PARAMETERS
#   VSS_GATEWAY_ORIGIN   required. The origin the remote agent is configured
#                        with, e.g. http://vss-gateway.example.com:7777.
#   RESOLVE_TARGET       optional ip:port. Supplies the name->address mapping
#                        when this host has no DNS record or /etc/hosts entry
#                        for the canonical name, which SRD 9.3 explicitly
#                        permits ("/etc/hosts on the ... remote agent host").
#                        When set, section 1 records that resolution was
#                        operator-supplied instead of claiming a DNS zone.
#   SECOND_ORIGIN        optional. A second accepted origin for the two-origin
#                        split, e.g. the deployment's http://<host-ip>:7777.
#   DEPLOY_HOST          optional. Address of the deployment host, used by
#                        section 0 to prove this is a different machine.
#   VSS_CLI_PROJECT      optional path to services/agent for the `vss` CLI.
#                        Section 5 skips without it rather than hand-rolling
#                        REST calls the repository forbids.
#   HARNESS_COMMIT       optional commit recorded in the transcript header, for
#                        runs from a mount where `git rev-parse` cannot work.
#   AGENT_HOST_NOTE      optional free text recorded in the transcript header.
#                        Worth setting when the harness runs in a container, so
#                        the transcript names the physical machine that
#                        container was on -- `hostname` inside a container is a
#                        container id and says nothing about the separation
#                        being claimed.
#   TRANSCRIPT           optional path for the recorded transcript.
#
# EXIT CODES
#   0  every applicable assertion passed
#   1  at least one assertion failed
#   2  the harness could not establish its own preconditions and refuses to
#      report a verdict (missing origin, or it is running on the deployment
#      host, which would make an "off-host" pass meaningless)

set -uo pipefail

# --------------------------------------------------------------- parameters ---

ORIGIN="${VSS_GATEWAY_ORIGIN:-}"
RESOLVE_TARGET="${RESOLVE_TARGET:-}"
SECOND_ORIGIN="${SECOND_ORIGIN:-}"
DEPLOY_HOST="${DEPLOY_HOST:-}"
VSS_CLI_PROJECT="${VSS_CLI_PROJECT:-}"
TRANSCRIPT="${TRANSCRIPT:-}"
CURL_MAX_TIME="${CURL_MAX_TIME:-20}"

if [[ -z "${ORIGIN}" ]]; then
  echo "FATAL: VSS_GATEWAY_ORIGIN is unset. This harness proves that a remote"
  echo "       agent reaches the deployment through one configured origin; it"
  echo "       will not invent one. See deploy/docker/remote-agent.env.example."
  exit 2
fi

# Split the origin into the parts the Host header and --resolve need. The port
# is part of the gateway's identity, not an afterthought: HAProxy's known_host
# ACL pairs each accepted host with one specific port.
ORIGIN_SCHEME="${ORIGIN%%://*}"
_rest="${ORIGIN#*://}"
ORIGIN_AUTH="${_rest%%/*}"
CANON_HOST="${ORIGIN_AUTH%%:*}"
case "${ORIGIN_AUTH}" in
  *:*) CANON_PORT="${ORIGIN_AUTH##*:}" ;;
  *)   CANON_PORT=""; [[ "${ORIGIN_SCHEME}" == https ]] && CANON_PORT=443 || CANON_PORT=80 ;;
esac

PASS=0; FAIL=0; SKIP=0
declare -a FAILURES=() SKIPS=()

ok()   { echo "PASS  $1"; PASS=$((PASS+1)); }
bad()  { echo "FAIL  $1"; FAIL=$((FAIL+1)); FAILURES+=("$1"); }
skip() { echo "SKIP  $1"; echo "      reason: $2"; SKIP=$((SKIP+1)); SKIPS+=("$1 -- $2"); }
section() { echo; echo "=== $1 ==="; }
note()  { echo "      $1"; }

if [[ -n "${TRANSCRIPT}" ]]; then
  mkdir -p "$(dirname "${TRANSCRIPT}")" 2>/dev/null
  exec > >(tee "${TRANSCRIPT}") 2>&1
fi

# ----------------------------------------------------------------- helpers ---

# curl through the configured origin. Adds --resolve only when the operator
# supplied a mapping, so a run with real DNS exercises real DNS.
#
# --resolve takes host:port:address. RESOLVE_TARGET is accepted as either a
# bare address or address:port for symmetry with gateway-brev.sh, but only the
# address half is used: the port that matters is the origin's, because that is
# the port HAProxy's known_host ACL pairs the hostname with.
CURL_BASE=(curl -sS --max-time "${CURL_MAX_TIME}")
if [[ -n "${RESOLVE_TARGET}" ]]; then
  CURL_BASE+=(--resolve "${CANON_HOST}:${CANON_PORT}:${RESOLVE_TARGET%%:*}")
fi

# Fetch <path>; sets R_CODE, R_HEAD, R_BODY, R_EXIT. Never uses -f: a 404 or a
# 503 is data here, not an error to be swallowed.
R_CODE=""; R_HEAD=""; R_BODY=""; R_EXIT=0
fetch() {
  local path="$1"; shift
  local origin="${1:-${ORIGIN}}"; shift || true
  local tmph tmpb
  tmph="$(mktemp)"; tmpb="$(mktemp)"
  R_CODE="$("${CURL_BASE[@]}" "$@" -o "${tmpb}" -D "${tmph}" \
            -w '%{http_code}' "${origin}${path}" 2>/dev/null)"
  R_EXIT=$?
  R_HEAD="$(tr -d '\r' < "${tmph}")"
  R_BODY="$(head -c 4000 "${tmpb}")"
  rm -f "${tmph}" "${tmpb}"
  [[ -n "${R_CODE}" ]] || R_CODE="000"
}

hdr() { grep -i "^$1:" <<<"${R_HEAD}" | head -1 | sed 's/^[^:]*: *//'; }

# Report a fetch compactly: the things a reader needs to re-verify, which is
# the exit code and what came back, not the word "passed".
evidence() {
  note "curl exit=${R_EXIT} http=${R_CODE}${1:+ ($1)}"
  local body; body="$(tr '\n' ' ' <<<"${R_BODY}" | cut -c1-200)"
  [[ -n "${body// }" ]] && note "body[0:200]: ${body}"
}

# Classify a route response the way only a remote caller can: through the
# gateway's own absent-backend marker.
#   present  -- a backend answered
#   absent   -- 503 + x-vss-gateway-unavailable: the service is not deployed
#   broken   -- 503 with no marker: something is deployed and failing
#   denied   -- 404 + x-vss-gateway-deny: this origin is not allowlisted
classify() {
  local unavail deny
  unavail="$(hdr x-vss-gateway-unavailable)"
  deny="$(hdr x-vss-gateway-deny)"
  if   [[ -n "${deny}"    ]]; then echo "denied:${deny}"
  elif [[ -n "${unavail}" ]]; then echo "absent:${unavail}"
  elif [[ "${R_CODE}" == 503 ]]; then echo "broken:no-marker"
  elif [[ "${R_CODE}" == 000 ]]; then echo "unreachable:curl-${R_EXIT}"
  else echo "present:${R_CODE}"
  fi
}

echo "================================================================"
echo " FR-35 off-host gateway proof"
echo "================================================================"
echo "started            $(date -u +%FT%TZ)"
echo "agent host         $(hostname)"
echo "agent host addrs   $(hostname -I 2>/dev/null || echo unknown)"
[[ -n "${AGENT_HOST_NOTE:-}" ]] && echo "agent host note    ${AGENT_HOST_NOTE}"
echo "gateway origin     ${ORIGIN}"
echo "canonical host     ${CANON_HOST}   port ${CANON_PORT}"
echo "resolve override   ${RESOLVE_TARGET:-<none: using system resolution>}"
echo "second origin      ${SECOND_ORIGIN:-<none>}"
echo "deployment host    ${DEPLOY_HOST:-<not declared>}"
# HARNESS_COMMIT is worth setting when this runs from a mount rather than a
# checkout -- in a container, and in a git worktree especially, `.git` is a file
# pointing somewhere the mount does not reach, so rev-parse finds nothing and
# the transcript would record no provenance at all.
echo "harness            $(basename "${BASH_SOURCE[0]}") @ ${HARNESS_COMMIT:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && git rev-parse --short HEAD 2>/dev/null || echo 'unknown (set HARNESS_COMMIT)')}"

# =============================================================== section 0 ===
# The separation itself. Everything below is only meaningful if this host is
# not the deployment host and shares no Docker network with it, so this is
# asserted first and hard: a failure here exits 2 rather than counting a FAIL,
# because a verdict from the wrong machine is worse than no verdict.
section "0. This is a different machine, with no Docker network in common"

LOCAL_ADDRS="$(hostname -I 2>/dev/null | tr ' ' '\n' | grep -v '^$' | sort -u)"

if [[ -n "${DEPLOY_HOST}" ]]; then
  if grep -qxF "${DEPLOY_HOST}" <<<"${LOCAL_ADDRS}"; then
    echo "FATAL: the declared deployment host ${DEPLOY_HOST} is one of this"
    echo "       machine's own addresses. This is not an off-host run, and"
    echo "       FR-35 is specifically about a different machine."
    exit 2
  fi
  ok "0a deployment host ${DEPLOY_HOST} is not an address of this machine"
  note "this machine: $(tr '\n' ' ' <<<"${LOCAL_ADDRS}")"
else
  skip "0a deployment host is not one of this machine's addresses" \
       "DEPLOY_HOST not set, so the harness cannot compare addresses"
fi

# A remote agent has no Docker embedded DNS. If a raw Compose service name
# resolves here, this process is on the deployment's bridge and the whole run
# is invalid. This is the check that catches "off-host" runs that were actually
# a second container on the same host -- the exact thing FR-35 forbids.
BRIDGE_ONLY_NAMES=(vss-haproxy-ingress vss-va-mcp vst-ingress elasticsearch alert-bridge)
leaked=""
for n in "${BRIDGE_ONLY_NAMES[@]}"; do
  if getent hosts "${n}" >/dev/null 2>&1; then leaked+="${n} "; fi
done
if [[ -n "${leaked}" ]]; then
  echo "FATAL: Compose service name(s) resolve from here: ${leaked}"
  echo "       That means Docker embedded DNS is in play and this is not an"
  echo "       off-host position. Re-run from a host outside the deployment."
  exit 2
fi
ok "0b no Compose service name resolves here (no Docker embedded DNS)"
note "checked: ${BRIDGE_ONLY_NAMES[*]}"

# ...and the corollary that makes the gateway indirection load-bearing rather
# than decorative: the raw service endpoints an agent used to call directly are
# unreachable from here. If these succeeded, the agent would not need the
# gateway and FR-35 would be untestable.
unreachable_raw=0; reachable_raw=""
for spec in vss-va-mcp:9000 vst-ingress:30888 elasticsearch:9200; do
  h="${spec%%:*}"; p="${spec##*:}"
  if timeout 4 bash -c "</dev/tcp/${h}/${p}" 2>/dev/null; then
    reachable_raw+="${spec} "
  else
    unreachable_raw=$((unreachable_raw+1))
  fi
done
if (( unreachable_raw == 3 )); then
  ok "0c raw Docker service endpoints are unreachable from here (gateway is load-bearing)"
else
  bad "0c a raw Docker service endpoint answered from here: ${reachable_raw}"
fi

# =============================================================== section 1 ===
section "1. FR-35: the canonical gateway hostname resolves here to the external HAProxy endpoint"

# 1a. Resolution. Three legitimate shapes, reported differently because they
# are different claims, in descending strength:
#   * a DNS zone record -- FR-35's acceptance line verbatim;
#   * an /etc/hosts entry -- SRD 9.3's permitted alternative, and evidence only
#     about this host;
#   * an operator-supplied --resolve mapping -- the same claim as /etc/hosts
#     without editing a shared file, and it fakes resolution for curl alone.
# None of them is dressed up as either of the others.
if [[ "${CANON_HOST}" =~ ^[0-9.]+$ ]]; then
  skip "1a canonical gateway hostname resolves from this host" \
       "the configured origin is an IP literal (${CANON_HOST}); there is no name to resolve. \
FR-35 is about a hostname -- re-run with a DNS name to exercise it."
  RESOLVED_ADDR="${CANON_HOST}"
else
  sys_addr="$(getent hosts "${CANON_HOST}" 2>/dev/null | awk '{print $1}' | head -1)"
  if [[ -n "${sys_addr}" ]]; then
    # WHICH source answered is a different claim from "it resolved", and the
    # two are not interchangeable: FR-35's acceptance line says "external DNS",
    # while SRD 9.3 also permits "/etc/hosts on the remote agent host". A
    # hosts-file entry satisfies the weaker reading only, so the transcript has
    # to name the source rather than reporting both as one pass. `getent hosts`
    # consults nsswitch and will not say which of them answered, so ask.
    hosts_entry="$(grep -vE '^[[:space:]]*#' /etc/hosts 2>/dev/null \
                   | grep -wE "${CANON_HOST}" | head -1)"
    dns_addr=""
    if command -v dig >/dev/null 2>&1; then
      dns_addr="$(dig +short +time=3 +tries=1 A "${CANON_HOST}" 2>/dev/null \
                  | grep -E '^[0-9.]+$' | head -1)"
    elif command -v nslookup >/dev/null 2>&1; then
      dns_addr="$(nslookup -type=A "${CANON_HOST}" 2>/dev/null \
                  | awk '/^Address: /{print $2}' | head -1)"
    fi
    RESOLVED_ADDR="${sys_addr}"

    if [[ -n "${dns_addr}" ]]; then
      ok "1a canonical hostname '${CANON_HOST}' resolves from this host through DNS"
      note "getent hosts ${CANON_HOST} -> ${sys_addr}; DNS A record -> ${dns_addr}"
      note "this is FR-35's acceptance line met in its strongest form: a zone record,"
      note "resolvable by anything on this host, not just by this harness"
      [[ "${dns_addr}" == "${sys_addr}" ]] || \
        note "NOTE: the DNS answer and the system answer differ -- something local is \
overriding the zone, and ${sys_addr} is what a client here will actually use"
    elif [[ -n "${hosts_entry}" ]]; then
      ok "1a canonical hostname '${CANON_HOST}' resolves from this host via its /etc/hosts entry"
      note "getent hosts ${CANON_HOST} -> ${sys_addr}"
      note "source: /etc/hosts, not a DNS zone. No A record was returned for this name."
      note "SRD 9.3 permits '/etc/hosts on the ... remote agent host' as the DNS source,"
      note "so this satisfies FR-35 in that reading. It is WEAKER than the acceptance"
      note "line's 'external DNS resolves ...': nothing here shows the name is"
      note "resolvable anywhere but on this host. Do not report it as external DNS."
    else
      ok "1a canonical hostname '${CANON_HOST}' resolves from this host by system resolution"
      note "getent hosts ${CANON_HOST} -> ${sys_addr}"
      note "source undetermined: no /etc/hosts entry matched and no DNS client is"
      note "installed here to confirm a zone record. Install dig, or record the source"
      note "out of band, before describing this run as external DNS."
    fi

    # A name under a reserved TLD cannot exist in public DNS at all (RFC 6761,
    # RFC 2606), so it is worth saying plainly rather than letting a reader
    # infer a zone that could never be delegated.
    case "${CANON_HOST}" in
      *.test|*.invalid|*.localhost|*.example|*.local)
        note "'${CANON_HOST}' is under a reserved TLD, which can never be delegated in"
        note "public DNS. Whatever answered here is local to this host or to a private"
        note "resolver -- fine for a lab run, but it is not evidence of external DNS." ;;
    esac
  elif [[ -n "${RESOLVE_TARGET}" ]]; then
    ok "1a canonical hostname '${CANON_HOST}' is resolvable here via an operator-supplied mapping"
    note "no DNS/hosts record; mapping supplied out of band -> ${RESOLVE_TARGET%%:*}"
    note "SRD 9.3 permits '/etc/hosts on the ... remote agent host' as the DNS source."
    note "This is weaker evidence than a zone record and is labelled so deliberately."
    RESOLVED_ADDR="${RESOLVE_TARGET%%:*}"
  else
    bad "1a canonical hostname '${CANON_HOST}' does not resolve from this host"
    note "getent hosts returned nothing and no RESOLVE_TARGET was supplied"
    RESOLVED_ADDR=""
  fi
fi

# 1b. The address it resolves to is an external HAProxy endpoint -- reachable
# over TCP from here, and not a loopback or link-local address, which would
# mean the name had been pointed back at this machine.
if [[ -n "${RESOLVED_ADDR}" ]]; then
  case "${RESOLVED_ADDR}" in
    127.*|::1|169.254.*)
      bad "1b resolved address ${RESOLVED_ADDR} is loopback/link-local, not an external endpoint" ;;
    *)
      if timeout 8 bash -c "</dev/tcp/${RESOLVED_ADDR}/${CANON_PORT}" 2>/dev/null; then
        ok "1b the resolved endpoint ${RESOLVED_ADDR}:${CANON_PORT} accepts TCP from this host"
      else
        bad "1b the resolved endpoint ${RESOLVED_ADDR}:${CANON_PORT} refused TCP from this host"
      fi ;;
  esac
else
  skip "1b resolved endpoint accepts TCP" "1a produced no address"
fi

# 1c. HAProxy accepts the canonical hostname in the Host header. This is the
# acceptance-criteria line, and it is a real risk rather than a formality: the
# known_host ACL is an allowlist, and a name it was never told about returns
# 404 on every path however correct the route is.
fetch "/" "${ORIGIN}"
cls="$(classify)"
case "${cls}" in
  denied:*)
    bad "1c HAProxy accepts '${ORIGIN_AUTH}' in the Host header"
    evidence "${cls}"
    note "the gateway answered x-vss-gateway-deny: ${cls#denied:}"
    note "fix: add this hostname to VSS_PUBLIC_HOST and recreate vss-haproxy-ingress" ;;
  unreachable:*)
    bad "1c HAProxy accepts '${ORIGIN_AUTH}' in the Host header"
    evidence "${cls}" ;;
  *)
    ok "1c HAProxy accepts '${ORIGIN_AUTH}' in the Host header (no unknown-host deny)"
    evidence "${cls}" ;;
esac

# 1d. The negative control. Without this, 1c proves only that something
# answers; it does not prove the allowlist is an allowlist. A name the
# deployment was never told about must be refused, and refused in the
# identifiable way the README documents.
UNKNOWN_HOST="fr35-not-a-declared-origin.invalid"
if [[ -n "${RESOLVED_ADDR}" ]]; then
  u_head="$(curl -sS --max-time "${CURL_MAX_TIME}" -D- -o /dev/null \
            --resolve "${UNKNOWN_HOST}:${CANON_PORT}:${RESOLVED_ADDR}" \
            "${ORIGIN_SCHEME}://${UNKNOWN_HOST}:${CANON_PORT}/va-mcp/health" 2>/dev/null | tr -d '\r')"
  u_code="$(head -1 <<<"${u_head}" | awk '{print $2}')"
  u_deny="$(grep -i '^x-vss-gateway-deny:' <<<"${u_head}" | sed 's/^[^:]*: *//')"
  if [[ "${u_code}" == 404 && -n "${u_deny}" ]]; then
    ok "1d an undeclared origin is refused 404 with x-vss-gateway-deny: ${u_deny}"
    note "so 1c is the allowlist accepting a declared name, not the gateway answering anything"
  else
    bad "1d an undeclared origin should be refused 404 + x-vss-gateway-deny"
    note "got http=${u_code:-none} deny-header='${u_deny:-none}' for Host: ${UNKNOWN_HOST}"
  fi
else
  skip "1d undeclared origin is refused" "no resolved address to aim the control at"
fi

# =============================================================== section 2 ===
# FR-36, from the remote side, with the marker doing the inventory. Paths are
# the September SRD's route table plus the aliases this branch serves; each
# entry is "<label> <path> <what a present backend must look like>".
section "2. FR-36: required gateway routes from the remote agent host"

ROUTES=(
  "va-mcp            /va-mcp/health"
  "vios (vst)        /vst/api/v1/sensor/streams"
  "vios alias        /vios/api/v1/sensor/streams"
  "elasticsearch     /elasticsearch/"
  "alert-bridge      /alert-bridge/health"
  "alerts alias      /alerts/health"
  "rtvi-vlm          /rtvi-vlm/v1/models"
  "phoenix           /phoenix/"
  "lvs               /lvs/v1/ready"
  "video-summ alias  /video-summarization/v1/ready"
  "llm               /llm/v1/models"
)

declare -a ABSENT_ROUTES=() PRESENT_ROUTES=()
for entry in "${ROUTES[@]}"; do
  label="$(awk '{$NF=""; sub(/[ \t]+$/,""); print}' <<<"${entry}")"
  path="${entry##* }"
  fetch "${path}" "${ORIGIN}"
  cls="$(classify)"
  case "${cls}" in
    present:2*)
      ok "2 ${label} ${path} -> backend answered"
      evidence "${cls}"; PRESENT_ROUTES+=("${label}") ;;
    present:3*)
      ok "2 ${label} ${path} -> backend answered with a redirect"
      evidence "${cls}"; PRESENT_ROUTES+=("${label}") ;;
    absent:*)
      # Not a pass and not a failure: the gateway is telling a remote caller,
      # in the only way it can, that this service is not in this deployment.
      skip "2 ${label} ${path}" \
           "gateway reports the backend absent via x-vss-gateway-unavailable: ${cls#absent:}"
      evidence "${cls}"; ABSENT_ROUTES+=("${label}") ;;
    broken:*)
      bad "2 ${label} ${path} -> 503 with NO x-vss-gateway-unavailable marker"
      evidence "${cls}"
      note "an unmarked 503 is a deployed-but-failing backend, not an absent one" ;;
    denied:*)
      bad "2 ${label} ${path} -> origin refused (${cls#denied:})"
      evidence "${cls}" ;;
    *)
      # A 4xx/5xx from a present backend is a route, rewrite or backend-health
      # question, not an absence. Reported as a failure with the code so it can
      # be judged -- and attributed, because "the gateway is broken" and "the
      # thing behind the gateway is broken" have different owners. Replaying
      # the same path on a second accepted origin separates them: a status that
      # reproduces there is not specific to how this origin is handled.
      bad "2 ${label} ${path} -> unexpected ${cls}"
      evidence "${cls}"
      if [[ -n "${SECOND_ORIGIN}" ]]; then
        first_code="${R_CODE}"
        fetch "${path}" "${SECOND_ORIGIN}"
        if [[ "${R_CODE}" == "${first_code}" ]]; then
          note "attribution: the same ${first_code} comes back on ${SECOND_ORIGIN}, so this is"
          note "the backend's own response relayed faithfully, not this origin's handling"
        else
          note "attribution: ${SECOND_ORIGIN} answered ${R_CODE} for the same path, so the"
          note "status IS specific to the origin used -- that points at the gateway"
        fi
      fi ;;
  esac
done

# The marker's whole purpose, asserted rather than assumed: at least one absent
# backend must have been distinguishable from a real 503. On a deployment where
# every optional service happens to be present this legitimately cannot run.
if (( ${#ABSENT_ROUTES[@]} > 0 )); then
  ok "2z the absent-backend marker distinguished ${#ABSENT_ROUTES[@]} absent service(s) from a real 503"
  note "absent: ${ABSENT_ROUTES[*]}"
  note "each answered 503 + x-vss-gateway-unavailable, which is how a remote"
  note "caller tells 'not deployed' from 'deployed and broken' without compose access"
else
  skip "2z absent-backend marker distinguishes absent from broken" \
       "every probed route had a live backend, so no absent case arose in this deployment"
fi

# =============================================================== section 3 ===
# The MCP path specifically. /va-mcp/health is a health check; it does not show
# that the MCP protocol survives the proxy hop. Streamable-HTTP MCP needs POST,
# a session, an SSE-capable Accept, and a JSON-RPC body -- none of which a
# health probe exercises, and all of which are what actually breaks behind a
# path-rewriting proxy.
section "3. The MCP path carries real MCP protocol traffic, not just /health"

MCP_BASE="/va-mcp"
fetch "${MCP_BASE}/health" "${ORIGIN}"
if [[ "$(classify)" == present:2* ]]; then
  MCP_UP=1
else
  MCP_UP=0
fi

if (( MCP_UP )); then
  init_body='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"fr35-offhost-harness","version":"1"}}}'
  tmph="$(mktemp)"; tmpb="$(mktemp)"
  code="$("${CURL_BASE[@]}" -o "${tmpb}" -D "${tmph}" -w '%{http_code}' \
        -X POST "${ORIGIN}${MCP_BASE}/mcp" \
        -H 'Content-Type: application/json' \
        -H 'Accept: application/json, text/event-stream' \
        --data "${init_body}" 2>/dev/null)"; cexit=$?
  head_txt="$(tr -d '\r' < "${tmph}")"; body_txt="$(head -c 2000 "${tmpb}")"
  sid="$(grep -i '^mcp-session-id:' <<<"${head_txt}" | head -1 | sed 's/^[^:]*: *//')"
  rm -f "${tmph}" "${tmpb}"

  if [[ "${code}" == 2* ]] && grep -q '"protocolVersion"' <<<"${body_txt}"; then
    ok "3a MCP initialize succeeded through the gateway"
    note "curl exit=${cexit} http=${code} session=${sid:-<none>}"
    note "server: $(grep -o '"serverInfo":{[^}]*}' <<<"${body_txt}" | cut -c1-160)"

    # tools/list is the assertion that matters: it proves the proxy did not
    # merely forward one request but kept a usable MCP session across the hop.
    lh="$(mktemp)"; lb="$(mktemp)"
    lcode="$("${CURL_BASE[@]}" -o "${lb}" -D "${lh}" -w '%{http_code}' \
          -X POST "${ORIGIN}${MCP_BASE}/mcp" \
          -H 'Content-Type: application/json' \
          -H 'Accept: application/json, text/event-stream' \
          ${sid:+-H "mcp-session-id: ${sid}"} \
          --data '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' 2>/dev/null)"
    lbody="$(head -c 4000 "${lb}")"; rm -f "${lh}" "${lb}"
    ntools="$(grep -o '"name":"[a-zA-Z0-9_.-]*"' <<<"${lbody}" | wc -l | tr -d ' ')"
    if [[ "${lcode}" == 2* ]] && (( ntools > 0 )); then
      ok "3b MCP tools/list returned ${ntools} tool name(s) over the same session"
      TOOL_NAMES="$(grep -o '"name":"[a-zA-Z0-9_.-]*"' <<<"${lbody}" | sed 's/.*:"//;s/"//' | tr '\n' ' ')"
      note "http=${lcode}; tools: $(cut -c1-240 <<<"${TOOL_NAMES}")"

      # 3c is the assertion the brief actually asks for: a real tool answer,
      # not a health check. Pick a listed tool that needs no arguments and
      # invoke it, then require a result payload rather than a bare 200 -- an
      # MCP error is returned INSIDE a 200 as {"error":...}, so status alone
      # says nothing about whether the tool ran.
      CALL_TOOL=""
      for cand in vst_sensor_list video_analytics__get_places video_analytics__get_incidents; do
        grep -qw "${cand}" <<<"${TOOL_NAMES}" && { CALL_TOOL="${cand}"; break; }
      done
      if [[ -z "${CALL_TOOL}" ]]; then
        skip "3c a real MCP tool answer through the gateway" \
             "none of the known zero-argument tools are exposed here; tools: $(cut -c1-160 <<<"${TOOL_NAMES}")"
      else
        ch="$(mktemp)"; cb="$(mktemp)"
        ccode="$("${CURL_BASE[@]}" -o "${cb}" -D "${ch}" -w '%{http_code}' \
              -X POST "${ORIGIN}${MCP_BASE}/mcp" \
              -H 'Content-Type: application/json' \
              -H 'Accept: application/json, text/event-stream' \
              ${sid:+-H "mcp-session-id: ${sid}"} \
              --data "{\"jsonrpc\":\"2.0\",\"id\":3,\"method\":\"tools/call\",\"params\":{\"name\":\"${CALL_TOOL}\",\"arguments\":{}}}" 2>/dev/null)"
        cbody="$(head -c 4000 "${cb}")"; rm -f "${ch}" "${cb}"
        if [[ "${ccode}" == 2* ]] && grep -q '"result"' <<<"${cbody}" && ! grep -q '"isError":true' <<<"${cbody}"; then
          ok "3c MCP tool '${CALL_TOOL}' executed through the gateway and returned a result"
          note "http=${ccode}; result[0:400]: $(tr '\n' ' ' <<<"${cbody}" | grep -o '"result".*' | cut -c1-400)"
          note "this is a tool answer produced off-host, not a health probe"
        else
          bad "3c MCP tool '${CALL_TOOL}' through the gateway"
          note "http=${ccode}; body[0:400]: $(tr '\n' ' ' <<<"${cbody}" | cut -c1-400)"
          note "an MCP error arrives inside a 200, so the payload is what decides this"
        fi
      fi
    else
      bad "3b MCP tools/list over the gateway"
      note "http=${lcode} tools=${ntools}; body[0:240]: $(tr '\n' ' ' <<<"${lbody}" | cut -c1-240)"
      skip "3c a real MCP tool answer through the gateway" "tools/list did not return a usable tool set"
    fi
  else
    bad "3a MCP initialize through the gateway"
    note "curl exit=${cexit} http=${code}; body[0:240]: $(tr '\n' ' ' <<<"${body_txt}" | cut -c1-240)"
  fi
else
  skip "3a MCP protocol traffic through the gateway" \
       "the /va-mcp mount is not answering here; section 2 records why"
  skip "3b MCP tools/list through the gateway" "see 3a"
  skip "3c a real MCP tool answer through the gateway" "see 3a"
fi

# =============================================================== section 4 ===
# The two-origin split. The agent's origin and a browser's public origin are
# not required to be the same string, and the failure this catches is a service
# minting an absolute URL from the hop it can see rather than from the origin
# the client actually used -- which a same-origin test cannot see at all.
section "4. Two-origin split: agent origin and public origin are both honoured"

if [[ -n "${SECOND_ORIGIN}" ]]; then
  s_scheme="${SECOND_ORIGIN%%://*}"; s_rest="${SECOND_ORIGIN#*://}"
  s_auth="${s_rest%%/*}"
  fetch "/va-mcp/health" "${SECOND_ORIGIN}"
  s_cls="$(classify)"
  if [[ "${s_cls}" == present:2* || "${s_cls}" == absent:* ]]; then
    ok "4a the second origin ${s_auth} is also an accepted gateway identity"
    evidence "${s_cls}"
    note "both '${ORIGIN_AUTH}' and '${s_auth}' are in the known_host allowlist,"
    note "which is what lets an off-host agent and a browser use different names"
  else
    bad "4a the second origin ${s_auth} should be an accepted gateway identity"
    evidence "${s_cls}"
  fi

  # X-Forwarded-Proto handling: an external TLS terminator presents plain HTTP
  # to the deployment and declares the real scheme in a header. If the gateway
  # ignores it, a redirect comes back on http:// and a browser on an https page
  # blocks it as mixed content.
  #
  # This needs a path that actually redirects, and which one does depends on
  # what is deployed -- so the candidates are probed rather than assumed. A
  # prefix without its trailing slash is the reliable shape: backends behind
  # the gateway normally answer it with a 301 to the slash form.
  rcode=""; loc=""; loc_path=""
  for cand in /vst /vios /kibana /phoenix /elasticsearch; do
    rh="$(curl -sS --max-time "${CURL_MAX_TIME}" -D- -o /dev/null \
          ${RESOLVE_TARGET:+--resolve "${CANON_HOST}:${CANON_PORT}:${RESOLVE_TARGET%%:*}"} \
          -H 'X-Forwarded-Proto: https' "${ORIGIN}${cand}" 2>/dev/null | tr -d '\r')"
    rcode="$(head -1 <<<"${rh}" | awk '{print $2}')"
    loc="$(grep -i '^location:' <<<"${rh}" | head -1 | sed 's/^[^:]*: *//')"
    [[ -n "${loc}" ]] && { loc_path="${cand}"; break; }
  done
  if [[ -z "${loc}" ]]; then
    skip "4b X-Forwarded-Proto is honoured in minted redirects" \
         "none of /vst /vios /kibana /phoenix /elasticsearch returned a Location header here, so there is no minted redirect to inspect"
  elif [[ "${loc}" == https://* || "${loc}" == /* ]]; then
    note "probed ${loc_path}"
    ok "4b a redirect minted under X-Forwarded-Proto: https is not downgraded to http://"
    note "http=${rcode} location: ${loc}"
    note "a relative Location is equally safe: the browser re-anchors it on the page origin"
  else
    bad "4b a redirect minted under X-Forwarded-Proto: https came back as ${loc%%:*}://"
    note "http=${rcode} location: ${loc}"
    note "on an https page a browser blocks this as mixed content"
  fi
else
  skip "4a second origin is an accepted gateway identity" "SECOND_ORIGIN not set"
  skip "4b X-Forwarded-Proto is honoured in minted redirects" "SECOND_ORIGIN not set"
fi

# =============================================================== section 5 ===
# A real answer, produced by the tool the repository tells operators to use.
# AGENTS.md is emphatic that a hand-built REST call which returns *something*
# is worse than a clean failure, because nothing downstream can tell it was
# improvised -- so this section drives `vss` or it skips.
section "5. A real tool answer through the gateway, via the vss CLI"

if [[ -z "${VSS_CLI_PROJECT}" ]]; then
  skip "5 a real tool answer via the vss CLI" \
       "VSS_CLI_PROJECT not set. The harness will not substitute hand-built REST calls for the CLI."
elif [[ ! -d "${VSS_CLI_PROJECT}" ]]; then
  skip "5 a real tool answer via the vss CLI" \
       "VSS_CLI_PROJECT=${VSS_CLI_PROJECT} is not a directory"
elif ! command -v uv >/dev/null 2>&1; then
  skip "5 a real tool answer via the vss CLI" "uv is not installed on this host"
else
  # A dedicated config home, for one reason that is worth stating plainly:
  # `vss` records its origin in ~/.vss/config.json, so a CLI that has ever been
  # pointed at another deployment will happily answer from THAT one if
  # `configure` fails here. During development of this harness it did exactly
  # that -- `vios list` returned a sensor belonging to an unrelated box and the
  # check counted it as a pass. An isolated config home makes that impossible
  # rather than unlikely, and it leaves the operator's own config untouched.
  CLI_HOME="$(mktemp -d)"
  export VSS_CONFIG_HOME="${CLI_HOME}"
  note "VSS_CONFIG_HOME=${CLI_HOME} (empty: no previously configured origin can answer)"

  vss() { uv run --project "${VSS_CLI_PROJECT}" --no-dev --extra cli vss "$@"; }

  echo "--- vss configure --base-url ${ORIGIN} ---"
  cfg_out="$(vss configure --base-url "${ORIGIN}" 2>&1)"; cfg_rc=$?
  echo "${cfg_out}" | tail -20
  note "exit=${cfg_rc}"

  if (( cfg_rc != 0 )); then
    bad "5a vss configure against ${ORIGIN}"
    # The commonest cause, and the one FR-35 is about: the CLI performs real
    # name resolution. A curl --resolve mapping does not exist as far as it is
    # concerned, so a canonical hostname with no DNS or /etc/hosts entry fails
    # here even though sections 1-4 passed.
    if grep -qi "name or service not known\|temporary failure in name resolution" <<<"${cfg_out}"; then
      note "cause: '${CANON_HOST}' does not resolve for a normal client on this host."
      note "Sections 1-4 used curl --resolve, which the CLI cannot use. This is the"
      note "difference between a probe that fakes resolution and an agent that needs"
      note "it: to make this section pass, give the remote host a real /etc/hosts"
      note "entry or DNS record for the canonical name (SRD 9.3)."
    fi
    # Refuse to run the rest. Commands issued now would answer from whatever
    # origin is on disk, which is not what this harness claims to measure.
    skip "5b a real tool answer via the vss CLI" \
         "configure did not record ${ORIGIN}; running commands now would report on some other origin"
    skip "5c a vss command produced a real answer" "see 5b"
  else
    # Belt and braces: confirm what actually got recorded before trusting an
    # answer. `configure` exiting 0 and the config naming our origin are two
    # different claims.
    show_out="$(vss configure show 2>&1)"; show_rc=$?
    echo "--- vss configure show ---"; echo "${show_out}" | head -20
    if grep -qF "${ORIGIN_AUTH}" <<<"${show_out}"; then
      ok "5a vss configure discovered the deployment through ${ORIGIN} and recorded it"
    else
      bad "5a vss configure recorded an origin other than ${ORIGIN}"
      note "configure show did not mention ${ORIGIN_AUTH}; refusing to attribute answers to this gateway"
    fi

    echo "--- vss configure check ---"
    chk_out="$(vss configure check 2>&1)"; chk_rc=$?
    echo "${chk_out}" | tail -40
    note "exit=${chk_rc}"

    # Exit codes carry the meaning here, per AGENTS.md: 0 is an answer (an
    # empty result included), 4 means a service this group needs is not in the
    # deployment, 3 is a backend problem. Branch on the code, never on parsing
    # stdout for the word "error".
    ran_any=0
    # Read-only listings, one per command group the alerts profile can serve.
    # The groups whose backends are absent exit 4, which is the documented
    # "this deployment does not have what this group needs" and a SKIP -- so
    # this loop exercises both halves of the exit-code contract.
    for cmd in "vios list" "search list" "summarize list"; do
      echo "--- vss ${cmd} ---"
      out="$(vss ${cmd} 2>&1)"; rc=$?
      echo "${out}" | head -30
      note "exit=${rc}"
      case ${rc} in
        0) ok "5b vss ${cmd} returned an answer through the gateway (exit 0)"
           note "an empty result at exit 0 is an answer: the deployment has nothing matching"
           ran_any=1 ;;
        4) skip "5b vss ${cmd}" "exit 4: a service this command group needs is not in this deployment" ;;
        *) bad "5b vss ${cmd} failed with exit ${rc}"
           note "not retried and not re-attempted over raw REST, per AGENTS.md rule 4" ;;
      esac
    done
    if (( ran_any )); then
      ok "5c at least one vss command produced a real answer from off-host through the gateway"
    else
      skip "5c a vss command produced a real answer" \
           "no command group in this deployment answered at exit 0"
    fi
  fi
  rm -rf "${CLI_HOME}"
fi

# =============================================================== section 6 ===
# FR-29 route exposure tiers, which only an off-host caller can establish.
#
# An internal-only route is served only to a caller satisfying BOTH predicates
# in haproxy.cfg.template: `h_internal` (the Host is an origin that exists only
# inside the deployment) AND `gw_internal_src` (the packet came from a private
# range). On the deployment host both are trivially true, so a colocated test
# cannot distinguish "enforced" from "not enforced". Here they are not, and the
# tier becomes observable.
section "6. FR-29: internal-only routes are refused off-host, remote-agent-accessible ones are not"

INTERNAL_ONLY_PATHS=(/behavior-analytics/ /perception-sdr/)

# Is this caller in one of the ranges gw_internal_src covers? This decides what
# 6c is entitled to expect. Judged from our own addresses, which is the honest
# limit of what we can see: behind NAT the gateway observes a different source,
# and 6c says so rather than assuming.
SRC_PRIVATE=0
while read -r a; do
  case "${a}" in
    10.*|127.*|192.168.*) SRC_PRIVATE=1 ;;
    172.1[6-9].*|172.2[0-9].*|172.3[01].*) SRC_PRIVATE=1 ;;
  esac
done <<<"${LOCAL_ADDRS}"

# 6a. The FR-29 claim: on the origin the deployment publishes, an internal-only
# mount is refused, identifiably, to this off-host caller.
refused=0; unrefused=""
for p in "${INTERNAL_ONLY_PATHS[@]}"; do
  fetch "${p}" "${ORIGIN}"
  d="$(hdr x-vss-gateway-deny)"
  if [[ "${R_CODE}" == 403 && "${d}" == "internal-only" ]]; then
    refused=$((refused+1))
    note "${p} -> http=${R_CODE} x-vss-gateway-deny: ${d}"
  else
    unrefused+="${p}(http=${R_CODE},deny=${d:-none}) "
  fi
done
if (( refused == ${#INTERNAL_ONLY_PATHS[@]} )); then
  ok "6a all ${refused} internal-only route(s) are refused 403 + x-vss-gateway-deny: internal-only on the published origin"
  note "this is the FR-29 claim, and it is only testable from a caller that fails h_internal"
else
  bad "6a an internal-only route was not refused on the published origin: ${unrefused}"
  note "FR-29 classifies these as internal-only; reaching them on the deployment's"
  note "published identity is the thing the classification is supposed to prevent"
fi

# 6b. ...and the tier is selective rather than a blanket block. Without this,
# 6a is satisfied by a gateway that refuses everything.
fetch "/va-mcp/health" "${ORIGIN}"
d="$(hdr x-vss-gateway-deny)"
if [[ "${R_CODE}" == 2* && -z "${d}" ]]; then
  ok "6b a remote-agent-accessible route (/va-mcp/health) is served to the same caller on the same origin"
  evidence "$(classify)"
  note "so 6a is a tier being enforced, not the gateway refusing this caller outright"
else
  bad "6b /va-mcp/health should be served to a remote agent on the published origin"
  evidence "$(classify)"
fi

# 6c. The residual the template documents, stated there rather than left to be
# discovered: "a caller that is both on a private network with the deployment
# and knows to send an internal Host is admitted." This asserts the deployed
# behaviour matches that documented model -- including when the model's answer
# is "admitted", which is a known gap awaiting FR-32 auth, not a defect this
# harness invented.
if [[ -z "${RESOLVED_ADDR}" ]]; then
  skip "6c behaviour with an internal Host matches the documented FR-29 residual" \
       "no resolved address to aim the probe at"
else
  ih="$(curl -sS --max-time "${CURL_MAX_TIME}" -D- -o /dev/null \
        --resolve "localhost:${CANON_PORT}:${RESOLVED_ADDR}" \
        "${ORIGIN_SCHEME}://localhost:${CANON_PORT}/behavior-analytics/" 2>/dev/null | tr -d '\r')"
  icode="$(head -1 <<<"${ih}" | awk '{print $2}')"
  ideny="$(grep -i '^x-vss-gateway-deny:' <<<"${ih}" | head -1 | sed 's/^[^:]*: *//')"
  iunav="$(grep -i '^x-vss-gateway-unavailable:' <<<"${ih}" | head -1 | sed 's/^[^:]*: *//')"
  note "Host: localhost -> http=${icode:-none} deny=${ideny:-none} unavailable=${iunav:-none}"
  if [[ "${ideny}" == "internal-only" ]]; then
    ok "6c an internal Host from this host is still refused internal-only"
    note "stronger than the documented model guarantees here: the source this caller"
    note "presents to the gateway is evidently outside gw_internal_src's ranges"
  elif (( SRC_PRIVATE )); then
    # Admission is the documented outcome for this caller. The absence of the
    # 403 is the whole observation; what the backend then says (200, or 503
    # with the absent marker) is a separate question.
    ok "6c an internal Host from a private-range caller is admitted past the internal-only gate, as documented"
    note "this caller holds an address in gw_internal_src's ranges, so both FR-29"
    note "predicates are met and no 403 is expected -- and none was returned"
    note "haproxy.cfg.template records this verbatim as the RESIDUAL: narrowing it"
    note "needs FR-29's source allowlist or FR-32's route auth. It is a known gap,"
    note "not a regression, and it is reachable from off-host on a corporate 10/8."
  else
    bad "6c an internal Host was admitted from a caller outside the private ranges"
    note "gw_internal_src should have refused this; the documented model says a"
    note "caller off the private network fails it whatever Host it invents"
  fi
fi

# ================================================================= summary ===
section "Summary"

echo "agent host        $(hostname) [$(tr '\n' ' ' <<<"${LOCAL_ADDRS}")]"
echo "deployment host   ${DEPLOY_HOST:-<not declared>}"
echo "gateway origin    ${ORIGIN}"
echo "routes present    ${PRESENT_ROUTES[*]:-<none>}"
echo "routes absent     ${ABSENT_ROUTES[*]:-<none>} (503 + x-vss-gateway-unavailable)"
echo "finished          $(date -u +%FT%TZ)"
echo
echo "pass ${PASS}  fail ${FAIL}  skip ${SKIP}"

if (( ${#FAILURES[@]} )); then
  echo
  echo "Failures:"
  for f in "${FAILURES[@]}"; do echo "  - ${f}"; done
fi
if (( ${#SKIPS[@]} )); then
  echo
  echo "Skips (each is a check that could not run here, never a check that failed):"
  for s in "${SKIPS[@]}"; do echo "  - ${s}"; done
fi

echo
if (( FAIL == 0 )); then
  echo "RESULT: every applicable assertion passed from an off-host agent position."
else
  echo "RESULT: ${FAIL} assertion(s) failed. Read them against the deployment that"
  echo "        produced them -- an absent optional backend is a SKIP above, not a FAIL,"
  echo "        so a FAIL here is a gateway or backend problem rather than a profile gap."
fi

(( FAIL == 0 )) || exit 1
exit 0
