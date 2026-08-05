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

3. **Decide the deployment state from the user's intent — do not redeploy by
   default.** The user may want tests run against a deployment that is already
   up. Only redeploy when they ask for it, or when the stack is not running.

   | User intent | Action |
   |---|---|
   | "use the running deployment" / already deployed and no redeploy asked | Leave it as-is; go to Step 0 |
   | "redeploy" / "fresh deploy" / stack not running | `deploy --force` |
   | "clean deploy" / "fresh start" / wants stale data gone | `stop all --clean`, then deploy |

   ```bash
   cd <PROJECT_ROOT>/services/vios/deployment/stream-processing
   python3 oneclick_dc_deployment.py stop all --clean   # only when a clean slate was requested
   python3 oneclick_dc_deployment.py deploy --force
   ```

   > `--clean` is irreversible — it deletes recordings, sensors and NVStreamer
   > videos. Confirm with the user first (see `skills/deployment/stop.md`).

   When reusing a running deployment, treat pre-existing sensors and recordings
   as **environment state, not results**: they can slow long-polling suites and
   can surface as offline/stale entries. Never score leftover artifacts as
   product defects — see `AGENT.md`.

   **If the deployment should run locally built images**, pass the repository
   and tag overrides; otherwise the pinned registry images are used and the
   tests exercise code that is not yours. See `skills/deployment/deploy.md`
   Step 1b.

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

6. **Seed test data — MANDATORY for stream/download/webrtc suites.**
   A freshly deployed stack has **no sensors and no recordings**. Suites such as
   `file_download`, `webrtc`, `url_optimization` and much of `unit_tests` will
   fail with confusing assertion errors that look like product defects but are
   simply "there is nothing recorded". See **Step 0** below — do this before
   running any suite that needs streams.

---

## Step 0 — Seed test data (NVStreamer clips → VIOS sensors → recordings)

Skip only if you are running a suite that genuinely needs no streams (e.g.
`tests/unit_tests/mcp_gateway/`). When in doubt, seed.

### 0a. Locate sample clips — ASK THE USER IF THERE ARE NONE

The BDD suite seeds itself from `scripts/stream_prerequisite.py`, which looks
for clips in this order:

1. `$TEST_VIDEOS_DIR` (env var)
2. `/app/test_videos` — **only exists inside the BDD container image**
3. `<PROJECT_ROOT>/test/bdd_tests/test_videos/` — in a git checkout this holds
   only a `README.md`

So on a **native (non-container) run the prerequisite finds nothing**. It is
deliberately best-effort — it logs a warning and continues rather than raising —
so the suite proceeds against an empty system and reports failures that are
really missing data.

```bash
ls "$TEST_VIDEOS_DIR" /app/test_videos <PROJECT_ROOT>/test/bdd_tests/test_videos 2>/dev/null
```

**If no clips are found, STOP and ask the user for a directory of sample
videos.** Do not proceed and do not report the resulting failures as defects.
Suggested prompt:

> "The BDD stream suites need sample videos to upload to NVStreamer, and none
> are present (`/app/test_videos` only exists inside the BDD container image).
> Where are your clips? On VST dev machines they are often under
> `/home/vst/vst_release/streamer_videos/clip/`. I'll set `TEST_VIDEOS_DIR` to
> that path."

Then either point the seeder at it:

```bash
export TEST_VIDEOS_DIR=/path/to/clips
```

…or, **if you are deploying anyway**, hand the directory to NVStreamer at deploy
time so its videos come from there directly:

```bash
python3 oneclick_dc_deployment.py deploy --target all --force \
    --nvstreamer-video-path /path/to/clips
```

Use `--nvstreamer-video-path` when the user supplies a video directory and a
deployment is being made; use `TEST_VIDEOS_DIR` when testing an existing
deployment you are not redeploying.

**Codec constraint:** NVStreamer accepts H.264 / H.265 only. An mpeg4 clip is
rejected with `HTTP 422 UnprocessableEntityError: Video encode format not
supported: mpeg4`. That is correct validation, **not** a defect — just exclude
the clip. Prefer a handful of small clips; a multi-hundred-MB file makes the
upload step needlessly slow.

### 0a-ii. Confirm how streams reach VIOS before seeding anything

How VIOS obtains sensors from NVStreamer is a **deployment** behaviour, not a
test one — `skills/deployment/deploy.md` **Step 2e** is authoritative. In short:
`configs/rtsp_streams.json` has an `Nvstreamer` array whose `enabled` flag
decides whether `sensor-ms` auto-imports each endpoint's streams at start-up
(once), or whether you must add sensors manually.

For a test run you only need the outcome — check both counts agree before
seeding or running anything:

```bash
curl -s "http://<NVSTREAMER_HOST>:31000/api/v1/sensor/streams" \
  | python3 -c "import json,sys; print('nvstreamer streams:', len(json.load(sys.stdin)))"
curl -s "<BASE_URL>/vst/api/v1/sensor/list" \
  | python3 -c "import json,sys; print('vios sensors    :', len(json.load(sys.stdin)))"
```

- **VIOS sensors already cover the NVStreamer streams** → seeding is done; skip
  0b and go to 0c (verify recordings).
- **VIOS sensors = 0 while NVStreamer has streams** → the import did not happen
  (started too early, or `enabled: false`). Fix per deploy.md Step 2e — do not
  "fix" it by uploading more clips.
- **NVStreamer has no streams** → continue with 0b.

### 0b. Upload to NVStreamer and import into VIOS

**Skip this step when NVStreamer already serves the streams** — i.e. the deploy
used `--nvstreamer-video-path`, or `/api/v1/sensor/streams` already lists the
expected clips. In that case go straight to 0c and verify recordings; uploading
again only duplicates content.

