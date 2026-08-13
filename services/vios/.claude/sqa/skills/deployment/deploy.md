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
| VIOS / stream-processor (default) | — | `deploy --force` (VIOS only; NVStreamer is opt-in — see Step 2) |
| NVStreamer only | `nvstreamer` | `deploy --target nvstreamer --force` |
| Full stack (stream-processor + NVStreamer) | `all` | `deploy --target all --force` |

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

### Step 1b-i — Also override the repository, not just the tag

The tag flags change only the **tag**. Locally built images additionally live
under a different **repository** than the registry paths pinned in
`compose.env`, so a tag override alone points compose at a registry path that
has no such tag and the deploy fails with `manifest unknown` before any
container starts.

The script exposes repository overrides for exactly this case — use them rather
than retagging images by hand:

| Flag | Effect |
|---|---|
| `--streamprocessor-image REF` / `--sensor-image REF` | Replace the reference **verbatim**; pair with the matching `*-tag` |
| `--nvstreamer-image REPO` | Replace the NVStreamer repository verbatim; pair with `--nvstreamer-tag` |
| `--image-registry REGISTRY` | Swap only the org **prefix**, keeping `compose.env`'s basename |

**Prefer the `--*-image` flags for local builds.** `--image-registry` keeps the
basename from `compose.env`, which generally differs from the basename
`build.sh` produces, so a prefix swap alone yields a reference that does not
exist locally.

```bash
python3 oneclick_dc_deployment.py deploy all --force \
    --streamprocessor-image <REPO>/<BASENAME> --streamprocessor-tag <BUILD_TAG> \
    --sensor-image <REPO>/<BASENAME>          --sensor-tag <BUILD_TAG> \
    --nvstreamer-image <REPO>                 --nvstreamer-tag <BUILD_TAG>
```

Always read the actual names off the build before choosing values — do not
assume either scheme:

```bash
docker images --format '{{.Repository}}:{{.Tag}} {{.CreatedSince}}' \
  | grep -Ei 'vst-|nvstreamer|ingress'
```

Note the image overrides apply to the stream-processor and sensor services only;
`NGINX_IMAGE` (ingress) keeps its `compose.env` value unless set there directly.

### Step 1b-ii — Confirm the deployment is running YOUR images

Tag names prove nothing; compare image **IDs**. Do this whenever the point of
the deployment is to validate a local build:

```bash
for c in streamprocessing-ms-1 sensor-ms nvstreamer-1 vst-ingress; do
  printf '%-24s %s  %s\n' "$c" \
    "$(docker inspect -f '{{.Image}}' $c 2>/dev/null | cut -c8-19)" \
    "$(docker inspect -f '{{.Config.Image}}' $c 2>/dev/null)"
done
docker images --format '{{.ID}} {{.Repository}}:{{.Tag}}' | grep -E 'vios/|nvstreamer:latest'
```

Each running ID must match the locally built image ID. Note `--all-tag` does
**not** cover `NGINX_IMAGE`, so `vst-ingress` keeps its pinned registry tag
unless you override it explicitly — usually fine, but state it in the report.

---

## Step 2 — Run the deployment script

```bash
cd <PROJECT_ROOT>/services/vios/deployment/stream-processing
```

### Step 2a — Classify the deploy intent

NVStreamer is **opt-in and independent of the adaptor** — a bare deploy never deploys, starts, or probes it. Decide what to deploy from the user's prompt:

| Pattern in the user's prompt | Decision | Next step |
|---|---|---|
| Mentions `nvstreamer` together with any of `+`, `&`, `and`, `with`, `alongside`, `plus` — e.g. *"deploy vios + nvstreamer"*, *"deploy vios and nvstreamer"* | **Deploy NVStreamer too**, regardless of adaptor. | Jump to Step 2d and run BOTH commands (or `deploy --target all --force` for "full stack" wording). |
| `full stack` / `everything` / `deploy both` | `--target all` | Run: `deploy --target all --force`. |
| `deploy nvstreamer` / `deploy the streamer` (NVStreamer only, no VIOS) | NVStreamer-only target | Run: `deploy --target nvstreamer --force`. |
| **Anything else** — bare `deploy` / `deploy vios` / `deploy vst` / `start vios deployment`, or `without nvstreamer` | **VIOS only — do NOT touch NVStreamer.** | Run the mms-credentials pre-flight (Step 2b) if the adaptor is mms-type, then Step 2d: `deploy --force`. |

