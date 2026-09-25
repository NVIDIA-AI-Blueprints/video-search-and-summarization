# Agent handoff

> **INTERNAL.** This directory is a personal MV3DT deployment harness on branch `test/mv3dt-docs`. It is gitignored everywhere else. Do not cite it in a bug, in Slack, or in email. The shipped documentation is `docs/mv3dt-standalone-deployment.mdx` and the component README.

The procedure for a person is [README.md](README.md). This file is the part a later agent needs before editing the harness.

## Do not

- Change these scripts to make a product bug disappear. The defect belongs in `services/rtvi/rt-cv-3d/` or in the shipped docs.
- Start or document the testbed's own perception launchers as if they were this repo. Pipeline 2 is a separate stack.
- Hand-write `/etc/sysctl.d/99-vss.conf`. The persistent kernel settings live in [Kernel Settings](https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization/blob/develop/skills/vss-build-vision-ai/references/prerequisites.md#kernel-settings).

## Layout of the work

`datasets.yml` is the only dataset registry. `setup-data.sh` stages data and launches nothing. `launch-deployment.sh` launches one target and writes only that target's config:

| Target | What it writes | What it starts |
| --- | --- | --- |
| `standalone` | `$RTCV/docker/.env` (`MODELS_DIR`, `NUM_CAMS`, `VIDEO_DIR`) | Component compose, plus pipeline 1 when `INPUT_MODE=stream` |
| `blueprint` | `deepstream/configs/` and `NUM_STREAMS` in `overrides.env` | `blueprint-deploy.sh` with `-m mv3dt` always passed |

`$RTCV` is `services/rtvi/rt-cv-3d/rt-cv-mv3dt`. The blueprint data directory is `data/vss-warehouse-app-data` (`-D` / `VSS_DATA_DIR`).

`blueprint-deploy.sh` defaults to `MODE=2d` when `-m` is absent, and it ignores `MODE` in `overrides.env`. A direct call without `-m mv3dt` comes up as a clean 2D stack.

## Standalone ReID

`ST_PROFILE` defaults to `mosquitto,kafka,reid`. Both the standalone `up` and `PULL=1` pass it as `COMPOSE_PROFILES`. Set `ST_PROFILE=mosquitto,kafka` to leave ReID off. `standalone down` uses `docker compose --profile "*" down`, so a ReID profile that was started does get removed.

As of this handoff, these four assignments in `launch-deployment.sh` ignore the environment:

```bash
OSD=0
DISPLAY=:1
SAVE_VIDEO=0
INPUT_MODE=file
```

The header still describes them as env knobs. Restore `${VAR:-default}` before trusting `OSD`, `SAVE_VIDEO`, or `INPUT_MODE` from the shell.

## What down actually removes

Compose `down` only sees its own project. Container names are global, so a name held by another project blocks the next `up`.

| Command | Project it stops | What it does not stop |
| --- | --- | --- |
| `standalone down` | Component compose, then `standalone/pipeline1/vst-stack.sh` | Pipeline 2 |
| `blueprint down` | Compose project `vss`, then `data_log` under `VSS_DATA_DIR` | Pipeline 1, pipeline 2, standalone recordings |

Pipeline 2 was started by `standalone/pipeline2/run.sh`. Its containers (`vst-ingress`, `sdr-streamprocessing`, `streamprocessing-ms-1`, `centralizedb`, `redis-server`, `nvstreamer-1`) belong to projects `docker-compose` and `nvstreamer`. Stop them with `standalone/pipeline2/down.sh`.

`reap_name_squatters` removes containers whose names this repo's compose files claim but whose project is not the one being torn down. It runs only at the end of `down`, not before `up`. It currently aborts with no message: `grep -rh` over `deploy/docker` exits 2 on the root-owned unreadable file `deploy/docker/industry-profiles/warehouse-operations/warehouse-mv3dt-app/sdrc/configs/config.yml`, and `set -euo pipefail` ends the script. The visible symptom is `State down completed` with no following `Down.`, and a later `up` still dying on `container name "/elasticsearch" is already in use` from project `pipeline2`. Fix the scan so an unreadable file does not fail the pipeline. Do not chmod that config as the fix.

## Where the topics live

| Deployment | `mdx-raw` and `mdx-bev` |
| --- | --- |
| Blueprint | Bind mount `$VSS_DATA_DIR/data_log/kafka`. `blueprint down` deletes it. |
| Standalone | Inside the `kafka` container. No volume. `compose down` deletes the container and the topics with it. |

`cleanup_all_datalog.sh` only cleans `$VSS_DATA_DIR/data_log` for the blueprint. It does not touch `$RTCV/video-output/`, `$RTCV/bev-output/`, or pipeline 2's `vst_data`. Standalone recordings survive `standalone down`.

## Files

Tracked exceptions are listed in `data/.gitignore`. Anything else under `data/` is local assets or personal notes. A copy of `docker/.env` or `overrides.env` kept here is a snapshot. Editing it changes nothing.
