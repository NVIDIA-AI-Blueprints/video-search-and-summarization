#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Run the mechanical parts of the deployment flow in one call and block until
# the project's containers have settled.
#
# Covers Step 1c, Step 1d, Step 3, Step 3b, Step 3d, Step 5 and the
# container-state half of Step 5b. It does not tear down (Step 0), check
# credentials (Step 0a), probe artifact entitlement (Step 3c), or run the
# profile's endpoint probes, and it deploys without pausing for the Step 4
# review, so use it only when the request already authorizes an autonomous
# deploy. Placement, endpoints and models are the caller's decisions and arrive
# as --set KEY=VALUE.
#
#   deploy.sh --profile base --set LLM_MODE=remote --set LLM_BASE_URL=https://integrate.api.nvidia.com

set -uo pipefail

PROFILE=""; TIMEOUT=1800; REPO=""; SETS=()

usage() {
  cat <<'USAGE_EOF'
deploy.sh --profile <name> [--set KEY=VALUE]... [--timeout SEC] [--repo PATH]

  --profile   developer profile: base, alerts, lvs, search, warehouse
  --set       an env_overrides entry, repeatable; written to generated.env
  --timeout   seconds to wait for containers to settle (default 1800)
  --repo      repo root; auto-detected from this script's location

Exits non-zero on any failure and prints the per-container state.
USAGE_EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile|--set|--timeout|--repo)
      # Without the arity check a trailing bare flag makes `shift 2` fail, and
      # with no `set -e` the loop then spins on the same argument forever.
      [[ $# -ge 2 ]] || { echo "[deploy] $1 requires a value" >&2; usage >&2; exit 2; }
      case "$1" in
        --profile) PROFILE="$2" ;;
        --set)     SETS+=("$2") ;;
        --timeout) TIMEOUT="$2" ;;
        --repo)    REPO="$2" ;;
      esac
      shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "[deploy] unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done
[[ -n "${PROFILE}" ]] || { echo "[deploy] --profile is required" >&2; usage >&2; exit 2; }
[[ "${PROFILE}" =~ ^[a-z0-9][a-z0-9_-]*$ ]] || { echo "[deploy] invalid profile: ${PROFILE}" >&2; exit 2; }
[[ "${TIMEOUT}" =~ ^[0-9]+$ ]] || { echo "[deploy] --timeout must be an integer" >&2; exit 2; }

if [[ -z "${REPO}" ]]; then
  REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." 2>/dev/null && pwd)"
fi
[[ -d "${REPO}/deploy/docker" ]] || {
  echo "[deploy] not a VSS checkout: ${REPO} (pass --repo)" >&2; exit 2; }

PDIR="${REPO}/deploy/docker/developer-profiles/dev-profile-${PROFILE}"
ENV_SRC="${PDIR}/.env"
ENV_POST="${PDIR}/overrides.env"
ENV_GEN="${PDIR}/generated.env"
if [[ ! -f "${ENV_SRC}" || ! -f "${ENV_POST}" ]]; then
  echo "[deploy] unknown profile '${PROFILE}'. Available:" >&2
  ls -d "${REPO}"/deploy/docker/developer-profiles/dev-profile-* 2>/dev/null \
    | sed 's|.*/dev-profile-|  |' >&2
  exit 2
fi

fail() { echo "[deploy] $*" >&2; echo "[deploy] RESULT: FAILED" >&2; exit 1; }

# Two runs against one checkout overwrite the same generated.env and
# resolved.yml, and each tears the other's containers down.
exec 9>"${PDIR}/.deploy.lock" || fail "cannot open the deployment lock"
flock -n 9 || fail "another deploy is already running for profile '${PROFILE}'"

# Replace every declaration of a key, so a later duplicate cannot win.
set_kv() {
  local f="$1" entry="$2" k v line found=0 tmp
  [[ "${entry}" == *=* ]] || fail "--set expects KEY=VALUE, got '${entry}'"
  k="${entry%%=*}"; v="${entry#*=}"
  [[ "${k}" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || fail "invalid override key '${k}'"
  tmp="$(mktemp "${f}.XXXXXX")" || fail "cannot write next to ${f}"
  while IFS= read -r line || [[ -n "${line}" ]]; do
    if [[ "${line}" == "${k}="* ]]; then
      [[ "${found}" -eq 0 ]] && printf '%s=%s\n' "${k}" "${v}" >>"${tmp}"
      found=1
    else
      printf '%s\n' "${line}" >>"${tmp}"
    fi
  done <"${f}"
  [[ "${found}" -eq 0 ]] && printf '%s=%s\n' "${k}" "${v}" >>"${tmp}"
  mv "${tmp}" "${f}" || { rm -f "${tmp}"; fail "cannot replace ${f}"; }
}

# Step 1c. generated.env is the per-deploy working copy; .env and overrides.env
# stay read-only.
cp "${ENV_POST}" "${ENV_GEN}" || fail "cannot write ${ENV_GEN}"
echo "[deploy] profile=${PROFILE} generated.env initialized from overrides.env"

# Step 1d. On Brev the secure link is HTTPS on 443, so host, protocol and port
# must move together: EXTERNAL_IP alone leaves http://...:7777 links that the
# browser blocks as mixed content.
if grep -qE '^BREV_ENV_ID=' /etc/environment 2>/dev/null; then
  brev_env_id="$(awk -F= '/^BREV_ENV_ID=/ {gsub(/"/, "", $2); print $2; exit}' /etc/environment)"
  brev_host="7777-${brev_env_id}.brevlab.com"
  set_kv "${ENV_GEN}" "EXTERNAL_IP=${brev_host}"
  set_kv "${ENV_GEN}" "VSS_PUBLIC_HOST=${brev_host}"
  set_kv "${ENV_GEN}" "VSS_PUBLIC_HTTP_PROTOCOL=https"
  set_kv "${ENV_GEN}" "VSS_PUBLIC_WS_PROTOCOL=wss"
  set_kv "${ENV_GEN}" "VSS_PUBLIC_PORT=443"
  echo "[deploy] Brev detected, secure link ${brev_host}"
