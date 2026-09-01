# RTVI-CV-3D / MV3DT — Deployment Guide

Unified setup and run instructions for two ways MV3DT can be deployed from this repo.

Scope is **MV3DT only**. The repo's `blueprint-deploy.sh` launcher serves `2d`/`3d`/`mv3dt` and defaults to `2d`, so it always needs `-m mv3dt` — `./launch-deployment.sh blueprint` (§5.2) passes that for you.


|            | **Standalone** (§4)                      | **Warehouse Blueprint / WHBP** (§5)      |
| ---------- | ---------------------------------------- | ---------------------------------------- |
| What       | The RT-CV-3D component on its own        | The full warehouse VSS stack             |
| Path       | `services/rtvi/rt-cv-3d/rt-cv-mv3dt`     | `deploy/docker`                          |
| Launcher   | `scripts/*.sh` + `docker compose`        | `scripts/blueprint-deploy.sh`            |
| Perception | RT-DETR + MV3DT, BEV fusion              | RT-DETR + MV3DT, BEV fusion              |
| Streams    | You supply RTSP, or recorded `.mp4`      | Configurator + VST, automatic            |
| Extras     | Kafka + MQTT only                        | Elasticsearch, Kibana, VST UI, analytics |
| Use it for | Testing the component; per-camera tuning | End-to-end product behaviour             |


Both consume the **same models and the same datasets**. That shared part is §1–§3 and is driven by `setup-data.sh`, which stages data and launches nothing; the deployments diverge from §4, each with its own launcher.

**Contents**

