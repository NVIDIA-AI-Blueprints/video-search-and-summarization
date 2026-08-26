#!/usr/bin/env bash
#
# download-embedding-models.sh — fetch embedding / ReID model assets into the
# current directory, skipping anything already present.
#
#   ./radio-clip_vdeployable_v1.0/         # with --secondary
#   ./siglip_v2_vdeployable_v1.0/
#   ./clip-reid/                           # with --clipreid: source, ckpt, cache
#   ./reid_model.onnx                      # with --clipreid
#
# --secondary fetches the TAO models the ReID service itself loads
# (SECONDARY_EMBEDDING_ONNX_MODEL_PATH). --clipreid builds the tracker ONNX that
# DeepStream loads; the service never reads it, so it is off by default.
#
# --clipreid runs the torch export in this process when torch is importable (the
# reid-embed image ships it). Pass --image only to offload the export to a
# sibling container instead; that path additionally needs the docker CLI and a
# mounted docker socket, and the CLIP-ReID tree must live at the same path here
# as on the host, because the host daemon resolves the nested -v argument.
#
# Usage:
#   ./download-embedding-models.sh --secondary
#   ./download-embedding-models.sh --clipreid
#   ./download-embedding-models.sh --secondary --clipreid
#   ./download-embedding-models.sh --clipreid --image reid-service:latest
#
# Needs: curl for both stages, plus a GPU for --clipreid (the converter calls
#        .cuda()). The NGC CLI is bootstrapped into /tmp when absent, mirroring
#        rtvi-cv/download-models.sh.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

MODELS=(
  "nvidia/tao/siglip_v2:deployable_v1.0"
)

# Matches NGC_ORG_DEFAULT in rtvi-cv/download-models.sh.
NGC_ORG_DEFAULT="${NGC_ORG_DEFAULT:-nvidia}"

# The fixed UID/GID the RT-CV app runs as after dropping privileges. This script
# usually runs as root in an init container, so anything it writes must be handed
# over or the perception container cannot read it.
STORAGE_UID="${STORAGE_UID:-1001}"
STORAGE_GID="${STORAGE_GID:-1001}"

# Market-1501 ViT-CLIP-ReID-SIE-OLP — Syliz517/CLIP-ReID README "Trained models".
CLIPREID_SRC_URL="https://codeload.github.com/Syliz517/CLIP-ReID/tar.gz/refs/heads/master"
CLIPREID_CKPT_URL="https://drive.google.com/file/d/1K32xrosw0gPrxYCWXER81mhWObEW5-d4/view"
CLIPREID_CKPT_ID="1K32xrosw0gPrxYCWXER81mhWObEW5-d4"
CLIPREID_CKPT_NAME="Market1501_clipreid_12x12sie_ViT-B-16_60.pth"
CONVERT_SCRIPT_NAME="convert_clipreid_to_onnx.py"

HERE="$(pwd)"
DEST_DIR="$HERE"
CLIPREID_DIR="$HERE/clip-reid"
CLIPREID_ONNX="$HERE/reid_model.onnx"
CLIPREID_SRC="${CLIPREID_DIR}/src"
CLIPREID_CKPT_DIR="${CLIPREID_DIR}/checkpoints"
CLIPREID_CACHE="${CLIPREID_DIR}/cache"
CLIPREID_CKPT="${CLIPREID_CKPT_DIR}/${CLIPREID_CKPT_NAME}"

IMAGE=""
FORCE=0
SECONDARY=0
CLIPREID=0
CLIPREID_BACKEND=""
WORK=""

usage() {
  cat <<EOF
Usage: $0 [--secondary] [--clipreid] [options]

Download model assets into the current directory. Pick at least one stage.

Stages:
  --secondary           Fetch SigLIP / RADIO-CLIP via NGC CLI. These are what
                        the ReID service loads for secondary embedding.
  --clipreid            Fetch CLIP-ReID source + checkpoint and export
                        reid_model.onnx (the DeepStream tracker model).

Options:
  --image IMAGE[:TAG]   Offload the --clipreid steps to a sibling container
                        instead of running them here. Only needed when this
                        environment has no python torch. Requires the docker
                        CLI and a mounted docker socket.
  --force               Re-fetch / re-convert even if files already exist
  -h, --help            Show this help
EOF
}