fi

# overrides.env ships these as /path/to placeholders. dev-profile.sh resolves
# them to the deployment directory and its data-dir; use the same values so a
# stack deployed either way reads and writes the same paths.
data_dir="${REPO}/deploy/docker/data-dir"
set_kv "${ENV_GEN}" "VSS_APPS_DIR=${REPO}/deploy/docker"
set_kv "${ENV_GEN}" "VSS_DATA_DIR=${data_dir}"

# Caller overrides land after the Brev block so an explicit --set wins.
for kv in ${SETS+"${SETS[@]}"}; do
  set_kv "${ENV_GEN}" "${kv}"
  [[ "${kv%%=*}" == "VSS_DATA_DIR" ]] && data_dir="${kv#*=}"
done
if [[ ${#SETS[@]} -gt 0 ]]; then
  echo "[deploy] applied ${#SETS[@]} override(s)"
fi
[[ "${data_dir}" == /* ]] || fail "VSS_DATA_DIR must be an absolute path"

if grep -nE '=.*"?/path/to' "${ENV_GEN}" >/dev/null; then
  echo "[deploy] unresolved placeholders in generated.env:" >&2
  grep -nE '=.*"?/path/to' "${ENV_GEN}" >&2
  fail "pass the real values with --set"
fi

cd "${REPO}/deploy/docker" || fail "cannot enter ${REPO}/deploy/docker"

# Step 3. Both --env-file arguments are mandatory and ordered: without the pair,
# COMPOSE_PROFILES can be unset and `up -d` exits 0 having started nothing.
docker compose --env-file "${ENV_SRC}" --env-file "${ENV_GEN}" config >resolved.yml \
  || fail "compose config failed"

# Step 3b. An unexpanded ${VAR} means compose never saw that value. $${VAR} is
# compose's escape for a literal ${VAR} passed through to the container, so the
# pattern must not match a brace preceded by a dollar.
unexpanded="$(grep -nE '(^|[^$])\$\{[A-Za-z_][A-Za-z0-9_]*' resolved.yml)"
case $? in
  0) echo "[deploy] unexpanded tokens in resolved.yml:" >&2
     printf '%s\n' "${unexpanded}" | head -20 >&2
     fail "resolved.yml has unexpanded \${...} tokens" ;;
  1) ;;
  *) fail "cannot read resolved.yml" ;;
esac

# Step 3d. compose config leaves depends_on entries pointing at services that
# profile filtering removed, and the schema validator rejects them even when
# they are optional, so up -d aborts before any container starts.
command -v uv >/dev/null 2>&1 \
  || fail "uv is required for normalize_resolved_yml.py: curl -LsSf https://astral.sh/uv/install.sh | sh"
uv run "${REPO}/skills/vss-deploy-profile/scripts/normalize_resolved_yml.py" resolved.yml \
  || fail "normalize_resolved_yml.py failed"

# One render answers three questions at once, so the project name, the service
# count and the bind paths cannot disagree with each other.
config_json="$(docker compose --env-file "${ENV_SRC}" --env-file "${ENV_GEN}" \
  -f resolved.yml config --format json 2>/dev/null)" \
  || fail "resolved.yml still invalid after normalize"

read -r project n_expected < <(printf '%s' "${config_json}" | python3 -c '
import json, sys
d = json.load(sys.stdin)
print(d.get("name", ""), len(d.get("services") or {}))')
[[ -n "${project}" ]] || fail "resolved.yml has no project name"
[[ "${n_expected}" -gt 0 ]] || fail "the profile selects no services; check COMPOSE_PROFILES"
echo "[deploy] project ${project} declares ${n_expected} services"

# Host paths under VSS_DATA_DIR hold runtime state and mostly do not exist on a
# fresh checkout. Containers here run as several uids (redis is 999, most others
# 1000), so a path created by the invoking user, or by Compose as root, is not
# writable by them. references/data-directory.md prescribes chmod on these
# subdirs and forbids any recursive chown. Existing paths are chmod'ed too,
# because a previous run may have left them owned by root.
n_dirs=0
while IFS= read -r d; do
  [[ -z "${d}" ]] && continue
  [[ ! -e "${d}" || -d "${d}" ]] || fail "bind source exists but is not a directory: ${d}"
  if [[ ! -d "${d}" ]]; then
    mkdir -p "${d}" || fail "cannot create ${d}"
    n_dirs=$((n_dirs+1))
  fi
  chmod -R 777 "${d}" 2>/dev/null || fail "cannot make ${d} writable by the containers"
done < <(printf '%s' "${config_json}" | python3 -c '
import json, os, sys
root = os.path.normpath(sys.argv[1])
out = set()
for svc in (json.load(sys.stdin).get("services") or {}).values():
    for v in svc.get("volumes") or []:
        if not isinstance(v, dict) or v.get("type") != "bind":
            continue
        src = os.path.normpath(v.get("source") or "")
        if src == root or src.startswith(root + os.sep):
            out.add(src)
print("\n".join(sorted(out)))' "${data_dir}")
echo "[deploy] prepared ${n_dirs} new data directories under ${data_dir}"

# Step 5. Name the project explicitly: COMPOSE_PROJECT_NAME in the environment
# outranks the name: key in resolved.yml, which would point every command below
# at a different project. No blanket --force-recreate either: it destroys warm
# NIM containers and costs another cold start each.
docker compose -p "${project}" --env-file "${ENV_SRC}" --env-file "${ENV_GEN}" \
  -f resolved.yml up -d || fail "up -d failed"

# Step 5b, gate 0. A short count almost always means a missing --env-file above.
n_started="$(docker compose -p "${project}" -f resolved.yml ps -a -q 2>/dev/null | wc -l)"
[[ "${n_started}" -ge "${n_expected}" ]] \
  || fail "started ${n_started} containers, expected ${n_expected}"

# Step 5b. up -d only creates containers. Classify every container state rather
# than waiting out the clock on one that will never recover: a non-zero exit,
# dead, paused or unhealthy is terminal. Restarts alone are not, because a
# service still pulling model weights restarts a few times and then comes up.
# A clean exit 0 is a finished one-shot job, not a failure.
RESTART_LIMIT=5
echo "[deploy] waiting for containers to settle (timeout ${TIMEOUT}s)..."
deadline=$(( $(date +%s) + TIMEOUT ))
while :; do
  unsettled=0; total=0; failed=""; reason=""
  while IFS= read -r id; do
    [[ -z "${id}" ]] && continue
    IFS='|' read -r name state health restarts code < <(docker inspect -f \
      '{{.Name}}|{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}|{{.RestartCount}}|{{.State.ExitCode}}' \
      "${id}" 2>/dev/null)
    [[ -z "${name:-}" ]] && continue
    total=$((total+1))
    if [[ "${restarts:-0}" -ge ${RESTART_LIMIT} ]]; then
      failed="${name#/}"; reason="restarted ${restarts} times without settling"; break
    fi
    case "${state}:${health}" in
      running:healthy|running:none) ;;
      exited:*)   [[ "${code}" -eq 0 ]] || { failed="${name#/}"; reason="exited with status ${code}"; break; } ;;
      running:unhealthy) failed="${name#/}"; reason="reported unhealthy"; break ;;
      dead:*|paused:*)   failed="${name#/}"; reason="is ${state}"; break ;;
      *) unsettled=$((unsettled+1)) ;;
    esac
  done < <(docker compose -p "${project}" -f resolved.yml ps -a -q 2>/dev/null)

  [[ -n "${failed}" ]] && { echo "[deploy] ${failed}: ${reason}" >&2; break; }
  if [[ "${total}" -lt "${n_expected}" ]]; then
    echo "[deploy] only ${total}/${n_expected} expected containers remain" >&2; break
  fi
  if [[ "${unsettled}" -eq 0 ]]; then
    echo "[deploy] all ${total} containers settled"
    echo "[deploy] RESULT: OK"
    exit 0
  fi
  [[ $(date +%s) -ge ${deadline} ]] && { echo "[deploy] timeout, ${unsettled}/${total} unsettled" >&2; break; }
  sleep 5
done

echo "[deploy] === STATUS ===" >&2
docker compose -p "${project}" -f resolved.yml ps -a >&2
while IFS= read -r id; do
  [[ -z "${id}" ]] && continue
  IFS='|' read -r name state health code < <(docker inspect -f \
    '{{.Name}}|{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}|{{.State.ExitCode}}' \
    "${id}" 2>/dev/null)
  case "${state}:${health}" in
    running:healthy|running:none) continue ;;
    exited:*) [[ "${code}" -eq 0 ]] && continue ;;
  esac
  echo "[deploy] --- ${name#/} (${state}/${health}) ---" >&2
  docker logs --tail 25 "${id}" 2>&1 | sed 's/^/    /' >&2
done < <(docker compose -p "${project}" -f resolved.yml ps -a -q 2>/dev/null)
echo "[deploy] === END STATUS ===" >&2
fail "the stack did not settle"