> NVStreamer is **not** auto-deployed or probed for bare deploys. After a VIOS-only deploy where the adaptor is `vst_rtsp`/`streamer`, note in the response that sensors need an RTSP source (re-run with `+ nvstreamer`, or point them at an external RTSP/camera).

### Step 2b — Adaptor pre-flight (only when needed)

Bare deploys are **VIOS-only** and do **not** probe or deploy NVStreamer. The only adaptor-dependent pre-flight is for **mms-type** adaptors:

**If `VST_ADAPTOR` is an mms-type adaptor (`milestone_onvif`, `milestone_soap`), run the credentials pre-flight in `skills/deployment/adaptor-mode.md` Step 2.5 BEFORE invoking the deploy command.** Missing `ip`/`user`/`password` in `adaptor_config.json` cause silent runtime failures. The skill covers prompt-parsing, the ask-the-user phrasing, the dry-run diff, the write, and the credentials-hygiene reminder.

For adaptor↔NGINX_MODE↔JSON consistency and post-deploy verification, see `skills/deployment/adaptor-mode.md`. (Reminder: the `-sdrc` suffix on `NGINX_MODE` is the SDRC toggle, independent of the adaptor.)

### Step 2d — Commands

```bash
# (only for explicit "+ nvstreamer" / full-stack / nvstreamer-only requests — never for a bare deploy)
# Add --nvstreamer-tag <BUILD_TAG> if deploying a locally built NVStreamer (see Step 1b)
python3 oneclick_dc_deployment.py deploy --target nvstreamer --force

# Default VIOS deploy (VIOS only — NVStreamer is opt-in)
# Add --all-tag <BUILD_TAG> if deploying locally built VIOS images (see Step 1b)
python3 oneclick_dc_deployment.py deploy --force
```

**Deployment mode (SDRC vs direct) — independent of the adaptor and of NVStreamer.** The stack defaults to **SDRC** (`NGINX_MODE=vst-sdrc`, `sdr-controller` running). If the user asked for **direct mode** ("no sdrc" / "direct" / "single-pod" / "no sdr-controller"), append **`--no-sdrc`** to the VIOS deploy command; it persistently rewrites `compose.env` to direct values (`VST_USE_SDRC=false`, `NGINX_MODE=vst`, cleared `COMPOSE_PROFILES`, stream-processor on `:30001`). `vst-sdrc` is **not** an adaptor — do not touch the adaptor settings for this. **Scaling requires SDRC:** direct mode is single-pod only, so if the user wants multiple stream-processor pods / scale-out (>~100 streams), keep SDRC and do **not** pass `--no-sdrc`. Example:
```bash
python3 oneclick_dc_deployment.py deploy --no-sdrc --force
```

**Full stack (`--target all`)** — single command, do NOT add an nvstreamer step before this; the script handles NVStreamer internally:
```bash
# Add --all-tag <BUILD_TAG> --nvstreamer-tag <BUILD_TAG> if deploying locally built images (see Step 1b)
python3 oneclick_dc_deployment.py deploy --target all --force
```

**NVStreamer only:**
```bash
# Add --nvstreamer-tag <BUILD_TAG> if deploying a locally built NVStreamer (see Step 1b)
python3 oneclick_dc_deployment.py deploy --target nvstreamer --force
```

Additional flags for the **VIOS deploy command only** (do not append to the nvstreamer step):
```bash
--skip-sysctl       # Skip host network-buffer (sysctl) tuning. Pass this for
                    # agent/CI runs without passwordless sudo. The script also
                    # auto-skips with a warning if stdin is non-TTY and sudo
                    # would prompt, but this flag is clearer in the log.
```

### Sysctl pre-flight (run BEFORE the VIOS deploy command)

The deploy script tunes four kernel network-buffer sysctls (`net.core.rmem_max`, `net.core.wmem_max`, `net.ipv4.tcp_rmem`, `net.ipv4.tcp_wmem`) to support high-throughput streaming. If the host already has these at the target values, the script no-ops. Otherwise it invokes `sudo sysctl -w …` — which **prompts for a password unless sudo is passwordless**.

