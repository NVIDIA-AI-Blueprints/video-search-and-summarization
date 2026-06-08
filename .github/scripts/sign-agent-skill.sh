#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Sign or verify a detached OpenSSF Model Signing / OMS signature for an
# agent skill directory.
#
# The NVIDIA nv-agent-root-cert.pem file is a verification trust anchor. It is
# not a private key and cannot sign a skill. Signing requires the private key
# for the signing certificate, plus the certificate chain that roots in the
# NVIDIA agent capabilities CA.

set -euo pipefail

NVIDIA_ROOT_CERT_URL="https://raw.githubusercontent.com/NVIDIA/skills/main/nv-agent-root-cert.pem"
MODEL_SIGNING_UVX_SPEC="model-signing==1.1.1"

usage() {
  cat <<'EOF'
Usage:
  .github/scripts/sign-agent-skill.sh verify SKILL_DIR [options]
  .github/scripts/sign-agent-skill.sh sign SKILL_DIR --private-key KEY --signing-certificate CERT --certificate-chain CHAIN [options]

Modes:
  verify  Verify SKILL_DIR/skill.oms.sig with the NVIDIA agent root cert.
  sign    Generate SKILL_DIR/skill.oms.sig using private signing material, then verify it.

Options:
  --signature PATH             Signature path. Defaults to SKILL_DIR/skill.oms.sig.
  --certificate-chain PATH     Certificate chain file. Required for sign. For verify, defaults to
                               downloading nv-agent-root-cert.pem from NVIDIA/skills.
                               May be repeated for sign.
  --private-key PATH           PEM private key for the signing certificate. Required for sign.
  --signing-certificate PATH   PEM signing certificate. Required for sign.
  --model-signing-bin PATH     model_signing executable. Defaults to MODEL_SIGNING_BIN,
                               then model_signing on PATH, then uvx with pinned
                               model-signing==1.1.1.
  -h, --help                   Show this help.

Examples:
  .github/scripts/sign-agent-skill.sh verify skills/vss-setup-behavior-analytics

  .github/scripts/sign-agent-skill.sh sign skills/my-skill \
    --private-key /secure/agent-skills-signing.key \
    --signing-certificate /secure/agent-skills-signing.crt \
    --certificate-chain /secure/agent-skills-chain.pem
EOF
}

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

require_file() {
  local label="$1"
  local path="$2"

  [[ -n "$path" ]] || die "missing $label"
  [[ -f "$path" ]] || die "$label does not exist: $path"
}

run_model_signing() {
  if [[ -n "${model_signing_bin:-}" ]]; then
    "$model_signing_bin" "$@"
  elif [[ -n "${MODEL_SIGNING_BIN:-}" ]]; then
    "$MODEL_SIGNING_BIN" "$@"
  elif command -v model_signing >/dev/null 2>&1; then
    model_signing "$@"
  elif command -v uvx >/dev/null 2>&1; then
    uvx --from "$MODEL_SIGNING_UVX_SPEC" model_signing "$@"
  else
    die "model_signing is not installed and uvx is unavailable"
  fi
}

download_root_cert() {
  local cert_path="$1"

  if command -v curl >/dev/null 2>&1; then
    curl -fsSL "$NVIDIA_ROOT_CERT_URL" -o "$cert_path"
  elif command -v wget >/dev/null 2>&1; then
    wget -qO "$cert_path" "$NVIDIA_ROOT_CERT_URL"
  else
    die "curl or wget is required to download nv-agent-root-cert.pem"
  fi
}

if [[ $# -lt 1 ]]; then
  usage
  exit 2
fi

mode="$1"
shift

case "$mode" in
  -h|--help)
    usage
    exit 0
    ;;
  sign|verify)
    ;;
  *)
    usage >&2
    die "unknown mode: $mode"
    ;;
esac

[[ $# -ge 1 ]] || die "missing SKILL_DIR"
skill_dir="$1"
shift

signature=""
private_key=""
signing_certificate=""
model_signing_bin=""
certificate_chains=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --signature)
      [[ $# -ge 2 ]] || die "--signature requires a value"
      signature="$2"
      shift 2
      ;;
    --private-key)
      [[ $# -ge 2 ]] || die "--private-key requires a value"
      private_key="$2"
      shift 2
      ;;
    --signing-certificate)
      [[ $# -ge 2 ]] || die "--signing-certificate requires a value"
      signing_certificate="$2"
      shift 2
      ;;
    --certificate-chain)
      [[ $# -ge 2 ]] || die "--certificate-chain requires a value"
      certificate_chains+=("$2")
      shift 2
      ;;
    --model-signing-bin)
      [[ $# -ge 2 ]] || die "--model-signing-bin requires a value"
      model_signing_bin="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "unknown option: $1"
      ;;
  esac
done

[[ -d "$skill_dir" ]] || die "SKILL_DIR does not exist: $skill_dir"
signature="${signature:-$skill_dir/skill.oms.sig}"

tmp_dir=""
cleanup() {
  if [[ -n "$tmp_dir" ]]; then
    rm -rf "$tmp_dir"
  fi
}
trap cleanup EXIT

if [[ "$mode" == "verify" && "${#certificate_chains[@]}" -eq 0 ]]; then
  tmp_dir="$(mktemp -d)"
  root_cert="$tmp_dir/nv-agent-root-cert.pem"
  download_root_cert "$root_cert"
  certificate_chains=("$root_cert")
fi

verify_signature() {
  local verify_args=(
    verify certificate "$skill_dir"
    --signature "$signature"
    --log_fingerprints
  )

  for chain in "${certificate_chains[@]}"; do
    verify_args+=(--certificate_chain "$chain")
  done

  run_model_signing "${verify_args[@]}"
}

if [[ "$mode" == "verify" ]]; then
  require_file "signature" "$signature"
  for chain in "${certificate_chains[@]}"; do
    require_file "certificate chain" "$chain"
  done
  verify_signature
  exit 0
fi

require_file "private key" "$private_key"
require_file "signing certificate" "$signing_certificate"

if [[ "${#certificate_chains[@]}" -eq 0 ]]; then
  die "sign mode requires --certificate-chain. nv-agent-root-cert.pem can verify the result, but it cannot sign."
fi
for chain in "${certificate_chains[@]}"; do
  require_file "certificate chain" "$chain"
done

sign_args=(
  sign certificate "$skill_dir"
  --signature "$signature"
  --private_key "$private_key"
  --signing_certificate "$signing_certificate"
)

for chain in "${certificate_chains[@]}"; do
  sign_args+=(--certificate_chain "$chain")
done

run_model_signing "${sign_args[@]}"
verify_signature
