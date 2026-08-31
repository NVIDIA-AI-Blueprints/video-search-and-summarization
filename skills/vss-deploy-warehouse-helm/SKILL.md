---
name: vss-deploy-warehouse-helm
description: Use when the user asks to deploy, upgrade, or size the VSS warehouse blueprint (2D / 3D / MV3DT) on Kubernetes via Helm — as opposed to Docker Compose, which is covered by vss-deploy-profile's warehouse reference. Handles GPU-aware NUM_STREAMS capping so the deployment matches what the perception pipeline can actually sustain.
license: Apache-2.0
metadata:
  version: "1.0.0"
  author: "NVIDIA Video Search and Summarization team"
  github-url: "https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization"
  tags: "nvidia blueprint deployment helm kubernetes warehouse"
---
# VSS Warehouse — Helm Deploy

Do not use this skill for:

- Docker Compose warehouse deployment — use `vss-deploy-profile`'s
  [`references/warehouse.md`](../vss-deploy-profile/references/warehouse.md); it owns the
  `HARDWARE_PROFILE` → GPU mapping table and the `blueprint_config.yml` stream-cap semantics
  this skill reuses.
- Non-warehouse Helm profiles (`base`, `search`, `lvs`, `alerts`) — those don't have a
  `bp-configurator` GPU-aware stream cap; deploy them per their own chart READMEs.
- Runtime operations (adding cameras, querying behavior analytics) — use `vss-manage-alerts` /
  `vss-query-analytics` against the running deployment.

## Why this exists

Docker Compose's warehouse deploy caps `NUM_STREAMS` per GPU automatically: the configurator reads
`deploy/docker/industry-profiles/warehouse-operations/blueprint-configurator/blueprint_config.yml`'s
`max_streams_supported` table for the detected `HARDWARE_PROFILE` and mode, and clamps
`final_stream_count = min(NUM_STREAMS, max_streams_supported)`.

The Helm charts (`deploy/helm/industry-profiles/warehouse-operations/warehouse-{2d,3d,mv3dt}-app`)
do **not** do this — their `bp-configurator.env` ships a fixed `NUM_STREAMS` and never sets
`HARDWARE_PROFILE` at all (`ENABLE_PROFILE_CONFIGURATOR=false`). A user who asks for more streams
than the GPU can sustain gets no protection. This skill closes that gap by computing the same cap
Compose would apply and writing it into a Helm values-override file before install.

## Available Scripts

| Script | Purpose | Arguments |
|---|---|---|
| [`../../deploy/helm/industry-profiles/warehouse-operations/scripts/compute_stream_cap.py`](../../deploy/helm/industry-profiles/warehouse-operations/scripts/compute_stream_cap.py) | Detect GPU (or take an explicit `HARDWARE_PROFILE`), read `max_streams_supported` from `blueprint_config.yml`, cap the requested stream count, and write a `bp-configurator.env`-patched values-override YAML. | `--mode {2d,3d,mv3dt} --num-streams N [--hardware-profile P] [--gpu-index I] [-o FILE]` |

This script has no skill/agent dependency — a user who doesn't want to use this skill can run it
directly (`python3 compute_stream_cap.py --mode 2d --num-streams 8`) and pass the generated file to
`helm upgrade/install -f` themselves.

## Instructions

1. **Determine mode** (`2d` / `3d` / `mv3dt`) and the desired stream count from the user's request.
2. **Run the stream-cap script** from the repo root:
   ```bash
   python3 deploy/helm/industry-profiles/warehouse-operations/scripts/compute_stream_cap.py \
     --mode <mode> --num-streams <N> -o values-stream-cap.generated.yaml
   ```
   - Without `--hardware-profile`, it runs `nvidia-smi` on GPU index 0 and maps the name to a
     `HARDWARE_PROFILE` using the same table as [`vss-deploy-profile`'s warehouse
     reference](../vss-deploy-profile/references/warehouse.md#supported-hardware). If detection
     fails or the GPU isn't in that table, pass `--hardware-profile` explicitly.
   - It prints the effective (possibly capped) stream count and the `syncFileCount` value to keep
     in step (see [`references/streams.md`](references/streams.md) for why).
   - It never lowers the request silently without saying so — a cap is always logged to stderr.
3. **Prepare the rest of the values** — secrets, ingress/`externalHost`, storage class — per
   [`references/streams.md`](references/streams.md), which has the full `helm upgrade --install`
   command with the generated file layered in via `-f`.
4. **Install/upgrade**, chaining the generated file after any other `-f`/`--set` overrides so it
   wins on `bp-configurator.env`:
   ```bash
   helm dependency update deploy/helm/industry-profiles/warehouse-operations/warehouse-<mode>-app
   helm upgrade --install wh deploy/helm/industry-profiles/warehouse-operations/warehouse-<mode>-app \
     -n <namespace> --create-namespace \
     -f values-stream-cap.generated.yaml \
     --set vios.vss-vios-nvstreamer.syncFileCount=<effective-streams> \
     ...  # secrets/ingress overrides, see references/streams.md
   ```
5. **Re-run the script whenever `NUM_STREAMS` or the target GPU changes** — the values-override
   file isn't tracked automatically; re-generate and re-`helm upgrade` after a hardware change.

## Prerequisites

Same NGC secrets, storage class, and ingress prerequisites as any warehouse Helm deploy — see
`deploy/helm/industry-profiles/warehouse-operations/warehouse-<mode>-app/README.md` §Prerequisites.
This skill only adds the stream-cap step; it doesn't replace chart setup.
