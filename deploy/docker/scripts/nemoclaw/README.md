# NemoClaw VSS Installer

`init_nemoclaw.sh` bootstraps a NemoClaw sandbox on a Brev instance, configures its NVIDIA-hosted model provider, uploads the repository `skills/`, and updates OpenClaw allowed origins.

It supports two onboard providers, selected via the **required** `NEMOCLAW_PROVIDER` env var:

- `build` — NVIDIA Endpoints (`integrate.api.nvidia.com`), authenticated with `NVIDIA_API_KEY`.
- `custom` — any OpenAI-compatible endpoint (e.g. a local vLLM), configured with `NEMOCLAW_ENDPOINT_URL` and `COMPATIBLE_API_KEY`.

## What It Does

When you run `init_nemoclaw.sh`, it:

1. Runs NemoClaw onboarding if `nemoclaw` is already available, or falls back to `/home/ubuntu/NemoClaw/install.sh`.
2. Configures the OpenShell inference provider to use the remote NVIDIA-hosted model API.
3. Applies the VSS sandbox policy from `assets/vss_nemoclaw_policy.yaml`.
4. Uploads the repository `skills/` into the sandbox workspace.
5. Updates OpenClaw's allowed origins and prints the final OpenClaw UI URL when available.

## Expected Environment

This script is meant to run on a NemoClaw-ready Ubuntu machine, typically a Brev instance, with this repository already checked out.

The following repo content is expected to exist:

- `skills/`
- `assets/vss_nemoclaw_policy.yaml`
- `deploy/docker/scripts/nemoclaw/update_openclaw_config.py`

The following host tools or resources are also expected:

- `python3`
- `docker`
- `sudo`
- a working NemoClaw install source at `/home/ubuntu/NemoClaw/install.sh`, unless `nemoclaw` is already in `PATH`

## Usage

`NEMOCLAW_PROVIDER` is required. The script exits immediately if it is unset.

### `build` provider (NVIDIA Endpoints)

```bash
NEMOCLAW_PROVIDER=build \
NVIDIA_API_KEY="$NVIDIA_API_KEY" \
  bash deploy/docker/scripts/nemoclaw/init_nemoclaw.sh demo
```

Or use explicit flags:

```bash
NEMOCLAW_PROVIDER=build \
  bash deploy/docker/scripts/nemoclaw/init_nemoclaw.sh \
    --sandbox-name demo \
    --model nvidia/nemotron-3-super-120b-a12b \
    --nvidia-api-key "$NVIDIA_API_KEY"
```

### `custom` provider (OpenAI-compatible endpoint)

`NEMOCLAW_ENDPOINT_URL` and `COMPATIBLE_API_KEY` are required when `NEMOCLAW_PROVIDER=custom`:

```bash
NEMOCLAW_PROVIDER=custom \
NEMOCLAW_ENDPOINT_URL=http://host.docker.internal:8000/v1 \
NEMOCLAW_MODEL=Qwen/Qwen3.6-35B-A3B-FP8 \
COMPATIBLE_API_KEY=nemoclaw-local-qwen \
NVIDIA_API_KEY="$NVIDIA_API_KEY" \
  bash deploy/docker/scripts/nemoclaw/init_nemoclaw.sh demo
```

Equivalent with CLI flags:

```bash
NEMOCLAW_PROVIDER=custom \
  bash deploy/docker/scripts/nemoclaw/init_nemoclaw.sh \
    --sandbox-name demo \
    --model Qwen/Qwen3.6-35B-A3B-FP8 \
    --endpoint-url http://host.docker.internal:8000/v1 \
    --compatible-api-key nemoclaw-local-qwen \
    --nvidia-api-key "$NVIDIA_API_KEY"
```

### Background run on a Brev instance

```bash
nohup env NEMOCLAW_PROVIDER=build NVIDIA_API_KEY="$NVIDIA_API_KEY" \
  bash /home/ubuntu/video-search-and-summarization/deploy/docker/scripts/nemoclaw/init_nemoclaw.sh \
  > /tmp/nemoclaw_install.log 2>&1 &
```

## Options

