#!/usr/bin/env bash
# =============================================================================
#  setup-data.sh — stage models and datasets for both MV3DT deployments.
#
#  The standalone component and the warehouse blueprint want the same datasets
#  in two different shapes, under two different names. This script owns that
#  translation so neither deployment's instructions have to.
#
#    standalone   $VSS_REPO/standalone/datasets/<alias>/
#                   calibration.json  dataset.env  map.png  camInfo/
#
#    blueprint    $VSS_REPO/deploy/docker/industry-profiles/warehouse-operations/
#                   warehouse-mv3dt-app/calibration/sample-data/<name>/
#                   calibration.json  camInfo/  images/{Top.png,imageMetadata.json}
#
#  Data only — this script never launches anything and never reconfigures a
#  deployment. Launching, and the config each deployment needs at launch, live in
#  launch-deployment.sh.
#
#  Usage:
#    ./setup-data.sh list                    what is registered, and what is staged
#    ./setup-data.sh check                   verify host prerequisites and layout
#    ./setup-data.sh fetch [--ngc|--netapp]  download models + sample videos
#    ./setup-data.sh sync <dataset|all>      materialize a dataset into both layouts
#
#  A dataset may be given by either name or alias: `sync 12cam` and
#  `sync Simple_Warehouse_Synthetic_040424` are the same thing.
# =============================================================================
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"


# ─────────────────────────────────────────────────────────────────────────────
#  list
# ─────────────────────────────────────────────────────────────────────────────

cmd_list() {
  printf '%-40s %-8s %5s %-8s %-9s %s\n' NAME ALIAS CAMS ORIGIN VIDEOS NOTE
  printf '%.0s─' {1..86}; printf '\n'
  local n
  while read -r n; do
    local DS_NAME DS_ALIAS DS_CAMS DS_ORIGIN DS_VIDEOS DS_SUBSET DS_TRACKER
    load_dataset "$n"
    local have="missing"
    if [ -d "$DS_VIDEOS" ]; then
      have="$(ls -1 "$DS_VIDEOS"/*.mp4 2>/dev/null | wc -l | tr -d ' ')/$DS_CAMS"
      [ "$DS_SUBSET" = 1 ] && have="$have*"
    fi
    printf '%-40s %-8s %5s %-8s %-9s %s\n' \
      "$DS_NAME" "$DS_ALIAS" "$DS_CAMS" "$DS_ORIGIN" "$have" \
      "${DS_TRACKER:+tuned tracker}"
  done < <(all_datasets)
  echo
  echo "VIDEOS = clips present / cameras in calibration.  * = subset expected."
}

# ─────────────────────────────────────────────────────────────────────────────
#  check
# ─────────────────────────────────────────────────────────────────────────────

