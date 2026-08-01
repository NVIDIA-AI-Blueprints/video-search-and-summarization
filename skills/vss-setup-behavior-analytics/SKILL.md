---
name: vss-setup-behavior-analytics
description: Use this skill to deploy the vss-behavior-analytics service standalone (entrypoint, config-source, optional calibration). Not for the full warehouse deploy.
license: Apache-2.0
metadata:
  author: "NVIDIA Video Search and Summarization team"
  version: "3.2.0"
  github-url: "https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization"
  tags: "nvidia blueprint operational deployment behavior-analytics"
---

## Purpose

Deploy the behavior-analytics service standalone with the user's chosen entrypoint, config, and calibration.

## Instructions

Follow the routing tables and step-by-step workflows below. Each section that ends in *workflow*, *quick start*, or *flow* is intended to be executed top-to-bottom. Detailed reference material lives in `references/`.

## Examples

Worked end-to-end examples are kept under `evals/` (each `*.json` manifest
contains a runnable scenario). Run a Tier-3 evaluation to replay them:

```bash
nv-base validate skills/vss-setup-behavior-analytics --agent-eval
```

A minimal standalone bring-up looks like:

```bash
cd $REPO/deploy/docker
export VSS_APPS_DIR=$(pwd)
docker compose -f services/analytics/behavior-analytics/compose.yml up -d vss-behavior-analytics-base
```

Follow `references/deploy-behavior-analytics-service.md` for the full
workflow (entrypoint pick, config source, dynamic updates).

## Limitations

- **No HTTP API.** This is a broker stream processor — it reads and writes Kafka / Redis Streams / MQTT and serves
  no REST endpoint, so there is nothing to `curl` and no `/health` to probe. Verify it through container logs and
  the output topics.
