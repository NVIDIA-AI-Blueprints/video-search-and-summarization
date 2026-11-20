# Decision-model alerts on Jetson AGX Thor

Run the D1 + Cosmos video example with Docker Compose. D1-2B scores
a rolling window of frames. When a rule matches, the application retrieves the
event clip from VIOS and asks NVIDIA Cosmos3 Nano Reasoner to review it. The
example UI shows the current D1 decision, saved reviews, and Q&A about the
selected clip.

The example adds D1 services to the [VSS Docker deployment](../../README.md)
through an explicit Compose overlay. The example's `.env` holds stable defaults.
Its `overrides.env` is a template for your local settings.

This is an independently built example, not an officially released developer
profile. Its UI, API proxy, dependencies and tests live in this folder. It does
not copy or patch the core VSS UI and does not require a change to that UI.

## Before you start

- Use a Jetson AGX Thor that meets the [VSS prerequisites](https://docs.nvidia.com/vss/getting-started/prerequisites), with Docker Compose and the NVIDIA Container Toolkit. This example pins the VSS images to 3.3.0.
- Have an NGC API key with access to the VSS and PyTorch images and Cosmos model. D1-2B weights are fetched by the model container at the pinned revision.
- Provide two continuous H.264 RTSP/TCP sources, or two local H.264 MP4s that can loop. For the traffic example, pick one with flowing traffic and one with a sustained queue.
- Use a Thor with no conflicting VSS deployment. Some shared Compose services use fixed container names and ports even when the project name is different.

## Configure the example

From the VSS checkout that contains this example:

```bash
cd deploy/docker
example=examples/decision-model-alerts
if [ ! -e "$example/user-overrides.env" ]; then
  cp "$example/overrides.env" "$example/user-overrides.env"
fi

export VSS_APPS_DIR="$PWD"
export VSS_DATA_DIR="$HOME/vss-d1-cosmos-data"
export VSS_CONTAINER_REGISTRY=nvcr.io/nvidia/vss-core
export VSS_CONTAINER_TAG=3.3.0
mkdir -p "$VSS_DATA_DIR"
if [ ! -d "$VSS_DATA_DIR/vios-configs" ]; then
  cp -a services/vios/configs "$VSS_DATA_DIR/vios-configs"
fi

for dir in elastic/data elastic/logs kafka redis/data redis/log \
    vst/vst_data/logs/nginx_logs vst/vst_video vst/temp_files; do
  if [ ! -d "$VSS_DATA_DIR/data_log/$dir" ]; then
    install -d -m 0777 "$VSS_DATA_DIR/data_log/$dir"
  fi
done
if [ ! -d "$VSS_DATA_DIR/data_log/vst/clip_storage" ]; then
  sudo install -d -o 1001 -g 1001 -m 0770 "$VSS_DATA_DIR/data_log/vst/clip_storage"
fi
```

The data paths are dedicated to this example. The writable bind directories allow
the existing VSS containers to use their configured service users.

Export `NGC_CLI_API_KEY` using the [VSS credential setup](https://docs.nvidia.com/vss/getting-started/prerequisites#ngc-hugging-face-api-keys)
and log Docker in from the same shell:

```bash
printf '%s' "$NGC_CLI_API_KEY" | \
  docker login nvcr.io --username '$oauthtoken' --password-stdin
```

### Choose your inputs

For **RTSP**, enter `D1_FLOWING_RTSP_URL` and `D1_QUEUED_RTSP_URL` in the
ignored `user-overrides.env`. Use addresses reachable from Docker and URLs
without embedded credentials. The `D1_FLOWING_KIND` and `D1_QUEUED_KIND`
settings label a source as `live` or `recorded`.

For **local MP4s**, the optional overlay publishes each file on Compose's
private network. Export the actual absolute paths on Thor. The files must be
readable by the container. The overlay sets the input URLs and recorded labels:

```bash
export D1_FLOWING_MP4=/absolute/path/to/flowing.mp4
export D1_QUEUED_MP4=/absolute/path/to/queued.mp4
```

Use a fresh data directory and project when changing between different source
URLs. The bootstrap checks existing VIOS sensors and D1 rules and preserves
operator configuration when the same inputs are used again.

## Start and verify

Keep the shell open so the path and credential settings are available.
Compose builds the example UI from its own Dockerfile and pinned package lock.
Define the Compose command with the stock deployment and the example overlay.
**For local MP4s**, uncomment the `files+=` line before running it.

```bash
files=(-f compose.yml -f "$example/compose.yml")
# files+=(-f "$example/fixtures.compose.yml")

d1_compose() {
  docker compose "${files[@]}" \
    --env-file containers.env \
    --env-file "$example/.env" \
    --env-file "$example/user-overrides.env" "$@"
}

d1_compose config --services
d1_compose up -d --build
d1_compose ps --all
```

On first start the model containers download their weights. `d1-topics` and
`d1-bootstrap` are one-time jobs. Both should exit with code `0`. Inspect
their logs if the UI does not start:

```bash
d1_compose logs --tail 100 d1-model rtvi-vlm d1-topics d1-bootstrap
curl -fsS http://127.0.0.1:7777/api/proxy/d1/snapshot >/dev/null
```

Open **http://127.0.0.1:7777** on Thor. The example binds published ports to
loopback. From a laptop, forward the ingress port with the following command
after replacing `USER@JETSON`, and open the same address locally:

```bash
ssh -N -L 7777:127.0.0.1:7777 USER@JETSON
```

This opens the standalone example through VSS ingress. It does not add a tab to
the stock VSS application. An embedding host can use `?embed=1` to hide the
example's outer header. Adding a generic extension tab to the stock UI is a
separate integration; do not patch `Home.tsx` during the example image build.

1. **Flowing.** Check for a moving preview and a fresh D1 score. Below the
   trigger, there should be no new candidate or review.
2. **Queued.** Let VIOS record at least 15 seconds of the queued source, then
   select it. The example trigger is a yes score of at least 0.85 on three
   consecutive checks. Play the resulting clip and match its source and time to
   the Cosmos verdict. The requested window includes 15 seconds before and five
   seconds after the trigger. Scores and timing vary with the footage.
3. **Q&A.** Add a saved clip to chat and ask which lanes are moving. Compare
   the answer with that clip while the current D1 score continues to update.

For a new camera, tune the rule using its footage. To enable prompt drafting,
set `NEMOTRON_URL` in `user-overrides.env` to a private Nemotron Bolt endpoint.
Manual monitoring settings and Cosmos clip Q&A work with the default setup.

Stop the example while preserving recordings, caches, and configuration:

```bash
d1_compose stop
```

## Development checks

Use Python 3.12 and Node.js 22.23.3 for these checks. From the repository root:

```bash
bash deploy/docker/test-scripts/test-decision-model-alerts-example.sh
python3 -m venv /tmp/decision-model-alerts-tests
/tmp/decision-model-alerts-tests/bin/pip install \
  -r deploy/docker/examples/decision-model-alerts/runtime/requirements.txt \
  pytest==9.1.1
PYTHONPATH=deploy/docker/examples/decision-model-alerts/runtime \
  /tmp/decision-model-alerts-tests/bin/python -m pytest \
  deploy/docker/examples/decision-model-alerts/runtime/tests \
  deploy/docker/test-scripts/test_d1_bootstrap.py

cd deploy/docker/examples/decision-model-alerts/ui
npm ci
npm test -- --runInBand
npm run typecheck
npm run build
```

The Compose test resolves the deployment and checks that other profiles leave
D1 disabled. The bootstrap test verifies sensor and rule registration without
accessing a running deployment. The UI tests run directly in the example; they
do not rely on the stock UI workspace discovering an overlay. The example CI
workflow runs these checks and builds the standalone UI image. Run the
start-and-verify sequence above on a dedicated Thor for full hardware validation.
