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

# Skill: Deploy VIOS Stack

Deploy VIOS and NVStreamer services using the one-click Docker Compose script.

---

## Prerequisites

Run these checks before deploying. Stop immediately if any fail — do not attempt the deployment script.

```bash
# 1. Docker daemon accessible (user must be in docker group)
docker ps
# If this fails with permission denied → STOP. Tell the user:
#   "Add your user to the docker group and re-login:
#    sudo usermod -aG docker $USER && newgrp docker"

# 2. Docker and Compose versions
docker --version && docker compose version

# 3. NVIDIA container runtime
docker info 2>/dev/null | grep -i nvidia

# 4. Ports free
ss -tlnp | grep -E "30888|31000|31001|31002|31003|31004"
# Expected: no output (ports are free)
```

If `docker ps` fails, stop and give the user the docker group command — do not proceed further.

---

## Step 1 — Determine deployment mode

Infer the target from user context or the invoking agent's workflow. Do not prompt unless genuinely ambiguous.

| Mode | Target flag | Command |
|---|---|---|
| Stream-processor (default) | — | nvstreamer step + `deploy --auto --force` (two steps — see Step 2) |
| NVStreamer only | `nvstreamer` | `deploy --target nvstreamer --auto --force` |
| Full stack (all microservices + NVStreamer) | `all` | `deploy --target all --auto --force` |
| Scaled microservices | `scaled` | nvstreamer step + `deploy --target scaled --auto --force` (two steps) |
| With MinIO | — | add `--with-minio` |
| With monitoring | — | add `--with-monitoring` |

---

## Step 1b — Detect image tags (MANDATORY — do this before every deploy)

`compose.env` contains pinned versioned tags. `build.sh` always produces `latest` tagged images and does NOT update `compose.env`. You must determine the correct tag flags — otherwise the deploy will silently use stale registry images.

**If this deploy follows a build in the current session:** use the BUILD_TAG established in `skills/build/build-containers.md` Step 6 directly. Pass `--all-tag <BUILD_TAG> --nvstreamer-tag <BUILD_TAG>` to the deploy command. Skip the probe below.

**If deploying standalone (no build done in this session):** probe for recent local images:

```bash
# Check if :latest images were built within the last 24 hours
docker images --format "{{.Repository}}:{{.Tag}}\t{{.CreatedAt}}" \
  | grep -E "vst-streamprocessing:latest|nvstreamer:latest"
```

Then decide:

| Condition | Action |
|---|---|
| `nvstreamer:latest` or `vst-streamprocessing:latest` created within last 24 hours | **Ask the user:** "I found a locally built `latest` image (built at `<timestamp>`). Do you want to deploy using that instead of the pinned registry tag?" |
| User says yes | Use `--nvstreamer-tag latest` / `--all-tag latest` for whichever images are recent |
| User says no, or no recent `:latest` found | Proceed without tag flags — compose.env pinned tags will be used |

Do not add `--pull-always` — that pulls from the registry and discards the local build.

---

## Step 2 — Run the deployment script

NVStreamer must always be deployed before the stream-processor. Follow the sequences below exactly — do NOT add an extra nvstreamer step before `--target all`; that target already deploys NVStreamer internally and adding another step would deploy it twice.

**Stream-processor (default) — explicit two steps (script does NOT deploy NVStreamer for this target):**
```bash
cd <PROJECT_ROOT>/deployment
# add --nvstreamer-tag <BUILD_TAG> if deploying a locally built NVStreamer (see Step 1b)
python3 oneclick_dc_deployment_for_dev.py deploy --target nvstreamer --auto --force

# add --all-tag <BUILD_TAG> if deploying locally built VIOS images (see Step 1b)
python3 oneclick_dc_deployment_for_dev.py deploy --auto --force
```

**Full stack (`--target all`) — single command (script deploys NVStreamer internally, do not add an nvstreamer step before this):**
```bash
# add --all-tag <BUILD_TAG> --nvstreamer-tag <BUILD_TAG> if deploying locally built images (see Step 1b)
python3 oneclick_dc_deployment_for_dev.py deploy --target all --auto --force
```

**Scaled — two steps:**
```bash
# add --nvstreamer-tag <BUILD_TAG> if deploying a locally built NVStreamer (see Step 1b)
python3 oneclick_dc_deployment_for_dev.py deploy --target nvstreamer --auto --force

# add --all-tag <BUILD_TAG> if deploying locally built VIOS images (see Step 1b)
python3 oneclick_dc_deployment_for_dev.py deploy --target scaled --auto --force
```

**NVStreamer only:**
```bash
# add --nvstreamer-tag <BUILD_TAG> if deploying a locally built NVStreamer (see Step 1b)
python3 oneclick_dc_deployment_for_dev.py deploy --target nvstreamer --auto --force
```

Additional flags for the **VIOS deploy command only** (do not append to the nvstreamer step):
```bash
--with-minio        # MinIO storage
--with-monitoring   # Grafana/Prometheus
```

---

## Step 3 — Resolve BASE_URL from deployment output

The script prints the detected host IP. Capture it and set BASE_URL:
```
BASE_URL = http://<detected-host-ip>:30888
```

If the script output does not show the IP, detect it:
```bash
hostname -I | awk '{print $1}'
```

Pass BASE_URL to any subsequent skill (tests, UI browsing).

---

## Step 4 — Verify deployment health

```bash
# Check all containers are running
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" | grep -E "vios|nvstreamer|redis"

# Check VIOS API health (health endpoint is localhost-only, not accessible via external IP)
curl -s -o /dev/null -w "%{http_code}" http://localhost:30888/api/health
# Expected: 200
```

If health check returns non-200 or containers show `Restarting` status, go to `guides/troubleshooting.md`.

---

## Step 4b — Sync config.json with resolved BASE_URL

Update `test/bdd_tests/config.json` so the MCP URL derivation uses the correct host. The file defaults to `localhost:30888` which causes all MCP gateway tests to fail.

```bash
python3 - <<EOF
import json, sys
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

---

## Step 5 — Report outcome

Report to the user:
- Which containers are running and their status
- Resolved BASE_URL
- VIOS UI: `<BASE_URL>/vios/#/dashboard`
- Any warnings from the deployment script output

---

## Image Tag Flags Reference

| Flag | Controls |
|---|---|
| `--all-tag <TAG>` | All service images |
| `--sensor-tag <TAG>` | Sensor module |
| `--rtsp-tag <TAG>` | RTSP server module |
| `--recorder-tag <TAG>` | Recorder module |
| `--livestream-tag <TAG>` | Livestream module |
| `--nvstreamer-tag <TAG>` | NVStreamer |

If no tag flags are given, the script uses whatever is configured in the compose files.