- [1. Prerequisites](#1-prerequisites)
- [2. One-time setup](#2-one-time-setup)
- [3. Datasets](#3-datasets)
- [4. Deployment A — standalone component](#4-deployment-a--standalone-component)
- [5. Deployment B — warehouse blueprint](#5-deployment-b--warehouse-blueprint)
- [6. Troubleshooting](#6-troubleshooting)
- [7. Reference](#7-reference)

---



## 1. Prerequisites

- NVIDIA driver + NVIDIA Container Toolkit, and `docker compose` v2.
- An NGC API key with access to `nvidia` and `nv-staging/vss-core` — the VIOS, SDR and Envoy images still resolve under `nvcr.io`. The MV3DT perception and BEV-fusion images pull anonymously from `ghcr.io/nvidia-ai-blueprints/vss` and need no key.
- NVIDIA GitLab access, for the streaming testbed repo (§2.2).
- Access to the `//netapp-hq/` share, for the custom datasets, e.g., 12cam, 28cam (§2.4).
- One GPU (`RT_CV_DEVICE_ID=0`).
- A host display only for OSD or the live BEV window. Everything else is headless-capable.

Three kernel settings are required:

```bash
sudo sysctl -w vm.max_map_count=262144
sudo sysctl -w net.core.rmem_max=5242880
sudo sysctl -w net.core.wmem_max=5242880
```

`vm.max_map_count` caps how many memory-mapped areas one process may hold (default 65530). Elasticsearch mmaps every Lucene index segment, so the count scales with index size and eventually exceeds the default — the symptom is `OutOfMemoryError: Map failed`, which is address-space exhaustion, not memory pressure. Kafka maps its segment index files too, but uses far fewer. The two socket buffers are unrelated to Elasticsearch: they are for RTSP/VST and Kafka network throughput.

> Elasticsearch here runs with `discovery.type: single-node`, which is development mode and **skips bootstrap checks**. It will start fine on a host with the default 65530 and only fail later, once the index has grown enough segments. You get a delayed, misattributed failure rather than a refusal at startup — which is why this is a prerequisite and not a troubleshooting entry.

`sysctl -w` does not survive a reboot. For the persistent form — and the full kernel-settings list this repo standardizes on, including the IPv6 and TCP buffer entries — follow `skills/vss-deploy-profile/references/prerequisites.md` [§ Kernel Settings](../skills/vss-deploy-profile/references/prerequisites.md#kernel-settings), which writes a complete `/etc/sysctl.d/99-vss.conf`. Do not hand-write a partial copy of that file; it will be overwritten.

Once per login session, if you will use OSD or the live BEV window:

```bash
xhost +                 # let the perception container draw on your display
echo "$DISPLAY"         # note the value; pass it explicitly if it is not :0
```



### 1.1 NGC CLI

**x86_64:**

```bash
uname -m   # should print x86_64
curl -sLo /tmp/ngccli.zip https://api.ngc.nvidia.com/v2/resources/nvidia/ngc-apps/ngc_cli/versions/4.34.10/files/ngccli_linux.zip
sudo rm -rf /usr/local/lib/ngc-cli /usr/local/bin/ngc
sudo unzip -qo /tmp/ngccli.zip -d /usr/local/lib
sudo chmod +x /usr/local/lib/ngc-cli/ngc
sudo ln -sfn /usr/local/lib/ngc-cli/ngc /usr/local/bin/ngc
ngc --version
```

**aarch64 (DGX Spark / Thor / Orin):**

```bash
uname -m   # should print aarch64
curl -sLo /tmp/ngccli_arm64.zip https://api.ngc.nvidia.com/v2/resources/nvidia/ngc-apps/ngc_cli/versions/4.34.10/files/ngccli_arm64.zip
sudo rm -rf /usr/local/lib/ngc-cli /usr/local/bin/ngc
sudo unzip -qo /tmp/ngccli_arm64.zip -d /usr/local/lib
sudo chmod +x /usr/local/lib/ngc-cli/ngc
sudo ln -sfn /usr/local/lib/ngc-cli/ngc /usr/local/bin/ngc
ngc --version
```



### 1.2 Credentials

The key is read from the environment, not passed as a flag — `blueprint-deploy.sh up` runs `docker login nvcr.io` with it, so it must be exported in the shell that calls the launcher.

```bash
export NGC_CLI_API_KEY=<your key>
export NGC_CLI_ORG=nvidia
```

---



## 2. One-time setup



### 2.1 Canonical layout

Everything below assumes this layout. It is the same on every machine.

```
$VSS_REPO/                                  ← one clone, this repo
├── data/                                   ← gitignored except the five files above the assets
│   ├── DEPLOY.md                           ← this document
│   ├── datasets.yml                        ← dataset registry
│   ├── common.sh                           ← paths, registry reader, helpers
│   ├── setup-data.sh                       ← stages datasets into both layouts
│   ├── launch-deployment.sh                ← runs either one     (§4, §5)
│   ├── vss-warehouse-app-data/             ← NGC package, extracted
│   │   ├── models/                         ← RT-DETR, BodyPose3DNet
│   │   ├── videos/<dataset>/               ← sample clips, <sensor_id>.mp4
│   │   ├── playback/  data_log/  auto-calib/
│   ├── vss-warehouse-app-data_v3.2.0/      ← NGC download artifact; deletable
│   └── vss-mv3dt-custom-datasets/          ← netapp datasets (12/28-cam)
│       └── <name>/{calibration.json, videos/}
├── standalone/                             ← streaming testbed clone (§2.2)
│   └── datasets/<alias>/                   ← generated by setup-data.sh
├── services/rtvi/rt-cv-3d/rt-cv-mv3dt/     ← standalone component  (§4)
└── deploy/docker/                          ← warehouse blueprint   (§5)
```

Shorthand used throughout:

```bash
export VSS_REPO="$HOME/Documents/vss"
export DATA="$VSS_REPO/data"
export APP_DATA="$DATA/vss-warehouse-app-data"      # this is VSS_DATA_DIR (-D)
export RTCV="$VSS_REPO/services/rtvi/rt-cv-3d/rt-cv-mv3dt"
export STANDALONE="$VSS_REPO/standalone"
```



### 2.2 Clone

```bash
git clone git@github.com:NVIDIA-AI-Blueprints/video-search-and-summarization.git "$VSS_REPO"
cd "$VSS_REPO" && git checkout test/mv3dt-docs

# Streaming testbed (VST + NVStreamer) — required for RTSP runs in §4.
git clone ssh://git@gitlab-master.nvidia.com:12051/aotianw/rt-cv-3d-mv3dt-standalone.git "$STANDALONE"
chmod -R o+rX "$STANDALONE"
```

> The testbed **must** end up at `$STANDALONE`, i.e. `$VSS_REPO/standalone`. It stays untracked — the parent repo ignores it.

The exports in §2.1 are shorthand for *your shell*, so the run commands in §4 and §5 stay readable. `setup-data.sh` also honours `APP_DATA`, `CUSTOM_DATA`, `STANDALONE`, `RTCV` and `PROFILE` if exported, defaulting to the canonical layout otherwise — so re-pointing one moves both your clone and where the script looks. `./setup-data.sh check` lists any override in effect, since an override is per-machine divergence and that is precisely what this layout exists to avoid. Use one only where the layout genuinely cannot fit, the main case being a testbed clone kept outside this repo: nesting one git repo inside another is why `standalone/` shows up permanently untracked in `git status`. `VSS_REPO` is the exception — it is derived from where the script lives and an exported value is ignored.

### 2.3 Verify the host

```bash
cd "$DATA" && ./setup-data.sh check
```

Checks tools, layout, `docker/.env` sanity, and the environment. Fix anything reported `FAIL` before continuing; `warn` lines are optional capabilities.

### 2.4 Fetch models and sample videos

```bash
cd "$DATA"
./setup-data.sh fetch            # both sources
./setup-data.sh fetch --ngc      # NGC app-data package only
./setup-data.sh fetch --netapp   # custom datasets only
```

The NGC half downloads and extracts `vss-warehouse-app-data:3.2.0` — the models plus four sample video datasets, of which `warehouse-4cams-20mx20m-synthetic` is the MV3DT one. It is ~2.3 GB compressed; budget time.

What `fetch --ngc` runs, if you ever need to do it by hand — on a machine without this repo, or when the wrapper fails:

```bash
cd "$DATA"
export NGC_CLI_API_KEY=<your key>
export NGC_CLI_ORG=nvidia
ngc registry resource download-version nvidia/vss-warehouse/vss-warehouse-app-data:3.2.0
tar -xzf vss-warehouse-app-data_v3.2.0/vss-warehouse-app-data.tar.gz -C "$DATA"
sudo chmod -R 777 "$DATA/vss-warehouse-app-data"
```

> **Extract with** `-C "$DATA"`**, not by** `cd`**-ing into the download directory first.** The tarball has a single top-level `vss-warehouse-app-data/`, so extracting from inside `vss-warehouse-app-data_v3.2.0/` buries it one level deeper — `data/vss-warehouse-app-data_v3.2.0/vss-warehouse-app-data/` — which is a different data root and invalidates every path in §2.1. Older instructions did exactly that, and it is where the per-machine layout drift this guide replaces came from.

No CLI? Download the tarball from the [NGC catalog](https://catalog.ngc.nvidia.com/orgs/nvidia/teams/vss-warehouse/resources/vss-warehouse-app-data?version=3.2.0) and extract it the same way.

`vss-warehouse-app-data_v3.2.0/` is only the download artifact. Once extracted it holds nothing but the 2.3 GB tarball, and deleting it reclaims that space — `fetch --ngc` re-downloads if you ever need it again.

The netapp part needs the share mounted first:

```bash
sudo mkdir -p /mnt/netapp-hq/handheld/ds_track
sudo mount //netapp-hq/handheld/ds_track /mnt/netapp-hq/handheld/ds_track -o username=$USER,domain=nvidia.com
```

Both steps are idempotent and skip work that is already done.

---



## 3. Datasets

A dataset is only usable where **both halves agree**: the video clips and a calibration whose sensor ids exactly equal the video filename stems.

```
sensor id  ==  video stem  ==  camInfo/<id>.yml  ==  the key you register
```

If they disagree MV3DT cannot look up the camera model, and the failure is silent but fatal — camInfo covers cameras that never produce frames, the tracker keeps a stale camera map, and the run dies seconds in. `setup-data.sh` checks this and refuses rather than letting it through.

The two deployments want different shapes *and* different names for the same dataset. That mapping lives in `datasets.yml` and nowhere else:


| Registry name                           | Alias    | Cams         | Origin | Notes         |
| --------------------------------------- | -------- | ------------ | ------ | ------------- |
| `warehouse-4cams-20mx20m-synthetic`     | `4cam`   | 4            | NGC    | default       |
| `Simple_Warehouse_Synthetic_040424`     | `12cam`  | 12           | netapp | tuned tracker |
| `MTMC_Warehouse_Synthetic_012424_28cam` | `28cam`  | 28           | netapp | rebuilds b28  |
| `nv-warehouse-145cams`                  | `145cam` | 145 (subset) | netapp | subset        |


The standalone deployment uses the **alias**; the blueprint uses the **name**. `setup-data.sh` accepts either.

### 3.1 Stage a dataset

```bash
cd "$DATA"
./setup-data.sh list             # what is registered, and what is actually staged
./setup-data.sh sync 4cam        # one dataset, both layouts
./setup-data.sh sync all         # everything present
```

For each dataset `sync` derives the camera list from the video files, verifies it against the calibration, generates `camInfo/` + the sparse MQTT pub/sub graph, and writes both layouts:

```
standalone/datasets/<alias>/           calibration.json  dataset.env  map.png  camInfo/
warehouse-<mode>-app/…/<name>/         calibration.json  camInfo/  images/{Top.png,imageMetadata.json}
```

It then prints the launch command for each deployment. `sync` stages data and stops there — it starts nothing and reconfigures no deployment.

`sync` always stages both layouts. If the testbed has not been cloned it says so and stages the blueprint half alone — the two are independent.

Each dataset also gets its own copy of `camInfo/` and `pub_sub_info_config.yml` in both trees. The generators write to one shared area (`generated/`), so those per-dataset copies are what let you stage everything once with `sync all` and then launch any of them without re-syncing.

### 3.2 What sync will and will not overwrite

- **Never writes to a registry source.** When a dataset's `calibration:` resolves to the very file `sync` would stage — which is what `calibration: whbp` means, the source being the committed copy under `warehouse-mv3dt-app` — it is left untouched and any divergence is reported instead. Otherwise one missing clip would overwrite the source with a subset and destroy it, since the next run subsets the already-subset file.
- **Restages** `calibration.json` **from the registry source**, using the subset when one was taken. Four consumers read that file, so staging the full 145-sensor copy for a subset dataset would import sensors that can never report. The unabridged original lives in the asset tree as the registry's `calibration:` source, so nothing is lost — but a hand-edit to the staged copy will not survive the next sync. Edit the source instead.
- **Writes no launch configuration at all.** `docker/.env` belongs to `launch-deployment.sh standalone`, and `deepstream/configs/` plus `NUM_STREAMS` belong to `launch-deployment.sh blueprint`. Each launcher writes its own deployment's config just before starting it, so staging a dataset never reconfigures a deployment you did not ask for. The blueprint's two files are tracked; revert them with:
  ```bash
  git checkout -- deploy/docker/industry-profiles/warehouse-operations/
  ```
- **Creates and self-ignores** any sample-data directory that did not exist.



### 3.3 Adding your own dataset

1. Put the clips in `$APP_DATA/videos/<name>/` (or under `$DATA/vss-mv3dt-custom-datasets/<name>/videos/`), one `<sensor_id>.mp4` per camera.
2. Put a real `calibration.json` where the registry entry will point, with sensor ids matching those stems.
3. Add an entry to `datasets.yml` — copy the nearest existing one.
4. `./setup-data.sh sync <name>`.

For a genuinely new site you also need a floor map (`map.png` / `images/Top.png`) for the BEV views. Without one, sync writes a blank placeholder so the import step stays valid, and warns.

If the calibration declares more cameras than you have clips for (the 145-cam case), set `subset: true`. Sync then restricts the calibration to sensors that actually have a matching video, generates camInfo from that, and stages the restricted copy as the blueprint's `calibration.json` — so behaviour analytics and the calibration import see the same camera set perception does. Keep the full original as the registry's `calibration:` source, in the asset tree rather than the repo, since the staged copy is overwritten on every sync.

---



## 4. Deployment A — standalone component

The component has no streaming front-end: you supply RTSP URLs, or point it at recorded `.mp4` files. For RTSP we use the testbed (NVStreamer + VST), which republishes dataset clips as time-synchronized streams carrying SEI timestamps — MV3DT needs that cross-camera sync.

Metadata path: `perception → Kafka mdx-raw → bev-fusion → Kafka mdx-bev`

> The component README is the source of truth for the customer-facing flow. This section is the filled-in local version of it.



### 4.1 Configure

`$RTCV/docker/.env` is split between keys `sync` maintains and keys you own.

**Set by** `./launch-deployment.sh standalone up`, from the dataset you launch — no need to edit these when switching datasets:

```bash
MODELS_DIR=...
NUM_CAMS=...    # follows the clips actually present, not the registry count
VIDEO_DIR=...   # the synced dataset's video dir
```

`NUM_CAMS` tracks the clips on disk, which matters for a subset dataset, e.g., 145cam dataset but using only 8 cams/videos

**Yours to set** — these are per-run choices, not properties of a dataset:

```bash
INPUT_MODE=stream         # stream (RTSP) | file (recorded .mp4)
SAVE_VIDEO=0
GPU_DEVICE=0
DS_HTTP_PORT=9000
KAFKA_BOOTSTRAP=localhost:9092
PERCEPTION_TAG=3.3.0-26.07.2     # append -sbsa on DGX Spark
```

Anything already exported in your shell **wins** over `docker/.env`, for both `stage-configs.sh` and compose. That is what the inline `NUM_CAMS=...` prefixes below rely on, so you can still override a synced value for one run without editing the file.

### 4.2 Run (RTSP — the default path)

```bash
cd "$DATA"
OSD=1 DISPLAY=:1 ./launch-deployment.sh standalone up 145cam # 4cams, 12cam, 28cam, 145cam
./launch-deployment.sh standalone down              # component + testbed
```

That runs the whole sequence: writes the dataset-derived keys into `docker/.env`, starts the synchronized streams, stages the DeepStream configs, brings up perception and BEV fusion, then registers the streams. `generated/` is a single staging area that `sync` overwrites per dataset, so after `sync all` it holds whichever dataset came last in the registry. The launcher notices and activates the one you named from its per-dataset copy — you do not need to re-sync. It only refuses if that dataset was never synced at all.

Env knobs: `OSD=1` for the live tiled window (needs a display and `xhost +`), `SAVE_VIDEO=1` for `video-output/grid-view.mkv`, `NO_FOLLOW=1` to skip tailing the log. The tuned tracker for 12cam is applied automatically — it comes from the registry's `tracker_config`.

Along the way the testbed prints the proxy URLs:

```
===== VST proxy RTSP URLs (4) — SEI-synchronized =====
  1. rtsp://127.0.0.1:30554/live/Camera
  2. rtsp://127.0.0.1:30555/live/Camera_01
  3. rtsp://127.0.0.1:30555/live/Camera_02
  4. rtsp://127.0.0.1:30555/live/Camera_03
  ...
```

The URL count must equal the camera count. Reprint any time with `cd "$STANDALONE/pipeline1" && ./vst-stack.sh urls`. Each stream may be served on its own port, so the launcher uses the printed URLs verbatim rather than assuming one port; the pairs it registers are left in `$RTCV/my-streams.txt`.

Expect `ds-ready: YES` in the perception log, with PERF blocks at 0.0 FPS until registration completes. The first run at a new camera count builds the RT-DETR TensorRT engine — several minutes, up to ~30 for large batches. `add-streams.sh` waits for it.

### 4.3 Run (recorded files — no testbed, no registration)

```bash
cd "$DATA"
INPUT_MODE=file SAVE_VIDEO=1 ./launch-deployment.sh standalone up 4cam
```

Clips start immediately, PERF shows real FPS right away, the container exits at EOS, and `video-output/grid-view.mkv` finalizes on its own. One `<sensor_id>.mp4` per camera is required, which is what `sync` already verified.

To do any of this by hand instead — the launcher is a convenience, not a requirement — the underlying sequence is `vst-stack.sh up` → `stage-configs.sh` → `docker compose up -d` → `add-streams.sh`, with `NUM_CAMS` matching the dataset. §7.1 lists every knob those scripts read.

#### Convert the video to appropriate format

```bash
cd $RTCV/video-output
ffmpeg -i grid-view.mkv -r 30 -b:v 2M -c:v libx264 -preset fast -y grid-view.mp4
```



### 4.4 Verify

```bash
docker logs -f vss-rtvi-cv-mv3dt                      # every source ~30 FPS
./scripts/kafka-dump.sh --topic mdx-raw --count 20    # all sensor ids
./scripts/kafka-dump.sh --topic mdx-bev --count 20    # sensorId = bev-sensor-1
```

In `mdx-raw`, the same scene moment should carry matching timestamps across sensors, within ~33 ms. That is the sync check.

### 4.5 Visualize

```bash
cd "$RTCV"
# raw = per-camera measurements; fused = merged tracks. q to quit.
BEV_DATASET_PATH="$STANDALONE/datasets/<alias>" ./scripts/bev-visualizer.sh
BEV_SOURCE=fused BEV_DATASET_PATH="$STANDALONE/datasets/<alias>" ./scripts/bev-visualizer.sh

# Record ~60s to mp4 instead of opening a window, then Ctrl-C:
BEV_SAVE_VIDEO=1 BEV_DATASET_PATH="$STANDALONE/datasets/<alias>" ./scripts/bev-visualizer.sh
BEV_SAVE_VIDEO=1 BEV_SOURCE=fused BEV_DATASET_PATH="$MV3DT_DIR/workspace/datasets/nvwh_145cam" ./scripts/bev-visualizer.sh
```

Raw shows one point per camera view of an object; fused shows a single merged point. On Ctrl-C expect `Video saved: .../bev-output/trajectory_video_<stamp>.mp4 (N frames)`.

### 4.6 Teardown

Tear down before switching datasets — the launcher stops both the component and the testbed.

```bash
cd "$DATA" && ./launch-deployment.sh standalone down
# equivalently, by hand:
#   cd "$RTCV/docker" && docker compose --profile "*" down
#   cd "$STANDALONE/pipeline1" && ./vst-stack.sh down
```



### 4.7 Alternative — drive everything from the testbed

The testbed ships its own perception launchers, so you can bring up a whole pipeline with one command. Useful for quick dataset sanity checks; §4.2 remains the reference for testing the component itself.


| Pipeline    | What it is                                                                                                |
| ----------- | --------------------------------------------------------------------------------------------------------- |
| `pipeline0` | Leanest — NVStreamer only, barrier sync, supports `STREAM_FPS` throttling. Removed streams cannot rejoin. |
| `pipeline1` | Default — NVStreamer + VST proxy, runtime add/remove/re-add, `SOURCE=file` supported.                     |
| `pipeline2` | Production-like — one-click VST with 3D overlay, Kafka always on, BEV fusion, Elasticsearch.              |


```bash
cd "$STANDALONE/pipeline1" && DATASET=4cam ./run.sh && ./down.sh

# Throttled 28cam on the lean pipeline (below real time):
cd "$STANDALONE/pipeline0" && STREAM_FPS=10 DATASET=28cam ./run.sh
```

Unlike §4.2, these launchers run the testbed's *own* perception, which resolves models from `ASSETS_DIR` in `$STANDALONE/.env`. `setup-data.sh sync` keeps that and `CUSTOM_DATA_DIR` pointed at our asset trees, so there is nothing to edit by hand.

The testbed also ships `tools/generate-dataset-configs.sh <alias>` to build `camInfo/` and `pub_sub_info_config.yml` for 12cam/28cam. You do not need it: `sync` writes both into `standalone/datasets/<alias>/` already, from the same calibration. Run it only if you want a different neighbour graph than the registry's `neighbor_criteria`.

---



## 5. Deployment B — warehouse blueprint

The full VSS stack for the warehouse profile: streaming, perception, behavior analytics, a broker, Elasticsearch + Kibana and the video-analytics API, with MV3DT as the perception stage.

> This is **not** started with `docker compose up`. The launcher is `deploy/docker/scripts/blueprint-deploy.sh`. Editing compose files or `generated.env` by hand is not part of the flow — pass flags instead.

Two things worth knowing:

- `up` always runs `down` first. It is a clean-slate deploy: containers removed, `$VSS_DATA_DIR/data_log/*` wiped, `generated.env` deleted and rewritten. Nothing under `videos/`, `playback/` or `models/` is touched.
- `-D <data-dir>` is required for **both** `up` and `down`, and must be the same path each time.



### 5.1 Configuration layers

Deployment config is split across files; the launcher generates the active layer. At runtime compose reads, in order:

1. `deploy/docker/containers.env` — image registry defaults
2. `deploy/docker/industry-profiles/warehouse-operations/.env` — shared profile constants
3. `deploy/docker/industry-profiles/warehouse-operations/generated.env` — **generated, do not edit**

`generated.env` is rewritten from `overrides.env` on every `up`, so edits to it never stick. Put per-deployment values in `overrides.env`.

### 5.2 Deploy

```bash
cd "$DATA"
./launch-deployment.sh blueprint up 4cam        # or 12cam / 28cam / 145cam
./launch-deployment.sh blueprint up 12cam -n    # dry run; extra flags pass through
./launch-deployment.sh blueprint down
```

That is the whole thing. It resolves the alias to the registry name, points the tracker's camera map and `NUM_STREAMS` at that dataset, and invokes the launcher as:

```bash
blueprint-deploy.sh up -d warehouse -m mv3dt -p bp_wh_kafka -D "$APP_DATA" -s <name>
```

Override the profile with `BP_PROFILE=bp_wh_redis ./launch-deployment.sh blueprint up 4cam`. Pass `NO_FOLLOW=1` to skip tailing the perception log at the end.

##### Verify - [http://localhost:30888/vst/#/live-streams](http://localhost:30888/vst/#/live-streams)

**Why a wrapper.** `blueprint-deploy.sh` hardcodes `MODE=2d` when `-m` is absent — and unlike `BP_PROFILE` and `HARDWARE_PROFILE` right beside it, it does *not* fall back to `overrides.env`, so setting `MODE=mv3dt` there has no effect (`generated.env` is rewritten from the resolved value on every `up`). A forgotten `-m mv3dt` therefore gives you a 2D stack that comes up clean and looks like it worked. The wrapper makes that unforgettable.

The rest of this section is the underlying launcher, for when you need a flag the wrapper does not pass.

```bash
"$VSS_REPO/deploy/docker/scripts/blueprint-deploy.sh" --help
```


| Flag              | Meaning                                                           |
| ----------------- | ----------------------------------------------------------------- |
| `-d warehouse`    | required for `up`; the only deployment type                       |
| `-D <path>`       | required for `up` **and** `down` — use `$APP_DATA`                |
| `-m mv3dt`        | perception mode — the default is `2d`, so this is mandatory       |
| `-p <bp_profile>` | `bp_wh_kafka` (default here), `bp_wh_redis` or `bp_wh_auto_calib` |
| `-s <dataset>`    | override the default sample dataset                               |
| `-i <ip>`         | `HOST_IP` (default: primary IP from `ip route`)                   |
| `-E cpu`          | `gpu`                                                             |
| `-H <profile>`    | hardware profile — `H100`, `L40S`, `RTXPRO6000BW`, `DGX-SPARK`, … |
| `-n`              | dry run: print the commands, execute nothing                      |


Valid profiles for `mv3dt` are `bp_wh_kafka`, `bp_wh_redis` and `bp_wh_auto_calib`. `bp_wh` is 2d-only and **is invalid here**. `NGC_CLI_API_KEY` is read from the environment, not passed as a flag. `-s` takes the registry name, not the alias.

MV3DT runs RT-DETR + MV3DT per camera and then fuses BEV measurements, so a deploy brings up `vss-rtvi-cv-mv3dt`, `vss-rtvi-cv-bev-fusion` and mosquitto for the cross-camera `/trck/*` topics. Metadata path: `perception → mdx-raw → bev-fusion → mdx-bev`.

### 5.3 Verify

First deploy of a given camera count is slow: TensorRT builds the detector engine for that batch size, which can take up to ~30 minutes at 0 FPS. Expected, not a hang.

```bash
# Everything that should be Up. Exited (0) is normal for the *-init,
# *-topics and *-health-check one-shots.
docker ps --format 'table {{.Names}}\t{{.Status}}'

# Perception FPS — one line per stream, should settle near 30.
docker logs --since 60s vss-rtvi-cv-mv3dt 2>&1 | grep -aE 'stream_name' | tail -8
docker logs --since 60s vss-rtvi-cv-mv3dt 2>&1 | grep -a 'Active sources' | tail -1

curl -sf http://localhost:9200/_cat/health?v                       # Elasticsearch
curl -s  http://localhost:30888/vst/api/v1/sensor/list             # VST — one per camera
curl -sf -o /dev/null -w '%{http_code}\n' http://localhost:7777/   # ingress
```

The configurator has a 60 s start period and nvstreamer waits on it. If it never goes healthy, perception gets no streams — **check it first when the stack comes up empty.** In MV3DT the container is `vss-configurator-mv3dt`.

```bash
docker logs vss-configurator-mv3dt 2>&1 | tail -40
```

Transporter and Pallet classes are disabled from MV3DT 3D tracking — no 3D boxes on those classes is expected.

### 5.4 Teardown and cleanup

`-D` must be the same path used for `up`, so `data_log` gets cleaned. The wrapper passes it for you:

```bash
cd "$DATA" && ./launch-deployment.sh blueprint down
# equivalent to:
#   blueprint-deploy.sh down -D "$APP_DATA"
```

Cleanup without a full `down`/`up` cycle — run between repeated deploys on the same machine:

```bash
cd "$VSS_REPO/deploy/docker"
bash scripts/cleanup_all_datalog.sh -e industry-profiles/warehouse-operations/generated.env
# if generated.env is missing (no successful deploy yet):
bash scripts/cleanup_all_datalog.sh -e industry-profiles/warehouse-operations/overrides.env
```

---



## 6. Troubleshooting



### Shared

**Models not found at startup.** `MODELS_DIR` in `$RTCV/docker/.env` must be a *single* assignment pointing at the package's `models/` dir. A doubled `MODELS_DIR=MODELS_DIR=...` makes the mount resolve to a literal path. `setup-data.sh check` catches this.

**Streams accepted but never produce frames.** Sensor id mismatch. The registered key must equal the calibration sensor id, which must equal a `camInfo` stem. Re-run `./setup-data.sh sync <dataset>` — it verifies this and refuses on mismatch.

**Container starts but cannot read its configs / camInfo / calibration.** A permissions problem, not a config one. The perception and analytics containers run as a non-root user and bind-mount these read-only, so a host umask of `027` — which leaves new files `-rw-r-----` — makes them unreadable inside the container. `setup-data.sh sync` applies `chmod -R o+rX` to everything it stages, `launch-deployment.sh blueprint` does the same for `deepstream/configs/`, and the component's own `generate-configs.sh` and `stage-configs.sh` cover `generated/`. If you hand-copy a file into `sample-data/` or `deepstream/configs/`, apply it yourself:

```bash
chmod -R o+rX <the file or dir you copied>
```

No `sudo` is needed — you own these files. (`video-output/` is the exception: the container *writes* there, so `stage-configs.sh` gives it `777` when `SAVE_VIDEO=1`.)

**First launch stalls minutes at 0 FPS.** TensorRT engine build for that batch size. Later runs at the same count reuse the cached engine.

**OSD absent and the pipeline is frozen**, logs show `libEGL warning: egl: failed to create dri2 screen`. Your monitor is not attached to the GPU. Add to the perception service:

```yaml
devices:
  - /dev/dri
```



### Standalone

`vst-stack.sh` **prints fewer URLs than cameras.**

```bash
docker logs vss-vios-nvstreamer-mv3dt ; docker logs vss-vios-vst-mv3dt
./vst-stack.sh down && ./vst-stack.sh up
```

**OSD window does not appear.** `echo $DISPLAY`, run `xhost +`, confirm configs were staged with `OSD=1`, then recreate the container and re-register the streams:

```bash
OSD=1 ./scripts/stage-configs.sh && (cd docker && docker compose up -d perception)
```

`stage-configs.sh` **refuses** `SAVE_VIDEO=1`**.** Expected with `INPUT_MODE=stream`: a live recording has no end and the file sink does not rotate. Use `INPUT_MODE=file`, or accept the risk with `ALLOW_UNBOUNDED_RECORDING=1`.

**BEV visualizer opens but stays empty.** It tails the live topic. Confirm `kafka-dump.sh` shows messages while the visualizer runs. One `Waiting for first message` at launch is normal.

**Port conflicts (9000 / 9092 / 1883 / 30554).** Adjust `DS_HTTP_PORT`, `KAFKA_PORT` or `MQTT_PORT` in `$RTCV/docker/.env`. After changing `DS_HTTP_PORT`, re-run `stage-configs.sh` — the REST port is baked into the staged config. `MQTT_PORT` needs no regeneration: it reaches the container as an environment variable and is applied to the pub/sub config at startup (§7.3).

### Blueprint

`NGC_CLI_API_KEY is required for 'up'`**.** Export it in the same shell; it is env-only, there is no flag. The one exception is `-p bp_wh_auto_calib`.

`--data-dir (-D) is required`**.** Also required for `down`. Same path as `up`.

**Invalid profile for the mode.** `bp_wh` is 2d-only. Use `bp_wh_kafka`, `bp_wh_redis` or `bp_wh_auto_calib`.

**The stack came up 2D.** You called `blueprint-deploy.sh` directly and omitted `-m mv3dt`. It defaults to `MODE=2d` and will *not* read `MODE` from `overrides.env`, so setting it there does not help — and the profile then cascades to `bp_wh`, which also pulls the LLM/VLM NIMs. Use `./launch-deployment.sh blueprint up <dataset>`, which always passes the flag.

**Tracking is nonsense after switching datasets.** The blueprint mounts `deepstream/configs/` directly, so the tracker's camera map is whatever the last `sync` wrote — deploy 12cam after 4cam and 12 streams run against a 4-camera map. `./launch-deployment.sh blueprint up` rewrites that map from the dataset you name, every time; a hand-rolled `blueprint-deploy.sh` does not. The `standalone` target makes the equivalent check against `generated/camInfo` and refuses on mismatch.

**Stack is up but no video anywhere.** Check `vss-configurator` health (§5.3), then that the dataset has both halves — `./setup-data.sh list` shows this.

**Port already in use.** Host ports come from `overrides.env` (7777, 9000, 9200, 9092, 5601, 30888, 31000, 30554, 1883, 35000). Free the port or change it there and redeploy — editing `generated.env` alone will not stick.

**Deploy did something unexpected.** Re-run the same command with `-n` to see the resolved dataset, stream count, `COMPOSE_PROFILES` list and compose invocation.

**DGX Spark / SBSA hosts.** `-H DGX-SPARK` turns on the SBSA image variants automatically. On other Spark-class hosts pass `--use-sbsa-images` explicitly.

---



## 7. Reference



### 7.1 Standalone knobs


| Knob               | Read by                | Effect                                                                                                                                                                 |
| ------------------ | ---------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `INPUT_MODE`       | `stage-configs.sh`     | `stream` = live RTSP, registered after launch. `file` = per-camera `.mp4` from `VIDEO_DIR`, named `<sensor_id>.mp4`; clips play once and the container exits at EOS.   |
| `OSD`              | `stage-configs.sh`     | `1` = live tiled window with 3D boxes. Needs a host display, `DISPLAY` and `xhost +`.                                                                                  |
| `SAVE_VIDEO`       | `stage-configs.sh`     | `1` = write the tiled grid to `video-output/grid-view.mkv`. Headless-friendly. MP4 unsupported. Refused with `INPUT_MODE=stream` unless `ALLOW_UNBOUNDED_RECORDING=1`. |
| `TRACKER_CONFIG`   | `stage-configs.sh`     | Replace the sample tracker config with a dataset-tuned one (needed for 12cam).                                                                                         |
| `NUM_CAMS`         | both                   | Detector batch size, grid layout, fusion `MAX_EXPECTED_SENSORS`. Must match the dataset.                                                                               |
| `PULL`             | `launch-deployment.sh` | `1` = `docker compose pull` before launching, on either target. Off by default; see [§7.4](#74-image-freshness) for why launching alone never refreshes a tag.         |
| `BEV_SOURCE`       | `bev-visualizer.sh`    | `raw` = per-camera measurements (`mdx-raw`), `fused` = merged tracks (`mdx-bev`).                                                                                      |
| `BEV_SAVE_VIDEO`   | `bev-visualizer.sh`    | `1` = record to `bev-output/` instead of a window.                                                                                                                     |
| `BEV_DATASET_PATH` | `bev-visualizer.sh`    | Dataset dir holding `map.png` + `transforms.yml`.                                                                                                                      |




### 7.2 Blueprint endpoints

`HOST_IP` is the `-i` value, or your primary IP.


| URL                            | What                              |
| ------------------------------ | --------------------------------- |
| `http://<HOST_IP>:7777/`       | HAProxy ingress — start here      |
| `http://<HOST_IP>:30888/vst/`  | VST UI — live streams, 3D overlay |
| `http://<HOST_IP>:31000`       | NVStreamer                        |
| `http://<HOST_IP>:5601/kibana` | Kibana dashboards                 |
| `http://<HOST_IP>:8081`        | video-analytics API               |
| `http://<HOST_IP>:9000`        | RTVI CV                           |
| `http://<HOST_IP>:35000`       | Grafana                           |




### 7.3 Generating configs by hand

`setup-data.sh sync` wraps `generate-configs.sh`, which wraps these. Run them directly only when debugging the generators themselves.

```bash
cd "$VSS_REPO/tools/rtvi-cv-mv3dt-utils"
source venv/bin/activate     # python -m venv venv && pip install -r requirements.txt

python generate_cam_info_configs.py \
  --calibration-json <calibration.json> \
  --output-dir <camInfo dir> \
  --class 0 1.60 0.3 --class 1 1.60 0.3 --class 2 1.60 0.3 \
  --class 3 0.48 0.3 --class 4 0.2 0.52  --class 5 2.75 1.0

python generate_pub_sub_configs.py \
  --cam_info_path <camInfo dir> \
  --neighbor_criteria overlap_threshold:1e-6 \
  --output_path <configs dir>
```

`--class <id> <height_m> <radius_m>` are the object-model priors: 0–2 person/humanoid, 3 cart, 4 box, 5 forklift.

No `--mqtt_brokers` above, deliberately. The broker written into `pub_sub_info_config.yml` is a **placeholder, not configuration**: `ds-start-mv3dt.sh` rewrites every `host:port` in that file to `$MQTT_HOST:$MQTT_PORT` at container start, and those reach the container from `docker/.env` via `compose.yml`. The generator's own default (`127.0.0.1:1883`) is therefore fine — it is overwritten either way, so passing the flag only creates the impression that it is a knob. `setup-data.sh` does not set it either.

Camera clustering, for very large camera counts:

```bash
SDU_DIR="$VSS_REPO/libs/analytics/spatialai-data-utils"
source "$SDU_DIR/.venv/bin/activate"
PYTHONPATH="$SDU_DIR:${PYTHONPATH:-}" python \
  "$SDU_DIR/tools/camera_grouping/create_camera_clusters.py" \
  <calibration.json> --max_camera_per_group 4 --n_clusters 1 \
  --disable_param_tuning --overwrite
```



### 7.4 Image freshness

Launching never refreshes an image. Compose's default pull policy is `missing`: it resolves a tag the first time it pulls it and reuses that copy forever. `develop-latest` moves on the registry, but nothing revisits it, so a deployment can drift arbitrarily far behind without a word.

This is easy to miss because `blueprint-deploy.sh` runs `docker compose up --force-recreate --build`. Every container really is destroyed and recreated on each launch — from the cached image. `--build` only affects services with a `build:` section, which these image-only services do not have. Fresh containers, arbitrarily old bits.

Pass `PULL=1` to pull first:

```bash
PULL=1 ./launch-deployment.sh blueprint up 145cam
```

It is off by default deliberately: pulling breaks air-gapped installs, adds a registry round-trip per service, and would let the image set shift underneath a sweep meant to hold everything but one variable fixed. To check for drift without pulling, compare the local digest against the registry:

```bash
i=vss-vios-streamprocessing
docker image inspect ghcr.io/nvidia-ai-blueprints/vss/$i:develop-latest --format '{{index .RepoDigests 0}}'
docker manifest inspect ghcr.io/nvidia-ai-blueprints/vss/$i:develop-latest | head
```

Symptoms of staleness are indistinguishable from bugs: a fix present in your checkout has no effect at runtime, because the code shipping in the container is compiled or baked in, not read from the tree. `PULL=1` is the first thing to try when a merged fix appears to be missing.

### 7.5 Running your own fusion code

The BEV fusion service ships as a registry image, so edits under `services/rtvi/rt-cv-3d/rt-cv-bev-fusion/src` change nothing at runtime however many times you relaunch. `build-fusion.sh` builds the working copy into an image and points a deployment at it:

```bash
./build-fusion.sh on       # build vss-rt-cv-mv3dt-bev-fusion:local, pin to it
./build-fusion.sh apply    # recreate just that container — seconds, not a relaunch
./build-fusion.sh status
./build-fusion.sh off      # back to the registry image on the next launch
```

Every action takes an optional target, `blueprint` (the default) or `standalone`: `./build-fusion.sh on standalone`. The image is identical for both; only the pin and the recreate differ, because the two deployments read the fusion image ref from different places. Pass the target consistently — `status` reports the running container either way, since both deployments name it `vss-rtvi-cv-bev-fusion`, but it reads the pin only for the target you asked about, so a pinned standalone stack looks unpinned under `status blueprint`.

Re-run `on` after each source edit: the pin is by tag, so an edit alone changes nothing.

The image is a two-file layer on top of the release rather than a build of `Dockerfiles/measurement-fusion.Dockerfile`. That Dockerfile builds the distroless runtime from scratch — nvcr base, apt, pipenv, a gcc source tarball for license compliance — minutes and a working proxy to reproduce bytes already on disk. Layering keeps the released runtime untouched and rebuilds in about a second. CI still builds the real image; this is for the edit-run loop only.

For the blueprint the pin lands in `overrides.env`, last in compose's `--env-file` order and therefore ahead of the release default in `containers.env`. Standalone has no such file, and its `BEV_FUSION_IMAGE`/`BEV_FUSION_TAG` live in the same `docker/.env` the script reads the released ref from, so pinning there would erase what `off` restores. Its `compose.yml` is edited in place instead: the `bev-fusion` image line is replaced outright, and the original parked in a comment directly above it so `off` restores that line exactly. Replacing rather than adjusting the `${BEV_FUSION_IMAGE:-…}` fallback is deliberate — `docker/.env` sets that variable, so changing the fallback would have no effect at all. Both pins are tracked files, so they show up in `git diff` — deliberately, since a pinned deployment is not running what its tag says. Resist `docker cp`-ing a file into the running container instead: it works until the next launch, and `--force-recreate` then discards it silently, which reads as the change never having worked.

`PULL=1` cannot refresh a local-only image, so `pull_images` passes `--ignore-pull-failures` and lets the rest of the image set refresh around it.

### 7.6 Engine batch sizes

The RT-DETR engine is prebuilt per batch size (`_b4_`, `_b8_`, `_b12_` ship in the package). Any other camera count rebuilds from ONNX on first launch — a several-minute stall at 0 FPS, then it caches. 28cam is the notable one.

### 7.7 Files this guide owns


| File                              | Tracked | Purpose                                                 |
| --------------------------------- | ------- | ------------------------------------------------------- |
| `data/DEPLOY.md`                  | yes     | this document                                           |
| `data/datasets.yml`               | yes     | dataset registry                                        |
| `data/common.sh`                  | yes     | paths, registry reader, shared helpers                  |
| `data/setup-data.sh`              | yes     | dataset/model staging — launches nothing                |
| `data/launch-deployment.sh`       | yes     | runs either deployment; each owns its own launch config |
| `data/build-fusion.sh`            | yes     | builds and pins a local BEV fusion image (§7.5)         |
| `data/vss-warehouse-app-data/`    | no      | NGC package                                             |
| `data/vss-mv3dt-custom-datasets/` | no      | netapp datasets                                         |
| `standalone/`                     | no      | testbed clone                                           |


`data/` is gitignored except the six files above, via negation in `data/.gitignore`.

Anything else you keep under `data/` is yours — personal notes, or backup copies of `.env` files showing how you had a deployment configured. The tooling never reads them. Live configuration is only ever:


| Live file                                                            | Owner             |
| -------------------------------------------------------------------- | ----------------- |
| `services/rtvi/rt-cv-3d/rt-cv-mv3dt/docker/.env`                     | standalone (§4.1) |
| `deploy/docker/industry-profiles/warehouse-operations/overrides.env` | blueprint (§5.1)  |


A copy of either under `data/` is a snapshot, not an input — editing it changes nothing.