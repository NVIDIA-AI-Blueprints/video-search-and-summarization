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

# Skill: Run BDD Tests

Run pytest-bdd test suites against a running VIOS instance.

---

## Prerequisites

1. **Build containers first** — run `skills/build/build-containers.md` to build Docker images from source before deploying. This ensures tests run against the latest code.

2. **Resolve BASE_URL** — follow the BASE_URL resolution steps in `AGENT.md`. All health checks and test commands use this URL.

3. **Always do a fresh deployment before running tests** — stop any running stack first, then redeploy. This avoids stale database state from previous runs.

   ```bash
   # Stop existing stack
   cd <PROJECT_ROOT>/deployment
   python3 oneclick_dc_deployment_for_dev.py stop

   # Redeploy fresh
   python3 oneclick_dc_deployment_for_dev.py deploy --auto --force
   ```

   After redeployment, wait for VIOS to be healthy:
   `curl -s -o /dev/null -w "%{http_code}" http://localhost:30888/api/health`
   - The health endpoint is localhost-only — do not substitute BASE_URL here
   - Retry until `200` before continuing (poll every 5s, timeout after 120s)

4. **Sync config.json with BASE_URL** — the file defaults to `localhost:30888` which causes MCP gateway tests to derive the wrong URL. Always update it before running tests:

```bash
python3 - <<EOF
import json
config_path = "<PROJECT_ROOT>/test/bdd_tests/config.json"
base_url = "<BASE_URL>"
with open(config_path) as f:
    config = json.load(f)
config["api"]["base_url"] = base_url
with open(config_path, "w") as f:
    json.dump(config, f, indent=2)
print(f"config.json updated: api.base_url = {base_url}")
EOF
```

5. **Poetry environment** — if not set up, run `./setup.sh` first (one-time).

---

## Step 1 — Identify which tests to run

Consult `guides/decision-trees.md` if unsure. Common selections:

| Scope | Command suffix | Use when |
|---|---|---|
| All tests | `tests/` | Full regression |
| All unit tests | `tests/unit_tests/` | API regression across all modules |
| Sensor management | `tests/unit_tests/sensor_management/` | Sensor API changes |
| Storage management | `tests/unit_tests/storage_management/` | Storage/recording changes |
| Live stream | `tests/unit_tests/live_stream/` | Streaming pipeline changes |
| Replay stream | `tests/unit_tests/replay_stream/` | Playback/VOD changes |
| RTSP proxy | `tests/unit_tests/rtsp_proxy/` | RTSP proxy changes |
| Stream recorder | `tests/unit_tests/stream_recorder/` | Recording changes |
| MCP gateway | `tests/unit_tests/mcp_gateway/` | MCP integration changes |
| File upload | `tests/file_upload/` | Upload API changes |
| File download | `tests/file_download/` | Download API changes |
| WebRTC | `tests/webrtc/` | WebRTC stream changes |
| Performance | `tests/perf/` | Latency/throughput validation |

---

## Step 2 — Run tests

Always include `--junitxml` and `--html` flags so `check-results.md` can parse them. Substitute `<SCOPE>` and `<BASE_URL>`.

```bash
cd <PROJECT_ROOT>/test/bdd_tests

# Standard targeted run
poetry run pytest <SCOPE> -v \
  --base-url <BASE_URL> \
  --junitxml=reports/junit.xml \
  --html=reports/report.html \
  --self-contained-html

# Full regression with parallel execution
poetry run pytest tests/ -n auto -v \
  --base-url <BASE_URL> \
  --junitxml=reports/junit.xml \
  --html=reports/report.html \
  --self-contained-html
```

Examples:
```bash
# Sensor management only
poetry run pytest tests/unit_tests/sensor_management/ -v \
  --base-url http://localhost:30888 \
  --junitxml=reports/junit.xml \
  --html=reports/report.html \
  --self-contained-html

# All unit tests against a remote host
poetry run pytest tests/unit_tests/ -v \
  --base-url http://10.24.217.99:30888 \
  --junitxml=reports/junit.xml \
  --html=reports/report.html \
  --self-contained-html
```

---

## Step 3 — Monitor execution

Watch for:
- `PASSED` / `FAILED` / `ERROR` per test
- `E` marks indicate setup/teardown errors (often connectivity or leftover state — run `skills/testing/cleanup.md` before retrying)
- `F` marks indicate assertion failures (functional bugs)

If many tests fail immediately with connection errors → BASE_URL is wrong or VIOS is not reachable. Re-check the health endpoint before assuming functional failures.

---

## Step 4 — Collect results

Reports are always written to `test/bdd_tests/reports/`. Proceed to `skills/testing/check-results.md`.

---

## Environment Setup (first-time only)

```bash
cd <PROJECT_ROOT>/test/bdd_tests
./setup.sh
# Or manually:
pip install poetry
poetry install
poetry run setup-system-deps   # installs ffmpeg, mediainfo, jpeginfo
```
