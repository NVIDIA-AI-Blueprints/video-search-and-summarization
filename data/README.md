# RTVI-CV-3D / MV3DT Deployment Guide

> **INTERNAL. Personal notes, not product documentation.** Do not cite or quote it in a bug, in Slack or in email. The shipped documentation is `docs/mv3dt-standalone-deployment.mdx` and the component README.

Unified setup and run instructions for the two ways to deploy MV3DT from this (GitHub VSS) repo.

Scope is **MV3DT only**. The repo's `blueprint-deploy.sh` serves `2d`/`3d`/`mv3dt` and defaults to `2d`, so it always needs `-m mv3dt`. `./launch-deployment.sh blueprint` (§5.2) passes that for you.


|            | **Standalone** (§4)                      | **Warehouse Blueprint / WHBP** (§5)      |
| ---------- | ---------------------------------------- | ---------------------------------------- |
| What       | The RT-CV-3D component on its own        | The full Warehouse Blueprint stack       |
| Path       | `services/rtvi/rt-cv-3d/rt-cv-mv3dt`     | `deploy/docker`                          |
| Launcher   | `scripts/*.sh` + `docker compose`        | `scripts/blueprint-deploy.sh`            |
| Perception | RT-DETR + MV3DT, BEV fusion              | RT-DETR + MV3DT, BEV fusion              |
| Streams    | You supply RTSP, or recorded `.mp4`      | Configurator + VST, automatic            |
| Extras     | Kafka + MQTT only                        | Elasticsearch, Kibana, VST UI, analytics |
| Use it for | Testing the component, per-camera tuning | End-to-end product behaviour             |


Both use the **same models and the same datasets**. §1 to §3 cover that shared part, driven by `setup-data.sh`, which stages data and launches nothing. From §4 on, each deployment has its own launcher.

**Contents**

