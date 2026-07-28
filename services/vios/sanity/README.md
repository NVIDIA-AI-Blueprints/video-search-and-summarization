<!--
SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# VIOS + NVStreamer Sanity Harness

An orchestrated, evidence-producing sanity run for the VIOS stream-processor with NVStreamer.
It deploys the stack, provisions streams, drives each use-case (download, picture, WebRTC
live/replay, video-wall) with and without **overlay**, captures a snapshot/clip per use-case,
and emits a single **PDF** with the evidence and its HTTP links.

## What it runs

Plans live in [`sanity_plans.yaml`](./sanity_plans.yaml); toggle each with `enabled:`:

| Plan | Consumer | Streams | Overlay | Notes |
|------|----------|---------|---------|-------|
| Plan-1 | redis | variant matrix (codec/res/fps) from one video | full | includes `download_overlay_long` (60s sustained-box) |
| Plan-2 | kafka | 4 synchronized copies | full | WebRTC video-wall + latency perf |
| Plan-3 | kafka + webhooks | 1 NVStreamer RTSP + 1 VIOS file sensor | RTSP download/picture | direct mode; no Redis, SDRC, synchronization, or video-wall |
| Plan-4 | — | Milestone VMS cameras (adaptor) | none | Milestone owns metadata |
| Plan-5 | redis | ONVIF-discovered cameras | full | ONVIF adaptor (no NVStreamer) |

Each ENABLED plan runs against every ENABLED entry in its `systems:` map (local/remote/...).

## Dependencies

One command installs everything (pip deps, the Playwright browser, ffmpeg, and Google Chrome):

```bash
python3 services/vios/sanity/run_sanity.py --install-deps
```

Or manually:

```bash
pip install -r services/vios/sanity/requirements.txt
playwright install chromium          # PDF rendering + UI automation
bash services/vios/sanity/install_deps.sh   # ffmpeg + Google Chrome (apt/.deb)
```

**WebRTC capture requires Google Chrome** (auto-detected). The WebRTC video is H.264/H.265 and
Playwright's bundled Chromium lacks those proprietary codecs (the panel renders black); Chrome has
them. Override the browser with `VIOS_SANITY_CHROME=<path>`. `ffmpeg` (provides `ffprobe`) and
`docker` must also be on PATH. A run fails fast if a prerequisite is missing.

The harness deploys/uses the docker-compose stack under
`services/vios/deployment/stream-processing/docker-compose`, so a working Docker + that
deployment tooling (and access to the VIOS/NVStreamer images) is required.

## Run

Point it at your containers and it does the rest — writes them into `compose.env`, pulls them,
sets `HOST_IP` + repo-local paths, cleans any current deployment, deploys, runs, and serves the
evidence. You never hand-edit `compose.env`.

```bash
python3 services/vios/sanity/run_sanity.py \
    --plans services/vios/sanity/sanity_plans.yaml \
    --streamprocessing-image <my-streamprocessing-image> \
    --sensor-image           <my-sensor-image> \
    --nvstreamer-image       <my-nvstreamer-image> \
    --out /tmp/vios_sanity/report.pdf
```

Images can instead live in `sanity_plans.yaml` under `defaults.images:` (CLI overrides them).
If you already set the images in `compose.env`, just omit the flags.

Per-plan `setup.vst_config` and `setup.nvstreamer_config` override each service's
`vst_config.json`. Broker and notification settings belong in
`setup.vst_notification_config` and `setup.nvstreamer_notification_config`; the harness writes
those to the separate `notification_config.json` mounted by each service.

Plan-3 sets `setup.deployment_mode: direct` and `setup.event_transport: webhook`. The harness
starts the overlay plugin's `camera_streaming` and `camera_remove` HTTP receivers before VIOS,
disables message-broker event publication, and verifies that neither Redis nor SDRC is running.
Kafka remains the live overlay-metadata consumer; it is not used for camera lifecycle events.

Useful flags: `--only <case,case>` (subset), `--deploy-only`, `--no-serve` (don't auto-start the
evidence server), `--from-json <results.json>` (re-render the PDF without re-running),
`--es-retention-hours <hours>` (fake-ES history per sensor; default 3), `--restore-config`
(restore the config snapshot), `--install-deps`.

## Output, evidence & hosting

- **PDF** → `--out` (default `/tmp/vios_sanity/report.pdf`), rendered via headless Chromium.
- **Evidence** (snapshots/clips) → `out_dir` (default `/tmp/vios_sanity`), and each is copied
  into a unique run/plan namespace below `share_dir`. PDF links therefore do not collide when
  Plan 1, Plan 2, and a consolidated report share the same artifact server.