cmd_check() {
  local fail=0
  step "Tools"
  for t in docker jq python3 ffprobe; do
    if command -v "$t" >/dev/null 2>&1; then grn "  ok    $t"
    else ylw "  warn  $t not found"; fi
  done
  python3 -c 'import yaml' 2>/dev/null && grn "  ok    PyYAML" || { red "  FAIL  PyYAML (python3 -m pip install --user PyYAML)"; fail=1; }
  docker compose version >/dev/null 2>&1 && grn "  ok    docker compose v2" || { red "  FAIL  docker compose v2"; fail=1; }
  if command -v nvidia-smi >/dev/null 2>&1; then
    grn "  ok    nvidia-smi ($(nvidia-smi --query-gpu=name --format=csv,noheader | paste -sd', ' -))"
  else red "  FAIL  nvidia-smi"; fail=1; fi

  step "Layout"
  for p in "$APP_DATA/models" "$APP_DATA/videos" "$RTCV/scripts" "$PROFILE"; do
    [ -d "$p" ] && grn "  ok    ${p#$VSS_REPO/}" || { red "  FAIL  ${p#$VSS_REPO/} missing"; fail=1; }
  done
  [ -d "$STANDALONE/pipeline1" ] && grn "  ok    standalone/ (testbed clone)" \
    || ylw "  warn  standalone/ missing — RTSP runs unavailable (see DEPLOY.md §2.3)"
  [ -d "$CUSTOM_DATA" ] && grn "  ok    data/vss-mv3dt-custom-datasets/" \
    || ylw "  warn  data/vss-mv3dt-custom-datasets/ missing — 12/28/145-cam unavailable"

  # Overrides are honoured; show which are active so an unexpected tree is
  # visible rather than silent.
  local n
  for n in $PATH_OVERRIDES; do
    ylw "  note  \$$n overridden -> ${!n}"
  done
  if [ -n "$ENV_VSS_REPO" ] && \
     [ "$(readlink -f "$ENV_VSS_REPO" 2>/dev/null)" != "$(readlink -f "$VSS_REPO" 2>/dev/null)" ]; then
    ylw "  warn  \$VSS_REPO exported as $ENV_VSS_REPO but this script lives in $VSS_REPO — export ignored"
  fi

  step "Component .env"
  local envf="$RTCV/docker/.env"
  if [ -f "$envf" ]; then
    local dups; dups=$(grep -c '^MODELS_DIR=MODELS_DIR=' "$envf" || true)
    [ "$dups" = 0 ] && grn "  ok    MODELS_DIR is a single assignment" \
      || { red "  FAIL  MODELS_DIR is doubled ('MODELS_DIR=MODELS_DIR=...') — the models mount will resolve to a literal path"; fail=1; }
    local md; md=$(sed -nE 's/^MODELS_DIR=(.*)$/\1/p' "$envf" | tail -1)
    md="${md//\$\{HOME\}/$HOME}"
    [ -d "$md" ] && grn "  ok    MODELS_DIR resolves: $md" || { red "  FAIL  MODELS_DIR does not resolve: $md"; fail=1; }
  else
    red "  FAIL  $envf missing"; fail=1
  fi

  step "Environment"
  [ -n "${NGC_CLI_API_KEY:-}" ] && grn "  ok    NGC_CLI_API_KEY exported" \
    || ylw "  warn  NGC_CLI_API_KEY not exported — required for blueprint 'up' and for fetch"
  [ -n "${DISPLAY:-}" ] && grn "  ok    DISPLAY=$DISPLAY" \
    || ylw "  warn  DISPLAY unset — OSD and the live BEV window are unavailable"
  # Kernel settings, per skills/vss-deploy-profile/references/prerequisites.md.
  # vm.max_map_count is Elasticsearch (Lucene mmaps every index segment); the
  # socket buffers are RTSP/VST and Kafka throughput.
  local kv key min cur
  for kv in vm.max_map_count=262144 net.core.rmem_max=5242880 net.core.wmem_max=5242880; do
    key="${kv%%=*}"; min="${kv##*=}"
    cur=$(sysctl -n "$key" 2>/dev/null || echo 0)
    if [ "${cur:-0}" -ge "$min" ] 2>/dev/null; then
      grn "  ok    $key=$cur"
    else
      ylw "  warn  $key=$cur — want >=$min (sudo sysctl -w $key=$min)"
    fi
  done

  echo
  [ "$fail" = 0 ] && grn "check passed" || { red "check failed"; return 1; }
}

# ─────────────────────────────────────────────────────────────────────────────
#  fetch
# ─────────────────────────────────────────────────────────────────────────────

