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

# Troubleshooting Guide

Common failure patterns during deployment, testing, and UI inspection.

---

## Deployment Issues

### `docker ps` fails with permission denied

The user is not in the `docker` group. The deploy script no longer uses `sudo docker` — it requires the user to have direct Docker access instead.

Give the user this command:
```bash
sudo usermod -aG docker $USER && newgrp docker
```

After running it, open a new shell (or run `newgrp docker`) and retry. No sudo is needed for the deployment script itself.

---

### Containers in Restarting loop

```bash
docker ps --format "{{.Names}}\t{{.Status}}" | grep Restarting
docker logs <container-name> 2>&1 | tail -50
```

Common causes:
- **Missing config file**: The container cannot find its config at the mounted path. Check `--config-path` argument.
- **Port conflict**: Another process is on port 30888. `ss -tlnp | grep 30888`
- **GPU not available**: NvEnc/NvDec containers require NVIDIA runtime. `nvidia-smi` should succeed on the host.
- **Wrong image tag**: Image pulled does not match the expected API version. Check `--all-tag` or per-service tag flags.

### Health check returns non-200

```bash
# Check if VIOS API container is up
docker logs vios-sensor 2>&1 | grep -E "started|listening|error|fatal" | tail -30

# Check if the port is actually bound
ss -tlnp | grep 30888
```

VIOS logs a "Server started" message when ready. If absent, startup failed.

### "Cannot connect to Docker daemon"

```bash
sudo systemctl start docker
# Or add user to docker group:
sudo usermod -aG docker $USER && newgrp docker
```

### Deployment script prompts hang in --auto mode

The script falls back to prompts when it cannot auto-detect values (e.g., multiple network interfaces). Pass `--port <IP>` explicitly to override host IP detection.

---

## Stale Database — Sensor Failures (503/504)

When a sensor returns 503 (No Cluster) on recorder/livestream endpoints or 504 (Upstream Timeout) on picture/stream endpoints, the sensor entry in PostgreSQL may be stale — registered in a previous deployment whose backing NVStreamer video file no longer exists on disk.

### PostgreSQL connection

```
Container : centralizedb
DB        : nvcentralizedb
User      : vios
Password  : changeme
```

```bash
docker exec centralizedb psql -U vios -d nvcentralizedb -c "<query>"
```

### Relevant tables

**`sensor_details`** — one row per registered sensor

| Column | Meaning |
|---|---|
| `sensor_id` | Primary identifier (matches stream ID in tests) |
| `name` | Display name |
| `url` | Raw camera URL (empty for NVStreamer-backed sensors) |
| `sensor_status` | 0 = offline, 1 = online |

**`sensor_streams`** — stream details per sensor (one-to-one for NVStreamer sensors)

| Column | Meaning |
|---|---|
| `sensor_id` | FK → sensor_details |
| `stream_id` | Stream identifier used in API paths |
| `stream_live_url` | RTSP URL served by NVStreamer (e.g. `rtsp://<host>:31554/nvstream/.../file.mp4`) |
| `stream_proxy_url` | Internal proxy RTSP URL (`rtsp://<host>:30554/live/<id>`) |
| `stream_replay_url` | Replay RTSP URL |
| `stream_status` | 4 = healthy/proxied |
| `stream_resolution`, `stream_framerate`, `stream_encoding` | Stream metadata |

### Diagnostic queries

```bash
# List all sensors with their live URL and status
docker exec centralizedb psql -U vios -d nvcentralizedb -c \
  "SELECT sd.sensor_id, sd.sensor_status, ss.stream_live_url, ss.stream_status
   FROM sensor_details sd
   LEFT JOIN sensor_streams ss USING (sensor_id)
   ORDER BY sd.sensor_id;"

# Find sensors whose backing video file is likely missing
# (stream_live_url points to an NVStreamer path — verify the file exists on the host)
docker exec centralizedb psql -U vios -d nvcentralizedb -c \
  "SELECT sensor_id, stream_live_url FROM sensor_streams
   WHERE stream_live_url LIKE '%nvstream%';"
```

### Verifying the backing file exists

NVStreamer RTSP URLs follow the pattern:
```
rtsp://<host>:<rtsp_port>/nvstream<absolute_path_on_host>
```

RTSP port → NVStreamer instance mapping (from `compose.env`):
| RTSP port | NVStreamer | Host volume |
|---|---|---|
| 31554 | nvstreamer-1 | `/tmp/nvstreamer_auto_deploy/nvstreamer-1/` |
| 31564 | nvstreamer-2 | `/tmp/nvstreamer_auto_deploy/nvstreamer-2/` |
| 31574 | nvstreamer-3 | `/tmp/nvstreamer_auto_deploy/nvstreamer-3/` |
| 31584 | nvstreamer-4 | `/tmp/nvstreamer_auto_deploy/nvstreamer-4/` |
| 31594 | nvstreamer-5 | `/tmp/nvstreamer_auto_deploy/nvstreamer-5/` |