- [1. Prerequisites](#1-prerequisites)
  - [1.1 NGC CLI](#11-ngc-cli)
  - [1.2 Credentials](#12-credentials)
- [2. One-time setup](#2-one-time-setup)
  - [2.1 Canonical layout](#21-canonical-layout)
  - [2.2 Clone](#22-clone)
  - [2.3 Verify the host](#23-verify-the-host)
  - [2.4 Fetch models and sample videos](#24-fetch-models-and-sample-videos)
- [3. Datasets](#3-datasets)
  - [3.1 Stage a dataset](#31-stage-a-dataset)
  - [3.2 What sync will and will not overwrite](#32-what-sync-will-and-will-not-overwrite)
  - [3.3 Adding your own dataset](#33-adding-your-own-dataset)
- [4. Deployment A: standalone component](#4-deployment-a-standalone-component)
  - [4.1 Configure](#41-configure)
  - [4.2 Run RTSP streams (default)](#42-run-rtsp-streams-default)
  - [4.3 Run recorded files](#43-run-recorded-files)
  - [4.4 Verify](#44-verify)
  - [4.5 Visualize](#45-visualize)
  - [4.6 Teardown](#46-teardown)
- [5. Deployment B: warehouse blueprint](#5-deployment-b-warehouse-blueprint)
  - [5.1 Configuration layers](#51-configuration-layers)
  - [5.2 Deploy](#52-deploy)
  - [5.3 Verify](#53-verify)
  - [5.4 Teardown and cleanup](#54-teardown-and-cleanup)
- [6. Troubleshooting](#6-troubleshooting)
  - [Shared](#shared)
  - [Standalone](#standalone)
  - [Blueprint](#blueprint)
- [7. Reference](#7-reference)
  - [7.1 Standalone knobs](#71-standalone-knobs)
  - [7.2 Blueprint endpoints](#72-blueprint-endpoints)
  - [7.3 Generating configs by hand](#73-generating-configs-by-hand)
  - [7.4 Image freshness](#74-image-freshness)
  - [7.5 Running your own fusion code](#75-running-your-own-fusion-code)
  - [7.6 Engine batch sizes](#76-engine-batch-sizes)
  - [7.7 Files this guide owns](#77-files-this-guide-owns)

---



## 1. Prerequisites

- NVIDIA driver + NVIDIA Container Toolkit, and `docker compose` v2.
- An NGC API key with access to `nvidia` and `nv-staging/vss-core`. The VIOS, SDR and Envoy images still come from `nvcr.io`. The MV3DT perception and BEV-fusion images pull anonymously from `ghcr.io/nvidia-ai-blueprints/vss`.
- NVIDIA GitLab access, for the streaming testbed repo (§2.2).
- Access to the `//netapp-hq/` share, for the custom datasets, e.g., 12cam, 28cam (§2.4).
- One GPU (`RT_CV_DEVICE_ID=0`).
- A host display only for OSD (VIS) or the live BEV window. Everything else runs headless.

Three kernel settings are required:

```bash
sudo sysctl -w vm.max_map_count=262144
sudo sysctl -w net.core.rmem_max=5242880
sudo sysctl -w net.core.wmem_max=5242880
```

The persistent form and the full list are in [Kernel Settings](https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization/blob/develop/skills/vss-build-vision-ai/references/prerequisites.md#kernel-settings).

Once per login session, if you will use OSD or the live BEV window:

```bash
xhost +                 # let the perception container draw on your display
echo "$DISPLAY"         # note the value, pass it explicitly if it is not :0
```



### 1.1 NGC CLI

**x86_64:**

```bash
uname -m   # should print x86_64
curl -sLo /tmp/ngccli.zip https://api.ngc.nvidia.com/v2/resources/nvidia/ngc-apps/ngc_cli/versions/4.36.6/files/ngccli_linux.zip
sudo rm -rf /usr/local/lib/ngc-cli /usr/local/bin/ngc
sudo unzip -qo /tmp/ngccli.zip -d /usr/local/lib
sudo chmod +x /usr/local/lib/ngc-cli/ngc
sudo ln -sfn /usr/local/lib/ngc-cli/ngc /usr/local/bin/ngc
ngc --version
```

**aarch64 (DGX Spark / Thor / Orin):**

```bash
uname -m   # should print aarch64
curl -sLo /tmp/ngccli_arm64.zip https://api.ngc.nvidia.com/v2/resources/nvidia/ngc-apps/ngc_cli/versions/4.36.6/files/ngccli_arm64.zip
sudo rm -rf /usr/local/lib/ngc-cli /usr/local/bin/ngc
sudo unzip -qo /tmp/ngccli_arm64.zip -d /usr/local/lib
sudo chmod +x /usr/local/lib/ngc-cli/ngc
sudo ln -sfn /usr/local/lib/ngc-cli/ngc /usr/local/bin/ngc
ngc --version
```



### 1.2 Credentials

The key is read from the environment, not a flag. `blueprint-deploy.sh up` runs `docker login nvcr.io` with it, so export it in the shell that calls the launcher.

```bash
export NGC_CLI_API_KEY=<your key>
export NGC_CLI_ORG=nvidia
```

---



## 2. One-time setup



### 2.1 Canonical layout

Everything below assumes this layout, the same on every machine.

```
$VSS_REPO/                                  ← one clone, this repo
├── data/                                   ← gitignored except the files in §7.7
│   ├── README.md                           ← this document
│   ├── AGENTS.md                           ← handoff for later agents
│   ├── datasets.yml                        ← dataset registry
│   ├── common.sh                           ← paths, registry reader, helpers
│   ├── setup-data.sh                       ← stages datasets into both layouts
│   ├── launch-deployment.sh                ← runs either one     (§4, §5)
│   ├── vss-warehouse-app-data/             ← NGC package, extracted
│   │   ├── models/                         ← RT-DETR, BodyPose3DNet
│   │   ├── videos/<dataset>/               ← sample clips, <sensor_id>.mp4
│   │   ├── playback/  data_log/  auto-calib/
│   ├── vss-warehouse-app-data_v3.2.0/      ← NGC download artifact, deletable
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

# Streaming testbed (VST + NVStreamer), required for RTSP runs in §4.
git clone ssh://git@gitlab-master.nvidia.com:12051/aotianw/rt-cv-3d-mv3dt-standalone.git "$STANDALONE"
chmod -R o+rX "$STANDALONE"
```

> The testbed **must** live at `$STANDALONE` (`$VSS_REPO/standalone`). The parent repo ignores it, so it stays untracked.

The §2.1 exports keep later commands readable. `setup-data.sh` accepts overrides for `APP_DATA`, `CUSTOM_DATA`, `STANDALONE`, `RTCV` and `PROFILE`. `./setup-data.sh check` lists active overrides. `VSS_REPO` always comes from the script's location and cannot be overridden.

### 2.3 Verify the host

```bash
cd "$DATA" && ./setup-data.sh check
```

Checks tools, layout, `docker/.env` sanity and the environment. Fix any `FAIL` before continuing. `warn` lines are optional capabilities.

### 2.4 Fetch models and sample videos

```bash
cd "$DATA"
./setup-data.sh fetch            # both sources
./setup-data.sh fetch --ngc      # NGC app-data package only
./setup-data.sh fetch --netapp   # custom datasets only
```

The NGC half downloads and extracts `vss-warehouse-app-data:3.2.0`: the models plus four sample video datasets. `warehouse-4cams-20mx20m-synthetic` is the MV3DT one. It is about 2.3 GB compressed.

To do `fetch --ngc` by hand (no repo on the machine, or the wrapper fails):

```bash
cd "$DATA"
export NGC_CLI_API_KEY=<your key>
export NGC_CLI_ORG=nvidia
ngc registry resource download-version nvidia/vss-warehouse/vss-warehouse-app-data:3.2.0
tar -xzf vss-warehouse-app-data_v3.2.0/vss-warehouse-app-data.tar.gz -C "$DATA"
sudo chmod -R 777 "$DATA/vss-warehouse-app-data"
```

> **Extract with** `-C "$DATA"`**, not from inside the download directory.** The tarball has one top-level `vss-warehouse-app-data/`. Extracting from inside `vss-warehouse-app-data_v3.2.0/` nests it one level deeper and breaks every path in §2.1.

No CLI? Download the tarball from the [NGC catalog](https://catalog.ngc.nvidia.com/orgs/nvidia/teams/vss-warehouse/resources/vss-warehouse-app-data?version=3.2.0) and extract it the same way.

`vss-warehouse-app-data_v3.2.0/` is only the download artifact. After extraction it holds just the 2.3 GB tarball, so you can delete it. `fetch --ngc` re-downloads it if needed.

The netapp part needs the share mounted first:

```bash
sudo mkdir -p /mnt/netapp-hq/handheld/ds_track
sudo mount //netapp-hq/handheld/ds_track /mnt/netapp-hq/handheld/ds_track -o username=$USER,domain=nvidia.com
```

Both steps are idempotent and skip work already done.

---



## 3. Datasets

A dataset is usable only when **both halves agree**: the video clips, and a calibration whose sensor ids exactly match the video filename stems.

```
sensor id  ==  video stem  ==  camInfo/<id>.yml  ==  the key you register
```

If they disagree, MV3DT cannot find the camera model and the run dies seconds in with no clear error. `setup-data.sh` checks this and refuses a mismatch.

The two deployments want different shapes *and* names for the same dataset. That mapping lives only in `datasets.yml`:


| Registry name                           | Alias    | Cams         | Origin | Notes         |
| --------------------------------------- | -------- | ------------ | ------ | ------------- |
| `warehouse-4cams-20mx20m-synthetic`     | `4cam`   | 4            | NGC    | default       |
| `Simple_Warehouse_Synthetic_040424`     | `12cam`  | 12           | netapp | tuned tracker |
| `MTMC_Warehouse_Synthetic_012424_28cam` | `28cam`  | 28           | netapp | rebuilds b28  |
| `nv-warehouse-145cams`                  | `145cam` | 145 (subset) | netapp | subset        |


The standalone deployment uses the **alias**, the blueprint uses the **name**. `setup-data.sh` accepts either.

### 3.1 Stage a dataset

```bash
cd "$DATA"
./setup-data.sh list             # what is registered, and what is actually staged
./setup-data.sh sync 4cam        # one dataset, both layouts
./setup-data.sh sync all         # everything present
```

For each dataset, `sync` derives the camera list from the video files, checks it against the calibration, generates `camInfo/` and the sparse MQTT pub/sub graph, and writes both layouts:

```
standalone/datasets/<alias>/           calibration.json  dataset.env  map.png  camInfo/
warehouse-<mode>-app/…/<name>/         calibration.json  camInfo/  images/{Top.png,imageMetadata.json}
```

It then prints the launch command for each deployment. `sync` only stages data. It starts nothing and reconfigures no deployment.

If the testbed is not cloned, `sync` says so and stages only the blueprint half.

Each dataset gets its own copy of `camInfo/` and `pub_sub_info_config.yml` in both trees, so you can `sync all` once and then launch any dataset without re-syncing.

### 3.2 What sync will and will not overwrite

- **Never writes to a registry source.** If a dataset's `calibration:` points at the file `sync` would stage (as `calibration: whbp` does), it is left untouched and any divergence is reported. Otherwise one missing clip would overwrite the source with a subset.
- **Restages** `calibration.json` **from the registry source**, as a subset when one was taken, so consumers never see sensors that cannot report. Hand-edits to the staged copy are lost on the next sync. Edit the source instead.
- **Writes no launch configuration.** `docker/.env` belongs to `launch-deployment.sh standalone`. `deepstream/configs/` and `NUM_STREAMS` belong to `launch-deployment.sh blueprint`. Each launcher writes its own config just before starting. The blueprint's two files are tracked. Revert them with:
  ```bash
  git checkout -- deploy/docker/industry-profiles/warehouse-operations/
  ```
- **Creates and self-ignores** any missing sample-data directory.



### 3.3 Adding your own dataset

1. Put the clips in `$APP_DATA/videos/<name>/` (or under `$DATA/vss-mv3dt-custom-datasets/<name>/videos/`), one `<sensor_id>.mp4` per camera.
2. Put a real `calibration.json` where the registry entry will point, with sensor ids matching those stems.
3. Add an entry to `datasets.yml` by copying the nearest one. Include `type: real` or `type: synthetic`. It selects the Sparse4D model, anchors and detector thresholds, and `blueprint-deploy.sh` refuses a custom dataset without it.
4. `./setup-data.sh sync <name>`.

A new site also needs a floor map (`map.png` / `images/Top.png`) for the BEV views. Without one, sync writes a blank placeholder and warns.

If the calibration has more cameras than clips (the 145-cam case), set `subset: true`. Sync then restricts the calibration to sensors with a matching video and stages that copy, so analytics and perception see the same cameras. Keep the full original as the registry's `calibration:` source in the asset tree, since the staged copy is overwritten on every sync.

---



## 4. Deployment A: standalone component

The component has no streaming front-end. You supply RTSP URLs or point it at recorded `.mp4` files. For RTSP, the testbed (NVStreamer + VST) republishes dataset clips as time-synchronized streams with SEI timestamps, which MV3DT needs for cross-camera sync.

Metadata path: `perception → Kafka mdx-raw → bev-fusion → Kafka mdx-bev`

> The component README is the source of truth for the customer-facing flow. This section is the local, filled-in version.



### 4.1 Configure

`$RTCV/docker/.env` has keys the launcher maintains and keys you own.

**Set by** `./launch-deployment.sh standalone up` from the dataset you launch. No need to edit these when switching datasets:

```bash
MODELS_DIR=...
NUM_CAMS=...    # follows the clips actually present, not the registry count
VIDEO_DIR=...   # the synced dataset's video dir
```

`NUM_CAMS` follows the clips on disk, which matters for subsets, e.g., the 145cam dataset with only 8 videos.

**Yours to set.** These are per-run choices:

```bash
INPUT_MODE=stream         # stream (RTSP) | file (recorded .mp4)
SAVE_VIDEO=0
GPU_DEVICE=0
DS_HTTP_PORT=9000
KAFKA_BOOTSTRAP=localhost:9092
PERCEPTION_TAG=3.3.0-26.07.2     # append -sbsa on DGX Spark
```

Values exported in your shell **override** `docker/.env` for both `stage-configs.sh` and compose. That is how the inline `NUM_CAMS=...` prefixes below work.

### 4.2 Run RTSP streams (default)

```bash
cd "$DATA"
OSD=1 DISPLAY=:1 ./launch-deployment.sh standalone up 145cam # 4cams, 12cam, 28cam, 145cam
# Then tear down
# ./launch-deployment.sh standalone down              # component + testbed
```

This writes the dataset keys into `docker/.env`, starts the synchronized streams, stages the DeepStream configs, brings up perception and BEV fusion, and registers the streams. It activates the named dataset from its per-dataset copy, so no re-sync is needed after `sync all`. It refuses only if that dataset was never synced.


| Knob             | Effect                                                        |
| ---------------- | ------------------------------------------------------------- |
| `OSD=1`          | Live tiled window. Needs a display and `xhost +`.             |
| `SAVE_VIDEO=1`   | Write `video-output/grid-view.mkv`.                           |
| `NO_FOLLOW=1`    | Skip tailing the log.                                         |
| `tracker_config` | 12cam tuned tracker. Applied automatically from the registry. |


The testbed prints the proxy URLs:

```
===== VST proxy RTSP URLs (4) — SEI-synchronized =====
  1. rtsp://127.0.0.1:30554/live/Camera
  2. rtsp://127.0.0.1:30555/live/Camera_01
  3. rtsp://127.0.0.1:30555/live/Camera_02
  4. rtsp://127.0.0.1:30555/live/Camera_03
  ...
```

The URL count must equal the camera count.  Streams may use different ports, so the launcher uses the printed URLs verbatim. The registered pairs are saved to `$RTCV/my-streams.txt`.

```bash
# Re-print with:
cd "$STANDALONE/pipeline1" && ./vst-stack.sh urls
# Goes to $RTCV/my-streams.txt
```

Expect `ds-ready: YES` in the perception log, with PERF at 0.0 FPS until registration completes. The first run at a new camera count builds a TensorRT engine (§7.6). `add-streams.sh` waits for it.

### 4.3 Run recorded files

```bash
cd "$DATA"
INPUT_MODE=file SAVE_VIDEO=1 OSD=0 ./launch-deployment.sh standalone up 4cam
```

Clips start immediately, PERF shows real FPS, the container exits at EOS and `video-output/grid-view.mkv` finalizes on its own. Each camera needs one `<sensor_id>.mp4`, which `sync` already verified.

To run the stream path by hand, with `NUM_CAMS` matching the dataset:

```bash
cd "$STANDALONE/pipeline1" && ./vst-stack.sh up
cd "$RTCV" && NUM_CAMS=<count> ./scripts/stage-configs.sh
cd "$RTCV/docker" && NUM_CAMS=<count> docker compose up -d
cd "$RTCV" && ./scripts/add-streams.sh --file my-streams.txt
```

§7.1 lists every knob those scripts read.

#### Convert the video to MP4

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

In `mdx-raw`, the same moment should carry matching timestamps across sensors, within about 33 ms. That is the sync check.

### 4.5 Visualize

```bash
cd "$RTCV"
# raw = per-camera measurements, fused = merged tracks. q to quit.
BEV_DATASET_PATH="$STANDALONE/datasets/<alias>" ./scripts/bev-visualizer.sh
BEV_SOURCE=fused BEV_DATASET_PATH="$STANDALONE/datasets/<alias>" ./scripts/bev-visualizer.sh

# Record ~60s to mp4 instead of opening a window, then Ctrl-C:
BEV_SAVE_VIDEO=1 BEV_DATASET_PATH="$STANDALONE/datasets/<alias>" ./scripts/bev-visualizer.sh
BEV_SAVE_VIDEO=1 BEV_SOURCE=fused BEV_DATASET_PATH="$MV3DT_DIR/workspace/datasets/nvwh_145cam" ./scripts/bev-visualizer.sh
```

Raw shows one point per camera view of an object. Fused shows one merged point. On Ctrl-C expect `Video saved: .../bev-output/trajectory_video_<stamp>.mp4 (N frames)`.

### 4.6 Teardown

Tear down before switching datasets. The launcher stops both the component and the testbed.

```bash
cd "$DATA" && ./launch-deployment.sh standalone down
# equivalently, by hand:
#   cd "$RTCV/docker" && docker compose --profile "*" down
#   cd "$STANDALONE/pipeline1" && ./vst-stack.sh down
```

---



## 5. Deployment B: warehouse blueprint

The full VSS warehouse stack: streaming, perception, behavior analytics, a broker, Elasticsearch + Kibana and the video-analytics API, with MV3DT as the perception stage.

> Do **not** start this with `docker compose up`. Use `deploy/docker/scripts/blueprint-deploy.sh`, and pass flags rather than editing compose files or `generated.env`.

Two things worth knowing:

- `up` always runs `down` first: containers removed, `$VSS_DATA_DIR/data_log/*` wiped, `generated.env` rewritten. `videos/`, `playback/` and `models/` are untouched.
- `-D <data-dir>` is required for **both** `up` and `down`, with the same path each time.



### 5.1 Configuration layers

The launcher generates the active config layer. Compose reads, in order:

1. `deploy/docker/containers.env`: image registry defaults
2. `deploy/docker/industry-profiles/warehouse-operations/.env`: shared profile constants
3. `deploy/docker/industry-profiles/warehouse-operations/generated.env`: **generated, do not edit**

`generated.env` is rewritten from `overrides.env` on every `up`, so put per-deployment values in `overrides.env`.

### 5.2 Deploy

```bash
cd "$DATA"
./launch-deployment.sh blueprint up 4cam        # or 12cam / 28cam / 145cam
./launch-deployment.sh blueprint up 12cam -n    # dry run, extra flags pass through
./launch-deployment.sh blueprint down
```

It resolves the alias to the registry name, points the tracker's camera map and `NUM_STREAMS` at that dataset, and runs:

```bash
blueprint-deploy.sh up -d warehouse -m mv3dt -p bp_wh_kafka -D "$APP_DATA" -s <name>
```

Override the profile, or skip tailing the perception log:

```bash
cd "$DATA"
BP_PROFILE=bp_wh_redis ./launch-deployment.sh blueprint up 4cam
NO_FOLLOW=1 ./launch-deployment.sh blueprint up 4cam
```



##### Verify: [http://localhost:30888/vst/#/live-streams](http://localhost:30888/vst/#/live-streams)

**Why a wrapper.** `blueprint-deploy.sh` defaults to `MODE=2d` when `-m` is absent and ignores `MODE` in `overrides.env`. Forgetting `-m mv3dt` gives a clean 2D stack that looks like it worked. The wrapper always passes the flag.

The rest of this section covers the underlying launcher, for flags the wrapper does not pass.

```bash
$VSS_REPO/deploy/docker/scripts/blueprint-deploy.sh --help
```


| Flag              | Meaning                                                           |
| ----------------- | ----------------------------------------------------------------- |
| `-d warehouse`    | required for `up`, the only deployment type                       |
| `-D <path>`       | required for `up` **and** `down`. Use `$APP_DATA`                 |
| `-m mv3dt`        | perception mode. The default is `2d`, so this is mandatory        |
| `-p <bp_profile>` | `bp_wh_kafka` (default here), `bp_wh_redis` or `bp_wh_auto_calib` |
| `-s <dataset>`    | override the default sample dataset                               |
| `-i <ip>`         | `HOST_IP` (default: primary IP from `ip route`)                   |
| `-E cpu`          | `gpu`                                                             |
| `-H <profile>`    | hardware profile: `H100`, `L40S`, `RTXPRO6000BW`, `DGX-SPARK`, …  |
| `-n`              | dry run: print the commands, execute nothing                      |


Valid `mv3dt` profiles are `bp_wh_kafka`, `bp_wh_redis` and `bp_wh_auto_calib`. `bp_wh` is 2d-only and **invalid here**. `NGC_CLI_API_KEY` comes from the environment, not a flag. `-s` takes the registry name, not the alias.

A deploy brings up `vss-rtvi-cv-mv3dt` (RT-DETR + MV3DT per camera), `vss-rtvi-cv-bev-fusion` and mosquitto for the cross-camera `/trck/*` topics. Metadata path: `perception → mdx-raw → bev-fusion → mdx-bev`.

### 5.3 Verify

The first deploy at a given camera count can sit at 0 FPS while TensorRT builds the detector engine (§7.6).

```bash
# Everything that should be Up. Exited (0) is normal for the *-init,
# *-topics and *-health-check one-shots.
docker ps --format 'table {{.Names}}\t{{.Status}}'

# Perception FPS, one line per stream, should settle near 30.
docker logs --since 60s vss-rtvi-cv-mv3dt 2>&1 | grep -aE 'stream_name' | tail -8
docker logs --since 60s vss-rtvi-cv-mv3dt 2>&1 | grep -a 'Active sources' | tail -1

curl -sf http://localhost:9200/_cat/health?v                       # Elasticsearch
curl -s  http://localhost:30888/vst/api/v1/sensor/list             # VST, one per camera
curl -sf -o /dev/null -w '%{http_code}\n' http://localhost:7777/   # ingress
```

The configurator has a 60 s start period and nvstreamer waits on it. If it never goes healthy, perception gets no streams, so **check it first when the stack comes up empty.** The MV3DT container is `vss-configurator-mv3dt`.

```bash
docker logs vss-configurator-mv3dt 2>&1 | tail -40
```

Transporter and Pallet classes are excluded from MV3DT 3D tracking, so no 3D boxes on them is expected.

### 5.4 Teardown and cleanup

`-D` must match the `up` path so `data_log` gets cleaned. The wrapper handles it:

```bash
cd $DATA && ./launch-deployment.sh blueprint down
# equivalent to:
#   cd $VSS_REPO/deploy/docker/scripts   && bash blueprint-deploy.sh down -D $APP_DATA
```

Cleanup between repeated deploys, without a full `down`/`up`:

```bash
cd "$VSS_REPO/deploy/docker"
bash scripts/cleanup_all_datalog.sh -e industry-profiles/warehouse-operations/generated.env
# if generated.env is missing (no successful deploy yet):
bash scripts/cleanup_all_datalog.sh -e industry-profiles/warehouse-operations/overrides.env
```

---



## 6. Troubleshooting



### Shared

**Models not found at startup.** `MODELS_DIR` in `$RTCV/docker/.env` must be a *single* assignment pointing at the package's `models/` dir. A doubled `MODELS_DIR=MODELS_DIR=...` breaks the mount. `setup-data.sh check` catches this.

**Streams accepted but never produce frames.** Sensor id mismatch. The registered key, the calibration sensor id and the `camInfo` stem must all match. Re-run `./setup-data.sh sync <dataset>`, which refuses on mismatch.

**Container cannot read its configs, camInfo or calibration.** Permissions. The containers run as non-root and mount these read-only, so a host umask of `027` makes new files unreadable inside. The tooling applies `chmod -R o+rX` to everything it stages. If you hand-copy a file into `sample-data/` or `deepstream/configs/`, apply it yourself:

```bash
chmod -R o+rX <the file or dir you copied>
```

No `sudo` needed. (`video-output/` is the exception: the container writes there, so `stage-configs.sh` makes it `777` when `SAVE_VIDEO=1`.)

**OSD absent and the pipeline is frozen**, with `libEGL warning: egl: failed to create dri2 screen` in the logs. The monitor is not attached to the GPU. Add to the perception service:

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

**OSD window does not appear.** Confirm the display, stage with `OSD=1`, then recreate the container and re-register the streams:

```bash
echo "$DISPLAY"
xhost +
cd "$RTCV"
OSD=1 ./scripts/stage-configs.sh
cd docker && docker compose up -d perception
```

`stage-configs.sh` **refuses** `SAVE_VIDEO=1`**.** Expected with `INPUT_MODE=stream`: a live recording never ends and the file sink does not rotate. Use `INPUT_MODE=file`, or set `ALLOW_UNBOUNDED_RECORDING=1`.

**BEV visualizer opens but stays empty.** It tails the live topic. Confirm `kafka-dump.sh` shows messages while it runs. One `Waiting for first message` at launch is normal.

**Port conflicts (9000 / 9092 / 1883 / 30554).** Adjust `DS_HTTP_PORT`, `KAFKA_PORT` or `MQTT_PORT` in `$RTCV/docker/.env`. After changing `DS_HTTP_PORT`, re-run `stage-configs.sh`, since the REST port is baked into the staged config. `MQTT_PORT` is applied at container start and needs no regeneration (§7.3).

### Blueprint

`NGC_CLI_API_KEY is required for 'up'`**.** Export it in the same shell. There is no flag. The exception is `-p bp_wh_auto_calib`.

`--data-dir (-D) is required`**.** Also for `down`, with the same path as `up`.

**Invalid profile for the mode.** `bp_wh` is 2d-only. Use `bp_wh_kafka`, `bp_wh_redis` or `bp_wh_auto_calib`.

**The stack came up 2D.** You called `blueprint-deploy.sh` directly without `-m mv3dt`. Setting `MODE` in `overrides.env` does not help. The profile also falls back to `bp_wh`, which pulls the LLM/VLM NIMs. Use `./launch-deployment.sh blueprint up <dataset>`.

**Tracking is nonsense after switching datasets.** The blueprint mounts `deepstream/configs/` directly, so the tracker's camera map is whatever was written last. `./launch-deployment.sh blueprint up` rewrites it from the dataset you name, but a hand-run `blueprint-deploy.sh` does not. The `standalone` target checks `generated/camInfo` and refuses on mismatch.

**Stack is up but no video anywhere.** Check `vss-configurator` health (§5.3), then confirm the dataset has both halves with `./setup-data.sh list`.

**VST live view is always 2 streams per row.** This is baked into the UI bundle (`services/vios/ui/vios-ui/src/pages/vst/LiveStream.tsx`, `<Grid size={{ xs: 12, md: 6 }}>`). MUI is 12-column, so `md: 4` gives 3 per row, `md: 3` gives 4, `md: 2` gives 6. A permanent change means rebuilding `vss-vios-ingress`.

To try a value on a running stack, use `vst-tiles.sh`. It patches only the live and replay grids in the served bundle, starting from the pristine image bundle each run. Do not `sed` `xs:12,md:6` globally: it appears 38 times and only one is the live grid.

```bash
cd $DATA
./vst-tiles.sh 3          # 3 per row, live view only
./vst-tiles.sh 2 both     # back to stock, live and replay
./vst-tiles.sh 4 replay   # replay grid only
```

Per-row must divide 12. The patch lives in the container, so recreating it loses the change. To undo:

```bash
( cd "$DATA" && ./vst-tiles.sh 2 both )
docker compose up --force-recreate vss-vios-ingress
```

**The patch shows in incognito but not a normal window.** Browser cache. nginx serves `/vst/assets/` without `Cache-Control`, and the patched file keeps its name, so the browser never refetches it. A hard reload is not reliable. In DevTools use Network > Disable cache, or Application > Storage > Clear site data. A real rebuild changes the filename hash and avoids this.

`vss-vios-ingress` **crash-loops with** `host not found in upstream "sdr-controller"`**.** Not an nginx problem. `up` stopped partway and left most containers in `Created`, so `sdr-controller` has no DNS alias and nginx exits at startup. Check:

```bash
docker ps -a --filter name=mv3dt --filter name=sdr-controller --format '{{.Names}}\t{{.Status}}'
```

If `sdr-controller`, `vss-rtvi-cv-mv3dt` and `vss-configurator-mv3dt` are `Created` while only redis, elasticsearch and postgres are `Up`, re-run `./launch-deployment.sh blueprint up <dataset>`. Restarting ingress alone does not help.

**Port already in use.** Host ports come from `overrides.env` (7777, 9000, 9200, 9092, 5601, 30888, 31000, 30554, 1883, 35000). Free the port, or change it there and redeploy. Editing `generated.env` does not stick.

**Deploy did something unexpected.** Re-run the same command with `-n` to see the resolved dataset, stream count, `COMPOSE_PROFILES` list and compose invocation.

**DGX Spark / SBSA hosts.** `-H DGX-SPARK` turns on the SBSA image variants automatically. On other Spark-class hosts pass `--use-sbsa-images` explicitly.

---



## 7. Reference



### 7.1 Standalone knobs


| Knob               | Read by                | Effect                                                                                                                                                                 |
| ------------------ | ---------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `INPUT_MODE`       | `stage-configs.sh`     | `stream` = live RTSP, registered after launch. `file` = per-camera `<sensor_id>.mp4` from `VIDEO_DIR`. Clips play once and the container exits at EOS.                 |
| `OSD`              | `stage-configs.sh`     | `1` = live tiled window with 3D boxes. Needs a host display, `DISPLAY` and `xhost +`.                                                                                  |
| `SAVE_VIDEO`       | `stage-configs.sh`     | `1` = write the tiled grid to `video-output/grid-view.mkv`. Headless-friendly. MP4 unsupported. Refused with `INPUT_MODE=stream` unless `ALLOW_UNBOUNDED_RECORDING=1`. |
| `TRACKER_CONFIG`   | `stage-configs.sh`     | Replace the sample tracker config with a dataset-tuned one (needed for 12cam).                                                                                         |
| `NUM_CAMS`         | both                   | Detector batch size, grid layout, fusion `MAX_EXPECTED_SENSORS`. Must match the dataset.                                                                               |
| `PULL`             | `launch-deployment.sh` | `1` = `docker compose pull` before launching, on either target. Off by default. See [§7.4](#74-image-freshness).                                                       |
| `BEV_SOURCE`       | `bev-visualizer.sh`    | `raw` = per-camera measurements (`mdx-raw`), `fused` = merged tracks (`mdx-bev`).                                                                                      |
| `BEV_SAVE_VIDEO`   | `bev-visualizer.sh`    | `1` = record to `bev-output/` instead of a window.                                                                                                                     |
| `BEV_DATASET_PATH` | `bev-visualizer.sh`    | Dataset dir holding `map.png` + `transforms.yml`.                                                                                                                      |




### 7.2 Blueprint endpoints

`HOST_IP` is the `-i` value, or your primary IP.


| URL                            | What                              |
| ------------------------------ | --------------------------------- |
| `http://<HOST_IP>:7777/`       | HAProxy ingress, start here       |
| `http://<HOST_IP>:30888/vst/`  | VST UI (live streams, 3D overlay) |
| `http://<HOST_IP>:31000`       | NVStreamer                        |
| `http://<HOST_IP>:5601/kibana` | Kibana dashboards                 |
| `http://<HOST_IP>:8081`        | video-analytics API               |
| `http://<HOST_IP>:9000`        | RTVI CV                           |
| `http://<HOST_IP>:35000`       | Grafana                           |




### 7.3 Generating configs by hand

`setup-data.sh sync` wraps `generate-configs.sh`, which wraps these. Run them directly only to debug the generators.

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

`--class <id> <height_m> <radius_m>` are the object-model priors: 0 to 2 person/humanoid, 3 cart, 4 box, 5 forklift.

`--mqtt_brokers` is omitted on purpose. At container start, `ds-start-mv3dt.sh` rewrites every broker `host:port` in `pub_sub_info_config.yml` to `$MQTT_HOST:$MQTT_PORT` from `docker/.env`. The generator's default (`127.0.0.1:1883`) is only a placeholder.

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

Launching recreates containers from cached images. It does not refresh moving tags such as `develop-latest`, and `--build` has no effect on services without a `build:` section.

Pass `PULL=1` to pull first:

```bash
PULL=1 ./launch-deployment.sh blueprint up 145cam
```

Pulling is off by default for air-gapped installs and reproducible experiments. To check for drift without pulling, compare the local digest with the registry:

```bash
i=vss-vios-streamprocessing
docker image inspect ghcr.io/nvidia-ai-blueprints/vss/$i:develop-latest --format '{{index .RepoDigests 0}}'
docker manifest inspect ghcr.io/nvidia-ai-blueprints/vss/$i:develop-latest | head
```

A fix in your checkout has no effect on code baked into a container. Try `PULL=1` when a merged fix seems missing.

### 7.5 Running your own fusion code

The BEV fusion service ships as a registry image, so edits under `services/rtvi/rt-cv-3d/rt-cv-bev-fusion/src` do nothing at runtime. `build-fusion.sh` builds the working copy into an image and points a deployment at it:

```bash
./build-fusion.sh on       # build vss-rt-cv-mv3dt-bev-fusion:local, pin to it
./build-fusion.sh apply    # recreate just that container (seconds, not a relaunch)
./build-fusion.sh status
./build-fusion.sh off      # back to the registry image on the next launch
```

Each action accepts `blueprint` (default) or `standalone`, e.g., `./build-fusion.sh on standalone`. Use the same target consistently because `status` reads only that target's pin.

Re-run `on` after each source edit. The pin is by tag, so an edit alone changes nothing.

The script layers the changed files onto the release image for a fast edit-run loop. CI still builds the full image.

The blueprint pin goes in `overrides.env`. The standalone pin changes `compose.yml`. Both are tracked, so the pin appears in `git diff`. Do not use `docker cp`, since the next launch discards the change.

`PULL=1` cannot refresh a local-only image, so `pull_images` passes `--ignore-pull-failures` and refreshes the rest.

### 7.6 Engine batch sizes

The RT-DETR engine is prebuilt per batch size (`_b4_`, `_b8_`, `_b12_` ship in the package). Any other camera count rebuilds from ONNX on first launch, stalling several minutes at 0 FPS before it caches. 28cam is the notable case.

### 7.7 Files this guide owns


| File                              | Tracked | Purpose                                                 |
| --------------------------------- | ------- | ------------------------------------------------------- |
| `data/README.md`                  | yes     | this document                                           |
| `data/AGENTS.md`                  | yes     | handoff for later agents                                |
| `data/datasets.yml`               | yes     | dataset registry                                        |
| `data/common.sh`                  | yes     | paths, registry reader, shared helpers                  |
| `data/setup-data.sh`              | yes     | dataset/model staging, launches nothing                 |
| `data/launch-deployment.sh`       | yes     | runs either deployment, each owns its own launch config |
| `data/build-fusion.sh`            | yes     | builds and pins a local BEV fusion image (§7.5)         |
| `data/vst-tiles.sh`               | yes     | VST tiles per row on a running ingress (§6)             |
| `data/vss-warehouse-app-data/`    | no      | NGC package                                             |
| `data/vss-mv3dt-custom-datasets/` | no      | netapp datasets                                         |
| `standalone/`                     | no      | testbed clone                                           |


`data/` is gitignored except the eight tracked files in the table above, via negation in `data/.gitignore`.

Anything else under `data/` is yours, e.g., personal notes or `.env` backups. The tooling never reads them. Live configuration is only:


| Live file                                                            | Owner             |
| -------------------------------------------------------------------- | ----------------- |
| `services/rtvi/rt-cv-3d/rt-cv-mv3dt/docker/.env`                     | standalone (§4.1) |
| `deploy/docker/industry-profiles/warehouse-operations/overrides.env` | blueprint (§5.1)  |


A copy of either under `data/` is a snapshot. Editing it changes nothing.