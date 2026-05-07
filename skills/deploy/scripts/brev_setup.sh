#!/usr/bin/env bash
# Source this script to populate Brev-specific env vars and patch the
# profile `.env` files before `docker compose up`.
# See references/brev.md for details.
#
# Usage:
#   source skills/deploy/scripts/brev_setup.sh                 # verbose, auto-patch all profile .envs
#   source skills/deploy/scripts/brev_setup.sh --quiet         # no stdout
#   source skills/deploy/scripts/brev_setup.sh --env-file PATH # patch only PATH
#   source skills/deploy/scripts/brev_setup.sh --no-write      # detect/export only, no .env patching
#
# Inputs (env vars, all optional):
#   BREV_ENV_FILE       Path to environment file (default /etc/environment).
#                       Override in tests.
#   PROXY_PORT          Nginx proxy port (default 7777).
#   BREV_LINK_PREFIX    Secure-link port prefix (default ${PROXY_PORT}0 per
#                       launchable convention).
#
# Exports (only if BREV_ENV_ID is detected):
#   BREV_ENV_ID         From $BREV_ENV_FILE
#   PROXY_PORT          With default 7777
#   BREV_LINK_PREFIX    Computed secure-link prefix
#   BREV_PUBLIC_HOST    `${BREV_LINK_PREFIX}-${BREV_ENV_ID}.brevlab.com`
#
# Patched into each target `.env` (only when on Brev):
#   VSS_PUBLIC_HOST            $BREV_PUBLIC_HOST
#   VSS_PUBLIC_PORT            443
#   VSS_PUBLIC_HTTP_PROTOCOL   https
#   VSS_PUBLIC_WS_PROTOCOL     wss
#
# Why patch the .env. The haproxy ingress (`vss-haproxy-ingress`) gates
# every request on a `known_host` ACL whose entries are computed from
# `VSS_PUBLIC_HOST` / `EXTERNAL_IP` / `HOST_IP` / `localhost`. Brev's
# Cloudflare tunnel forwards `Host: <link-prefix>-<id>.brevlab.com` —
# not in the ACL — so haproxy returns 404 to the browser even though
# `curl http://localhost:7777/` from the host returns 200. We have to
# rewrite VSS_PUBLIC_* in the source `.env` because compose's
# `--env-file` takes precedence over shell exports, so simply exporting
# the vars in the parent shell does nothing.
#
# Default target selection. Without --env-file we patch every
# `developer-profiles/dev-profile-*/.env` under the repo root (deduced
# from this script's path). The patching is idempotent — re-sourcing
# the script does not double-write or shift values.
#
# Exit behavior: this script is sourced — it does not `exit`. Errors are
# reported on stderr and the function returns non-zero; missing
# /etc/environment is not an error (returns 0 silently).

_brev_quiet=""
_brev_env_target=""
_brev_no_write=""

# Argument parsing (positional --quiet kept for backward compatibility).
while [ $# -gt 0 ]; do
    case "$1" in
        --quiet)      _brev_quiet=1 ;;
        --no-write)   _brev_no_write=1 ;;
        --env-file)   shift; _brev_env_target="${1:-}" ;;
        --env-file=*) _brev_env_target="${1#--env-file=}" ;;
        *)            echo "brev_setup.sh: unknown arg: $1" >&2; return 2 2>/dev/null || exit 2 ;;
    esac
    shift
done

_brev_log() {
    [ -z "$_brev_quiet" ] && echo "$@"
    return 0
}

_brev_warn() {
    echo "brev_setup.sh: $*" >&2
}

# Resolve repo root from script location (skills/deploy/scripts/brev_setup.sh
# → repo). Falls back to $PWD if BASH_SOURCE is unavailable (e.g. POSIX sh).
_brev_script_dir=""
if [ -n "${BASH_SOURCE[0]:-}" ]; then
    _brev_script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)
fi
_brev_repo_root="${_brev_script_dir%/skills/deploy/scripts}"
[ -z "$_brev_repo_root" ] && _brev_repo_root="$PWD"

_brev_env_file="${BREV_ENV_FILE:-/etc/environment}"