find_convert_script() {
  local c
  for c in "$SCRIPT_DIR/$CONVERT_SCRIPT_NAME" "$HERE/$CONVERT_SCRIPT_NAME"; do
    [ -f "$c" ] && { printf '%s\n' "$c"; return 0; }
  done
  return 1
}

while [ $# -gt 0 ]; do
  case "$1" in
    --image)
      [ $# -ge 2 ] || { echo "ERROR: $1 needs a value" >&2; exit 1; }
      IMAGE="$2"
      shift 2
      ;;
    --secondary)
      SECONDARY=1
      shift
      ;;
    --clipreid)
      CLIPREID=1
      shift
      ;;
    --force)
      FORCE=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "ERROR: unknown option: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

if [ "$SECONDARY" = 0 ] && [ "$CLIPREID" = 0 ]; then
  echo "ERROR: nothing to do — pass --secondary and/or --clipreid." >&2
  usage >&2
  exit 1
fi

dir_nonempty() {
  [ -d "$1" ] && [ -n "$(ls -A "$1" 2>/dev/null)" ]
}

file_nonempty() {
  [ -f "$1" ] && [ -s "$1" ]
}

version_dir() {
  local spec="$1" name="${1##*/}"
  printf '%s_v%s\n' "${name%%:*}" "${spec##*:}"
}

ensure_image() {
  if docker image inspect "$IMAGE" >/dev/null 2>&1; then
    echo "── Using local image $IMAGE"
    return 0
  fi
  echo "── Pulling $IMAGE ..."
  docker pull "$IMAGE"
}

# One-shot GPU container with the CLIP-ReID tree mounted at /work.
# Runs as the host user so writes into /work stay owned by us. HOME is the
# mounted cache (pip --user + CLIP hub). USER/LOGNAME are set because our uid
# may have no /etc/passwd entry and torch calls getpass.getuser().
run_in_image() {
  mkdir -p "$CLIPREID_CACHE"
  docker run --rm \
    --runtime=nvidia \
    --gpus all \
    --user "$(id -u):$(id -g)" \
    -e HOME=/work/cache \
    -e USER="$(id -un)" \
    -e LOGNAME="$(id -un)" \
    -e NVIDIA_DRIVER_CAPABILITIES=compute,utility \
    -e PYTHONUNBUFFERED=1 \
    -v "$CLIPREID_DIR:/work" \
    --entrypoint bash \
    "$IMAGE" \
    -c "$@"
}

# Same steps as run_in_image, in this process. HOME points at the cache so pip
# and the CLIP hub download stay inside the CLIP-ReID tree rather than into a
# container layer that is discarded when the init container exits.
run_locally() {
  mkdir -p "$CLIPREID_CACHE"
  env HOME="$CLIPREID_CACHE" \
      USER="$(id -un)" \
      LOGNAME="$(id -un)" \
      PYTHONUNBUFFERED=1 \
      bash -c "$@"
}

run_step() {
  if [ "$CLIPREID_BACKEND" = "local" ]; then
    run_locally "$@"
  else
    run_in_image "$@"
  fi
}

# Sets CLIPREID_BACKEND and WORK: the CLIP-ReID tree as the chosen backend sees
# it. An explicit --image wins so an operator can force the sibling-container
# path even where torch is importable.
select_clipreid_backend() {
  if [ -n "$IMAGE" ]; then
    CLIPREID_BACKEND="image"
    WORK="/work"
    echo "── CLIP-ReID backend: sibling container ($IMAGE)"
    return 0
  fi
  if python3 -c 'import torch' >/dev/null 2>&1; then
    CLIPREID_BACKEND="local"
    WORK="$CLIPREID_DIR"
    echo "── CLIP-ReID backend: in-process ($(python3 -c 'import torch; print("torch " + torch.__version__)'))"
    return 0
  fi
  echo "ERROR: --clipreid needs an importable python torch, or --image to run" >&2
  echo "       the export in a sibling container that has one." >&2
  echo "         $0 --clipreid --image reid-service:latest" >&2
  exit 1
}

# Handing ownership over needs root. Decide once and announce it, 
PERMS_SKIP_LOGGED=0
can_apply_perms() {
  if [ "$(id -u)" = "0" ]; then
    return 0
  fi
  if [ "$PERMS_SKIP_LOGGED" = 0 ]; then
    echo "── Skipping model ownership handover (not root; running as $(id -un))"
    PERMS_SKIP_LOGGED=1
  fi
  return 1
}

