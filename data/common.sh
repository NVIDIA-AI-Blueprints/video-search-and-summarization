#!/usr/bin/env bash
# =============================================================================
#  common.sh — shared by setup-data.sh and launch-deployment.sh.
#
#  Paths, the datasets.yml reader, and the small helpers all three need.
#  Sourced, never executed.
#
#  Division of labour:
#    setup-data.sh         stages datasets into both layouts. Touches data only.
#    launch-deployment.sh  runs one of them. `standalone` owns docker/.env;
#                          `blueprint` owns deepstream/configs and NUM_STREAMS.
#
#  Each launcher writes its own deployment's config immediately before starting
#  it, so staging a dataset never reconfigures a deployment you did not ask for.
#
#  MV3DT only — the 2d and 3d perception modes are out of scope.
#  Dataset facts live in datasets.yml. See DEPLOY.md for the full procedure.
# =============================================================================
set -euo pipefail

# Resolve paths relative to this library, so callers work from anywhere.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# VSS_REPO is where this script lives, not a setting — data/setup-data.sh means
# the repo is one level up. An exported VSS_REPO is ignored; `check` says so.
ENV_VSS_REPO="${VSS_REPO:-}"
VSS_REPO="$(cd "$HERE/.." && pwd)"
REGISTRY="$HERE/datasets.yml"

# The five paths below default to the canonical layout (DEPLOY.md §2.1) but
# honour an exported value. The default is what keeps every machine identical;
# the override exists for the cases the layout cannot express — chiefly a
# testbed clone kept outside this repo, since nesting one git repo inside
# another leaves `standalone/` permanently untracked here. Overriding is
# legitimate, but it is per-machine divergence, so `check` reports what is in
# effect rather than letting a surprising tree stay invisible.
PATH_OVERRIDES=""
for _v in APP_DATA CUSTOM_DATA STANDALONE RTCV PROFILE; do
  [ -n "${!_v:-}" ] && PATH_OVERRIDES="$PATH_OVERRIDES $_v"
done
unset _v

APP_DATA="${APP_DATA:-$HERE/vss-warehouse-app-data}"
CUSTOM_DATA="${CUSTOM_DATA:-$HERE/vss-mv3dt-custom-datasets}"
STANDALONE="${STANDALONE:-$VSS_REPO/standalone}"
RTCV="${RTCV:-$VSS_REPO/services/rtvi/rt-cv-3d/rt-cv-mv3dt}"
PROFILE="${PROFILE:-$VSS_REPO/deploy/docker/industry-profiles/warehouse-operations}"

# NGC package to pull in `fetch`.
NGC_RESOURCE="${NGC_RESOURCE:-nvidia/vss-warehouse/vss-warehouse-app-data:3.2.0}"
# netapp share holding the custom (12/28/145-cam) datasets.
NETAPP_SHARE="${NETAPP_SHARE:-//netapp-hq/handheld/ds_track}"
NETAPP_MOUNT="${NETAPP_MOUNT:-/mnt/netapp-hq/handheld/ds_track}"
red()  { printf '\033[31m%s\033[0m\n' "$*"; }
grn()  { printf '\033[32m%s\033[0m\n' "$*"; }
ylw()  { printf '\033[33m%s\033[0m\n' "$*"; }
die()  { red "ERROR: $*" >&2; exit 1; }
step() { printf '\n\033[1m── %s\033[0m\n' "$*"; }

[ -f "$REGISTRY" ] || die "registry not found: $REGISTRY"

# ─────────────────────────────────────────────────────────────────────────────
#  Registry access
# ─────────────────────────────────────────────────────────────────────────────

# emit_dataset <name-or-alias> — print shell assignments for one dataset,
# with ${APP_DATA}/${CUSTOM_DATA}/${STANDALONE} tokens already resolved.
emit_dataset() {
  APP_DATA="$APP_DATA" CUSTOM_DATA="$CUSTOM_DATA" STANDALONE="$STANDALONE" \
  python3 - "$REGISTRY" "$1" <<'PY'
import os, re, shlex, sys
try:
    import yaml
except ImportError:
    sys.exit("PyYAML not installed. Try: python3 -m pip install --user PyYAML")

reg = yaml.safe_load(open(sys.argv[1]))
want = sys.argv[2]
tokens = {k: os.environ[k] for k in ("APP_DATA", "CUSTOM_DATA", "STANDALONE")}

def expand(v):
    if not isinstance(v, str):
        return v
    return re.sub(r"\$\{(\w+)\}", lambda m: tokens.get(m.group(1), m.group(0)), v)

for d in reg.get("datasets", []):
    if want in (d.get("name"), d.get("alias")):
        break
else:
    sys.exit(f"unknown dataset: {want}")

defaults = reg.get("defaults", {})
out = {
    "DS_NAME":  d["name"],
    "DS_ALIAS": d.get("alias", d["name"]),
    "DS_CAMS":  str(d.get("cameras", 0)),
    "DS_ORIGIN": d.get("origin", ""),
    "DS_SUBSET": "1" if d.get("subset") else "0",
    "DS_VIDEOS": expand(d.get("videos", "")),
    "DS_CALIB":  expand(d.get("calibration", "")),
    "DS_MAP":    expand(d.get("map", "")),
    "DS_TRACKER": expand(d.get("tracker_config", "")),
    "DS_CLASS_SPECS": os.environ.get("CLASS_SPECS") or defaults.get("class_specs", ""),
    "DS_NEIGHBOR":    defaults.get("neighbor_criteria", ""),
}
for k, v in out.items():
    print(f"{k}={shlex.quote(str(v))}")
PY
}

