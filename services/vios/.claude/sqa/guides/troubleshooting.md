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

### Deployment script prompts hang in non-interactive mode

The new `oneclick_dc_deployment.py` is non-interactive by default for its own prompts. Two remaining places can still ask for input:

1. **Multiple network interfaces detected** — pass `--host <IP>` explicitly to override host IP detection.
2. **Sudo password prompt during sysctl tuning** — the script applies host network buffer tuning (`net.core.rmem_max`, etc.) on every `deploy`. If the host requires a password for `sudo` and the script is running with stdin not connected to a TTY (agent/CI/piped), it would have hung historically — now it **auto-skips with a warning** instead. To silence the warning, pass `--skip-sysctl` explicitly. To actually apply the tuning, either:
   - Run interactively, OR
   - Pre-prime sudo with `sudo -v` in the same shell before invoking the script (credential cache stays valid for ~5 min by default), OR
   - Apply the four sysctl values manually via `/etc/sysctl.d/`. The script's idempotent check will then no-op on the next deploy without invoking sudo at all.

   To decide upfront which of those branches you're in (without invoking sudo) run the static pre-flight probe — it returns `status=skip|passwordless|needs_password` in one line:
   ```bash
   python3 oneclick_dc_deployment.py preflight-sysctl
   ```

If the buffers are already at or above the targets (e.g., after a previous successful deploy), the script skips sudo entirely on re-deploys.

---

### Deploy exits immediately with `manifest unknown` after a local build

The tag flags override the **tag** only, not the repository. A locally built
image lives under a different repository than the registry path pinned in
`compose.env`, so compose resolves to a registry reference that has no such tag
and **no containers start**.

Add the repository overrides (`--image-registry`, `--nvstreamer-image`, or the
per-service `--*-image` flags) alongside the tag flags — see
`skills/deployment/deploy.md` Step 1b-i.

### UI build fails during npm install (missing build tool reported)

Restricted outbound access can break a dependency's post-install fetch, aborting
the install so a later build tool appears missing. The reported error names the
tool, not the network. Check egress and prefer an internal mirror — see
`skills/build/build-containers.md`.

---

## BDD Failures Caused by Missing or Stale Test Data

**Always rule these out before reporting a product defect.**

### Symptom: `file_download` / `webrtc` / replay tests fail on a fresh deployment

A newly deployed stack has no sensors and no recordings. The suite's own
prerequisite (`scripts/stream_prerequisite.py`) is best-effort — on a native run
it cannot find clips (`/app/test_videos` exists only inside the BDD container
image), logs a warning, and lets the session continue against an **empty
system**. Failures then look like defects but are missing data.

Fix: run `skills/testing/run-bdd-tests.md` **Step 0** (set `TEST_VIDEOS_DIR`,
seed NVStreamer, scan, verify recordings). Ask the user for clips if none exist.

Quick check — is anything recorded at all?

```bash
curl -s "<BASE_URL>/vst/api/v1/storage/file/list" \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print('segments:', sum(len(v) for v in d.values() if isinstance(v,list)))"
```

The payload is a **dict keyed by sensor id**, not a list — parsing it as a list
reports `0` on a perfectly healthy system.

### Symptom: suite runs for a very long time, or times out

Recordings accumulate across deployments because `stop` without `--clean` keeps
the VST volume. Suites that poll every stream with a per-stream wait (see the
`max_wait` / `parallelism` values in `config.json`) scale with the amount of
stored data, so a long-lived volume can push a single test well past its
expected duration.

Fix: if the user wants a clean slate, `python3 oneclick_dc_deployment.py stop
all --clean`, redeploy, reseed. Confirm first — `--clean` deletes recordings
irreversibly. If they want the existing deployment kept, narrow the test scope
instead and note the pre-existing data in the report.

### Symptom: NVStreamer upload rejected

```
HTTP 422 UnprocessableEntityError: Video encode format not supported: mpeg4
```

Correct validation, not a defect. NVStreamer accepts H.264 / H.265; exclude the
clip and continue.

### Symptom: offline / bad-state sensors reported as defects

Orphaned sensors from earlier runs persist when data is not wiped. Per
`AGENT.md`, clean the baseline before asserting system health; never score
leftover test artifacts as product FAIL.

---

## Stale Database — Sensor Failures (503/504)

When a sensor returns 503 (No Cluster) on recorder/livestream endpoints or 504 (Upstream Timeout) on picture/stream endpoints, the sensor entry in PostgreSQL may be stale — registered in a previous deployment whose backing NVStreamer video file no longer exists on disk.

### PostgreSQL connection

```
Container : centralizedb
DB        : nvcentralizedb
User      : vios
Password  : vios123
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
# Copy any valid clip already served by this NVStreamer instance over the
# missing filename. If the instance has no clips, upload one first (PUT
# /vst/api/v1/storage/file/<name>) or ask the user for a directory of valid
# video files. Sample clips are no longer shipped in the repo -- they are baked
# into the BDD test image at /app/test_videos.
# Preserve the missing file's original extension (.mp4/.mkv/.ts); the source
# clip should use the same container so the stream plays.
cp /tmp/nvstreamer_auto_deploy/nvstreamer-1/<existing_clip>.<ext> \
   /tmp/nvstreamer_auto_deploy/nvstreamer-1/<missing_file>.<ext>
```
NVStreamer picks up new files without a restart.

### SDR `wl_d is None` — what it means

If SDR logs show `wl_d is None` for a sensor event, SDR received a `camera_add`/`camera_proxy` Redis event but had no routing entry for that sensor. This is a downstream symptom of the stream never becoming healthy — it does not need independent fixing once the root cause (missing file or stale DB row) is resolved.

---

## BDD Test Issues

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
cd "$(git rev-parse --show-toplevel)/services/vios/deployment/stream-processing"
python3 oneclick_dc_deployment.py stop && \
python3 oneclick_dc_deployment.py deploy --force
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