Use this step when NVStreamer has no streams (fresh instance, no video path).
The prerequisite module does upload → scan → readiness-wait in one call:

```bash
cd <PROJECT_ROOT>/test/bdd_tests
TEST_VIDEOS_DIR=/path/to/clips poetry run python -c "
import json, logging, sys
logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
sys.path.insert(0, '.')
from scripts.stream_prerequisite import ensure_streams
cfg = json.load(open('config.json'))
print(ensure_streams(cfg['api']['base_url'], cfg['api'].get('verify_ssl', False), cfg))
"
```

Expect a summary like:
`{'seeded': True, 'uploaded': 3, 'scanned': True, 'live_ready': True, 'replay_ready': True}`

Under the hood this is:
1. `PUT {nvstreamer}/vst/api/v1/storage/file/<name>?timestamp=…` per clip
2. `POST {vst}/vst/api/v1/sensor/scan` so VIOS imports the RTSP streams
3. Poll `/vst/api/v1/live/streams` and `/vst/api/v1/replay/streams` until populated

> `config.json` must already point at the deployment (`api.base_url` **and**
> `nvstreamer.host`) — see prerequisite 4. Seeding against `localhost` while
> testing a remote host silently seeds the wrong box.

### 0c. VERIFY recordings are actually accumulating

`live_ready` only means streams exist. Confirm VIOS is really recording before
running download/replay suites — otherwise those suites fail on empty storage:

```bash
curl -s "<BASE_URL>/vst/api/v1/storage/file/list" | python3 -c "
import json,sys
d = json.load(sys.stdin)          # dict: sensorId -> [segments]
tot = 0
for sid, files in d.items():
    n = len(files) if isinstance(files, list) else 0
    tot += n
    print(f'  {sid}: {n} segment(s)')
print('TOTAL segments:', tot)
"
```

- `TOTAL segments: 0` → not recording yet. Wait ~60s and re-check. If still 0,
  investigate before running tests (check `docker logs streamprocessing-ms-1`).
- Note the response is a **dict keyed by sensor id**, not a list — a parser that
  assumes a list reports `0` on a healthy system.
- Allow a few minutes of recording so multi-segment tests (e.g.
  `file_download/test_download_inter_file_gap.py`, which picks "a non-first
  recorded file") have several segments per sensor to choose from.

### 0d. Upload one clip directly to VIOS (file-sensor coverage)

The RTSP path above does not exercise **file sensors**. `tests/file_upload/` and
parts of `tests/unit_tests/storage_management/` need a file uploaded straight to
VIOS, which creates a file-backed sensor:

```bash
curl --location --request PUT --fail \
  "<BASE_URL>/vst/api/v1/storage/file/<name>.mp4?sensorId=<name>&timestamp=2025-01-01T00:00:00.000Z" \
  --header "Content-Type: video/mp4" \
  --data-binary "@/path/to/<name>.mp4"
```

Returns the created `sensorId` / `streamId`.

### Step 0 checklist

| Check | Expected |
|---|---|
| Clips resolved (or user asked) | non-empty dir of `.mp4` / `.mkv` / `.ts` |
| `ensure_streams` summary | `seeded/scanned/live_ready/replay_ready` all true |
| `/vst/api/v1/live/streams` | non-empty |
| `storage/file/list` total | > 0 and growing |
| Direct file upload done | returns `sensorId` + `streamId` |

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
| NVStreamer routes | Exact file under `tests/unit_tests/nvstreamer/` | Manual NVStreamer UI/API base-path validation; disabled by default |
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
  --base-url http://<HOST>:30888 \
  --junitxml=reports/junit.xml \
  --html=reports/report.html \
  --self-contained-html
```

### NVStreamer route tests

The two NVStreamer route modules are disabled by default so generic test
collection and standard CI skip them. To run one manually, set
`RUN_NVSTREAMER_ROUTE_TESTS=1` and select the exact file matching the active
deployment. Do not opt in while targeting the whole `nvstreamer/` directory:
the root and prefixed suites make mutually exclusive assertions.

```bash
# Direct NVStreamer: NVSTREAMER_UI_BASE_PATH is unset or empty
RUN_NVSTREAMER_ROUTE_TESTS=1 poetry run pytest \
  tests/unit_tests/nvstreamer/test_nvstreamer_root_routes.py -v \
  --base-url http://localhost:31000 \
  --junitxml=reports/junit.xml \
  --html=reports/report.html \
  --self-contained-html

# NVStreamer behind a prefix-stripping proxy:
# NVSTREAMER_UI_BASE_PATH=/nvstreamer
RUN_NVSTREAMER_ROUTE_TESTS=1 poetry run pytest \
  tests/unit_tests/nvstreamer/test_nvstreamer_prefixed_routes.py -v \
  --base-url http://<HAPROXY_HOST>:<HAPROXY_PORT> \
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

## Sample Video Files

The BDD sample clips (10s H.264/H.265, MP4/MKV) are **baked into the BDD test
image** at `/app/test_videos` -- they are no longer committed under
`tools/data/`. Tests do not reference them directly: a session prerequisite
(`scripts/stream_prerequisite.py`) uploads them to NVStreamer and triggers a VST
sensor scan when NVStreamer has no streams.

If you need to seed a video source manually (e.g. a non-default deployment where
NVStreamer has no streams and the prerequisite did not run):

- **Ask the user to point to a directory that contains valid video files**
  (MP4/MKV/TS with H.264 or H.265). Do not assume `tools/data/` exists.
- Upload them to NVStreamer (`PUT /vst/api/v1/storage/file/<name>`), then run a
  sensor scan from the VST UI (or `POST /vst/api/v1/sensor/scan`).

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
