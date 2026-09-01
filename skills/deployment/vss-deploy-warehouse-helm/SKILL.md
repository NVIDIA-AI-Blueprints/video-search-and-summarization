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
| [`../../../deploy/helm/industry-profiles/warehouse-operations/scripts/compute_stream_cap.py`](../../../deploy/helm/industry-profiles/warehouse-operations/scripts/compute_stream_cap.py) | Detect GPU (or take an explicit `HARDWARE_PROFILE`), read `max_streams_supported` from `blueprint_config.yml`, cap the requested stream count, and write a `bp-configurator.env`-patched values-override YAML. Pass any values file(s) your install already uses via `-f` so custom `bp-configurator.env` entries in them aren't dropped. | `--mode {2d,3d,mv3dt} --num-streams N [--hardware-profile P] [--gpu-index I] [-f VALUES]... [-o FILE]` |

This script has no skill/agent dependency — a user who doesn't want to use this skill can run it
directly (`python3 compute_stream_cap.py --mode 2d --num-streams 8`) and pass the generated file to
`helm upgrade/install -f` themselves.

## Instructions

1. **Precheck the cluster and required inputs** before touching Helm — don't assume a fresh
   cluster already has these. Run each check and report pass/fail back to the user:
   ```bash
   kubectl cluster-info                         # cluster reachable
   kubectl get nodes                             # all nodes Ready
   kubectl get storageclass                      # a StorageClass exists
   kubectl get nodes -o jsonpath='{.items[*].status.allocatable.nvidia\.com/gpu}{"\n"}'
                                                  # non-empty -> GPU Operator has registered GPUs
   helm version --short                           # Helm 3.x
   ```
   Also ask whether the user already has an NGC API key — that can't be checked from cluster
   state, only asked about.

   **On any failure, don't just link the user to the README and stop — hand them the actual fix,
   copied from the chart README, and offer to run it for them:**
   - No `StorageClass` → relay the `local-path-provisioner` install + `kubectl patch storageclass`
     snippet from `warehouse-<mode>-app/README.md` §Prerequisites (bare-metal option) — or ask
     what StorageClass they intend to use if they already have one in mind.
   - No `nvidia.com/gpu` allocatable → relay the NVIDIA GPU Operator install steps from
     §Prerequisites (links to the GPU Operator getting-started guide) and the recommended driver
     versions listed there.
   - Cluster unreachable / nodes not `Ready` → this one the user has to fix outside Helm/this
     skill entirely; say so plainly rather than suggesting a chart-level fix.
   - No NGC API key → point at §Required secrets in the chart README for how to create the pull
     secret, don't just say "get an NGC API key."

   Only proceed to step 2 once cluster/StorageClass/GPU-Operator/Helm all pass and the user has
   confirmed they have an NGC API key — an install started before that will fail partway through
   in a way that's harder to debug than catching it here.
