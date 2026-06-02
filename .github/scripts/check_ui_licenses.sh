#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Mirrors CI Job 10 (license-check-ui). Runs license-checker against the
# resolved npm tree and fails if any package's license is not on the allow list.
# Used by pre-commit so devs see the same failure locally that CI surfaces.

set -euo pipefail

repo_root=$(git rev-parse --show-toplevel)
ui_worktree=$(mktemp -d)
trap 'rm -rf "$ui_worktree"' EXIT

tar --exclude=node_modules --exclude=.next --exclude=dist --exclude=build \
  -cf - -C "$repo_root/services/ui" . \
  | tar -xf - -C "$ui_worktree"

cd "$ui_worktree"

npm ci --silent
npx --yes license-checker --json --excludePrivatePackages > "$ui_worktree/licenses.json"

node <<'EOF'
const licenses = require(process.cwd() + "/licenses.json");
const allowed = new Set([
  "MIT","MIT-0","Apache-2.0","BSD-2-Clause","BSD-3-Clause","ISC","0BSD",
  "Unlicense","CC0-1.0","CC-BY-4.0","CC-BY-3.0","Python-2.0",
  "BlueOak-1.0.0","MPL-2.0",
]);
const excludePrefixes = ["@img/sharp-libvips", "@aiqtoolkit-ui/common"];
const failures = [];
for (const [pkg, info] of Object.entries(licenses)) {
  const name = pkg.replace(/@[^@]+$/, "");
  if (excludePrefixes.some(p => name.startsWith(p))) continue;
  const lic = String(info.licenses || "UNKNOWN");
  const parts = lic.replace(/[()]/g, "").split(/ OR | AND /);
  const ok = parts.some(p => allowed.has(p.trim().replace(/\*$/, "")));
  if (!ok) failures.push(pkg + ": " + lic);
}
if (failures.length) {
  console.error("ERROR: " + failures.length + " package(s) with disallowed licenses:");
  failures.forEach(f => console.error("  " + f));
  process.exit(1);
}
console.log("OK: " + Object.keys(licenses).length + " packages checked.");
EOF
