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

# Skill: Stop VIOS Services

Stop deployed VIOS and/or NVStreamer services. Two independent choices: **scope** (Step 1) and **whether to wipe data** (Step 2).

---

## Step 1 — Determine scope

Infer from context. Default to stopping all services.
Only pause to confirm if the user has not specified scope AND active recordings or in-progress tests could be lost (check `docker ps` for running recorder containers).

| Scope phrase from user | Target |
|---|---|
| (no scope mentioned) / "stop everything" / "stop all" / "tear down" | `all` (default) |
| "stop vst" / "stop vios" / "stop the stream-processor" | `vst` |
| "stop nvstreamer" / "stop the streamer" | `nvstreamer` |

---

## Step 2 — Determine data-cleanup intent (CRITICAL)

Stopping containers only removes processes; **persistent host-side data survives by default** (VST volume directory at `${VST_VOLUME}` and NVStreamer videos at `${NVSTREAMER_VIDEO_DIR}`). The `--clean` flag is what tells the script to also remove that data. Map the user's phrase:

| User intent / phrasing | Pass `--clean`? |
|---|---|
| "stop" / "stop the deployment" / "tear down" / "shut down" / "stop containers" | NO |
| **"clean stop"** / **"stop and clean"** / **"stop and clean up"** | **YES** |
| "wipe data" / "remove data" / "purge data" / "delete the data" | **YES** |
| "purge volumes" / "remove vst_volume" / "delete the volume" | **YES** |
| "fresh slate" / "from scratch (for next deploy)" / "reset state" | **YES** |
| "full cleanup" / "complete cleanup" / "everything including data" | **YES** |

If the user's intent is ambiguous between just-stop and clean-stop, **ask once** before wiping data — `--clean` is irreversible. Phrasing template: *"Stop the containers only, or also delete persistent data (VST volume + NVStreamer videos)? Data removal is irreversible."*

---

## Step 3 — Run the command

```bash
cd <PROJECT_ROOT>/services/vios/deployment/stream-processing

# --- Stop only (data preserved) ---
python3 oneclick_dc_deployment.py stop                       # all targets
python3 oneclick_dc_deployment.py stop vst                   # VST/VIOS only
python3 oneclick_dc_deployment.py stop nvstreamer            # NVStreamer only

# --- Stop AND remove persistent data ---
python3 oneclick_dc_deployment.py stop --clean               # both vst_volume + NVStreamer videos
python3 oneclick_dc_deployment.py stop vst --clean           # only vst_volume (postgres, recordings, etc.)
python3 oneclick_dc_deployment.py stop nvstreamer --clean    # only NVStreamer videos
```

Notes on `--clean`:

- Auto-confirms the destructive prompt — no interactive approval needed.
- Removes the VST volume directory using a **throwaway Docker container** (no host `sudo` prompt, even when files are root-owned inside the bind mount).
- Also removes the postgres named volume (`pg_data`).
- For NVStreamer it removes the script-managed videos root; if the user previously had a custom path via `--nvstreamer-video-path`, the script will ask separately about that legacy path.

---

## Step 4 — Verify shutdown

```bash
docker ps --format "{{.Names}}" | grep -E "vios|nvstreamer|redis|sensor-ms|streamprocessing|centralizedb"
# Expected: no output
```

If containers are still running after the stop command, force-remove them:
```bash
docker ps -q --filter "name=vios" | xargs -r docker stop
docker ps -q --filter "name=nvstreamer" | xargs -r docker stop
```

If `--clean` was requested, also verify the data is gone:
```bash
ls services/vios/deployment/stream-processing/docker-compose/vst_volume 2>/dev/null || echo "vst_volume removed ✓"
docker volume ls --filter "name=pg_data" --format "{{.Name}}"
# Expected: no output (pg_data named volume gone)
```

---

## Notes

- `--clean` is the **stop-time** flag to wipe persistent data.
- `--fresh-start` is the **deploy-time** equivalent (stops existing + wipes + redeploys in one command). **Do not use unless explicitly requested** — same destructiveness as `--clean` but combined with a redeploy.
- Redis is shared infrastructure; stopping it affects all services.
- `--clean` does NOT remove built Docker images, only data volumes. To free image disk space, run `docker image prune -a` separately and only with explicit user consent.
