#!/usr/bin/env bash
#
# tracker-models.sh — the NanoTrack v2 model files for object tracking on kwanz-ws, fetched and never committed
#
# Usage:
#   ./tracker-models.sh install   # download any missing or mismatched file, check its SHA-256, rename into place
#   ./tracker-models.sh check     # print each file with its SHA-256 and OK/MISSING/MISMATCH (exit 1 unless all OK)
#
# Object tracking (INCIDENT_OBJECT_TRACKING_ENABLED) prefers OpenCV's NanoTrack when both .onnx files are in
# INCIDENT_OBJECT_TRACKING_MODEL_DIR and falls back to MIL otherwise. native-services.sh exports that variable
# as this tools dir and runs `install` before it starts vss-agent. The files have no licence file upstream, so
# they are fetched at deploy time, pinned by SHA-256, and kept out of git (.tools/ is gitignored).
#
# Environment:
#   VSS_TRACKER_MODELS_DIR       install dir (default <repo>/.tools/tracker-models)
#   VSS_TRACKER_MODELS_BASE_URL  download URL (default the pinned HonglinChu/SiamTrackers commit)
#   VSS_TRACKER_BACKBONE_SHA256  expected hash of the backbone file (default the pinned value)
#   VSS_TRACKER_HEAD_SHA256      expected hash of the head file (default the pinned value)

set -euo pipefail

TRACKER_MODELS_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TRACKER_MODELS_REPO_ROOT="$(cd "${TRACKER_MODELS_SCRIPT_DIR}/../../../../.." && pwd)"
VSS_TRACKER_MODELS_DIR="${VSS_TRACKER_MODELS_DIR:-${TRACKER_MODELS_REPO_ROOT}/.tools/tracker-models}"
VSS_TRACKER_MODELS_BASE_URL="${VSS_TRACKER_MODELS_BASE_URL:-https://raw.githubusercontent.com/HonglinChu/SiamTrackers/8fe6791fecf13b95e6f19770af8963a9fc98759b/NanoTrack/models/nanotrackv2}"
VSS_TRACKER_BACKBONE_SHA256="${VSS_TRACKER_BACKBONE_SHA256:-530bdd0cd00f19afab79a863e71ba71e3312395a5dc9151af675082bdaaa2fc4}"
VSS_TRACKER_HEAD_SHA256="${VSS_TRACKER_HEAD_SHA256:-0d8c0637be849f092cc7236cae02e55c8b9455ebe37ba50601d6115db4247cd9}"

# "<file name> <expected sha256>" per model file.
_tracker_models_list() {
  echo "nanotrack_backbone_sim.onnx ${VSS_TRACKER_BACKBONE_SHA256}"
  echo "nanotrack_head_sim.onnx ${VSS_TRACKER_HEAD_SHA256}"
}

tracker_models_dir() {
  echo "${VSS_TRACKER_MODELS_DIR}"
}

_tracker_models_sha256() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  else
    shasum -a 256 "$1" | awk '{print $1}'
  fi
}

# "<name> <sha256|-> OK|MISSING|MISMATCH" per file; returns 1 unless every file is OK.
tracker_models_check() {
  local name expected actual state status=0
  while read -r name expected; do
    actual="-"
    state="MISSING"
    if [ -f "${VSS_TRACKER_MODELS_DIR}/${name}" ]; then
      actual="$(_tracker_models_sha256 "${VSS_TRACKER_MODELS_DIR}/${name}")"
      state="OK"
      [ "${actual}" = "${expected}" ] || state="MISMATCH"
    fi
    echo "${name} ${actual} ${state}"
    [ "${state}" = "OK" ] || status=1
  done < <(_tracker_models_list)
  return "${status}"
}

tracker_models_install() {
  local name expected actual tmp status=0
  mkdir -p "${VSS_TRACKER_MODELS_DIR}"
  while read -r name expected; do
    if [ -f "${VSS_TRACKER_MODELS_DIR}/${name}" ] &&
      [ "$(_tracker_models_sha256 "${VSS_TRACKER_MODELS_DIR}/${name}")" = "${expected}" ]; then
      echo "  ${name} already installed"
      continue
    fi
    tmp="$(mktemp "${VSS_TRACKER_MODELS_DIR}/.${name}.XXXXXX")"
    if ! curl -fsSL --max-time 60 -o "${tmp}" "${VSS_TRACKER_MODELS_BASE_URL}/${name}"; then
      echo "WARNING: could not download ${name} from ${VSS_TRACKER_MODELS_BASE_URL}" >&2
      rm -f "${tmp}"
      status=1
      continue
    fi
    actual="$(_tracker_models_sha256 "${tmp}")"
    if [ "${actual}" != "${expected}" ]; then
      echo "WARNING: checksum mismatch for ${name} (expected '${expected}', got '${actual}')" >&2
      rm -f "${tmp}"
      status=1
      continue
    fi
    # One rename per file, so a running agent never sees a half-written file.
    chmod 664 "${tmp}"
    mv -f "${tmp}" "${VSS_TRACKER_MODELS_DIR}/${name}"
    echo "  Installed ${name}"
  done < <(_tracker_models_list)
  chmod g+rwx "${VSS_TRACKER_MODELS_DIR}" 2>/dev/null || true
  return "${status}"
}

if [ "${BASH_SOURCE[0]}" = "${0}" ]; then
  case "${1:-check}" in
    install) tracker_models_install ;;
    check) tracker_models_check ;;
    *)
      echo "Usage: $0 {install|check}" >&2
      exit 1
      ;;
  esac
fi