# The model-tree permission contract from rtvi-cv/download-models.sh: dirs 0777,
# files 0644, owned by STORAGE_UID:STORAGE_GID. Scoped to one written artifact so
# pre-existing files elsewhere in the tree keep their ownership. Failures here are
# fatal, as in download-models.sh — an artifact the app cannot read is not a
# success worth reporting.
apply_artifact_perms() {
  local path="$1"
  [ -e "$path" ] || return 0
  can_apply_perms || return 0
  chown -R "${STORAGE_UID}:${STORAGE_GID}" "$path"
  if [ -d "$path" ]; then
    find "$path" -type d -exec chmod 0777 {} +
    find "$path" -type f -exec chmod 0644 {} +
  else
    chmod 0644 "$path"
    # TensorRT writes the ReID engine plan next to the ONNX, so the containing
    # dir has to stay writable by the app user too. Non-fatal, as in
    # download-models.sh: the parent may be pre-existing and owned elsewhere.
    local parent
    parent="$(dirname "$path")"
    chown "${STORAGE_UID}:${STORAGE_GID}" "$parent" 2>/dev/null || true
    chmod 0777 "$parent" 2>/dev/null || true
  fi
}

ngc_cli_zip_for_arch() {
  case "${1:-$(uname -m)}" in
    aarch64|arm64) echo "ngccli_arm64.zip" ;;
    *)             echo "ngccli_linux.zip" ;;
  esac
}

# Mirrors ensure_ngc_cli in rtvi-cv/download-models.sh: the images that run this
# script do not ship the NGC CLI, so fetch the arch-matched build into /tmp.
ensure_ngc_cli() {
  if command -v ngc >/dev/null 2>&1; then
    return 0
  fi
  if [ "$(id -u)" != "0" ]; then
    echo "ERROR: the NGC CLI is not on PATH and installing it needs root." >&2
    echo "       Install it from https://org.ngc.nvidia.com/setup/installers/cli," >&2
    echo "       or run this stage as root." >&2
    exit 1
  fi
  echo "── Bootstrapping the NGC CLI (not on PATH) ..."
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq && apt-get install -y -qq ca-certificates wget unzip > /dev/null
  local ngc_cli_zip
  ngc_cli_zip="$(ngc_cli_zip_for_arch)"
  pushd /tmp > /dev/null
  wget -q "https://ngc.nvidia.com/downloads/${ngc_cli_zip}" -O ngccli.zip
  unzip -q -o ngccli.zip && chmod +x ngc-cli/ngc
  popd > /dev/null
  export PATH="/tmp/ngc-cli:${PATH}"
  ngc --version
}