- **CPU-only.** It loads no models and reserves no GPU (`gpu_count: 0` in this skill's own evals), so GPU memory and
  NIM rate-limits are not constraints here.
- **At least one processor must be enabled, and forgetting is quiet.** With every `numWorkersFor*`
  at `0` the runner logs `FATAL - Error in app: No processors registered`, closes its listeners and returns — the
  process exits **0**, so to anything watching exit codes it looks like a clean shutdown. Only the
  log distinguishes it.
- **A destination the config omits is a disabled output, not an error.** The sink logs
  `No destination configured for '<key>'; output for it is disabled` once per key and drops the rest, so a missing
  topic looks like an empty stream rather than a failure.
- **One behavior producer per deployment.** Two instances producing behaviors for the same sensors write every
  behavior twice, from processes with independent state. Nothing detects this.

## Troubleshooting

- **Error**: container restart-loops immediately; log shows `FATAL - Config file ... contains invalid JSON` or
  `... has invalid structure`. **Cause**: the mounted config is malformed or fails `AppConfig` validation.
  **Solution**: fix the JSON / schema — the app calls `exit(1)`, so compose's restart policy cycles it forever.
- **Error**: container exits almost immediately with status `Exited (0)` and the log ends in
  `FATAL - Error in app: No processors registered in app ...`. **Cause**: every `numWorkersFor*` is `0` (the shipped
  `composite_config.json` ships this way on purpose). **Solution**: set the worker count for the capabilities you
  want. Note the exit code is 0, so a `restart: on-failure` policy will *not* cycle it — it just stays stopped.
- **Error**: container shows `Restarting (N)` and the log ends in a Kafka/Redis connection error. **Cause**: no
  broker reachable. The client retries a bounded number of times, then the worker raises and the scheduler shuts the
  whole app down. **Solution**: bring up the broker, or expect the restart loop until one exists.
- **Error**: an expected topic stays empty. **Cause**: either the destination is not defined in the config (look for
  the one-time `No destination configured` warning) or the processor that writes it has `0` workers.
  **Solution**: define the topic and set the worker count.
- **Error**: log shows `Error reading calibration type from ...: defaulting to IMAGE`. **Cause**: `--calibration`
  was omitted or unreadable. **Solution**: this is not fatal — the app runs image-calibrated, which silently changes
  coordinate semantics. Mount a calibration if you meant a cartesian or geo deployment.

# VSS Setup Behavior Analytics — Standalone

Deploy **just** the `vss-behavior-analytics` container (the spatial-AI analytics pipeline from the upstream `behavior-analytics` repo), not as part of the full warehouse blueprint stack.

The full operational walkthrough — entrypoint table, config-source options, calibration types, dynamic-update wire contract, troubleshooting — is [`references/deploy-behavior-analytics-service.md`](references/deploy-behavior-analytics-service.md). This SKILL.md only handles routing and prerequisites.

## When to use

- "Deploy behavior analytics" / "run behavior-analytics standalone"
- "I just want to run analytics, not the full stack"
- "Change the entrypoint to search_and_alerts / analytics 3D / mv3dt / the config-driven Composite app"
- "Use my own behavior-analytics config / calibration JSON"
- "Point behavior-analytics at the warehouse-3d (or mv3dt) config without spinning up the rest of the warehouse profile"
- "Dynamic config / dynamic calibration into a running behavior-analytics"

## When NOT to use

This skill deploys one container. Hand off instead when the request is:

- **The full stack** (UI, agent, perception, storage) — [`vss-deploy-profile`](../vss-deploy-profile/SKILL.md). Do not run both in parallel; it owns behavior-analytics as part of the profile.
- **Producing the frames this service consumes** — detection/tracking is upstream: [`vss-deploy-detection-tracking-2d`](../vss-deploy-detection-tracking-2d/SKILL.md) or [`-3d`](../vss-deploy-detection-tracking-3d/SKILL.md). This service analyses `mdx-raw`; it does not create it.
- **Generating a calibration file** — [`vss-generate-video-calibration`](../vss-generate-video-calibration/SKILL.md). This skill only *mounts* one.
- **Reading the output** — incidents, metrics and sensor queries are [`vss-query-analytics`](../vss-query-analytics/SKILL.md); alert workflows and verification verdicts are [`vss-manage-alerts`](../vss-manage-alerts/SKILL.md).
- **The REST API in front of the data** — that is a different service: [`vss-setup-video-analytics-api`](../vss-setup-video-analytics-api/SKILL.md). Behavior-analytics itself exposes no HTTP endpoint.

## Prerequisites

1. **Repo checkout** with `$VSS_APPS_DIR` pointing at `<repo>/deploy/docker/`. Required by the service compose's volume binds.
2. **Registry access** — none needed for the default image: `ghcr.io/nvidia-ai-blueprints/vss/vss-behavior-analytics` is public, so `docker pull` works unauthenticated. You only need credentials if you override `VSS_CONTAINER_REGISTRY` to NGC — see [`references/ngc-api-key-registry-login.md`](references/ngc-api-key-registry-login.md).
3. **Docker runtime** — Docker Engine **28.3.3** with Docker Compose plugin **v2.39.1+**. Verify with `docker --version` and `docker compose version`.
4. **Optional broker** (Kafka / Redis Streams / MQTT). The container starts fine **without** one — the Kafka client retries a bounded number of times, then the app exits and `restart: always` cycles the container. Status will show `Restarting (N)` in `docker ps` until a broker is reachable. With a broker, dynamic config / dynamic calibration over `mdx-notification` become available.
5. **Optional config / calibration files on disk** if the user is bringing their own.

If any required prerequisite fails, surface the gap before going further.

## Workflow

Hand the user [`references/deploy-behavior-analytics-service.md`](references/deploy-behavior-analytics-service.md) and walk them through its steps in order:

1. Pick an entrypoint (analytics 2D / 3D / mv3dt, search_and_alerts, or CompositeApp for config-driven composition).
2. Choose a config — profile-shipped or custom.
3. Choose a calibration — optional; profile-shipped or custom; otherwise the app waits for a dynamic-calibration notification.
4. Decide whether a broker is reachable; if yes, point them at the dynamic-update flows.

The compose-file edits, YAML diffs, deploy + verify commands, and troubleshooting table all live in that reference — don't duplicate them here.

## Dynamic updates (runtime, no restart)

Once the container is up **and a broker is reachable**, two runtime-update flows are available — neither requires redeploying:

**Dynamic config** — patch `app[]` / `sensors[]` at runtime by publishing to `mdx-notification` under Kafka key
`behavior-analytics-config`. Only allowlisted keys apply; everything else is rejected in the ack rather than
silently ignored. Successful upserts are persisted to disk, applied to every worker, and ACK'd back.
Message shape, headers, ack semantics and the allowlist: [`references/dynamic-config.md`](references/dynamic-config.md).

**Dynamic calibration** — replace sensors / ROIs / tripwires / homographies at runtime under Kafka key
`calibration` on the same topic. Payloads are schema-validated before anything is persisted, and a violation is
dropped with a `calibration schema violation` warning, leaving the previously-good calibration loaded.
Message shape, per-action validation policy and the no-ack caveat:
[`references/dynamic-calibration.md`](references/dynamic-calibration.md).

Both flows live entirely on the broker — the producer can be `video-analytics-api`, your own script, or any Kafka client that mirrors the wire shape. They're the recommended way to change configuration after the container is running, so the operator doesn't have to redeploy.

## Routing rules

- If the user wants "the full stack" (UI / agent / perception): hand off to [`vss-deploy-profile`](../vss-deploy-profile/SKILL.md) with profile `warehouse` (or `alerts`). Don't run this skill in parallel.
- If the user needs to fold behavior-analytics into a composed/multi-service deployment — which Kafka topics it consumes and emits, and how it wires to producers/consumers around it: see the integration contract in [`references/integrate-behavior-analytics-service.md`](references/integrate-behavior-analytics-service.md).
- If the user wants to publish a runtime config / calibration update to an already-running container: walk the [Dynamic updates](#dynamic-updates-runtime-no-restart) section. Both flows need a reachable broker.
- If the user describes a behavior-analytics behavior change they want to validate (new incident type, new ROI rule, new sensor): point them at [`references/configuration.md`](references/configuration.md), [`references/dynamic-config.md`](references/dynamic-config.md), or [`references/dynamic-calibration.md`](references/dynamic-calibration.md) before editing the JSON.
