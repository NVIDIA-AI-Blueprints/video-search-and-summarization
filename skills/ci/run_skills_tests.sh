#!/usr/bin/env bash
# Entrypoint for the metromind/ci-vss-oss `skills` sub-pipeline.
# Iterates each top-level skill directory and runs its tests if present.
# A skill without tests is skipped (logged), not failed — that's expected
# while the test suite is still being built out.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT/skills"

shopt -s nullglob
ran=0
skipped=0
for skill_dir in */; do
  skill="${skill_dir%/}"
  [ "$skill" = "ci" ] && continue
  if [ -d "$skill_dir/tests" ]; then
    echo "[skills/run_skills_tests] $skill: pytest $skill_dir/tests/"
    ( cd "$skill_dir" && python3 -m pytest tests/ -v )
    ran=$((ran+1))
  else
    echo "[skills/run_skills_tests] $skill: no tests/ directory — skipping"
    skipped=$((skipped+1))
  fi
done
echo "[skills/run_skills_tests] done — $ran ran, $skipped skipped"
