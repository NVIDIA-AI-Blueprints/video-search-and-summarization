# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# SQA Agent — Master Guide

## Overview

This agent handles end-to-end quality assurance for VIOS (Video IO & Storage): deploying the stack, running BDD tests, inspecting test results, and verifying behavior through the web UI.

**Project root:** the directory containing `.claude/sqa/` (do not hardcode an absolute path — resolve it at runtime with `git rev-parse --show-toplevel` or by locating `build.sh`).

---

## Resolving BASE_URL

Every skill that talks to VIOS needs a `BASE_URL`. Resolve it once at the start of a task, in priority order:

1. URL passed explicitly by the user (e.g. `/sqa test http://10.24.217.99:30888`)
2. `VIOS_BASE_URL` environment variable: `echo $VIOS_BASE_URL`
3. Probe localhost: `curl -s -o /dev/null -w "%{http_code}" http://localhost:30888/api/health` — use `http://localhost:30888` if it returns `200`
4. Read the host IP that the last deployment printed, or check `deployment/**/docker-compose*.y*ml` for the configured port
5. Ask the user

Once resolved, use `BASE_URL` consistently — never mix localhost and remote host within the same task.

> **Note:** The health API (`/api/health`) only responds on `localhost:30888` — it is not accessible via the external host IP. Always use `http://localhost:30888/api/health` for health checks regardless of BASE_URL.

---

## Capabilities

| Capability | Skill file |
|---|---|
| Build VIOS containers from source | `skills/build/build-containers.md` |
| Deploy VIOS stack (Docker Compose) | `skills/deployment/deploy.md` |
| Stop deployed services | `skills/deployment/stop.md` |
| Clean test state before a run | `skills/testing/cleanup.md` |
| Run BDD tests | `skills/testing/run-bdd-tests.md` |
| Interpret test results | `skills/testing/check-results.md` |
| Browse and interact with web UI | `skills/ui/browse-dashboard.md` |

---

## Arguments

| Argument | Effect |
|---|---|
| (none — default) | build → deploy (nvstreamer + streamprocessor) → run all tests + validate UI |
| `--skip-build` | skip build, go straight to deploy → test |
| `test <module>` | build → deploy → run named module's tests only |
| `sanity` / `sanity tests` | build → deploy → run entire BDD suite |
| `results` | interpret last test run results — no build/deploy |
| `ui <BASE_URL>` | validate web UI only — no build/deploy/tests |

**Natural-language intent mapping** — when the user's request combines a deploy phrase with a test phrase, always run BDD tests. Never treat "deploy" in isolation when a test intent is also present:

| If the request contains … | Treat as |
|---|---|
| "deploy" + "sanity" / "sanity test(s)" | `sanity` — build → deploy → full BDD suite |
| "deploy" + "run tests" / "run bdd" / "test it" / "and test" | `sanity` |
| "deploy" + "test `<module>`" | `test <module>` |
| "deploy" + "smoke" / "quick test" | `test sensor_management` (lightest suite) |
| "deploy only" / "just deploy" / "deploy without tests" | route to vios-deployment agent — no tests |

---

## Workflow

Execute these steps in order. Deviate only when the argument table above says otherwise.

1. Resolve PROJECT_ROOT: `git rev-parse --show-toplevel`
2. Unless `--skip-build` or `results`/`ui` argument: build containers via `skills/build/build-containers.md`
3. Unless `results` or `ui` argument:
   - Stop any existing stack
   - NVStreamer must always be deployed before the stream-processor:
     - Default (stream-processor): `deploy --target nvstreamer --auto --force`, then `deploy --auto --force`
     - Full stack: `deploy --target all --auto --force` — NVStreamer-first handled internally
     - Scaled: `deploy --target nvstreamer --auto --force`, then `deploy --target scaled --auto --force`
   - If a build was done in step 2, pass `--all-tag <BUILD_TAG> --nvstreamer-tag <BUILD_TAG>` to the deploy commands (BUILD_TAG from `skills/build/build-containers.md` Step 6). See `skills/deployment/deploy.md` Step 1b for the full decision.
   - See `skills/deployment/deploy.md` for full options
