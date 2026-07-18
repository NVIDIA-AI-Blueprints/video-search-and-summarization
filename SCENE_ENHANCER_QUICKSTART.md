# SceneEnhancer first-clone quickstart

This guide starts the Smart Cities alerts profile with the two-pass
`SceneEnhancer` flow from a fresh clone.

> Until updated Alert MS and UI images are published to `nvcr.io`, the two
> local image builds below are required. The registry images currently
> referenced by Compose do not contain the enrichment-routing or UI parsing
> changes.

## Prerequisites

- A supported NVIDIA GPU, driver, and NVIDIA Container Toolkit
- Docker with the Compose plugin
- JupyterLab
- An NGC API key with access to the required images and model artifacts
- A Google Maps API key if the Map tab is required

## 1. Clone and open the deployment notebook

```bash
git clone <repository-url> video-search-and-summarization
cd video-search-and-summarization
jupyter lab deploy/docker/scripts/deploy_smartcities_launchable.ipynb
```

In the notebook's **Configuration** cell, set:

```python
NGC_CLI_API_KEY = "<your-ngc-api-key>"
PROFILE = "alerts"
HARDWARE_PROFILE = "RTXPRO6000BW"  # Change for the deployment GPU.
GOOGLE_MAPS_API_KEY = "<your-google-maps-api-key>"
NVSTREAMER_VIDEO_DIR = "vss-nvstreamer-video-dir"

# Point the notebook at this clone instead of cloning another branch.
DEPLOY_SOURCE_PATH = os.path.expanduser(
    "~/video-search-and-summarization"
)

ALERTS_MODE = "verification"

# Use the host's LAN address for both values. No public IP is required.
HOST_IP_OVERRIDE = "<host-lan-ip>"
EXTERNAL_IP_OVERRIDE = "<host-lan-ip>"
```

Run the notebook through the **Deploy Profile** cell. It performs the required
Smart Cities setup:

- merges `industry-profiles/smartcities/.env` over the alerts profile;
- writes the clone-specific `NVSTREAMER_VIDEO_DIR`;
- copies the Smart Cities Compose/configuration/calibration assets;
- downloads the sample videos;
- creates
  `deploy/docker/developer-profiles/dev-profile-alerts/generated.env`;
- starts the base stack.

Do not commit API keys, `generated.env`, notebook outputs, or generated profile
copies.

No manual parser-specific `.env` variable is required. The committed Smart
Cities configuration already enables:

```yaml
vlm:
  response_parser: custom_parsers.scene_enhancer.SceneEnhancer

alert_agent:
  enrichment:
    enabled: true
    apply_response_parser: true
```

## 2. Build the source changes

From the repository root:

```bash
docker build \
  -t vss-alert-ms:scene-enhancer \
  services/alert

docker build \
  -t vss-agent-ui:scene-enhancer \
  -f services/ui/Dockerfile \
  .
```

## 3. Recreate only Alert MS and the UI

```bash
docker compose -p mdx \
  --env-file deploy/docker/developer-profiles/dev-profile-alerts/generated.env \
  -f deploy/docker/compose.yml -f - \
  up -d --no-deps --force-recreate alert-bridge vss-ui <<'EOF'
services:
  alert-bridge:
    image: vss-alert-ms:scene-enhancer
  vss-ui:
    image: vss-agent-ui:scene-enhancer
EOF
```

The `EOF` block is only a temporary local-image override. It is not part of
the production deployment design.

## 4. Verify

```bash
docker inspect vss-alert-bridge vss-agent-ui \
  --format '{{.Name}} image={{.Config.Image}} state={{.State.Status}}'

docker logs --since 5m vss-alert-bridge
```

For a newly processed collision, Elasticsearch incident metadata should
contain:

- a non-empty first-pass `info.verdict`;
- parsed second-pass fields and response status in `info.enrichment`;
- no enrichment-generated `info.vlm_response`.

The alerts UI displays `info.enrichment.description`, then falls back to
legacy `info.vlm_response.description` records and
`analyticsModule.description`. The raw second-pass response is available only
through opt-in debug logging and is not stored on each alert.

## Production deployment

The release pipeline should build and publish immutable Alert MS and UI tags
from this repository, then update the corresponding image references in:

- `deploy/docker/services/alert/compose.yml`
- `deploy/docker/services/ui/compose.yml`

After that, a fresh deployment needs neither the local builds nor the `EOF`
override:

```bash
docker compose -p mdx \
  --env-file deploy/docker/developer-profiles/dev-profile-alerts/generated.env \
  -f deploy/docker/compose.yml \
  up -d
```