# load_dataset <name-or-alias> — populate the caller's DS_* locals, or die
# cleanly. `eval "$(emit_dataset ...)"` swallows the failure, and `set -u` then
# reports an unbound variable instead of the real problem.
load_dataset() {
  local _out
  _out=$(emit_dataset "$1") || die "unknown dataset: $1  (try ./setup-data.sh list)"
  eval "$_out"
}

all_datasets() {
  python3 - "$REGISTRY" <<'PY'
import sys, yaml
for d in yaml.safe_load(open(sys.argv[1])).get("datasets", []):
    print(d["name"])
PY
}

# ─────────────────────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────────────────────

# video_stems <dir> — camera ids, C-sorted, one per line. Must equal the
# calibration sensor ids and the camInfo stems; that is the key MV3DT uses to
# attach each stream's projection model.
video_stems() { (cd "$1" && LC_ALL=C ls -1 ./*.mp4 2>/dev/null | sed 's#^\./##; s/\.mp4$//'); }

sensor_ids() { jq -r '[.sensors[] | select(.type=="camera") | .id] | sort | .[]' "$1"; }

# cp_safe <src> <dst> — copy unless they are already the same file. Several
# datasets are registered with a source that is also one of the destinations
# (4cam's calibration lives in the testbed tree), so a plain cp would fail.
cp_safe() {
  [ -e "$1" ] || return 0
  [ "$(readlink -f "$1")" = "$(readlink -f "$2" 2>/dev/null || echo /nonexistent)" ] && return 0
  cp "$1" "$2"
}

# The MV3DT app dir inside the warehouse profile — the only one in scope.
WHBP_APP="$PROFILE/warehouse-mv3dt-app"

# resolve_calibration <name> <spec> — absolute path to the source
# calibration.json. `whbp` means "already committed under warehouse-mv3dt-app".
resolve_calibration() {
  local name="$1" spec="$2"
  case "$spec" in
    whbp) echo "$WHBP_APP/calibration/sample-data/$name/calibration.json" ;;
    "")   echo "" ;;
    *)    echo "$spec" ;;
  esac
}

# ensure_testbed_env — the testbed's own root .env carries ASSETS_DIR and
# CUSTOM_DATA_DIR. vst-stack.sh does not need them (sync writes an absolute
# VIDEO_DIR into dataset.env), but the testbed's own perception launchers do:
# pipeline2/run.sh and common/run-perception.sh both resolve MODELS from
# $ASSETS_DIR. Keep them in step with our layout so DEPLOY.md §4.7 works.
ensure_testbed_env() {
  local envf="$STANDALONE/.env"
  [ -f "$envf" ] || return 0
  local k v changed=0
  for k in ASSETS_DIR:"$APP_DATA" CUSTOM_DATA_DIR:"$CUSTOM_DATA"; do
    v="${k#*:}"; k="${k%%:*}"
    grep -qE "^${k}=" "$envf" || continue
    local cur; cur=$(sed -nE "s|^${k}=(.*)$|\1|p" "$envf" | tail -1)
    cur="${cur//\$\{HOME\}/$HOME}"; cur="${cur//\$HOME/$HOME}"
    [ "$cur" = "$v" ] && continue
    sed -i "s|^${k}=.*|${k}=${v}|" "$envf"; changed=1
  done
  [ "$changed" = 1 ] && echo "  testbed .env: ASSETS_DIR / CUSTOM_DATA_DIR realigned"
  return 0
}

# set_env_key <file> <key> <value> — rewrite KEY=... in place, or append it.
# Prints the change; silent when already correct. Also repairs a doubled
# assignment (MODELS_DIR=MODELS_DIR=...), which resolves to a literal path.
set_env_key() {
  local f="$1" k="$2" v="$3" cur
  [ -f "$f" ] || return 0
  if grep -qE "^${k}=" "$f"; then
    cur=$(sed -nE "s|^${k}=(.*)$|\1|p" "$f" | tail -1)
    cur="${cur//\$\{HOME\}/$HOME}"
    [ "$cur" = "$v" ] && return 0
    sed -i "s|^${k}=.*|${k}=${v}|" "$f"
  else
    printf '%s=%s\n' "$k" "$v" >> "$f"
  fi
  echo "    ${k}=${v}"
}
