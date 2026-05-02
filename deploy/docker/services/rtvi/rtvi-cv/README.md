# Perception Module

Shared DeepStream perception stack used across all blueprints: the published `vss-rt-cv` image plus a repo-local unified entrypoint (`ds-start.sh`, bind-mounted) supporting warehouse RT-DETR, RT-DETR+GDINO, and Sparse4D model families.

## Running Standalone

Blueprint compose files still expect `MDX_SAMPLE_APPS_DIR` to be the **`deployments` root** (so paths like `$MDX_SAMPLE_APPS_DIR/developer-profiles/...` resolve). The `rtvi/rtvi-cv/compose.yaml` bind mount for `ds-start.sh` is **`./ds-start.sh`** relative to that file, so it does not use `MDX_SAMPLE_APPS_DIR` and cannot double up when your env points at `.../rtvi/rtvi-cv`.

Standalone smoke test:

```bash
cd deploy/docker/services/rtvi/rtvi-cv
docker compose -f compose.yaml up
# With SDR: docker compose -f compose.yaml --profile sdr up
```

## Configuration

| Variable            | Default                                 | Description                                                                                                                |
|---------------------|-----------------------------------------|----------------------------------------------------------------------------------------------------------------------------|
| `DS_MODEL_FAMILY`   | `rtdetr-warehouse`                      | Model family: `rtdetr-warehouse` (aliases `cnn`), `rtdetr-gdino` (alias `rtdetr`), `sparse4d-warehouse` (alias `sparse4d`) |
| `DS_MODE_FLAG`      | `1`                                     | DeepStream `-m` parameter                                                                                                  |
| `DS_MESSAGE_RATE`   | `1`                                     | `--message-rate` parameter                                                                                                 |
| `DS_TRACKER_REID`   | `false`                                 | Enable `--tracker-reid` (warehouse path)                                                                                   |
| `DS_SHOW_SENSOR_ID` | `false`                                 | Enable `--show-sensor-id`                                                                                                  |
| `DS_CONFIG_FILE`    | `run_config-api-rtdetr-protobuf700.txt` | Config file (RT-DETR+GDINO path)                                                                                           |
| `MODEL_TYPE`        | `cnn`                                   | Model type for the perception app                                                                                          |
| `STREAM_TYPE`       | `kafka`                                 | Message broker: `kafka` or `redis`                                                                                         |
| `NUM_SENSORS`       | `30`                                    | Batch size (RT-DETR/GDINO)                                                                                                 |
| `MODEL_NAME_2D`     | —                                       | Set to `GDINO` for GDINO model                                                                                             |

## Blueprint Integration

Blueprints use `extends` on `compose.yaml` and add blueprint-specific volumes and environment (configs and models are bind-mounted into the image):

```yaml
services:
  perception-2d:
    extends:
      file: $MDX_SAMPLE_APPS_DIR/rtvi/rtvi-cv/compose.yaml
      service: perception
    profiles: ["my_profile"]
    container_name: vss-rtvi-cv
    volumes:
      - $MDX_SAMPLE_APPS_DIR/my-blueprint/deepstream/configs/ds-main-config.txt:/opt/.../ds-main-config.txt
    environment:
      DS_MODEL_FAMILY: rtdetr-warehouse
```

SDR services extend `perception-sdr` from the same `compose.yaml` and override only the WDM env vars that differ.

## Files

```
deploy/docker/services/rtvi/rtvi-cv/
├── ds-start.sh          # Unified entrypoint
├── compose.yaml         # Base services for `extends` + standalone
└── README.md
```
