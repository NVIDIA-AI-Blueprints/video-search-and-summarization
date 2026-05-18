---
name: vss-setup-behavior-analytics
description: >
  Deploy the `vss-behavior-analytics` service standalone — no agent, no perception, no UI.
  Use when the user says "deploy behavior analytics", "run behavior-analytics standalone",
  "set up behavior analytics service", "change the analytics entrypoint", "swap the
  behavior-analytics config / calibration", "run analytics 2D / 3D / mv3dt / dev_example /
  fusion_search by itself", or wants to point behavior-analytics at a custom config /
  calibration file without redeploying the full warehouse blueprint. Walks the user
  through entrypoint selection, config-source choice, optional calibration, and the
  dynamic-config / dynamic-calibration flows once a broker is reachable.
license: Apache-2.0
metadata:
  version: "3.2.0"
  github-url: "https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization"
  tags: "nvidia blueprint operational deployment behavior-analytics"
---

# VSS Setup Behavior Analytics — Standalone

Deploy **just** the `vss-behavior-analytics` container (the spatial-AI analytics pipeline from the upstream `behavior-analytics` repo), not as part of the full warehouse blueprint stack.

The full operational walkthrough — entrypoint table, config-source options, calibration types, dynamic-update wire contract, troubleshooting — is [`references/deploy-behavior-analytics-service.md`](references/deploy-behavior-analytics-service.md). This SKILL.md only handles routing and prerequisites.

## When to use

- "Deploy behavior analytics" / "run behavior-analytics standalone"
- "I just want to run analytics, not the full stack"
- "Change the entrypoint to fusion_search / dev_example / analytics 3D / mv3dt"
- "Use my own behavior-analytics config / calibration JSON"
- "Point behavior-analytics at the warehouse-3d (or mv3dt) config without spinning up the rest of the warehouse profile"
- "Dynamic config / dynamic calibration into a running behavior-analytics"

## Prerequisites

1. **Repo checkout** with `$VSS_APPS_DIR` pointing at `<repo>/deploy/docker/`. Required by the service compose's volume binds.
2. **NGC credentials** — `$NGC_CLI_API_KEY` set so docker can pull the image. See [`../vss-deploy-profile/references/ngc.md`](../vss-deploy-profile/references/ngc.md).
3. **Optional broker** (Kafka / Redis Streams / MQTT). The container starts fine **without** one — the Kafka client retries a bounded number of times, then the app exits and `restart: always` cycles the container. Status will show `Restarting (N)` in `docker ps` until a broker is reachable. With a broker, dynamic config / dynamic calibration over `mdx-notification` become available.
4. **Optional config / calibration files on disk** if the user is bringing their own.

If $1 or $2 fails, surface the gap before going further.

## Workflow

Hand the user [`references/deploy-behavior-analytics-service.md`](references/deploy-behavior-analytics-service.md) and walk them through its four steps in order:

1. Pick an entrypoint (analytics 2D / 3D / mv3dt, dev_example, fusion_search).
2. Choose a config — profile-shipped or custom.
3. Choose a calibration — optional; profile-shipped or custom; otherwise the app waits for a dynamic-calibration notification.
4. Decide whether a broker is reachable; if yes, point them at the dynamic-update flows.

The compose-file edits, YAML diffs, deploy + verify commands, and troubleshooting table all live in that reference — don't duplicate them here.

## Dynamic updates (runtime, no restart)

Once the container is up **and a broker is reachable**, two runtime-update flows are available — neither requires redeploying:

### Dynamic config

Publish an `upsert` (per-key patch) or `upsert-all` (full snapshot) message to the `mdx-notification` topic with Kafka key `behavior-analytics-config` and headers:

- `event.type`: `upsert` | `upsert-all` | `request-config` | `ack`
- `reference-id`: `video-analytics-api-<uuid>` (web-api originated), `behavior-analytics-<uuid>` (bootstrap reply), or the source-type literal (`kafka` / `redis` / `mqtt`) for direct-publisher upserts.

Body: `{"status": ..., "config": <patch>, "error": ...}`.

The listener validates each message at the envelope layer (rejects unknown keys, missing config, malformed status/error) and at the per-payload layer (rejects forbidden sections, bad item shapes). Successful upserts are persisted to disk, applied to every worker, and ACK'd back over the topic.

Full wire contract + ack semantics: [`references/dynamic-config.md`](references/dynamic-config.md).

### Dynamic calibration

Publish to the same topic with Kafka key `calibration` and headers:

- `event.type`: `upsert-all` (full snapshot) | `upsert` (per-sensor merge) | `delete` (per-sensor removal)
- `timestamp`: ISO-8601 UTC (`YYYY-MM-DDTHH:MM:SS.fffZ`).

Body: JSON sensor list (and ROIs / tripwires / homographies for `upsert-all`).

The listener validates against the vendored AJV schema before persisting. Schema violations log a `calibration schema violation` warning and are dropped — the previously-good calibration stays loaded.

Full wire contract + per-action validation policy: [`references/dynamic-calibration.md`](references/dynamic-calibration.md).

Both flows live entirely on the broker — the producer can be `video-analytics-api`, your own script, or any Kafka client that mirrors the wire shape. They're the recommended way to change configuration after the container is running, so the operator doesn't have to redeploy.

## Routing rules

- If the user wants "the full stack" (UI / agent / perception): hand off to [`vss-deploy-profile`](../vss-deploy-profile/SKILL.md) with profile `warehouse` (or `alerts`). Don't run this skill in parallel.
- If the user wants to publish a runtime config / calibration update to an already-running container: walk the [Dynamic updates](#dynamic-updates-runtime-no-restart) section above. Both flows need a reachable broker.
- If the user describes a behavior-analytics behavior change they want to validate (new incident type, new ROI rule, new sensor): point them at [`references/configuration.md`](references/configuration.md), [`references/dynamic-config.md`](references/dynamic-config.md), or [`references/dynamic-calibration.md`](references/dynamic-calibration.md) before editing the JSON.