Run the script's built-in probe — it returns a single machine-parseable line and **never invokes sudo** (only `sudo -n true`, which exits cleanly either way):

```bash
python3 oneclick_dc_deployment.py preflight-sysctl
# → SYSCTL_PREFLIGHT status=<S> rmem_max=… wmem_max=… tcp_rmem="…" tcp_wmem="…" rmem_max_target=… tcp_target="…" sudo=<S>
```

Extract `status` and branch:

```bash
STATUS=$(python3 oneclick_dc_deployment.py preflight-sysctl | grep -oE 'status=[a-z_]+' | cut -d= -f2)
```

- `status=skip` → all four sysctls already meet target; append `--skip-sysctl` to silence the info log (script would no-op anyway).
- `status=passwordless` → tuning is needed AND `sudo -n` works; run deploy normally — sudo passes through silently.
- `status=needs_password` AND **interactive user available** → ask the user *"Deploy will tune host network buffers (rmem_max → 2 MB, etc.); enter sudo password, or pass --skip-sysctl to skip (throughput may be lower)?"* then proceed accordingly.
- `status=needs_password` AND **non-interactive (you can't surface a prompt)** → append `--skip-sysctl` to avoid hanging. The script will also auto-skip with a warning, but the explicit flag is clearer in the deploy log.

> **Why this command (vs. inline `sysctl`/`sudo -n` shell):** the script command is static-analyzable (no `$(…)`, no `if`, no `sudo -n` invocation at the agent layer), so coding agents auto-allowlist it instead of asking the user to approve every pre-flight run.

---

## Step 2e — NVStreamer must be publishing BEFORE the stream-processor starts

Applies to **every** deployment that includes NVStreamer — not just test runs.

VIOS reads `configs/rtsp_streams.json`, which holds an `Nvstreamer` array with
one entry per instance:

```json
"Nvstreamer": [
  { "enabled": true, "endpoint": "localhost:31000",
    "api": "/api/v1/sensor/streams", "max_stream_count": 100 },
  { "enabled": true, "endpoint": "localhost:31001", "...": "..." }
]
```

Inspect it before deploying — it determines whether sensors appear at all:

```bash
python3 -c "
import json
d = json.load(open('docker-compose/configs/rtsp_streams.json'))
for i, e in enumerate(d.get('Nvstreamer', [])):
    print(i, e['enabled'], e['endpoint'], e['api'])
"
```

### `enabled: true` (default) — VIOS auto-imports, once, at start-up

`sensor-ms` pulls every stream from each enabled endpoint via
`/api/v1/sensor/streams` **when it starts**. If the stream-processor starts
before NVStreamer has finished publishing, VIOS imports an incomplete set — or
none — and **nothing re-imports them later**. The deployment looks healthy while
having no sensors.

Deploy NVStreamer first, wait until its stream list is non-empty and stable,
then start the stream-processor:

```bash
prev=-1
for i in $(seq 1 30); do
  n=$(curl -s "http://<NVSTREAMER_HOST>:31000/api/v1/sensor/streams" \
      | python3 -c "import json,sys; print(len(json.load(sys.stdin)))" 2>/dev/null || echo 0)
  echo "  nvstreamer streams=$n"
  [ "$n" -gt 0 ] && [ "$n" = "$prev" ] && break
  prev=$n; sleep 5
done
```

`deploy --target all` sequences the two internally, but still verify afterwards
(Step 4c) — a slow NVStreamer start can still lose the race.

If the import already ran too early, re-trigger it without a full redeploy:

```bash
python3 oneclick_dc_deployment.py recreate streamprocessing
```

### `enabled: false` — no auto-import; add sensors manually

VIOS pulls nothing. Read the stream list from NVStreamer and add each one to
VIOS as a sensor using the RTSP URL in the response:

```bash
curl -s "http://<NVSTREAMER_HOST>:31000/api/v1/sensor/streams"
# then add each stream to VIOS: <BASE_URL>/vst/api/v1/sensor/add
```

### Where NVStreamer's streams come from

`--nvstreamer-video-path PATH` makes NVStreamer scan PATH and publish one RTSP
URL per `.mp4` / `.mkv` file, so the directory contents alone define the stream
list — no upload step is needed. Files whose codec NVStreamer rejects are
skipped, so the stream count is legitimately lower than the file count.

Without a video path, NVStreamer starts empty and streams must be uploaded to it
before VIOS can import anything.

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

## Step 4c — Verify NVStreamer streams reached VIOS

Run whenever NVStreamer is part of the deployment. A healthy container set does
**not** imply the import succeeded (Step 2e).

```bash
# streams published by NVStreamer
curl -s "http://<HOST>:31000/api/v1/sensor/streams" \
  | python3 -c "import json,sys; print('nvstreamer streams:', len(json.load(sys.stdin)))"

# sensors VIOS actually imported
curl -s "<BASE_URL>/vst/api/v1/sensor/list" \
  | python3 -c "import json,sys; print('vios sensors    :', len(json.load(sys.stdin)))"
```

| Result | Meaning / action |
|---|---|
| VIOS sensors ≥ NVStreamer streams | Import succeeded |
| VIOS sensors = 0 while NVStreamer > 0 | Stream-processor started too early, or `enabled: false` — see Step 2e |
| VIOS sensors < NVStreamer streams | Partial import; `recreate streamprocessing` and re-check |

Report the two counts in the deployment summary so the next step (tests, demos)
is not run against a stack with no sensors.

---

## Step 5 — Report outcome

Report to the user:
- Which containers are running and their status
- `VIOS UI: <BASE_URL>/vios/#/dashboard` — always label it **"VIOS UI"** (never a bare "UI"). The link already shows host:port, so do **not** print a separate `BASE_URL` line (it's an internal value, not user-facing output).
- For NVStreamer deploys / full stack: `NVStreamer UI: http://<HOST>:<NVSTREAMER_HTTP_PORT_N>/#/dashboard` — one line per active instance (ports `31000–31004`).
- Active adaptor + deployment mode (SDRC vs direct)
- Any warnings from the deployment script output

**Always state the provenance — "deployed successfully" is not a result on its
own.** A stack can be perfectly healthy while running last week's registry
images, or while holding zero sensors. Include:

| Report | Why |
|---|---|
| Source branch + commit that produced the images | Ties the deployment to the code under test |
| Per service: image ID, and whether it is a local build or a registry image | `--*-image`/`--*-tag` cover the stream-processor, sensor and NVStreamer only; ingress and the rest keep their `compose.env` values |
| NVStreamer stream count vs VIOS sensor count | Healthy containers do not imply a successful import (Step 4c) |
| Whether persisted data survived (`stop` vs `stop --clean`) | Pre-existing recordings/sensors change how later results should be read |

State plainly which services are **not** running your build rather than leaving
it implied — someone reading the report should never have to guess whether a
result reflects their change.

---

## Sample Video Files

Deployments now start **without** any pre-seeded videos -- the sample clips are
no longer shipped under `tools/data/` (they are baked into the BDD test image
and used only by the BDD suite). NVStreamer comes up with no streams.

If a deployment needs a video source:

- **Ask the user to point to a directory that contains valid video files**
  (MP4/MKV/TS carrying H.264 or H.265). Do not assume `tools/data/` exists.
- Upload those files to NVStreamer (`PUT /vst/api/v1/storage/file/<name>`), then
  run a sensor scan from the VST UI (or `POST /vst/api/v1/sensor/scan`) so VIOS
  imports the RTSP streams.

---

## Image Tag Flags Reference

| Flag | Controls |
|---|---|
| `--all-tag <TAG>` | Stream-processor + sensor images |
| `--streamprocessor-tag <TAG>` | Stream-processor module |
| `--sensor-tag <TAG>` | Sensor module |
| `--nvstreamer-tag <TAG>` | NVStreamer |
| `--streamprocessor-image <REF>` | Full stream-processor image ref (pair with `--streamprocessor-tag`) |
| `--sensor-image <REF>` | Full sensor image ref (pair with `--sensor-tag`) |
| `--image-registry <REG>` | Swap only the registry/org prefix on stream-processor + sensor images |
| `--nvstreamer-image <REPO>` | Swap the NVStreamer image repository |

If no tag flags are given, the script uses whatever is configured in the compose files.