```bash
# Extract the filename from the URL and check the host volume
# e.g. for rtsp://10.x.x.x:31554/nvstream/home/vios/.../ITS_003_nvs2_6.mp4
ls /tmp/nvstreamer_auto_deploy/nvstreamer-1/ | grep ITS_003_nvs2_6
```

### Resolution

**Option A — remove the stale sensor from the DB:**
```bash
docker exec centralizedb psql -U vios -d nvcentralizedb -c \
  "DELETE FROM sensor_streams WHERE sensor_id = '<id>';
   DELETE FROM sensor_details WHERE sensor_id = '<id>';"
```
Then restart sensor-ms so it reloads: `docker restart sensor-ms`.

**Option B — stage a backing file so the stream resolves:**
```bash
cp /tmp/nvstreamer_auto_deploy/nvstreamer-1/<existing_file>.mp4 \
   /tmp/nvstreamer_auto_deploy/nvstreamer-1/<missing_file>.mp4
```
NVStreamer picks up new files without a restart.

### SDR `wl_d is None` — what it means

If SDR logs show `wl_d is None` for a sensor event, SDR received a `camera_add`/`camera_proxy` Redis event but had no routing entry for that sensor. This is a downstream symptom of the stream never becoming healthy — it does not need independent fixing once the root cause (missing file or stale DB row) is resolved.

---

## BDD Test Issues

### MCP gateway tests all fail with `ConnectError` to `localhost:8001`

`test/bdd_tests/config.json` has `api.base_url` pointing to `localhost:30888`. The MCP URL is derived from this at fixture setup, so it resolves to `localhost:8001` instead of the real host.

The deploy and test skills now auto-sync this file. If it still happens, run manually:
```bash
python3 - <<EOF
import json
with open("test/bdd_tests/config.json") as f:
    config = json.load(f)
config["api"]["base_url"] = "<BASE_URL>"
with open("test/bdd_tests/config.json", "w") as f:
    json.dump(config, f, indent=2)
EOF
```

---

### All tests fail with `ConnectionRefusedError`

VIOS is not reachable from the test runner.
1. Confirm VIOS is up: `curl http://localhost:30888/api/health` (health endpoint is localhost-only)
2. Verify `config.json` → `api.base_url` matches the running host (the agent auto-syncs this, but check if it was skipped).
3. Confirm no firewall blocking port 30888: `telnet <HOST> 30888`

### Tests fail with `401 Unauthorized`

The test user credentials are missing or expired.
- Check `test/bdd_tests/config.json` for auth configuration.
- Re-authenticate if the VIOS instance was restarted with a fresh database.

### Tests fail with `404 Not Found` on valid endpoints

The required VIOS module was not built or deployed. For example, RTSP tests need the `rtspserver` module container.
- Check which containers are running: `docker ps --format "{{.Names}}"`
- Ensure the module is deployed: re-run with the correct service scope.

### Fixture `ERROR` (not FAILED) on many tests

Usually leftover state from a previous failed run.
```bash
# Clean test state by restarting VIOS containers (without --fresh-start to preserve data)
cd "$(git rev-parse --show-toplevel)/deployment"
python3 oneclick_dc_deployment_for_dev.py stop && \
python3 oneclick_dc_deployment_for_dev.py deploy --auto --force
```

### Poetry command not found

```bash
pip install poetry
# or
curl -sSL https://install.python-poetry.org | python3 -
export PATH="$HOME/.local/bin:$PATH"
```

### `setup-system-deps` fails

Requires sudo. Run manually:
```bash
sudo apt-get install -y ffmpeg mediainfo jpeginfo
```

---

## UI Issues

### Page is blank after navigation

1. Check `browser_console_messages` for JS errors.
2. Check `browser_network_requests` — look for failed `/api/` calls (4xx/5xx).
3. Confirm the base URL in `webroot/config.js` matches the running VIOS host.

### Login redirect loop

Session cookie issue. Clear browser state:
```
Use browser_evaluate: document.cookie = ''; location.reload();
```
Then re-login.

### Video player shows black screen

- Stream may not be active. Verify a live stream source is configured.
- Check VIOS logs for pipeline errors: `docker logs vios-livestream 2>&1 | grep -i "error\|pipeline\|gst"`
- The player may need a few seconds to buffer — use `browser_wait_for` (10–15s) before taking a screenshot.

---

## Log Locations

| Component | How to access |
|---|---|
| VIOS sensor module | `docker logs vios-sensor` |
| VIOS recorder | `docker logs vios-recorder` |
| VIOS livestream | `docker logs vios-livestream` |
| VIOS RTSP server | `docker logs vios-rtspserver` |
| NVStreamer | `docker logs nvstreamer-1` (through `nvstreamer-5`) |
| Redis | `docker logs vios-redis` |
| BDD test output | `test/bdd_tests/reports/report.html` |