fetch_ngc() {
  step "NGC app-data package"
  command -v ngc >/dev/null 2>&1 || die "ngc CLI not found — see DEPLOY.md §2.1"
  [ -n "${NGC_CLI_API_KEY:-}" ] || die "export NGC_CLI_API_KEY first"
  if [ -d "$APP_DATA/models" ] && [ -d "$APP_DATA/videos" ]; then
    grn "  already extracted: $APP_DATA"; return 0
  fi
  local ver="${NGC_RESOURCE##*:}" res="${NGC_RESOURCE%:*}"
  local dl="$HERE/$(basename "$res")_v${ver}"
  ( cd "$HERE" && NGC_CLI_ORG="${NGC_CLI_ORG:-nvidia}" \
      ngc registry resource download-version "$NGC_RESOURCE" )
  [ -f "$dl/vss-warehouse-app-data.tar.gz" ] || die "expected tarball not found under $dl"
  tar -xzf "$dl/vss-warehouse-app-data.tar.gz" -C "$HERE"
  sudo chmod -R 777 "$APP_DATA"
  grn "  extracted → $APP_DATA"
}

fetch_netapp() {
  step "netapp custom datasets"
  if [ -d "$CUSTOM_DATA" ] && [ -n "$(ls -A "$CUSTOM_DATA" 2>/dev/null)" ]; then
    grn "  already present: $CUSTOM_DATA"; return 0
  fi
  if [ ! -d "$NETAPP_MOUNT/vss-mv3dt-custom-datasets" ]; then
    ylw "  $NETAPP_MOUNT not mounted. Mount it, then re-run:"
    cat <<EOF

    sudo mkdir -p $NETAPP_MOUNT
    sudo mount $NETAPP_SHARE $NETAPP_MOUNT \\
      -o username=\$USER,domain=nvidia.com

EOF
    return 1
  fi
  cp -r "$NETAPP_MOUNT/vss-mv3dt-custom-datasets" "$HERE/"
  grn "  copied → $CUSTOM_DATA"
}

cmd_fetch() {
  local do_ngc=1 do_netapp=1
  case "${1:-}" in
    --ngc)    do_netapp=0 ;;
    --netapp) do_ngc=0 ;;
    "")       ;;
    *)        die "unknown fetch flag: $1" ;;
  esac
  [ "$do_ngc" = 1 ]    && fetch_ngc
  [ "$do_netapp" = 1 ] && fetch_netapp
  echo
  grn "fetch done — next: ./setup-data.sh sync all"
}

# ─────────────────────────────────────────────────────────────────────────────
#  sync
# ─────────────────────────────────────────────────────────────────────────────

# write_dataset_env <dir> <cams-csv> <num> <video-dir>
write_dataset_env() {
  local dir="$1" cams="$2" num="$3" videos="$4"
  local half=$(( (num + 1) / 2 ))
  local c0 c1
  c0=$(echo "$cams" | tr ',' '\n' | head -n "$half" | paste -sd, -)
  c1=$(echo "$cams" | tr ',' '\n' | tail -n +$((half + 1)) | paste -sd, -)
  cat > "$dir/dataset.env" <<EOF
# Generated by data/setup-data.sh — do not edit by hand.
# Regenerate with:  ./data/setup-data.sh sync $(basename "$dir")

NUM_CAMS=$num

# Host dir holding the camera .mp4 files (read by NVStreamer).
VIDEO_DIR=$videos

# Camera ids == video stems == camInfo/*.yml stems == calibration sensor ids.
CAMERAS="$cams"
# Even split for the 2-perception-container topology (NUM_CONTAINERS=2).
CAMERAS_0="$c0"
CAMERAS_1="$c1"
EOF
}