- **Failure manifest** → `failed_cases.json`, with UTC (`Z`) run/test timestamps, timezone,
  host information, summary counts, and the exact prepared HTTP request or browser invocation
  (full URL, query parameters, headers, body, and automation command) for every failed case.
- The harness **auto-starts a persistent artifact server** over `share_dir`. When user systemd
  is available, the server runs as a supervised transient unit so it survives the invoking shell
  and agent process. It binds to
  `0.0.0.0:18080` by default so the report is reachable at the host's corporate-network IP after
  the run exits. If that port is occupied by a different service, it selects the next free port.
  The final output prints an inline browser URL and an explicit download URL (`?download=1`).
  Disable the server with `--no-serve`, or point at an externally managed server with
  `VIOS_SANITY_FILE_SERVER`.

- A run is not reported as complete until its browser-delivery gate verifies the PDF's forced
  download response and every evidence URL. MP4/WebM checks require HTTP byte-range (`206`)
  support and a `video/*` content type. Non-blocking VST URL tests publish durable copies instead
  of leaving temporary VST storage URLs in the report. Generated HTML/PDF and published files
  are explicitly browser-readable (`0644`).

  HEVC/H.265 evidence is preserved as `*.original-hevc.mp4`; its report link points to an
  automatically generated H.264 preview (maximum 1080p, fast-start MP4) so Chrome can play it.
  Publication fails if the browser preview cannot be generated.

  The detached-process fallback is used only when user systemd is unavailable and emits a
  warning. For durable CI or shared-host use without user systemd, configure an externally
  managed range-capable server with `VIOS_SANITY_FILE_SERVER`.

  Corporate-network reachability still requires routing to the reported host IP and an inbound
  firewall rule for the selected TCP port. Override the advertised address with
  `VIOS_SANITY_HOST_IP` when the auto-detected interface is not the corporate-facing interface.

### Environment overrides

| Variable | Default | Purpose |
|----------|---------|---------|
| `VIOS_SANITY_HOST_IP` | auto-detected | host IP used in evidence links |
| `VIOS_SANITY_SHARE_DIR` | `/tmp/vios_sanity/share` | where published evidence is copied |
| `VIOS_SANITY_OUT_DIR` | `/tmp/vios_sanity` | working/evidence dir |
| `VIOS_SANITY_FILE_SERVER` | `http://<host_ip>:18080` | base URL for evidence links |
| `VIOS_SANITY_FILE_SERVER_PORT` | `18080` | port when deriving the base URL |
| `VIOS_SANITY_FILE_SERVER_BIND` | `0.0.0.0` | listen address for the built-in artifact server |
| `VIOS_SANITY_ARTIFACT_NAMESPACE` | generated per invocation | stable URL namespace for one report run |
| `VIOS_SANITY_CHROME` | system Google Chrome (auto-detected) | codec-capable browser for WebRTC capture (H.264/H.265) |
| `VIOS_SANITY_ES_RETENTION_HOURS` | `3` | rolling fake-ES metadata history retained per sensor |

## Overlay metadata

Overlay evidence is fed by a continuous metadata service
([`test/bdd_tests/scripts/overlay/metadata_service.py`](../test/bdd_tests/scripts/overlay/metadata_service.py))
that receives `camera_streaming` events from Redis in Plans 1/2/5 or from the two direct-mode
webhooks in Plan-3, reads each VIOS RTSP proxy's SEI, publishes live metadata to the configured
consumer broker, and serves a fake Elasticsearch (`:19200`) that VIOS's
`overlay.video_metadata_server` queries for download/replay overlay. The fake-ES retains a
rolling 3-hour event-time window per sensor by default (324,000 documents at 30 fps). Use
`--es-retention-hours 10` when a deployment needs ten hours of metadata; there is no separate
document-count ceiling. The harness starts and stops it per run.

### Preserve an active plan

Use `--keep-deployment` for exactly one already-running local NVStreamer plan. It performs no stop,
deploy, configuration restore, or service recreation. Use `--leave-deployment` when the harness
must deploy first and then leave Plan 1 plus its synthetic metadata service running after sanity.
Multi-plan runs use normal clean transitions.
Generated provisioning copies and control videos are cleaned after report publication; use
`--keep-transients` to retain them. During a normal Plan-1 → Plan-2 transition, clean-stop removes
the VST database/recordings and NVStreamer runtime metadata, while served videos are removed only
when their names match the current sanity input prefix; unrelated and UI-uploaded clips are kept.
All failure-manifest timestamps are UTC (`Z`).
