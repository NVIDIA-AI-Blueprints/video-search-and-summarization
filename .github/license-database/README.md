<!--
  SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
  SPDX-License-Identifier: Apache-2.0
-->

# License database

This directory is the source of truth for the **License Check** CI job
(see `.github/workflows/ci.yml`). On every push to `main`, `develop`,
and PR branches, CI scans the runtime dependencies of:

- **Agent** (`services/agent`, Python via `uv`/PyPI)
- **UI** (`services/ui`, npm production deps from the workspace tree)

and verifies each dependency against the files below. The job hard-fails
when a dependency uses a copyleft license (GPL, AGPL, LGPL without OSRB
sign-off, SSPL, BUSL, etc.) or when a brand-new dependency appears that
no one has reviewed yet.

The companion **License Headers** job
(`.github/scripts/check_license_headers.py`) verifies that every
NVIDIA-authored source file (`.py`, `.sh`, `.yml`, `.toml`, `.ts`,
`.tsx`, `.js`, `Dockerfile`, …) starts with the standard NVIDIA SPDX +
Apache-2.0 header. Vendored code is excluded; see the script for the
full exclusion list.

## Files

| File | Purpose |
| --- | --- |
| `policy.yaml` | SPDX patterns we accept (`allowed`), reject (`denied`), or require OSRB review for (`review_required`). Also lists workspace-internal packages to ignore. |
| `permissive-licenses.csv` | Approved packages with permissive licenses (MIT, BSD, Apache-2.0, ISC, MPL-2.0, …). Add new entries here when CI flags a new dep with a permissive license. |
| `non-permissive-licenses.csv` | Approved packages with non-permissive (LGPL etc.) licenses that have an OSRB exception on file. Adding to this list **requires** OSRB sign-off documented in the `Comments` column. |
| `license-overrides.csv` | Manual license overrides for packages whose license string is misdetected (e.g. PyPI returns the full license text instead of an SPDX ID). |

The schemas mirror the OSRB CSVs used in the upstream
`deep-search` repository, with one extra column (`Ecosystem`) so a single
file can describe both Python and npm packages.

## When CI fails: what to do

1. Open the failed job and download the artifact named
   `license-check-output-<ecosystem>`. It contains:
   - `<ecosystem>-license-report.csv` — full classification of every dep.
   - `<ecosystem>-denied.csv` — packages that triggered a hard fail (copyleft).
   - `<ecosystem>-new.csv` — brand-new packages, never seen in the database.
   - `<ecosystem>-unknown.csv` — packages whose license could not be determined.
2. For each row in `<ecosystem>-denied.csv`:
   - **Hard requirement**: avoid the dependency if at all possible. If
     replacement is not feasible, file an OSRB request and (after approval)
     add an entry to `non-permissive-licenses.csv` documenting the OSRB
     case ID in the `Comments` column.
3. For each row in `<ecosystem>-new.csv`:
   - Confirm the detected license is correct (cross-check the project's
     source repo).
   - If correct and permissive → append to `permissive-licenses.csv`.
   - If non-permissive → file OSRB and append to
     `non-permissive-licenses.csv` once approved.
4. For each row in `<ecosystem>-unknown.csv`:
   - The detector could not classify the license. Inspect the source
     repository, then add an entry to `license-overrides.csv` with the
     correct SPDX ID and a `License URL` pointing at the actual
     `LICENSE` file.

Once the database is updated, re-run CI. The check is deterministic.

## Running locally

The CI calls `pip-licenses` and `license-checker-rseidelsohn` to produce
JSON dependency reports, then hands them to the script:

```bash
# Python
cd services/agent
uv sync --group dev --frozen
uv run --with pip-licenses pip-licenses --format=json --with-urls > /tmp/pip.json
python3 ../../.github/scripts/check_licenses.py \
  --ecosystem python --input /tmp/pip.json \
  --output-dir /tmp/license-check

# UI
cd services/ui
npm ci
npx --yes license-checker-rseidelsohn --json --production \
  --excludePrivatePackages > /tmp/ui.json
python3 ../../.github/scripts/check_licenses.py \
  --ecosystem npm --input /tmp/ui.json \
  --output-dir /tmp/license-check
```

Pass `--no-strict` to get the report without failing on issues, useful
during initial triage.
