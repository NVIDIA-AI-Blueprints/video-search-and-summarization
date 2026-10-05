# Streaming Compose Deployments

Run commands from `services/rtvi/rt-vlm/docker`. These recipes require Linux,
Docker Compose with GPU support, NVIDIA Container Toolkit, registry access,
and an RT-VLM image built from this branch. Build instructions are in
../STREAMING_VLM.md. Do not run native inference and NIM on the same GPU together.

## Native RTVI

Copy `streaming.env.example` to `.env`, set `RTVI_IMAGE` to your native-enabled
build, and configure model access with `NGC_API_KEY` or a local model mount.

```bash
docker compose -p rtvi-native -f compose.yaml -f compose.native-streaming.yaml up -d rtvi-server
curl --fail http://localhost:18094/v1/health/ready
```

This loads the model inside RTVI. NIM is not needed.

## Independent Turbo NIM

In `turbo-nim`, copy `.env.example` to `.env`. Replace both absolute directory
paths; the cache must be writable by NIM's runtime user. Authenticate to
`nvcr.io` with your own NGC entitlement, without committing credentials.

```bash
cd turbo-nim
docker compose -p rtvi-turbo up -d
docker compose -p rtvi-turbo logs -f nim
curl --fail http://localhost:18093/v1/health/ready
curl --fail http://localhost:18093/v1/models
```

The pinned experimental Turbo container loads Nano BF16 weights. DFlash is
disabled: this is an integration configuration, not the tuned throughput setup.
The default loopback binding permits host-local clients only. For bridged RTVI
or a different host, set `NIM_BIND_ADDRESS` to an appropriate host interface and
restrict access with your firewall; use an authenticated gateway outside trusted
test networks. Do not expose this unauthenticated service publicly.

## RTVI Using NIM

Back in `docker`, copy `streaming.env.example` to `.env`. Set `RTVI_IMAGE` to
this branch's image (native build support is optional). Set `VIA_VLM_ENDPOINT`
to the NIM address reachable **inside** the RTVI container and set
`VIA_VLM_OPENAI_MODEL_DEPLOYMENT_NAME` to the ID returned by `/v1/models`.
`host.docker.internal` resolves to the Linux host gateway, not its loopback.

```bash
docker compose -p rtvi-remote -f compose.yaml -f compose.nim-streaming.yaml up -d rtvi-server
curl --fail http://localhost:18094/v1/health/ready
```

RTVI decodes the RTSP feed and sends frames to persistent NIM REST sessions.
Do not use `localhost` in the endpoint unless RTVI shares the host network.
The inherited Compose stack includes Kafka and Redis; ensure host ports 9094
and `KAFKA_PORT` are free. Start only one RTVI recipe at a time.

## Optional RTSP Source

In `rtsp-test`, copy `.env.example` to `.env` and set `TEST_VIDEO` to an H.264
MP4. The publisher loops it in real time without transcoding.

```bash
cd rtsp-test
docker compose -p rtvi-rtsp-test up -d
docker compose -p rtvi-rtsp-test logs publisher mediamtx
```

Use `rtsp://host.docker.internal:18554/smoke` from same-host RTVI, or replace
the hostname with the RTSP host address. This unauthenticated test feed must
stay on a trusted network. Then run the REST test from the source directory:

```bash
python3 scripts/smoke_rtsp_streaming.py --endpoint http://localhost:18094/v1 \
  --rtsp-url rtsp://host.docker.internal:18554/smoke
```

The test registers a unique camera, requests ordered Streaming VLM captions,
checks nonempty responses, stops inference, and removes the camera even on
failure. It is a functional smoke test, not an accuracy or capacity benchmark.

Validation status: both RTVI overrides and the standalone NIM configuration
pass `docker compose config --quiet`. MediaMTX and FFmpeg were launched with
Compose and an RTSP client confirmed H.264 at 1920x1080. The offline REST-client
check passes success and failed-inference cleanup cases:
`python3 -m unittest discover -s scripts -p test_smoke_rtsp_streaming.py`
(run from the RT-VLM source directory). Full RTSP-to-RTVI-to-NIM caption
inference and the native RTSP path are not yet validated by these recipes.

Stop each deployment with its matching project name and Compose files:

```bash
docker compose -p rtvi-rtsp-test down
# Run in turbo-nim:
docker compose -p rtvi-turbo down
# Run in docker, choosing the override you started:
docker compose -p rtvi-remote -f compose.yaml -f compose.nim-streaming.yaml down
```