download_tao_models() {
  mkdir -p "$DEST_DIR"
  echo "── Target (TAO embedding): $DEST_DIR"

  # Root of the tree, node only: engine plans land here alongside the models.
  # Non-fatal even as root, matching main() in download-models.sh — the dir can
  # be pre-existing and owned by someone else.
  if can_apply_perms; then
    chown "${STORAGE_UID}:${STORAGE_GID}" "$DEST_DIR" 2>/dev/null || true
    chmod 0777 "$DEST_DIR" 2>/dev/null || true
  fi

  local downloaded=0 skipped=0
  local spec dir
  for spec in "${MODELS[@]}"; do
    dir="$DEST_DIR/$(version_dir "$spec")"
    if [ "$FORCE" != 1 ] && dir_nonempty "$dir"; then
      echo "── SKIP  $spec  →  ${dir##*/}/ already present"
      apply_artifact_perms "$dir"
      skipped=$((skipped + 1))
      continue
    fi
    ensure_ngc_cli
    echo "── Downloading $spec ..."
    rm -rf "$dir"
    # --org is passed explicitly, as rtvi-cv/download-models.sh does, so the CLI
    # does not need a configured org. These TAO models are world-readable, so an
    # unset NGC_CLI_API_KEY is fine; the CLI picks the key up from the
    # environment on its own when one is supplied for a gated model.
    if ngc registry model download-version "$spec" --org "$NGC_ORG_DEFAULT" --dest "$DEST_DIR"; then
      # The NGC CLI creates its version dir 0700, unreadable to the app user.
      apply_artifact_perms "$dir"
      downloaded=$((downloaded + 1))
    else
      rm -rf "$dir"
      echo "ERROR: download failed for $spec (org ${NGC_ORG_DEFAULT})." >&2
      echo "       Gated models need NGC_CLI_API_KEY set in the environment." >&2
      exit 1
    fi
  done

  echo "   TAO embedding: downloaded=$downloaded skipped=$skipped"
  for d in "$DEST_DIR"/*/; do [ -d "$d" ] && echo "     $d"; done
}

# Tarball rather than git clone: anonymous git RPCs get HTTP 401 and git then
# prompts for a username, which hangs an unattended run.
fetch_clipreid_repo() {
  mkdir -p "$CLIPREID_DIR"

  if [ "$FORCE" != 1 ] && [ -f "$CLIPREID_SRC/model/make_model_clipreid.py" ]; then
    echo "── SKIP  CLIP-ReID source  →  $CLIPREID_SRC already present"
    return 0
  fi

  echo "── Fetching CLIP-ReID source → $CLIPREID_SRC"
  echo "   $CLIPREID_SRC_URL"
  local tmp attempt
  tmp="$(mktemp -d "$CLIPREID_DIR/.src.XXXXXX")"
  for attempt in 1 2 3; do
    if curl -fsSL --retry 3 --retry-delay 2 --connect-timeout 30 "$CLIPREID_SRC_URL" |
        tar xz -C "$tmp" --strip-components=1 &&
        [ -f "$tmp/model/make_model_clipreid.py" ]; then
      rm -rf "$CLIPREID_SRC"
      mv "$tmp" "$CLIPREID_SRC"
      chmod 755 "$CLIPREID_SRC"
      return 0
    fi
    echo "   ⚠ fetch attempt $attempt failed"
    rm -rf "${tmp:?}"/* "${tmp:?}"/.[!.]* 2>/dev/null || true
    [ "$attempt" -lt 3 ] && sleep $((attempt * 5))
  done

  rm -rf "$tmp"
  echo "ERROR: could not fetch $CLIPREID_SRC_URL" >&2
  echo "       Unpack the repo yourself into $CLIPREID_SRC" >&2
  exit 1
}

# Upstream uses `if get_image == True`. Under ONNX tracing those flags are
# tensors, and Tensor == bool has no TorchScript op. Plain truthiness traces.
patch_clipreid_repo() {
  local f="$CLIPREID_SRC/model/make_model_clipreid.py"
  [ -f "$f" ] || { echo "ERROR: not found: $f" >&2; exit 1; }
  if grep -q "if get_image == True:\|if get_text == True:" "$f"; then
    echo "── Patching ${f#$CLIPREID_DIR/} for ONNX tracing (get_image / get_text)"
    sed -i 's/if get_text == True:/if get_text:/; s/if get_image == True:/if get_image:/' "$f"
  fi
}

download_clipreid_checkpoint() {
  mkdir -p "$CLIPREID_CKPT_DIR"
  if [ "$FORCE" != 1 ] && file_nonempty "$CLIPREID_CKPT"; then
    echo "── SKIP  checkpoint  →  ${CLIPREID_CKPT##*/} already present"
    return 0
  fi

  echo "── Downloading Market-1501 SIE+OLP checkpoint ..."
  echo "   $CLIPREID_CKPT_URL"
  rm -f "$CLIPREID_CKPT"
  if ! run_step \
      "pip3 install -q --disable-pip-version-check --no-warn-script-location gdown && \
       python3 -m gdown '${CLIPREID_CKPT_ID}' \
         -O '${WORK}/checkpoints/${CLIPREID_CKPT_NAME}'"; then
    rm -f "$CLIPREID_CKPT"
    echo "ERROR: checkpoint download failed." >&2
    echo "       Place ${CLIPREID_CKPT_NAME} at $CLIPREID_CKPT" >&2
    echo "       Source: $CLIPREID_CKPT_URL" >&2
    exit 1
  fi
  if ! file_nonempty "$CLIPREID_CKPT"; then
    echo "ERROR: checkpoint file missing or empty after download: $CLIPREID_CKPT" >&2
    exit 1
  fi
}