| Option | Description | Default |
|---|---|---|
| `--sandbox-name NAME` | Target sandbox name | `demo` |
| `--model NAME` | NemoClaw inference model | `nvidia/nemotron-3-super-120b-a12b` |
| `--nvidia-base-url URL` | NVIDIA API base URL for the `build` provider | `https://integrate.api.nvidia.com/v1` |
| `--nvidia-api-key KEY` | API key for the `build` provider | `NVIDIA_API_KEY` env fallback |
| `--endpoint-url URL` | OpenAI-compatible endpoint URL (required when `NEMOCLAW_PROVIDER=custom`) | — |
| `--compatible-api-key KEY` | API key for the OpenAI-compatible endpoint (required when `NEMOCLAW_PROVIDER=custom`) | — |
| `--openclaw-config-script PATH` | Path to `update_openclaw_config.py` | `deploy/docker/scripts/nemoclaw/update_openclaw_config.py` |
| `--policy-file PATH` | Custom sandbox policy file | `assets/vss_nemoclaw_policy.yaml` |
| `--help` | Show usage help | n/a |

## Environment Variables

The script also honors these environment variables:

- `VSS_REPO_DIR`: repo root used to resolve plugin assets and the default policy file
- `NEMOCLAW_SANDBOX_NAME`
- `NEMOCLAW_PROVIDER` (**required**) — `build` or `custom`
- `NEMOCLAW_ENDPOINT_URL` — OpenAI-compatible endpoint URL; required when `NEMOCLAW_PROVIDER=custom`
- `COMPATIBLE_API_KEY` — API key for the OpenAI-compatible endpoint; required when `NEMOCLAW_PROVIDER=custom`
- `OPENSHELL_PROVIDER_NAME`
- `NEMOCLAW_MODEL`
- `NVIDIA_BASE_URL`
- `NVIDIA_API_KEY`
- `OPENCLAW_CONFIG_UPDATE_SCRIPT`
- `NEMOCLAW_POLICY_FILE`
- `VSS_CONTAINER_NAME`: explicit OpenShell gateway container name, if autodetection is not sufficient
- `VSS_NAMESPACE`: Kubernetes namespace for the sandbox pod, default `openshell`

## Expected Output

Successful runs usually include log lines like:

```text
[init_nemoclaw] Start installing/onboarding NemoClaw
[init_nemoclaw] Finished installing/onboarding NemoClaw
[init_nemoclaw] Applying custom policy file /home/ubuntu/video-search-and-summarization/assets/vss_nemoclaw_policy.yaml to sandbox demo
[init_nemoclaw] VSS skills installed
[init_nemoclaw] Updating OpenClaw config for sandbox demo using script /home/ubuntu/video-search-and-summarization/deploy/docker/scripts/nemoclaw/update_openclaw_config.py
OpenClaw UI at https://18789-<brev-id>.brevlab.com/#token=<token>
```

If the config update succeeds, the helper also prints:

- `Updated /sandbox/.openclaw/openclaw.json` or `No JSON change needed ...`
- `Brev instance ID: ...`
- `Origin allowed in OpenClaw: https://18789-<brev-id>.brevlab.com`
- `Dashboard token: ...`

## Troubleshooting

- Verify `NEMOCLAW_PROVIDER` is set (`build` or `custom`) — the script exits immediately if it is unset.
- For `NEMOCLAW_PROVIDER=custom`, verify both `NEMOCLAW_ENDPOINT_URL` and `COMPATIBLE_API_KEY` are set (or pass `--endpoint-url` / `--compatible-api-key`).
- Verify `NVIDIA_API_KEY` is set before running the installer.
- If NemoClaw onboarding fails, verify `nemoclaw` is resolvable or that `/home/ubuntu/NemoClaw/install.sh` exists and is executable.
- If the custom policy is skipped, confirm `assets/vss_nemoclaw_policy.yaml` exists or pass `--policy-file`.
- If the skills upload is skipped, verify the repo checkout includes `skills/`.
- If the skills upload cannot determine a gateway container, set `VSS_CONTAINER_NAME` explicitly.
- If the OpenClaw origin update fails, run `python3 deploy/docker/scripts/nemoclaw/update_openclaw_config.py demo` directly to inspect the underlying error.
