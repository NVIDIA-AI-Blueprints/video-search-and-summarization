#!/usr/bin/env bash
# Entrypoint for the metromind/ci-vss-oss `dev` sub-pipeline.
# Runs the test suite for everything under dev/. Build is NOT performed —
# tests should not depend on freshly built agent/UI containers; if they
# need running services, they should rely on upstream-pinned images.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"

# Drop a single line of provenance so traces are scannable.
echo "[dev/run_dev_tests] running pytest dev/tests/"

python3 -m pytest dev/tests/ -v