convert_clipreid_onnx() {
  if [ "$FORCE" != 1 ] && file_nonempty "$CLIPREID_ONNX"; then
    echo "── SKIP  ONNX  →  $CLIPREID_ONNX already present"
    return 0
  fi

  echo "── Converting checkpoint → ONNX"
  local converter
  converter="$(find_convert_script || true)"
  [ -n "$converter" ] && [ -f "$converter" ] || {
    echo "ERROR: $CONVERT_SCRIPT_NAME not found next to this script or in $HERE" >&2
    exit 1
  }
  echo "   converter: $converter"
  cp "$converter" "$CLIPREID_DIR/$CONVERT_SCRIPT_NAME"
  rm -f "$CLIPREID_DIR/reid_model.onnx"

  # CLIP-ReID deps missing from reid-service:latest (torch/onnx/regex are there).
  if ! run_step \
      "pip3 install -q --disable-pip-version-check --no-warn-script-location yacs ftfy timm && \
       python3 ${WORK}/convert_clipreid_to_onnx.py \
         --repo-dir ${WORK}/src \
         --checkpoint ${WORK}/checkpoints/${CLIPREID_CKPT_NAME} \
         --output ${WORK}/reid_model.onnx"; then
    rm -f "$CLIPREID_DIR/reid_model.onnx"
    echo "ERROR: ONNX conversion failed." >&2
    exit 1
  fi

  file_nonempty "$CLIPREID_DIR/reid_model.onnx" || {
    echo "ERROR: ONNX missing after conversion." >&2
    exit 1
  }

  mv -f "$CLIPREID_DIR/reid_model.onnx" "$CLIPREID_ONNX"
  apply_artifact_perms "$CLIPREID_ONNX"
  echo "   → $CLIPREID_ONNX"
}

download_clipreid() {
  echo "── Target (CLIP-ReID build): $CLIPREID_DIR"
  echo "── Target (CLIP-ReID ONNX):  $CLIPREID_ONNX"

  command -v curl >/dev/null 2>&1 || {
    echo "ERROR: curl is required to fetch the CLIP-ReID source." >&2
    exit 1
  }

  if [ "$FORCE" != 1 ] \
      && [ -f "$CLIPREID_SRC/model/make_model_clipreid.py" ] \
      && file_nonempty "$CLIPREID_CKPT" \
      && file_nonempty "$CLIPREID_ONNX"; then
    echo "── SKIP  CLIP-ReID  →  source, checkpoint, and ONNX already present"
    # Still re-assert perms: a tree left behind by an earlier run that predates
    # the ownership handover would otherwise stay root-owned forever, since
    # every subsequent run lands here.
    apply_artifact_perms "$CLIPREID_ONNX"
    apply_artifact_perms "$CLIPREID_DIR"
    return 0
  fi

  select_clipreid_backend
  if [ "$CLIPREID_BACKEND" = "image" ]; then
    command -v docker >/dev/null 2>&1 || {
      echo "ERROR: --image needs the docker CLI and a mounted docker socket." >&2
      exit 1
    }
    ensure_image
  fi
  fetch_clipreid_repo
  patch_clipreid_repo
  download_clipreid_checkpoint
  convert_clipreid_onnx
  # The build tree (src, checkpoints, pip/CLIP cache) is written as root here, so
  # hand it over too — otherwise a later non-root rerun cannot reuse or clean it.
  apply_artifact_perms "$CLIPREID_DIR"
}

if [ "$SECONDARY" = 1 ]; then
  echo
  echo "══ TAO embedding models (SigLIP / RADIO-CLIP) ══"
  download_tao_models
fi

if [ "$CLIPREID" = 1 ]; then
  echo
  echo "══ CLIP-ReID (repo + checkpoint → ONNX) ══"
  download_clipreid
fi

echo
echo "DONE."
if [ "$SECONDARY" = 1 ]; then
  echo "  TAO embedding:  $DEST_DIR"
  for d in "$DEST_DIR"/*/; do [ -d "$d" ] && echo "    $d"; done
fi
if [ "$CLIPREID" = 1 ]; then
  echo "  CLIP-ReID src:  $CLIPREID_SRC"
  echo "  CLIP-ReID ckpt: $CLIPREID_CKPT"
  echo "  CLIP-ReID ONNX: $CLIPREID_ONNX"
  echo "  backend:        ${CLIPREID_BACKEND:-n/a}${IMAGE:+ ($IMAGE)}"
fi