2. **Ask ingress vs. NodePort** — this determines both what's installed in this step and which
   install command gets used in step 6, so resolve it before going further, don't default silently
   to one or the other:
   - **Ingress** (needed off-cluster / for a stable hostname) → check whether an ingress
     controller is already installed (`kubectl get ingressclass`). If not, relay the
     `haproxy-ingress` install snippet from `warehouse-<mode>-app/README.md` §"Install the ingress
     controller" and offer to run it. Note this is a one-time, per-cluster step, not per-app.
   - **NodePort** (simplest for a quick local/single-node deploy, no ingress controller needed) →
     tell the user the chart ships `values-nodeport.yaml` for this — the install command in step 6
     changes to `-f values-nodeport.yaml` layered under the stream-cap file, and the service URLs
     move to `<NODE_IP>:<port>` instead of `<NODE_IP>/<path>`. See §"No ingress controller:
     NodePort" and §URLs in the chart README for the exact ports.
   If the user hasn't said which they want and there's no clear signal (e.g. "just get it running
   locally" implies NodePort; "expose it for the team" implies Ingress), ask rather than guessing.
3. **Determine mode and whether to enable Alerts:**
   - **Mode.** Use `2d`, `3d`, or `mv3dt` if the request already names one. Otherwise ask —
     don't guess:
     - `2d` — 2D object detection & tracking.
     - `3d` — standalone RTVI-CV-3D / multi-camera 3D tracking on calibrated inputs.
     - `mv3dt` — Multi-View 3D Tracking warehouse profile.
   - **Alerts.** Not a fourth mode — an optional overlay, off by default, and only available on
     `2d` (`warehouse-2d-app` is the only chart with `vss-alert-bridge`/`agent`/`vss-agent-ui` as
     dependencies; `3d` and `mv3dt` don't have them). If the user is on `3d`/`mv3dt` and asks for
     Alerts, say it's not available there instead of trying to enable it. On `2d`, ask the user
     whether they want it, and explain the tradeoff first rather than enabling or skipping it
     for them: without Alerts they get the raw RT-CV detection/tracking stream; with it, detections
     also pass through a behavior-analytics stage and a VLM verification step (RT-VLM) before
     anything is surfaced as an incident, queryable through the agent/agent UI. That verification
     step is the reason to turn it on — it's what keeps every raw detection from becoming a ticket.
     If they want it, note the four flags have to be set together
     (`vss-alert-bridge.enabled`, `agent.enabled`, `vss-agent-ui.enabled`,
     `rtvi.vss-rtvi-vlm.enabled` — swap the last for an external `vlmBaseUrl` if not using the
     in-cluster VLM) plus Kafka/Elasticsearch/VST endpoint values. Full block:
     `warehouse-2d-app/README.md` §Alerts — layer it in during step 6.
   - **Stream count.** Ask if not given; it sizes the `NUM_STREAMS` cap in step 5.
4. **Ask whether the install customizes `bp-configurator.env`** (extra env vars, different
   defaults) — don't assume none exist just because the user didn't mention one. If they're
   unsure, ask them to check their existing `helm upgrade --install` command for anything touching
   `bp-configurator.env`, file-based or inline. State the outcome back to them either way:
   - **Values file** (`-f my-values.yaml`) → note its path. It gets passed to the script via `-f`
     in the next step *and* to `helm` itself in step 7 — the script's output only carries
     `bp-configurator.env`, so anything else in that file (storage class, ingress, alerts flags)
     still needs `helm` to see the original file directly. See
     [`references/streams.md`](references/streams.md#if-your-install-customizes-bp-configuratorenv).
   - **Inline** (`--set`/`--set-json` on `bp-configurator.env`) → the script only reads YAML files,
     it can't consume a `--set` string. Plain `helm get values` only returns what was explicitly
     set, not the full merged list — use `helm get values <release> -a` instead and take its
     `bp-configurator.env` block untrimmed, or `deep_merge` will still replace the whole list and
     drop the rest of the defaults. Then treat it as the values-file case above.
   - **No customizations** → say so explicitly (e.g. "no custom `bp-configurator.env` overrides,
     so nothing extra is needed here") and proceed without any of the above.
5. **Run the stream-cap script** from the repo root:
   ```bash
   python3 deploy/helm/industry-profiles/warehouse-operations/scripts/compute_stream_cap.py \
     --mode <mode> --num-streams <N> -o values-stream-cap.generated.yaml
   ```
   - If step 4 found a customizing values file, pass it here too via `-f` — otherwise the
     generated file (built from chart defaults, layered last) silently drops those customizations.
     See
     [`references/streams.md`](references/streams.md#if-your-install-customizes-bp-configuratorenv).
   - Without `--hardware-profile`, it runs `nvidia-smi` on GPU index 0 and maps the name to a
     `HARDWARE_PROFILE` using the same table as [`vss-deploy-profile`'s warehouse
     reference](../vss-deploy-profile/references/warehouse.md#supported-hardware). If detection
     fails or the GPU isn't in that table, pass `--hardware-profile` explicitly.
   - It prints the effective (possibly capped) stream count and the `syncFileCount` value to keep
     in step (see [`references/streams.md`](references/streams.md) for why).
   - It never lowers the request silently without saying so — a cap is always logged to stderr.
6. **Prepare the rest of the values** — secrets, storage class, either ingress/`externalHost` or
   the NodePort values file per the choice made in step 2, and — if Alerts was enabled in step 3 —
   the four-flag Alerts values block from `warehouse-2d-app/README.md` §Alerts (Kafka/
   Elasticsearch/VST endpoints included). If step 4 found a customizing values file, it goes here
   too (`-f my-values.yaml`) — passing it only to the script in step 5 covers `bp-configurator.env`
   but drops everything else in that file from the install. See
   [`references/streams.md`](references/streams.md) for the full `helm upgrade --install` command
   with the generated file layered in last via `-f`.
7. **Install/upgrade**, chaining the generated file after any other `-f`/`--set` overrides so it
   wins on `bp-configurator.env`. The base command is the same either way; only the
   ingress-vs-NodePort overrides differ:
   ```bash
   helm dependency update deploy/helm/industry-profiles/warehouse-operations/warehouse-<mode>-app

   # Ingress:
   helm upgrade --install wh deploy/helm/industry-profiles/warehouse-operations/warehouse-<mode>-app \
     -n <namespace> --create-namespace \
     --set global.vssIngress.enabled=true \
     --set global.externalHost=<NODE_IP> \
     --set global.storageClass=<STORAGE_CLASS> \
     --set vios.vss-vios-nvstreamer.syncFileCount=<effective-streams> \
     ... \
     -f values-stream-cap.generated.yaml   # last: wins on bp-configurator.env

   # NodePort:
   helm upgrade --install wh deploy/helm/industry-profiles/warehouse-operations/warehouse-<mode>-app \
     -n <namespace> --create-namespace \
     -f deploy/helm/industry-profiles/warehouse-operations/warehouse-<mode>-app/values-nodeport.yaml \
     --set global.storageClass=<STORAGE_CLASS> \
     --set vios.vss-vios-nvstreamer.syncFileCount=<effective-streams> \
     -f values-stream-cap.generated.yaml   # last: wins on bp-configurator.env
   ```
   `...` is the remaining secrets/URL overrides from step 6 — see
   [`references/streams.md`](references/streams.md).

   `-f values-stream-cap.generated.yaml` has to be the last `-f` in the command — that's what
   makes it win on `bp-configurator.env` (multiple `-f` files merge in order given, later wins
   per top-level key). That includes coming after `values-nodeport.yaml` in the NodePort case and
   after every other `-f` in both. `--set` doesn't follow this rule: Helm always applies `--set`
   after every `-f` file regardless of command-line position, so a stray `--set` on
   `bp-configurator.env` here would still win no matter where you put it — step 4 should already
   have converted any such override into a values file, not left it inline.
8. **Post-install validation** — confirm pods actually come up before declaring success; see
   `warehouse-<mode>-app/README.md` §Post-install validation.
9. **Re-run the script whenever `NUM_STREAMS` or the target GPU changes** — the values-override
   file isn't tracked automatically; re-generate and re-`helm upgrade` after a hardware change.

## Prerequisites

- **Kubernetes cluster** reachable via `kubectl`, all nodes `Ready`.
- **NVIDIA GPU Operator** installed, so nodes report `nvidia.com/gpu` as allocatable.
- **StorageClass** present for VST/Elasticsearch PVCs (`global.storageClass`).
- **Helm 3.x** and **kubectl**.
- **NGC API key** for the image pull secret and model/app-data download job.
- **Ingress controller** installed if using ingress (see the chart README's "No ingress
  controller: NodePort" section for the alternative).
- **TURN server** for WebRTC playback off-cluster (`global.turnServerUrl`).

Full detail, values, and exact commands: see
`deploy/helm/industry-profiles/warehouse-operations/warehouse-<mode>-app/README.md`
§Prerequisites (identical across `2d`/`3d`/`mv3dt`). This skill only adds the stream-cap step; it
doesn't replace chart setup — the precheck in step 1 is a fast sanity pass, not a substitute for
reading that section on first deploy.