sync_one() {
  local want="$1"
  local DS_NAME DS_ALIAS DS_CAMS DS_ORIGIN DS_SUBSET DS_VIDEOS DS_CALIB \
        DS_MAP DS_TRACKER DS_CLASS_SPECS DS_NEIGHBOR
  load_dataset "$want"

  step "$DS_NAME  (alias $DS_ALIAS, $DS_CAMS cams)"

  # ── videos ────────────────────────────────────────────────────────────────
  [ -d "$DS_VIDEOS" ] || { ylw "  skip — videos not present: $DS_VIDEOS"; return 0; }
  local cams num
  cams=$(video_stems "$DS_VIDEOS" | paste -sd, -)
  num=$(video_stems "$DS_VIDEOS" | grep -c . || true)
  [ "$num" -gt 0 ] || { ylw "  skip — no .mp4 files in $DS_VIDEOS"; return 0; }
  echo "  videos: $num clip(s) in ${DS_VIDEOS#$VSS_REPO/}"

  # The blueprint always reads clips from $APP_DATA/videos/<name>/. Datasets that
  # live under custom-datasets/ get linked into place rather than copied.
  local whbp_videos="$APP_DATA/videos/$DS_NAME"
  if [ "$(readlink -f "$DS_VIDEOS")" != "$(readlink -f "$whbp_videos" 2>/dev/null || echo)" ]; then
    if [ ! -e "$whbp_videos" ] || [ -L "$whbp_videos" ]; then
      ln -sfn "$DS_VIDEOS" "$whbp_videos"
      echo "  linked: videos/$DS_NAME → ${DS_VIDEOS#$VSS_REPO/}"
    fi
  fi

  # ── calibration ───────────────────────────────────────────────────────────
  local calib_src
  calib_src="$(resolve_calibration "$DS_NAME" "$DS_CALIB")"
  [ -f "$calib_src" ] || { ylw "  skip — calibration not found: $calib_src"; return 0; }

  # Sensor ids must match the video stems. Downstream a mismatch is silent but
  # fatal: camInfo covers cameras that never produce frames, the tracker keeps a
  # stale camera map, and the run dies seconds in.
  # The effective calibration — the subset when one was taken, otherwise the
  # registry source — is staged into generated/ next to camInfo and the pub/sub
  # graph, and both trees are populated from there. generated/ is the
  # component's own gitignored staging area and always exists; standalone/ is
  # optional — absent whenever the testbed has not been cloned — so it cannot
  # be the intermediate.
  mkdir -p "$RTCV/generated"
  local eff_calib="$RTCV/generated/calibration.json"
  if [ "$DS_SUBSET" = 1 ] || [ "$num" -ne "$DS_CAMS" ]; then
    jq --arg cams "$(video_stems "$DS_VIDEOS")" '
      ($cams | split("\n") | map(select(length > 0))) as $keep
      | .sensors |= map(select(.type == "camera" and (.id as $i | $keep | index($i) != null)))
    ' "$calib_src" > "$eff_calib"
    local kept; kept=$(jq '[.sensors[]|select(.type=="camera")]|length' "$eff_calib")
    [ "$kept" -eq "$num" ] || {
      red "  FAIL  calibration ids do not match the video stems"
      echo "        videos:  $cams" >&2
      echo "        sensors: $(sensor_ids "$calib_src" | paste -sd, -)" >&2
      return 1
    }
    echo "  calibration: subset to $kept sensor(s) with video"
  else
    diff <(sensor_ids "$calib_src") <(video_stems "$DS_VIDEOS") >/dev/null || {
      red "  FAIL  calibration ids do not match the video stems"
      echo "        videos:  $cams" >&2
      echo "        sensors: $(sensor_ids "$calib_src" | paste -sd, -)" >&2
      return 1
    }
    cp_safe "$calib_src" "$eff_calib"
    echo "  calibration: $num sensor(s), ids match video stems"
  fi

  # ── camInfo + pub/sub ─────────────────────────────────────────────────────
  echo "  generating camInfo + pub/sub (neighbor_criteria=$DS_NEIGHBOR)"
  local genlog; genlog=$(mktemp)
  if ! ( cd "$RTCV" && CLASS_SPECS="$DS_CLASS_SPECS" NEIGHBOR_CRITERIA="$DS_NEIGHBOR" \
           ./scripts/generate-configs.sh "$eff_calib" ) >"$genlog" 2>&1; then
    cat "$genlog" >&2; rm -f "$genlog"; die "generate-configs.sh failed for $DS_NAME"
  fi
  rm -f "$genlog"
  echo "  generated: $(ls -1 "$RTCV/generated/camInfo"/*.yml | wc -l | tr -d ' ') camInfo file(s)"

  # ── standalone layout ─────────────────────────────────────────────────────
  # The testbed is a separate clone and may simply not be here; the blueprint
  # half stands on its own, so this is a warning rather than a failure.
  if [ ! -d "$STANDALONE" ]; then
    ylw "  $STANDALONE not present — skipping testbed half (see DEPLOY.md §2.2)"
  else
    local sd="$STANDALONE/datasets/$DS_ALIAS"
    mkdir -p "$sd"
    cp_safe "$eff_calib" "$sd/calibration.json"
    write_dataset_env "$sd" "$cams" "$num" "$DS_VIDEOS"
    if [ -n "$DS_MAP" ] && [ "$DS_MAP" != whbp ] && [ -f "$DS_MAP" ] && [ "$DS_MAP" != "$sd/map.png" ]; then
      cp_safe "$DS_MAP" "$sd/map.png"
    fi
    [ -f "$sd/map.png" ] || ylw "  no map.png for $DS_ALIAS — the BEV visualizer needs one"
    mkdir -p "$sd/camInfo"
    cp -a "$RTCV/generated/camInfo/." "$sd/camInfo/"
    cp "$RTCV/generated/pub_sub_info_config.yml" "$sd/pub_sub_info_config.yml"
    # The perception container runs as a non-root user and bind-mounts these
    # read-only. A host umask of 027 leaves them group-only, so the container
    # cannot read them — same reason generate-configs.sh chmods its output.
    chmod -R o+rX "$sd"
    ensure_testbed_env
    grn "  standalone → ${sd#$VSS_REPO/}"
  fi

  # ── blueprint layout ──────────────────────────────────────────────────────
  local sdd="$WHBP_APP/calibration/sample-data/$DS_NAME"
  local created=0; [ -d "$sdd" ] || created=1
  mkdir -p "$sdd/images"
  # Self-ignore only directories we create. The shipped sample-data dirs are
  # tracked, and dropping a '*' .gitignore into one hides real files.
  if [ "$created" = 1 ] && [ ! -f "$sdd/.gitignore" ]; then echo '*' > "$sdd/.gitignore"; fi

  # Stage the EFFECTIVE calibration, i.e. the subset when one was taken. Four
  # consumers read this file (behaviour analytics, calibration import, and the
  # configurator via /opt/data); staging the full one for a subset dataset
  # imports sensors that can never report.
  #
  # A registry source is never a sync destination. For `calibration: whbp` the
  # two are the same file, so writing here would overwrite the source with
  # whatever was staged — and a subset (one clip missing, say) would destroy it
  # for good, since the next run would subset the already-subset file.
  if [ "$(readlink -f "$calib_src")" = "$(readlink -f "$sdd/calibration.json" 2>/dev/null || echo /nonexistent)" ]; then
    if diff -q <(jq -S . "$calib_src" 2>/dev/null) <(jq -S . "$eff_calib" 2>/dev/null) >/dev/null 2>&1; then
      echo "  calibration → warehouse-mv3dt-app (is the registry source, $num sensor(s))"
    else
      ylw "  calibration is the registry source and was NOT overwritten"
      ylw "    it declares $DS_CAMS camera(s) but only $num have video, so camInfo covers $num"
      ylw "    restore the missing clips, or move the source out of the repo (DEPLOY.md §3.3)"
    fi
  elif [ ! -f "$sdd/calibration.json" ]; then
    cp_safe "$eff_calib" "$sdd/calibration.json"
    echo "  calibration → warehouse-mv3dt-app (created, $num sensor(s))"
  elif diff -q <(jq -S . "$sdd/calibration.json" 2>/dev/null) \
               <(jq -S . "$eff_calib" 2>/dev/null) >/dev/null 2>&1; then
    echo "  calibration → warehouse-mv3dt-app (already matches)"
  else
    cp_safe "$eff_calib" "$sdd/calibration.json"
    echo "  calibration → warehouse-mv3dt-app (restaged, $num sensor(s))"
  fi

  # Floor map + its metadata. The importer needs a plan-view image; when the
  # dataset has no real map a blank canvas keeps the import step valid.
  # Always refresh from the map source when there is one. Guarding on absence
  # would pin a blank placeholder forever: once written, a map added later could
  # never replace it, and the importer would keep loading an empty floor plan.
  if [ -n "$DS_MAP" ] && [ "$DS_MAP" != whbp ] && [ -f "$DS_MAP" ]; then
    cp_safe "$DS_MAP" "$sdd/images/Top.png"
  elif [ ! -f "$sdd/images/Top.png" ] && command -v convert >/dev/null 2>&1; then
    convert -size 1920x1080 canvas:white "$sdd/images/Top.png"
    ylw "  no map for $DS_NAME — wrote a blank Top.png placeholder"
  fi
  [ -f "$sdd/images/imageMetadata.json" ] || cat > "$sdd/images/imageMetadata.json" <<'EOF'
{
	"images":[
		{
			"place":"building=Warehouse/room=Room-1/region=Region-1",
			"view":"plan-view",
			"fileName":"Top.png"
		}
	]
}
EOF

  mkdir -p "$sdd/camInfo"
  rm -f "$sdd/camInfo"/*.yml
  cp -a "$RTCV/generated/camInfo/." "$sdd/camInfo/"
  # Per-dataset stash, same reason as camInfo: generated/ is shared and holds
  # whichever dataset was synced last, so the launcher must not read from it.
  cp "$RTCV/generated/pub_sub_info_config.yml" "$sdd/pub_sub_info_config.yml"
  # calibration.json, images/ and camInfo/ are all bind-mounted into
  # containers running as a non-root user (warehouse-mv3dt-app.yml).
  chmod -R o+rX "$sdd"
  grn "  blueprint  → ${sdd#$VSS_REPO/}"
}

cmd_sync() {
  local target="${1:-}"
  [ -n "$target" ] || die "usage: setup-data.sh sync <dataset|all>"
  [ $# -le 1 ] || die "sync takes one dataset (or 'all'); got: $*"

  if [ "$target" = all ]; then
    local n
    while read -r n; do sync_one "$n"; done < <(all_datasets)
  else
    sync_one "$target"
  fi

  echo
  grn "sync done."
  if [ "$target" != all ]; then
    local DS_NAME DS_ALIAS DS_CAMS DS_TRACKER DS_VIDEOS
    load_dataset "$target"
    # NUM_CAMS must follow the clips actually present, not the registry's declared
    # count — for a subset dataset those differ (145 declared, 8 staged).
    local n; n=$(video_stems "$DS_VIDEOS" 2>/dev/null | grep -c . || true)
    [ "${n:-0}" -gt 0 ] || n="$DS_CAMS"
    cat <<EOF

Next — launch it (DEPLOY.md §4 / §5):
  cd $HERE && ./launch-deployment.sh standalone up $DS_ALIAS
  cd $HERE && ./launch-deployment.sh blueprint  up $DS_NAME
EOF
  fi
}

# ─────────────────────────────────────────────────────────────────────────────

case "${1:-}" in
  list)  shift; cmd_list "$@" ;;
  check) shift; cmd_check "$@" ;;
  fetch) shift; cmd_fetch "$@" ;;
  sync)  shift; cmd_sync "$@" ;;
  ""|-h|--help|help)
    sed -n '2,/^# ====/p' "${BASH_SOURCE[0]}" | sed 's/^#\{1,2\} \{0,1\}//; s/^#$//'
    ;;
  *) die "unknown command: $1 (try --help)" ;;
esac
