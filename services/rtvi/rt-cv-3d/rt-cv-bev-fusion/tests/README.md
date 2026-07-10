# MV3DT BEV Fusion Tests

Automated tests that run **on top of a built `vss-rt-cv-mv3dt-bev-fusion` image**,
plus pure-Python unit tests.

## Tiers

| Tier | Marker | Needs | What it covers |
|---|---|---|---|
| Unit | `unit` | python only | Pure BEV fusion logic (`measurement_fusion.fuse_frames` & helpers). |
| Integration | `integration` | docker | Brings up the built fusion image + Kafka, injects per-sensor frames into `mdx-raw`, and asserts correctly-fused `mdx-bev` output. Uses the `warehouse-sample` scenario (real sensor ids from the 4-cam sample calibration + 20×20 m world coords). Kafka only (the deployment broker). Readiness gates on the service's `/tmp/fusion_ready` sentinel. |
| E2E | `e2e` | GPU + NGC assets | Full `warehouse-3d-app-mv3dt` deploy on the 4-camera sample dataset; asserts perception FPS, fusion health, and growing `mdx-mv3dt-raw` / `mdx-bev` topics. |

## Install

```bash
python3 -m pip install -r tests/requirements.txt
```

## Run

```bash
cd tests

# Unit only (no docker)
pytest -m unit -v

# Integration against a built/pulled image (Kafka)
pytest -m integration -v --image-ref=vss-rt-cv-mv3dt-bev-fusion:local

# E2E (on a GPU host). Either point at an existing deployment ...
pytest -m e2e -v --deploy-root /path/to/vss-warehouse-compose_v3.1.0/deployments
# ... or have the test set it up + deploy + tear down (needs NGC_CLI_API_KEY, HOST_IP, HARDWARE_PROFILE):
NGC_CLI_API_KEY=... HOST_IP=... HARDWARE_PROFILE=RTXPRO6000BW \
  pytest -m e2e -v --e2e-run-setup --e2e-deploy
```

## Useful options

- `--image-ref` / env `IMAGE_REF` — image under test (default `vss-rt-cv-mv3dt-bev-fusion:local`).
- `--kafka-bootstrap` — Kafka bootstrap reachable from the test host (default `localhost:9092`).
- `--keep-stack` — leave the integration docker compose stack up for debugging.
- `--deploy-root` / env `MV3DT_DEPLOY_ROOT` — existing warehouse deployment to verify.
- `--e2e-deploy`, `--e2e-run-setup`, `--e2e-keep-up` — control the e2e deploy lifecycle.
