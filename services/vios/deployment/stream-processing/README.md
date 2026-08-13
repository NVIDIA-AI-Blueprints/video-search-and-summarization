<!--
SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# VIOS / NVStreamer Deployment

Single-page guide for the `oneclick_dc_deployment.py` Docker Compose deployer. The script wraps the same `docker compose up/down` commands documented under [Manual Docker Compose Flow](#manual-docker-compose-flow-advancedebugging) — use it for everyday work, drop down to the manual flow for debugging.

## Contents

- [Quick start](#quick-start)
- [Prerequisites](#prerequisites)
- [CLI cheatsheet](#cli-cheatsheet)
- [Usage examples](#usage-examples)
  - [Deploy](#deploy)
  - [Stop / clean](#stop--clean)
  - [Recreate a single container](#recreate-a-single-container)
  - [Image / tag overrides](#image--tag-overrides)
  - [Multi-instance NVStreamer](#multi-instance-nvstreamer)
  - [Pre-flight: sysctl tuning state](#pre-flight-sysctl-tuning-state)
  - [Configuration only (no deploy)](#configuration-only-no-deploy)
- [Deployment modes](#deployment-modes)
- [Adaptors (VST / MMS / ONVIF)](#adaptors-vst--mms--onvif)
- [Access URLs](#access-urls)
- [Configuration reference](#configuration-reference)
- [Directory structure](#directory-structure)
- [Manual Docker Compose flow (advanced / debugging)](#manual-docker-compose-flow-advancedebugging)
- [Recommended deploy ordering](#recommended-deploy-ordering)
- [Using an AI coding agent](#using-an-ai-coding-agent)
- [Troubleshooting](#troubleshooting)

---

## Quick start

```bash
cd services/vios/deployment/stream-processing

# Bring up VIOS + NVStreamer in one command (default SDRC mode, 1 NVStreamer instance)
# Add --no-sdrc for direct mode (no SDR/Envoy, single-pod)
python3 oneclick_dc_deployment.py deploy --target all --force
```

Then open the VIOS dashboard:

```
http://<HOST_IP>:30888/vst/#/dashboard
```

For local builds, also pass the image overrides — see [Image / tag overrides](#image--tag-overrides).

---

## Prerequisites

### Software
- Docker Engine + Docker Compose v2 (`docker compose version`)
- NVIDIA Container Toolkit (`docker info | grep -i nvidia` should show the runtime)
- Python 3.6+ (only if using `oneclick_dc_deployment.py`)
- Membership in the `docker` group (the script avoids `sudo` for cleanup; see [Stop / clean](#stop--clean))

### Hardware (validated)
| Item | Tested config |
|---|---|
| CPU | Intel Xeon Platinum 8352Y @ 2.20GHz, 128 vCPUs |
| Network | ≥ 2.5 Gb/s NIC for ~500 streams @ 5 Mbps |
| GPU | NVIDIA GPU compatible with the NVIDIA Container Toolkit |

### Host network buffers
For high-throughput streaming, four kernel sysctls need bumping (the script handles this on first run; bumps require `sudo` once, then are remembered):

```bash
sudo sysctl -w net.core.rmem_max=2000000
sudo sysctl -w net.core.wmem_max=2000000
sudo sysctl -w net.ipv4.tcp_rmem='4096 2000000 6291456'
sudo sysctl -w net.ipv4.tcp_wmem='4096 2000000 6291456'
```

If you're running unattended (CI / agent) and don't have passwordless sudo, pass `--skip-sysctl` to the deploy. The script also auto-skips with a warning when stdin is non-TTY and sudo would prompt — it never hangs.

To probe the current state without deploying:

```bash
python3 oneclick_dc_deployment.py preflight-sysctl
# SYSCTL_PREFLIGHT status=skip|passwordless|needs_password rmem_max=… …
```

---

## CLI cheatsheet

```
python3 oneclick_dc_deployment.py [ACTION] [TARGET|SERVICE...] [OPTIONS]

Actions:  deploy (default) | stop | recreate | config-only | preflight-sysctl
Targets:  vst (default; alias: vios) | nvstreamer | all
```

Run `python3 oneclick_dc_deployment.py --help` for the full flag list.

---

## Usage examples

All commands assume you're inside `services/vios/deployment/stream-processing/`.

### Deploy

```bash
# Default: VST stream-processor only, non-interactive smart defaults
python3 oneclick_dc_deployment.py deploy --force

# Full stack: VIOS + NVStreamer in one command
python3 oneclick_dc_deployment.py deploy --target all --force

# NVStreamer only
python3 oneclick_dc_deployment.py deploy --target nvstreamer --force

# Always pull the latest images before bringing services up
python3 oneclick_dc_deployment.py deploy --pull-always --force

# Prompt-driven mode (legacy interactive flow)
python3 oneclick_dc_deployment.py deploy --interactive

# Fresh start: stop existing, wipe VST volume data, deploy from clean state
python3 oneclick_dc_deployment.py deploy --fresh-start --force
```

### Stop / clean

```bash
# Stop everything; persistent data (vst_volume, NVStreamer videos) preserved
python3 oneclick_dc_deployment.py stop

# Stop only one target
python3 oneclick_dc_deployment.py stop vst
python3 oneclick_dc_deployment.py stop nvstreamer

# Stop AND remove persistent data (irreversible)
python3 oneclick_dc_deployment.py stop --clean              # both: vst_volume + NVStreamer videos
python3 oneclick_dc_deployment.py stop vst --clean          # vst_volume + pg_data only
python3 oneclick_dc_deployment.py stop nvstreamer --clean   # NVStreamer videos only
```

`--clean` removes root-owned bind-mount files via a throwaway Docker container — **no host `sudo` prompt** (delegated to the docker daemon, which you already have access to).

### Recreate a single container

Sometimes you only need to swap one container in-place — e.g. after rebuilding a single image. `recreate` uses `docker compose up --no-deps --force-recreate <service>` so the rest of the stack stays running:

```bash
# By alias (recommended)
python3 oneclick_dc_deployment.py recreate sensor                  # → sensor-ms
python3 oneclick_dc_deployment.py recreate streamprocessing        # → all streamprocessing-ms-N
python3 oneclick_dc_deployment.py recreate nvstreamer              # → all nvstreamer-N
python3 oneclick_dc_deployment.py recreate ingress                 # → vst-ingress
python3 oneclick_dc_deployment.py recreate db                      # → centralizedb

# Multiple at once
python3 oneclick_dc_deployment.py recreate sensor streamprocessing

# Specific instance (alias doesn't apply; direct name works)
python3 oneclick_dc_deployment.py recreate nvstreamer-3

# Pull a fresh image first
python3 oneclick_dc_deployment.py recreate sensor --pull-always

# Recreate with a new tag (rewrites compose.env, then recreates)
python3 oneclick_dc_deployment.py recreate sensor --sensor-tag v2.1.0
```

Aliases: `sensor`, `streamprocessing` (or `stream-processor` / `streamprocessor`), `nvstreamer` (or `streamer`), `ingress` (or `nginx`), `db` (or `postgres`), `sdr`, `envoy`. Direct service names also work — unknown names get rejected with a friendly "available services: …" hint.

### Image / tag overrides

```bash
# Tag override for ALL VST images (most common dev case)
python3 oneclick_dc_deployment.py deploy --all-tag latest --force

# Per-image tag overrides
python3 oneclick_dc_deployment.py deploy \
  --streamprocessor-tag v2.1.1 \
  --sensor-tag v2.1.2 \
  --nvstreamer-tag v1.5.0 \
  --force

# Full image-reference overrides (locally built images that don't match the shipped nvcr.io refs)
python3 oneclick_dc_deployment.py deploy --target all \
  --streamprocessor-image vios/vst-streamprocessing --streamprocessor-tag latest \
  --sensor-image vios/vst-sensor --sensor-tag latest \
  --nvstreamer-image nvstreamer --nvstreamer-tag latest \
  --force

# Swap just the registry prefix (basename + tag preserved)
python3 oneclick_dc_deployment.py deploy --image-registry my-registry.example.com/vios --force
```

### Multi-instance NVStreamer

```bash
# Run 3 NVStreamer instances (1..5 supported)
python3 oneclick_dc_deployment.py deploy --target nvstreamer --instances 3 --force

# 5 instances + VIOS together
python3 oneclick_dc_deployment.py deploy --target all --instances 5 --force

# Use a custom videos directory as-is for every instance
python3 oneclick_dc_deployment.py deploy --target nvstreamer \
  --nvstreamer-video-path /custom/nvstreamer/videos --force
```

The script rewrites `COMPOSE_PROFILES` in `docker-compose/nvstreamer/compose.env` to enable the requested instances.

### Pre-flight: sysctl tuning state

```bash
python3 oneclick_dc_deployment.py preflight-sysctl
```

Emits one machine-parseable line:

```
SYSCTL_PREFLIGHT status=<S> rmem_max=… wmem_max=… tcp_rmem="…" tcp_wmem="…" \
                  rmem_max_target=… tcp_target="…" sudo=<S>
```

`status` is `skip` (buffers already meet target), `passwordless` (tuning needed, `sudo -n` works), or `needs_password` (tuning needed, sudo would prompt). The probe never invokes sudo for real — only `sudo -n true`.

### Configuration only (no deploy)

```bash
python3 oneclick_dc_deployment.py config-only
```

Updates `compose.env` / `nvstreamer/compose.env` with smart defaults (or the values you pass via `--host` / `--config-path` / `--volume-path` / etc.) without starting any containers.

---

## Deployment modes

VIOS supports two topologies, selected by the toggle block at the top of `docker-compose/compose.env`. This mode is **independent of the adaptor** — `vst-sdrc` is not an adaptor.

| Mode | What it does | Containers |
|---|---|---|
| **SDRC** (default) | `sdr-controller` + Envoy on `:10000` route stream-bound APIs by header. Works single-pod and is required for multi-pod scaling. | 8: sensor-ms, streamprocessing-ms-1, vst-ingress, centralizedb + redis, sdr-controller + init chain |
| **Direct** | sensor-MS posts `/api/v1/proxy/stream/add` directly to the stream-processor pod on `:30001`. No SDR/Envoy in the data path. Single-pod only. | 4: sensor-ms, streamprocessing-ms-1, vst-ingress, centralizedb |

SDRC is the default. To deploy in **direct mode**, either flip the toggle block in `compose.env`, or run:

```bash
python3 oneclick_dc_deployment.py deploy --no-sdrc --force
```

`--no-sdrc` rewrites `compose.env` to direct values (`VST_USE_SDRC=false`, `NGINX_MODE=vst`, cleared `COMPOSE_PROFILES`, stream-processor on `:30001`) and persists, so later deploys stay direct until the toggle is changed back.

---

## Adaptors (VST / MMS / ONVIF)

`VST_ADAPTOR` in `compose.env` selects how sensor-MS discovers and controls cameras:

| `VST_ADAPTOR` | Type | RTSP source | Typical use |
|---|---|---|---|
| `vst_rtsp` (default) | vst | NVStreamer or any RTSP URL | Local dev with NVStreamer-served clips |
| `onvif` | vst | ONVIF cameras on the LAN | Direct ONVIF discovery, no VMS |
| `milestone_onvif` | mms | Milestone XProtect via ONVIF Bridge | Milestone VMS deployments |
| `milestone_soap` | mms | Milestone XProtect via SOAP API | Milestone VMS without ONVIF bridge |
| `streamer` | streamer | NVStreamer (file-backed) | Pre-recorded clip playback only |
| `native`, `remote`, `test_vms` | vst | (specialized) | Native hardware / multi-host / test mocks |

Two files must agree:

1. `compose.env` → `VST_ADAPTOR=<name>` AND a matching `NGINX_MODE` **family**: `mms` for mms-type adaptors, `vst` otherwise. The deployment-mode toggle independently adds the `-sdrc` suffix when SDRC is on (e.g. `vst-sdrc`) — that suffix is **not** part of adaptor selection (see [Deployment modes](#deployment-modes)).
2. `configs/adaptor_config.json` → exactly one entry with matching `name` and `enabled: true`; all others `enabled: false`. For `mms`-type, fill in `ip` / `user` / `password` / `port`.

For details and the consistency check, see [`services/vios/.claude/sqa/skills/deployment/adaptor-mode.md`](../../.claude/sqa/skills/deployment/adaptor-mode.md).

> **`adaptor_config.json` may contain plaintext credentials.** Either gitignore it or `git update-index --skip-worktree` once you've configured an mms adaptor.

---

## Access URLs

After deploy, services are at:

| Service | URL |
|---|---|
| VIOS UI | `http://<HOST_IP>:30888/vst/#/dashboard` |
| NVStreamer 1 | `http://<HOST_IP>:31000/#/dashboard` |
| NVStreamer 2–5 | `http://<HOST_IP>:31001/#/dashboard` … `:31004/#/dashboard` (if enabled) |

NVStreamer RTSP ports: `31554`, `31564`, `31574`, `31584`, `31594` (one per instance).

---

## Configuration reference

### Files

| File | Purpose |
|---|---|
| `docker-compose/compose.env` | VST_USE_SDRC toggle, image refs, ports, paths, adaptor selection |
| `docker-compose/nvstreamer/compose.env` | NVStreamer instances, profiles, video paths, ports |
| `docker-compose/configs/vst_config.json` | Runtime VST settings (max devices, storage, notifications) |
| `docker-compose/configs/adaptor_config.json` | Adaptor inventory (`enabled: true` on one entry) |
| `docker-compose/configs/rtsp_streams.json` | Bootstrap RTSP sources (NVStreamer instances) |
| `docker-compose/configs/nginx-vst.conf` / `nginx-vst-sdrc.conf` / `nginx-mms.conf` | Ingress routing (selected by `NGINX_MODE`; `vst-sdrc` is the SDRC default) |

### Key environment variables

| Variable | Description | Default |
|---|---|---|
| `HOST_IP` | Server IP address | Auto-detected |
| `VST_CONFIG_PATH` | Absolute path to `configs/` | Auto-set to local `configs/` |
| `VST_VOLUME` | Absolute path to host bind-mount root | Auto-set to local `vst_volume/` |
| `VST_USE_SDRC` | Deployment mode (`false` = direct, `true` = SDRC); independent of the adaptor | `true` |
| `VST_ADAPTOR` | Adaptor name (see [Adaptors](#adaptors-vst--mms--onvif)) | `vst_rtsp` |
| `NGINX_MODE` | Ingress config: family (`vst`/`mms`, from adaptor) + optional `-sdrc` SDRC suffix → `vst`, `vst-sdrc`, or `mms` | `vst-sdrc` |
| `STREAM_PROCESSOR_HTTP_PORT_1` | Stream-processor port | `30001` |
| `NVSTREAMER_HTTP_PORT_1..N` | NVStreamer HTTP ports | `31000–31004` |

---

## Directory structure

```
services/vios/deployment/stream-processing/
├── README.md                                # This file
├── oneclick_dc_deployment.py                # Main deployment script
└── docker-compose/
    ├── compose.env                          # VST + adaptor toggle + base config
    ├── docker-compose.yaml                  # sensor-ms, streamprocessing-ms-1, vst-ingress, centralizedb
    ├── configs/                             # vst_config.json, adaptor_config.json, nginx-*.conf
    ├── sdrc/                                # SDRC overlay (gated by COMPOSE_PROFILES=sdrc)
    └── nvstreamer/                          # NVStreamer compose subtree
        ├── compose.env
        └── docker-compose.yaml
```

---

## Manual Docker Compose flow (advanced / debugging)

The script just wraps `docker compose` calls. To drive compose directly (e.g., for debugging a stuck container):

### Deploy NVStreamer

```bash
cd docker-compose/nvstreamer

# Edit COMPOSE_PROFILES + NVSTREAMER_VIDEO_<N> paths in compose.env. Paths MUST be absolute.
# Example:
#   COMPOSE_PROFILES=nvstreamer-1,nvstreamer-2
#   NVSTREAMER_VIDEO_1=/absolute/path/to/videos1
#   NVSTREAMER_VIDEO_2=/absolute/path/to/videos2

docker compose -f docker-compose.yaml --env-file ./compose.env up --force-recreate -d

# Stop
docker compose -f docker-compose.yaml --env-file ./compose.env down --remove-orphans -v
```

### Deploy VST stream-processing

```bash
cd docker-compose/

# Edit compose.env (HOST_IP, VST_CONFIG_PATH, VST_VOLUME, VST_USE_SDRC, VST_ADAPTOR, NGINX_MODE)
# Edit configs/vst_config.json (max_devices_supported, total_video_storage_size_MB)
# Edit configs/rtsp_streams.json to register NVStreamer endpoints

# Start
docker compose -f docker-compose.yaml --env-file ./compose.env up --force-recreate -d

# Start in SDRC mode (after flipping the toggle in compose.env)
docker compose -f docker-compose.yaml --env-file ./compose.env --profile sdrc up --force-recreate -d

# Stop
docker compose -f docker-compose.yaml --env-file ./compose.env --profile sdrc down --remove-orphans -v
```

---

## Recommended deploy ordering

1. **NVStreamer first** — sensor-MS will scan it for available streams during startup.
2. **Wait for NVStreamer HTTP** — each active instance answers on its HTTP port (`curl http://<HOST_IP>:31000/` returns a response).
3. **VST stream-processing** — sensors register, recorders start, dashboard becomes reachable.

The `--target all` deploy handles this ordering automatically.

---

## Using an AI coding agent

The repo ships agent skills under `services/vios/.claude/sqa/` that map natural-language deploy requests to this script. Examples:

| Prompt | Action |
|---|---|
| *"deploy vios"* | Default deploy — **VIOS only** (NVStreamer is opt-in; not deployed or probed) |
| *"deploy vios + nvstreamer"* | Full-stack deploy (NVStreamer + VIOS) |
| *"deploy vios in milestone adaptor mode"* | Configures `VST_ADAPTOR=milestone_onvif`, asks for ip/user/password if missing, writes them with a dry-run diff |
| *"recreate sensor"* / *"recreate nvstreamer"* | Surgical per-container restart |
| *"clean stop"* / *"wipe data"* | `stop --clean` (sudo-free; throwaway-container cleanup) |

See `services/vios/.claude/sqa/DEPLOYMENT_AGENT.md` for the full intent-classifier rules.

---

## Troubleshooting

| Symptom | First check |
|---|---|
| Container won't start | `docker logs <container>` — the script also dumps health-check failures on timeout |
| Sensor never registers | Adaptor mismatch — verify `VST_ADAPTOR` in `compose.env` and the matching `enabled: true` entry in `adaptor_config.json` |
| Sensor registers but no streams | RTSP source unreachable — check NVStreamer HTTP / Milestone VMS connectivity from the host |
| `--clean` left data behind | Postgres uses a Docker-managed named volume (`pg_data`); the script removes it via `docker compose down -v` |
| `sudo` prompt mid-deploy | Pass `--skip-sysctl` (script also auto-skips on non-TTY) |
| `Incorrect Repository Format` on `docker pull` | Multi-arch OCI index with attestation manifests; pull with `--platform linux/amd64` (or `linux/arm64`) explicitly |
| Need to start over | `stop --clean` then `deploy --force`, OR `deploy --fresh-start --force` in one shot |

For the full agent-side troubleshooting tree, see `services/vios/.claude/sqa/guides/troubleshooting.md`.

For the full flag surface:

```bash
python3 oneclick_dc_deployment.py --help
```