if [ -r "$_brev_env_file" ]; then
    # Strip surrounding quotes and take the first match only.
    _brev_env_id=$(awk -F= '/^BREV_ENV_ID=/ {gsub(/"/, "", $2); print $2; exit}' "$_brev_env_file")
    if [ -n "$_brev_env_id" ]; then
        export BREV_ENV_ID="$_brev_env_id"
    fi
fi

# Helper: idempotent in-place sed of `KEY=...` line in an env file. If the
# key isn't present, append it. Quotes the value with single quotes so
# values that contain `:` (e.g. URLs) round-trip cleanly.
_brev_patch_env() {
    local file="$1" key="$2" value="$3"
    if [ ! -w "$file" ]; then
        _brev_warn "skip $file (not writable)"
        return 1
    fi
    if grep -qE "^[[:space:]]*${key}=" "$file"; then
        # Replace existing line (including any leading whitespace / comment-out toggles
        # are out of scope; we only match active assignments).
        sed -i "s|^[[:space:]]*${key}=.*|${key}=${value}|" "$file"
    else
        printf '\n# Added by brev_setup.sh\n%s=%s\n' "$key" "$value" >> "$file"
    fi
}

if [ -n "${BREV_ENV_ID:-}" ]; then
    export PROXY_PORT="${PROXY_PORT:-7777}"
    # Brev Launchable convention: secure-link prefix is ${PROXY_PORT}0 (e.g.
    # 7777 → 77770).  For manually-created links, override with
    # BREV_LINK_PREFIX=<prefix> before sourcing.
    export BREV_LINK_PREFIX="${BREV_LINK_PREFIX:-${PROXY_PORT}0}"
    export BREV_PUBLIC_HOST="${BREV_LINK_PREFIX}-${BREV_ENV_ID}.brevlab.com"
    _brev_base_url="https://${BREV_PUBLIC_HOST}"
    _brev_log "Brev detected:"
    _brev_log "  BREV_ENV_ID      = $BREV_ENV_ID"
    _brev_log "  PROXY_PORT       = $PROXY_PORT"
    _brev_log "  BREV_LINK_PREFIX = $BREV_LINK_PREFIX"
    _brev_log "  BREV_PUBLIC_HOST = $BREV_PUBLIC_HOST"
    _brev_log "  UI URL           = $_brev_base_url"
    unset _brev_base_url

    if [ -z "$_brev_no_write" ]; then
        # Build target list.
        _brev_targets=()
        if [ -n "$_brev_env_target" ]; then
            if [ -f "$_brev_env_target" ]; then
                _brev_targets+=("$_brev_env_target")
            else
                _brev_warn "--env-file $_brev_env_target does not exist"
            fi
        else
            # Auto-discover developer-profile env files. Iterate via shell glob
            # rather than `find`-pipe so the array stays POSIX-friendly and we
            # silently skip when the dir is absent (older repo layouts).
            for _f in "$_brev_repo_root"/deploy/docker/developer-profiles/dev-profile-*/.env; do
                [ -f "$_f" ] && _brev_targets+=("$_f")
            done
        fi

        if [ "${#_brev_targets[@]}" -eq 0 ]; then
            _brev_warn "no profile .env files matched; nothing to patch."
        else
            for _f in "${_brev_targets[@]}"; do
                _brev_patch_env "$_f" "VSS_PUBLIC_HOST"          "$BREV_PUBLIC_HOST"
                _brev_patch_env "$_f" "VSS_PUBLIC_PORT"          "443"
                _brev_patch_env "$_f" "VSS_PUBLIC_HTTP_PROTOCOL" "https"
                _brev_patch_env "$_f" "VSS_PUBLIC_WS_PROTOCOL"   "wss"
                _brev_log "  patched VSS_PUBLIC_* in $_f"
            done
        fi
        unset _brev_targets _f
    fi
else
    _brev_log "No BREV_ENV_ID in $_brev_env_file — not a Brev instance (or env file missing)."
fi

unset _brev_quiet _brev_env_target _brev_no_write _brev_log _brev_warn \
      _brev_script_dir _brev_repo_root _brev_env_file _brev_env_id _brev_patch_env