4. Sync `test/bdd_tests/config.json` → `api.base_url` with resolved BASE_URL (see Step 4b in `skills/deployment/deploy.md`)
5. Execute the appropriate skill:
   - Default: `skills/testing/run-bdd-tests.md` → `skills/testing/check-results.md` → `skills/ui/browse-dashboard.md`
   - `sanity` / `sanity tests`: `skills/testing/run-bdd-tests.md` (full suite) → `skills/testing/check-results.md`
   - `test <module>`: `skills/testing/run-bdd-tests.md` (module only) → `skills/testing/check-results.md`
   - `results`: `skills/testing/check-results.md` only
   - `ui`: `skills/ui/browse-dashboard.md` only
   - Prior failures or dirty state: `skills/testing/cleanup.md` first
6. Report: pass/fail counts, failed test names, error snippets, screenshots for UI issues

---

## Request Routing

| User intent / argument | Skills |
|---|---|
| (none — default) | build → deploy → `run-bdd-tests.md` → `check-results.md` → `browse-dashboard.md` |
| `--skip-build` | deploy → `run-bdd-tests.md` → `check-results.md` → `browse-dashboard.md` |
| `test <module>` | build → deploy → `run-bdd-tests.md` (module only) → `check-results.md` |
| `sanity` / `sanity tests` | build → deploy → `run-bdd-tests.md` (full suite) → `check-results.md` |
| "deploy" + any test intent ("sanity", "test it", "run tests", "run bdd", "smoke") | treat as `sanity` — always include BDD tests |
| `results` | `check-results.md` |
| `ui <BASE_URL>` | `browse-dashboard.md` |
| Scaled | build → deploy nvstreamer → deploy scaled → `run-bdd-tests.md` → `check-results.md` |
| "clean up", "reset state" | `cleanup.md` before test run |
| Deployment errors / test failures | `guides/troubleshooting.md` |
| Unsure which tests to run | `guides/decision-trees.md` |

---

## Key Paths

```
deployment/
  oneclick_dc_deployment_for_dev.py   # primary deploy script
  1click_README.md                    # deployment reference

test/bdd_tests/
  setup.sh                            # one-time environment setup
  tests/                              # pytest-bdd step implementations
    unit_tests/                       # per-module API tests
    file_upload/, file_download/,
    picture/, webrtc/, perf/          # integration test categories
  features/                           # Gherkin .feature files
  reports/                            # generated after every test run
    report.html
    junit.xml
    test_results.csv
    unit_tests/*.csv

webroot/index.html                    # UI entry point
```

---

## VIOS Service Endpoints

| Service | URL |
|---|---|
| VIOS API + UI | `BASE_URL` (default port 30888) |
| VIOS Dashboard | `BASE_URL/vios/#/dashboard` |
| NVStreamer (0–4) | `http://<HOST>:31000–31004/#/dashboard` |
| Grafana (if enabled) | `http://<HOST>:3000` |
| MinIO Console (if enabled) | `http://<HOST>:9001` |

---

## Constraints

- **"tot" / "top of tree":** If the user says "tot", "top of tree", or any phrase implying they want the latest code (e.g. "build from tot", "run sanity on tot"), run `git pull` in PROJECT_ROOT before starting the build.
- **Build before deploying by default** — run `skills/build/build-containers.md` first to ensure the deployment uses the latest source. Skip build only when explicitly requested (`--skip-build`) or when no deploy is needed (`results`, `ui` arguments).
- Do not run `--fresh-start` or destructive flags without explicit user approval.
- Do not modify `deployment/**/docker-compose*.y*ml` without instruction.
- **Always** sync `test/bdd_tests/config.json` → `api.base_url` to match the resolved BASE_URL before running tests. The file defaults to `localhost:30888` which causes MCP tests to derive the wrong URL. This sync is automatic and requires no user approval.
- **Always stop and redeploy before running tests** — never run tests against an already-running stack. A fresh deployment avoids stale database state from previous runs.
- Prefer targeted test runs over full suite unless full regression is requested.
- Always generate `reports/junit.xml` and `reports/report.html` — never run tests without report flags.
